[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Sidecar,

    [Parameter(Mandatory = $true)]
    [string]$BrowserDir,

    [Parameter(Mandatory = $true)]
    [string]$PlaywrightDriverDir,

    [Parameter(Mandatory = $true)]
    [string]$FrontendDir,

    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [ValidateRange(30, 900)]
    [int]$HealthTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$sidecarPath = (Resolve-Path $Sidecar).Path
$browserPath = (Resolve-Path $BrowserDir).Path
$playwrightDriverPath = (Resolve-Path $PlaywrightDriverDir).Path
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

function Get-LogTail([string]$Path, [int]$Lines = 80) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "(log file was not created)"
    }
    $content = @(Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue)
    if ($content.Count -eq 0) {
        return "(log file is empty; the onefile payload may still be extracting)"
    }
    return $content -join [Environment]::NewLine
}

function Stop-CompiledSidecarTree([System.Diagnostics.Process]$Process) {
    if (-not $Process -or $Process.HasExited) {
        return
    }
    try {
        $taskKill = Start-Process `
            -FilePath "$env:SystemRoot\System32\taskkill.exe" `
            -ArgumentList @("/PID", $Process.Id, "/T", "/F") `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($taskKill.ExitCode -ne 0 -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
    try {
        $Process.WaitForExit(15000) | Out-Null
    }
    catch {
        # The process may already have disappeared between checks.
    }
}

& $sidecarPath check --data-dir $databaseCheckPath --port 18772
if ($LASTEXITCODE -ne 0) {
    throw "Compiled sidecar database check failed"
}

& $sidecarPath browser-check `
    --browser-dir $browserPath `
    --playwright-driver-dir $playwrightDriverPath `
    --output $screenshotPath
if ($LASTEXITCODE -ne 0) {
    throw "Compiled Playwright browser check failed"
}
if (-not (Test-Path $screenshotPath) -or (Get-Item $screenshotPath).Length -lt 1024) {
    throw "Compiled browser screenshot is missing or empty"
}

$port = Get-FreeLoopbackPort
$sessionSecretBytes = New-Object byte[] 32
$randomNumberGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomNumberGenerator.GetBytes($sessionSecretBytes)
}
finally {
    $randomNumberGenerator.Dispose()
}
$sessionSecret = [BitConverter]::ToString($sessionSecretBytes).Replace("-", "").ToLowerInvariant()
$previousSecret = $env:SEARCHCAR_DESKTOP_SESSION_SECRET
$env:SEARCHCAR_DESKTOP_SESSION_SECRET = $sessionSecret
$sidecarProcess = $null
$startedAt = Get-Date

try {
    $sidecarProcess = Start-Process `
        -FilePath $sidecarPath `
        -ArgumentList @(
            "serve",
            "--data-dir", $serveDataPath,
            "--frontend-dir", $frontendPath,
            "--browser-dir", $browserPath,
            "--playwright-driver-dir", $playwrightDriverPath,
            "--host", "127.0.0.1",
            "--port", $port
        ) `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    $baseUrl = "http://127.0.0.1:$port"
    $healthy = $false
    # The earlier check command primes the versioned Nuitka cache. Starting
    # the server must therefore not repeat a full onefile extraction.
    $healthDeadline = [DateTime]::UtcNow.AddSeconds($HealthTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $healthDeadline) {
        if ($sidecarProcess.HasExited) {
            $exitCode = $sidecarProcess.ExitCode
            $stdoutTail = Get-LogTail $stdoutPath
            $stderrTail = Get-LogTail $stderrPath
            throw "Compiled sidecar exited before health check (exit $exitCode).`nSTDOUT:`n$stdoutTail`nSTDERR:`n$stderrTail"
        }
        try {
            $health = Invoke-WebRequest `
                -Uri "$baseUrl/api/health" `
                -TimeoutSec 1 `
                -NoProxy `
                -SkipHttpErrorCheck
            if ($health.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        $structuredLogPath = Join-Path $serveDataPath "logs\searchcar-core.jsonl"
        $stdoutTail = Get-LogTail $stdoutPath
        $stderrTail = Get-LogTail $stderrPath
        $structuredTail = Get-LogTail $structuredLogPath
        throw "Compiled sidecar health check timed out after $HealthTimeoutSeconds seconds.`nSTDOUT:`n$stdoutTail`nSTDERR:`n$stderrTail`nSTRUCTURED LOG:`n$structuredTail"
    }

    $unauthorized = Invoke-WebRequest `
        -Uri "$baseUrl/" `
        -TimeoutSec 2 `
        -NoProxy `
        -SkipHttpErrorCheck
    if ($unauthorized.StatusCode -ne 403) {
        throw "Desktop root was available without bootstrap session"
    }

    $webSession = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $bootstrap = Invoke-WebRequest `
        -Uri "$baseUrl/desktop/bootstrap?token=$sessionSecret" `
        -WebSession $webSession `
        -NoProxy `
        -TimeoutSec 5
    if ($bootstrap.StatusCode -ne 200) {
        throw "Desktop bootstrap did not reach the SPA"
    }
    $root = Invoke-WebRequest `
        -Uri "$baseUrl/" `
        -WebSession $webSession `
        -NoProxy `
        -TimeoutSec 2
    if ($root.StatusCode -ne 200 -or $root.Content -notmatch "SearchCar Desktop") {
        throw "Desktop SPA response is invalid"
    }

    $shutdown = Invoke-WebRequest `
        -Method Post `
        -Uri "$baseUrl/desktop/shutdown?token=$sessionSecret" `
        -NoProxy `
        -TimeoutSec 5 `
        -SkipHttpErrorCheck
    if ($shutdown.StatusCode -ne 202) {
        throw "Desktop sidecar rejected graceful shutdown"
    }
    if (-not $sidecarProcess.WaitForExit(45000)) {
        throw "Desktop sidecar did not stop gracefully"
    }

    $summary = [ordered]@{
        status = "ok"
        target = "x86_64-pc-windows-msvc"
        database = "ok"
        browser = "ok"
        protected_session = "ok"
        graceful_shutdown = "ok"
        sidecar_bytes = (Get-Item $sidecarPath).Length
        browser_bytes = (Get-ChildItem $browserPath -File -Recurse | Measure-Object -Property Length -Sum).Sum
        frontend_bytes = (Get-ChildItem $frontendPath -File -Recurse | Measure-Object -Property Length -Sum).Sum
        screenshot_bytes = (Get-Item $screenshotPath).Length
        elapsed_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 2)
    }
    $summary | ConvertTo-Json | Set-Content `
        (Join-Path $outputPath.FullName "compiled-smoke.json") `
        -Encoding utf8
}
finally {
    if ($sidecarProcess -and -not $sidecarProcess.HasExited) {
        Stop-CompiledSidecarTree $sidecarProcess
    }
    $env:SEARCHCAR_DESKTOP_SESSION_SECRET = $previousSecret
}

Get-Content (Join-Path $outputPath.FullName "compiled-smoke.json")
