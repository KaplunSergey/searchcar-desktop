[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [ValidateSet('Overwrite', 'Rename')][string]$Simulation = 'Rename'
)

# This installs the real bundle and changes HKCU registration. Run ONLY on an
# ephemeral GitHub Actions runner, never against a customer's installation.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'Installer update smoke test requires an ephemeral GitHub Actions runner.'
}
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$registry = 'HKCU:\Software\SearchCar\SearchCar Desktop'
if (Test-Path -LiteralPath $registry) {
    throw 'Refusing to run against a pre-existing SearchCar registration.'
}
$testRoot = Join-Path $env:RUNNER_TEMP ('searchcar-install-' + [guid]::NewGuid().ToString('N'))
# A non-default path with spaces and Unicode catches quoting/path regressions.
$installation = Join-Path $testRoot ('SearchCar ' + [char]0x0422 + ' custom path')
$otherDirectory = Join-Path $testRoot 'Different registered copy'
New-Item -ItemType Directory -Path $installation, $otherDirectory -Force | Out-Null

function Invoke-Installer([string]$Arguments) {
    $process = Start-Process -FilePath $installerPath -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit(180000)) {
        throw 'Installer did not exit within three minutes.'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Installer exit code: $($process.ExitCode)" }
}

Invoke-Installer "/S /D=$installation"
$executable = Join-Path $installation 'searchcar-desktop.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw 'Initial install is missing the main executable.' }
$expectedHash = (Get-FileHash -LiteralPath $executable).Hash
$shell = New-Object -ComObject WScript.Shell
# Read the Unicode Shell link API directly. WScript's legacy automation path
# can replace characters outside the runner's ANSI code page with '?'.
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class SearchCarShortcut {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink {}
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                     int capacity, IntPtr findData, uint flags);
    }
    public static string Target(string file) {
        object link = new ShellLink();
        try {
            ((IPersistFile)link).Load(file, 0);
            var path = new StringBuilder(32768);
            ((IShellLinkW)link).GetPath(path, path.Capacity, IntPtr.Zero, 0);
            return path.ToString();
        } finally { Marshal.ReleaseComObject(link); }
    }
}
'@
function Assert-Shortcuts([string]$Stage) {
    foreach ($folder in @('Desktop', 'Programs')) {
        $shortcut = Join-Path ([Environment]::GetFolderPath($folder)) 'SearchCar Desktop.lnk'
        $present = Test-Path -LiteralPath $shortcut
        $legacyTarget = if ($present) { $shell.CreateShortcut($shortcut).TargetPath } else { $null }
        $target = if ($present) { [SearchCarShortcut]::Target($shortcut) } else { $null }
        [ordered]@{ stage = $Stage; shortcut = $shortcut; present = $present; target = $target; legacyTarget = $legacyTarget; expected = $executable } | ConvertTo-Json -Compress | Write-Host
        if (-not $present -or $target -ne $executable) {
            throw "Shortcut does not point to the updated application: $shortcut (stage: $Stage; actual: $target; expected: $executable)"
        }
    }
}
Assert-Shortcuts 'initial'
if ((Get-Item -LiteralPath $registry).GetValue('') -ne $installation) {
    throw 'Unexpected installer registry identity.'
}

# Reproduce a registry entry pointing to a different copy. Updater must still
# replace the binary launched by the existing shortcut, at its actual path.
Set-Item -LiteralPath $registry -Value $otherDirectory
if ($Simulation -eq 'Rename') {
    # Preserve the original missing-main-binary regression scenario.
    Move-Item -LiteralPath $executable -Destination ($executable + '.before-update')
} else {
    # Keep the file identity/path used by the shortcuts. We test replacement,
    # not Windows' ability to follow a deliberately renamed link target.
    [IO.File]::WriteAllBytes($executable, [byte[]](0, 1, 2, 3))
}
Invoke-Installer "/S /UPDATE /ARGS /D=$installation"
if (-not (Test-Path -LiteralPath $executable) -or
    (Get-FileHash -LiteralPath $executable).Hash -ne $expectedHash) {
    throw 'Update did not replace the executable in the requested directory.'
}
if (Test-Path -LiteralPath (Join-Path $otherDirectory 'searchcar-desktop.exe')) {
    throw 'Update created a second installation through stale registry data.'
}
if ((Get-Item -LiteralPath $registry).GetValue('') -ne $installation) {
    throw 'Update did not restore the actual installation path in the registry.'
}
Assert-Shortcuts 'updated'
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
Write-Host 'Passed: update preserved the actual install path and both shortcuts despite stale registry data.'
