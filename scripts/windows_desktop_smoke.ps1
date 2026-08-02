[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Sidecar,

    [Parameter(Mandatory = $true)]
    [string]$BrowserDir,

    [Parameter(Mandatory = $true)]
    [string]$FrontendDir,

    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$sidecarPath = (Resolve-Path $Sidecar).Path
$browserPath = (Resolve-Path $BrowserDir).Path
$frontendPath = (Resolve-Path $FrontendDir).Path
$runtimePath = New-Item -ItemType Directory -Force $RuntimeRoot
$outputPath = New-Item -ItemType Directory -Force $OutputDir
$databaseCheckPath = Join-Path $runtimePath.FullName "database-check"
$serveDataPath = Join-Path $runtimePath.FullName "serve-data"
$screenshotPath = Join-Path $outputPath.FullName "compiled-browser-check.png"
$stdoutPath = Join-Path $outputPath.FullName "compiled-sidecar.stdout.log"
$stderrPath = Join-Path $outputPath.FullName "compiled-sidecar.stderr.log"

function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $listener.Start()
    try {
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

& $sidecarPath check --data-dir $databaseCheckPath --port 18772
if ($LASTEXITCODE -ne 0) {
    throw "Compiled sidecar database check failed"
}

& $sidecarPath browser-check `
    --browser-dir $browserPath `
    --output $screenshotPath
if ($LASTEXITCODE -ne 0) {
    throw "Compiled Playwright browser check failed"
}
if (-not (Test-Path $screenshotPath) -or (Get-Item $screenshotPath).Length -lt 1024) {
    throw "Compiled browser screenshot is missing or empty"
}

$port = Get-FreeLoopbackPort
$sessionSecret = [Convert]::ToHexString(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()
$previousSecret = $env:SEARCHCAR_DESKTOP_SESSION_SECRET
$env:SEARCHCAR_DESKTOP_SESSION_SECRET = $sessionSecret
$sidecarProcess = $null

try {
    $sidecarProcess = Start-Process `
        -FilePath $sidecarPath `
        -ArgumentList @(
            "serve",
            "--data-dir", $serveDataPath,
            "--frontend-dir", $frontendPath,
            "--browser-dir", $browserPath,
            "--host", "127.0.0.1",
            "--port", $port
        ) `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $baseUrl = "http://127.0.0.1:$port"
    $healthy = $false
    # A Nuitka onefile binary may need extra time for its first extraction on
    # a fresh Windows runner, especially while antivirus scanning is active.
    for ($attempt = 0; $attempt -lt 360; $attempt++) {
        if ($sidecarProcess.HasExited) {
            throw "Compiled sidecar exited before health check"
        }
        try {
            $health = Invoke-WebRequest `
                -Uri "$baseUrl/api/health" `
                -TimeoutSec 1 `
                -SkipHttpErrorCheck
            if ($health.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $healthy) {
        throw "Compiled sidecar health check timed out"
    }

    $unauthorized = Invoke-WebRequest `
        -Uri "$baseUrl/" `
        -TimeoutSec 2 `
        -SkipHttpErrorCheck
    if ($unauthorized.StatusCode -ne 403) {
        throw "Desktop root was available without bootstrap session"
    }

    $webSession = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $bootstrap = Invoke-WebRequest `
        -Uri "$baseUrl/desktop/bootstrap?token=$sessionSecret" `
        -WebSession $webSession `
        -TimeoutSec 5
    if ($bootstrap.StatusCode -ne 200) {
        throw "Desktop bootstrap did not reach the SPA"
    }
    $root = Invoke-WebRequest `
        -Uri "$baseUrl/" `
        -WebSession $webSession `
        -TimeoutSec 2
    if ($root.StatusCode -ne 200 -or $root.Content -notmatch "SearchCar Desktop") {
        throw "Desktop SPA response is invalid"
    }

    $summary = [ordered]@{
        status = "ok"
        target = "x86_64-pc-windows-msvc"
        database = "ok"
        browser = "ok"
        protected_session = "ok"
        sidecar_bytes = (Get-Item $sidecarPath).Length
        screenshot_bytes = (Get-Item $screenshotPath).Length
    }
    $summary | ConvertTo-Json | Set-Content `
        (Join-Path $outputPath.FullName "compiled-smoke.json") `
        -Encoding utf8
}
finally {
    if ($sidecarProcess -and -not $sidecarProcess.HasExited) {
        Stop-Process -Id $sidecarProcess.Id -Force
        $sidecarProcess.WaitForExit()
    }
    $env:SEARCHCAR_DESKTOP_SESSION_SECRET = $previousSecret
}

Get-Content (Join-Path $outputPath.FullName "compiled-smoke.json")
