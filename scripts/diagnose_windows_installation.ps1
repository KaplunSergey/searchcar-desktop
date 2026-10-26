[CmdletBinding()]
param([string]$OutputPath)

# Read-only inspection. Only the report is written; no installers are run,
# shortcuts changed, registry values changed, or application data collected.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this diagnostic on Windows.' }
. (Join-Path $PSScriptRoot 'windows_shortcut.ps1')
if (-not $OutputPath) {
    $OutputPath = Join-Path ([Environment]::GetFolderPath('Desktop')) (
        'SearchCar-installation-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    )
}
$issues = [System.Collections.Generic.List[string]]::new()
$findings = [System.Collections.Generic.List[object]]::new()
$candidates = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

function Add-Finding([string]$Severity, [string]$Code, [string]$Summary, [object]$Evidence) {
    [void]$findings.Add([ordered]@{
        severity = $Severity
        code = $Code
        summary = $Summary
        evidence = $Evidence
    })
}

function Test-SamePath([string]$Left, [string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    try {
        $leftPath = [IO.Path]::GetFullPath($Left).TrimEnd('\')
        $rightPath = [IO.Path]::GetFullPath($Right).TrimEnd('\')
        return $leftPath -ieq $rightPath
    } catch {
        return $Left.TrimEnd('\') -ieq $Right.TrimEnd('\')
    }
}

function Add-InstallationDirectory([string]$Directory) {
    if ($Directory) {
        $Directory = $Directory.Trim('"')
        foreach ($name in @('searchcar-desktop.exe', 'SearchCar Desktop.exe')) {
            [void]$candidates.Add((Join-Path $Directory $name))
        }
    }
}

$installations = @(
    foreach ($hive in @('HKCU', 'HKLM')) {
        foreach ($view in @('Registry64', 'Registry32')) {
            $hiveName = if ($hive -eq 'HKCU') { 'CurrentUser' } else { 'LocalMachine' }
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
                [Microsoft.Win32.RegistryHive]$hiveName, [Microsoft.Win32.RegistryView]$view
            )
            try {
                foreach ($keyName in @(
                    'Software\Microsoft\Windows\CurrentVersion\Uninstall\SearchCar Desktop',
                    'Software\SearchCar\SearchCar Desktop'
                )) {
                    $key = $base.OpenSubKey($keyName)
                    if (-not $key) { continue }
                    try {
                        $location = [string]$key.GetValue('InstallLocation', '')
                        $savedLocation = [string]$key.GetValue('', '')
                        Add-InstallationDirectory $location
                        Add-InstallationDirectory $savedLocation
                        [ordered]@{
                            hive = $hive; view = $view; key = $keyName
                            version = $key.GetValue('DisplayVersion', '')
                            install_location = $location; saved_location = $savedLocation
                            main_binary = $key.GetValue('MainBinaryName', '')
                        }
                    } finally { $key.Dispose() }
                }
            } finally { $base.Dispose() }
        }
    }
)

$processes = @()
try {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'searchcar-desktop.exe' OR Name = 'SearchCar Desktop.exe'" |
        ForEach-Object {
            if ($_.ExecutablePath) { [void]$candidates.Add($_.ExecutablePath) }
            [ordered]@{ pid = $_.ProcessId; executable = $_.ExecutablePath }
        })
} catch { $issues.Add('Could not read running application paths: ' + $_.Exception.Message) }

$shell = New-Object -ComObject WScript.Shell
$shortcuts = @(
    $roots = @(
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('CommonDesktopDirectory'),
        [Environment]::GetFolderPath('StartMenu'),
        [Environment]::GetFolderPath('CommonStartMenu'),
        (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar')
    ) | Select-Object -Unique
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        foreach ($file in Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -File -ErrorAction SilentlyContinue) {
            try {
                $link = $shell.CreateShortcut($file.FullName)
                $target = Get-SearchCarShortcutTarget $file.FullName
                if ($file.BaseName -notlike '*SearchCar*' -and $target -notlike '*SearchCar*') { continue }
                if ($target) { [void]$candidates.Add($target) }
                [ordered]@{ shortcut = $file.FullName; target = $target; working_directory = $link.WorkingDirectory }
            } catch { $issues.Add('Could not inspect shortcut: ' + $file.FullName) }
        }
    }
)
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
foreach ($root in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($root) { Add-InstallationDirectory (Join-Path $root 'SearchCar Desktop') }
}

$executables = @(
    foreach ($path in ($candidates | Sort-Object)) {
        $exists = Test-Path -LiteralPath $path -PathType Leaf
        $info = if ($exists) { [Diagnostics.FileVersionInfo]::GetVersionInfo($path) } else { $null }
        $file = if ($exists) { Get-Item -LiteralPath $path } else { $null }
        [ordered]@{
            path = $path; exists = $exists
            product = if ($info) { $info.ProductName } else { $null }
            version = if ($info) { $info.ProductVersion } else { $null }
            modified_at_utc = if ($file) { $file.LastWriteTimeUtc.ToString('o') } else { $null }
            size_bytes = if ($file) { $file.Length } else { $null }
            sha256 = if ($exists) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash } else { $null }
        }
    }
)

$existingExecutables = @($executables | Where-Object { $_.exists })
$existingDirectories = @(
    $existingExecutables | ForEach-Object { Split-Path -Parent $_.path } | Sort-Object -Unique
)
if ($existingDirectories.Count -gt 1) {
    Add-Finding 'warning' 'multiple_application_copies' `
        'Several SearchCar application directories were found. A shortcut or updater may use a different copy.' `
        $existingDirectories
}

$versions = @(
    $existingExecutables | ForEach-Object { $_.version } | Where-Object { $_ } | Sort-Object -Unique
)
if ($versions.Count -gt 1) {
    Add-Finding 'warning' 'different_application_versions' `
        'The discovered SearchCar executables have different versions.' $versions
}

$missingShortcutTargets = @(
    $shortcuts | Where-Object {
        $_.target -and -not (Test-Path -LiteralPath $_.target -PathType Leaf)
    } | ForEach-Object { [ordered]@{ shortcut = $_.shortcut; target = $_.target } }
)
if ($missingShortcutTargets.Count -gt 0) {
    Add-Finding 'warning' 'broken_shortcuts' `
        'Some SearchCar shortcuts point to files that no longer exist.' $missingShortcutTargets
}

$shortcutTargets = @(
    $shortcuts | ForEach-Object { $_.target } | Where-Object {
        $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
    } | Sort-Object -Unique
)
if ($shortcutTargets.Count -gt 1) {
    Add-Finding 'warning' 'shortcuts_open_different_copies' `
        'SearchCar shortcuts point to different executable files.' $shortcutTargets
}

foreach ($installation in $installations) {
    if ($installation.install_location -and $installation.saved_location -and
        -not (Test-SamePath $installation.install_location $installation.saved_location)) {
        Add-Finding 'warning' 'registry_location_conflict' `
            'Windows registration contains different current and saved installation locations.' `
            ([ordered]@{
                hive = $installation.hive
                view = $installation.view
                install_location = $installation.install_location
                saved_location = $installation.saved_location
            })
    }
}

foreach ($process in $processes) {
    if (-not $process.executable -or $shortcutTargets.Count -eq 0) { continue }
    $matchesShortcut = @($shortcutTargets | Where-Object {
        Test-SamePath $_ $process.executable
    }).Count -gt 0
    if (-not $matchesShortcut) {
        Add-Finding 'warning' 'running_copy_differs_from_shortcuts' `
            'The running SearchCar executable is not the file referenced by any discovered shortcut.' `
            ([ordered]@{ pid = $process.pid; executable = $process.executable; shortcut_targets = $shortcutTargets })
    }
}

if ($processes.Count -eq 0) {
    Add-Finding 'info' 'application_not_running' `
        'SearchCar was not running. Run this diagnostic again after opening it through the problematic shortcut.' @()
}
if ($findings.Count -eq 0) {
    Add-Finding 'info' 'no_path_conflict_detected' `
        'No conflicting application paths or versions were detected in this snapshot.' @()
}

[ordered]@{
    created_at = [DateTime]::UtcNow.ToString('o')
    computer = [ordered]@{
        windows_version = [Environment]::OSVersion.VersionString
        os_64_bit = [Environment]::Is64BitOperatingSystem
        process_architecture = $env:PROCESSOR_ARCHITECTURE
    }
    findings = @($findings.ToArray())
    installations = $installations; processes = $processes
    shortcuts = $shortcuts; executables = $executables; issues = @($issues.ToArray())
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Report saved: $OutputPath"
foreach ($finding in $findings) {
    Write-Host ("[{0}] {1}" -f $finding.severity.ToUpperInvariant(), $finding.summary)
}
Write-Host 'The report contains local installation paths (including your Windows username), versions and shortcuts.'
Write-Host 'No changes were made to SearchCar installations or your data.'
