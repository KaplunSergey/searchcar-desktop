[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$OutputDir)

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'Installed-shortcut regression requires an ephemeral GitHub Actions runner.'
}
. (Join-Path $PSScriptRoot 'windows_shortcut.ps1')
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$reportPath = Join-Path $OutputDir 'installation.json'
$installed = (Get-Item -LiteralPath 'HKCU:\Software\SearchCar\SearchCar Desktop').GetValue('')
$expected = Join-Path $installed 'searchcar-desktop.exe'
if (-not $expected.Contains([char]0x0422) -or -not (Test-Path -LiteralPath $expected -PathType Leaf)) {
    throw 'Run the installer Unicode-path smoke before this regression.'
}

function Assert-HealthyReport {
    & (Join-Path $PSScriptRoot 'diagnose_windows_installation.ps1') -OutputPath $reportPath
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding utf8 | ConvertFrom-Json
    if (-not $report.findings) { throw 'Installation diagnostic produced no findings summary.' }
    if (@($report.findings | Where-Object { $_.severity -eq 'warning' }).Count -gt 0) {
        throw 'Healthy Unicode installation produced diagnostic warnings.'
    }
    foreach ($folder in @('Desktop', 'Programs')) {
        $shortcut = Join-Path ([Environment]::GetFolderPath($folder)) 'SearchCar Desktop.lnk'
        $entries = @($report.shortcuts | Where-Object { $_.shortcut -eq $shortcut })
        if ($entries.Count -ne 1 -or $entries[0].target -ne $expected) {
            throw "Diagnostic did not preserve the Unicode shortcut target: $shortcut"
        }
    }
}

Assert-HealthyReport
# A real broken shortcut must still be reported, not normalized or ignored.
$fixtureId = [guid]::NewGuid().ToString('N')
$missing = Join-Path $env:RUNNER_TEMP "searchcar-missing-$fixtureId\searchcar-desktop.exe"
$fixture = Join-Path ([Environment]::GetFolderPath('Desktop')) "SearchCar diagnostic-test-$fixtureId.lnk"
if (Test-Path -LiteralPath $fixture) { throw 'Refusing to overwrite an existing shortcut.' }
$shell = New-Object -ComObject WScript.Shell
try {
    $link = $shell.CreateShortcut($fixture)
    $link.TargetPath = $missing
    $link.Save()
    if ((Get-SearchCarShortcutTarget $fixture) -ne $missing) { throw 'Wrong target was hidden by the Unicode reader.' }
    $brokenReport = Join-Path $OutputDir 'installation-broken-fixture.json'
    & (Join-Path $PSScriptRoot 'diagnose_windows_installation.ps1') -OutputPath $brokenReport
    $report = Get-Content -LiteralPath $brokenReport -Raw -Encoding utf8 | ConvertFrom-Json
    $finding = @($report.findings | Where-Object { $_.code -eq 'broken_shortcuts' })
    if ($finding.Count -ne 1 -or -not @($finding[0].evidence | Where-Object { $_.shortcut -eq $fixture -and $_.target -eq $missing }).Count) {
        throw 'Diagnostic failed to detect the deliberately broken shortcut.'
    }
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Force }
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}
Assert-HealthyReport
Write-Host 'Passed: Unicode paths remain intact, broken shortcuts are detected, and fixture cleanup restores healthy diagnostics.'
