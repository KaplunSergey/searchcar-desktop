[CmdletBinding()]
param(
    [switch]$IncludeDiagnosticStandalone,
    [switch]$ResumeAfterSidecar,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = $PSScriptRoot
$BuildPython = Join-Path $ProjectRoot ".desktop-build-venv-windows\Scripts\python.exe"
$ArtifactDirectory = Join-Path $ProjectRoot "work\windows-artifact"
$SmokeDirectory = Join-Path $ProjectRoot "work\windows-smoke"
$BuildLog = Join-Path $ProjectRoot "SearchCar Desktop Windows build.log"
$BuildSucceeded = $false
$TranscriptStarted = $false
$TemporaryTestRoot = $null

function Require-Command([string]$Name, [string]$InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is not installed. $InstallHint"
    }
}

function Import-MsvcEnvironment {
    if (Get-Command cl.exe -ErrorAction SilentlyContinue) {
        return
    }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        throw "Visual Studio 2022 Build Tools with the Desktop development with C++ workload is required."
    }
    $installation = (& $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath).Trim()
    if (-not $installation) {
        throw "Visual Studio C++ x64 build tools were not found. Install the Desktop development with C++ workload."
    }
    $developerCommand = Join-Path $installation "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $developerCommand -PathType Leaf)) {
        throw "VsDevCmd.bat was not found in the Visual Studio installation."
    }

    $developerEnvironmentCommand = "`"$developerCommand`" -no_logo -arch=x64 -host_arch=x64 && set"
    $environment = & $env:COMSPEC /d /s /c $developerEnvironmentCommand
    foreach ($line in $environment) {
        if ($line -notmatch "=") {
            continue
        }
        $name, $value = $line -split "=", 2
        if ($name -and -not $name.StartsWith("=")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    Require-Command "cl.exe" "Open Developer PowerShell for VS 2022 and run the build again."
}

try {
    Set-Location $ProjectRoot
    try {
        Start-Transcript -Path $BuildLog -Force | Out-Null
        $TranscriptStarted = $true
    }
    catch {
        Write-Warning "Transcript log is unavailable; continuing without it: $BuildLog"
    }

    if (-not [Environment]::Is64BitProcess) {
        throw "Use x64 PowerShell 7. The 32-bit shell cannot build SearchCar."
    }
    Require-Command "python.exe" "Install x64 Python 3.12."
    Require-Command "node.exe" "Install x64 Node.js 22."
    Require-Command "pnpm.cmd" "Run: npm install --global pnpm@11.17.0"
    Require-Command "rustup.exe" "Install rustup for the MSVC toolchain."
    Import-MsvcEnvironment

    $pythonVersion = (& python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    $pythonArchitecture = (& python.exe -c "import platform; print(platform.machine().lower())").Trim()
    if ($pythonVersion -ne "3.12" -or $pythonArchitecture -notin @("amd64", "x86_64")) {
        throw "SearchCar requires x64 Python 3.12; found Python $pythonVersion ($pythonArchitecture)."
    }
    $nodeMajor = [int]((& node.exe -p "process.versions.node.split('.')[0]").Trim())
    $nodeArchitecture = (& node.exe -p "process.arch").Trim()
    if ($nodeMajor -ne 22 -or $nodeArchitecture -ne "x64") {
        throw "SearchCar requires x64 Node.js 22; found Node $nodeMajor ($nodeArchitecture)."
    }

    rustup.exe toolchain install stable-x86_64-pc-windows-msvc --profile minimal
    rustup.exe default stable-x86_64-pc-windows-msvc
    rustup.exe target add x86_64-pc-windows-msvc

    $env:CARGO_TERM_COLOR = "always"
    $env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
    $env:PYTHONUTF8 = "1"

    if (-not $ResumeAfterSidecar) {
        if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
            python.exe -m venv (Join-Path $ProjectRoot ".desktop-build-venv-windows")
        }

        Write-Host "Installing pinned dependencies..." -ForegroundColor Cyan
        & $BuildPython -m pip install -r backend/requirements-desktop-build.txt
        pnpm.cmd install --frozen-lockfile
        pnpm.cmd version:check

        Write-Host "Running backend and frontend tests..." -ForegroundColor Cyan
        $env:PYTHONPATH = Join-Path $ProjectRoot "backend"
        $TemporaryTestRoot = Join-Path $env:TEMP `
            ("searchcar-local-build-tests-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force $TemporaryTestRoot | Out-Null
        $env:STORAGE_ROOT = Join-Path $TemporaryTestRoot "storage"
        $testDatabase = (Join-Path $TemporaryTestRoot "searchcar-tests.sqlite3").Replace("\", "/")
        $env:DATABASE_URL = "sqlite+pysqlite:///$testDatabase"
        & $BuildPython -m pytest backend/tests -q
        pnpm.cmd build
        pnpm.cmd test
        pnpm.cmd desktop:frontend:build

        Write-Host "Preparing Chromium and the Playwright driver..." -ForegroundColor Cyan
        & $BuildPython scripts/prepare_desktop_browser.py
        & $BuildPython scripts/prepare_desktop_playwright_driver.py

        if ($IncludeDiagnosticStandalone) {
            Write-Host "Building optional diagnostic standalone backend..." -ForegroundColor Cyan
            & $BuildPython scripts/build_desktop_sidecar.py --mode standalone
        }

        Write-Host "Building onefile production backend..." -ForegroundColor Cyan
        & $BuildPython scripts/build_desktop_sidecar.py --mode onefile
    }
    else {
        Write-Host "Resuming from the existing compiled sidecar..." -ForegroundColor Cyan
        $resumeInputs = @(
            "desktop/src-tauri/binaries/searchcar-core-x86_64-pc-windows-msvc.exe",
            "desktop/runtime/browsers",
            "desktop/runtime/playwright-driver",
            "desktop/dist/index.html"
        )
        foreach ($resumeInput in $resumeInputs) {
            if (-not (Test-Path -LiteralPath $resumeInput)) {
                throw "Cannot resume: required build input is missing: $resumeInput"
            }
        }
    }

    Write-Host "Smoke-testing the compiled backend and browser..." -ForegroundColor Cyan
    if (Test-Path -LiteralPath $SmokeDirectory) {
        Remove-Item -LiteralPath $SmokeDirectory -Recurse -Force
    }
    & (Join-Path $ProjectRoot "scripts\windows_desktop_smoke.ps1") `
        -Sidecar "desktop/src-tauri/binaries/searchcar-core-x86_64-pc-windows-msvc.exe" `
        -BrowserDir "desktop/runtime/browsers" `
        -PlaywrightDriverDir "desktop/runtime/playwright-driver" `
        -FrontendDir "desktop/dist" `
        -RuntimeRoot (Join-Path $SmokeDirectory "runtime") `
        -OutputDir (Join-Path $SmokeDirectory "results")

    Write-Host "Building the unsigned Windows x64 NSIS installer..." -ForegroundColor Cyan
    $nsisOutput = Join-Path $ProjectRoot "desktop\src-tauri\target\release\bundle\nsis"
    if (Test-Path -LiteralPath $nsisOutput) {
        Remove-Item -LiteralPath $nsisOutput -Recurse -Force
    }
    pnpm.cmd desktop:tauri:build --bundles nsis --no-sign --ci

    $installers = @(
        Get-ChildItem $nsisOutput -Filter "*-setup.exe" -File
    )
    if ($installers.Count -ne 1) {
        throw "Expected one NSIS installer, found $($installers.Count)."
    }
    if (Test-Path -LiteralPath $ArtifactDirectory) {
        Remove-Item -LiteralPath $ArtifactDirectory -Recurse -Force
    }
    New-Item -ItemType Directory -Force $ArtifactDirectory | Out-Null
    $artifactInstaller = Join-Path $ArtifactDirectory "SearchCar-Desktop-Windows-x64-setup.exe"
    Copy-Item -LiteralPath $installers[0].FullName -Destination $artifactInstaller -Force
    $artifactHash = (Get-FileHash $artifactInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    "$artifactHash  SearchCar-Desktop-Windows-x64-setup.exe" | Set-Content `
        (Join-Path $ArtifactDirectory "SHA256SUMS.txt") -Encoding ascii

    $privateKeyPresent = -not [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY)
    $passwordPresent = -not [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD)
    if ($privateKeyPresent -xor $passwordPresent) {
        throw "Set both Tauri updater signing variables, or leave both unset for a local pilot build."
    }
    $sourceSignature = "$($installers[0].FullName).sig"
    if ($privateKeyPresent -and -not (Test-Path -LiteralPath $sourceSignature -PathType Leaf)) {
        pnpm.cmd exec tauri signer sign $installers[0].FullName
    }
    $updaterSigned = Test-Path -LiteralPath $sourceSignature -PathType Leaf
    if ($updaterSigned) {
        Copy-Item -LiteralPath $sourceSignature `
            -Destination "$artifactInstaller.sig" -Force
    }

    Write-Host ""
    Write-Host "SearchCar Windows build completed successfully." -ForegroundColor Green
    Write-Host "Installer: $artifactInstaller"
    Write-Host "SHA-256: $artifactHash"
    Write-Host "Updater signature created: $updaterSigned"
    Write-Host "Windows OS code signing: disabled (pilot build)"
    $BuildSucceeded = $true
}
catch {
    Write-Host ""
    Write-Host "SearchCar Windows build failed:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Build log: $BuildLog"
}
finally {
    $temporaryTestRootPath = [string]$TemporaryTestRoot
    if ($temporaryTestRootPath -and [System.IO.Directory]::Exists($temporaryTestRootPath)) {
        try {
            [System.IO.Directory]::Delete($temporaryTestRootPath, $true)
        }
        catch {
            Write-Warning "Could not remove temporary test directory: $temporaryTestRootPath"
        }
    }
    if ($TranscriptStarted) {
        try {
            Stop-Transcript | Out-Null
        }
        catch {
            Write-Warning "Transcript could not be stopped cleanly."
        }
    }
    if (-not $NoPause) {
        Read-Host "Press Enter to close this window"
    }
}

if (-not $BuildSucceeded) {
    exit 1
}
