[CmdletBinding()]
param([string]$OutputPath)

# Read-only inspection. Only the report is written; no installers are run,
# shortcuts changed, registry values changed, or application data collected.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this diagnostic on Windows.' }
if (-not $OutputPath) {
    $OutputPath = Join-Path ([Environment]::GetFolderPath('Desktop')) (
        'SearchCar-installation-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    )
}
$issues = [System.Collections.Generic.List[string]]::new()
$candidates = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

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
                if ($file.BaseName -notlike '*SearchCar*' -and $link.TargetPath -notlike '*SearchCar*') { continue }
                if ($link.TargetPath) { [void]$candidates.Add($link.TargetPath) }
                [ordered]@{ shortcut = $file.FullName; target = $link.TargetPath; working_directory = $link.WorkingDirectory }
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
        [ordered]@{
            path = $path; exists = $exists
            product = if ($info) { $info.ProductName } else { $null }
            version = if ($info) { $info.ProductVersion } else { $null }
            sha256 = if ($exists) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash } else { $null }
        }
    }
)
[ordered]@{
    created_at = [DateTime]::UtcNow.ToString('o')
    installations = $installations; processes = $processes
    shortcuts = $shortcuts; executables = $executables; issues = @($issues.ToArray())
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Report saved: $OutputPath"
Write-Host 'The report contains local installation paths (including your Windows username), versions and shortcuts.'
Write-Host 'No changes were made to SearchCar installations or your data.'
