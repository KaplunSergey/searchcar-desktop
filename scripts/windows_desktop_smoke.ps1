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
Add-Type -AssemblyName System.Net.Http
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
$databaseStdoutPath = Join-Path $outputPath.FullName "compiled-database-check.stdout.log"
$databaseStderrPath = Join-Path $outputPath.FullName "compiled-database-check.stderr.log"
$browserStdoutPath = Join-Path $outputPath.FullName "compiled-browser-check.stdout.log"
$browserStderrPath = Join-Path $outputPath.FullName "compiled-browser-check.stderr.log"

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

function Invoke-LoopbackHttpRequest {
    param(
        [Parameter(Mandatory = $true)]
        [System.Net.Http.HttpClient]$Client,

        [Parameter(Mandatory = $true)]
        [ValidateSet("GET", "POST")]
        [string]$Method,

        [Parameter(Mandatory = $true)]
        [string]$Uri
    )

    $response = $null
    $requestBody = $null
    try {
        if ($Method -eq "POST") {
            $requestBody = [System.Net.Http.StringContent]::new("")
            $response = $Client.PostAsync($Uri, $requestBody).GetAwaiter().GetResult()
        }
        else {
            $response = $Client.GetAsync($Uri).GetAwaiter().GetResult()
        }
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return [PSCustomObject]@{
            StatusCode = [int]$response.StatusCode
            Content = [string]$content
        }
    }
    finally {
        if ($response) {
            $response.Dispose()
        }
        if ($requestBody) {
            $requestBody.Dispose()
        }
    }
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

function Invoke-LoggedSidecarCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$StandardOutputPath,

        [Parameter(Mandatory = $true)]
        [string]$StandardErrorPath
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $sidecarPath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        $startInfo.ArgumentList.Add($argument)
    }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "$Description could not start"
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        [System.IO.File]::WriteAllText($StandardOutputPath, $stdout)
        [System.IO.File]::WriteAllText($StandardErrorPath, $stderr)
        if ($process.ExitCode -ne 0) {
            throw "$Description failed (exit $($process.ExitCode)).`nSTDOUT:`n$(Get-LogTail $StandardOutputPath)`nSTDERR:`n$(Get-LogTail $StandardErrorPath)"
        }
        if ($stdout) {
            Write-Host $stdout.TrimEnd()
        }
        if ($stderr) {
            Write-Host $stderr.TrimEnd()
        }
    }
    finally {
        $process.Dispose()
    }
}

Invoke-LoggedSidecarCommand `
    -Description "Compiled sidecar database check" `
    -Arguments @("check", "--data-dir", $databaseCheckPath, "--port", "18772") `
    -StandardOutputPath $databaseStdoutPath `
    -StandardErrorPath $databaseStderrPath

Invoke-LoggedSidecarCommand `
    -Description "Compiled Playwright browser check" `
    -Arguments @(
        "browser-check",
        "--browser-dir", $browserPath,
        "--playwright-driver-dir", $playwrightDriverPath,
        "--output", $screenshotPath
    ) `
    -StandardOutputPath $browserStdoutPath `
    -StandardErrorPath $browserStderrPath
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
$loopbackClient = $null
$loopbackHandler = $null

try {
    $loopbackHandler = [System.Net.Http.HttpClientHandler]::new()
    $loopbackHandler.UseProxy = $false
    $loopbackHandler.UseCookies = $true
    $loopbackHandler.CookieContainer = [System.Net.CookieContainer]::new()
    $loopbackClient = [System.Net.Http.HttpClient]::new($loopbackHandler)
    $loopbackClient.Timeout = [TimeSpan]::FromSeconds(5)

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
    $lastHealthError = "(no HTTP exception was recorded)"
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
            $health = Invoke-LoopbackHttpRequest `
                -Client $loopbackClient `
                -Method GET `
                -Uri "$baseUrl/api/health"
            if ($health.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        }
        catch {
            $lastHealthError = $_.Exception.Message
        }
        if (-not $healthy) {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        $structuredLogPath = Join-Path $serveDataPath "logs\searchcar-core.jsonl"
        $stdoutTail = Get-LogTail $stdoutPath
        $stderrTail = Get-LogTail $stderrPath
        $structuredTail = Get-LogTail $structuredLogPath
        throw "Compiled sidecar health check timed out after $HealthTimeoutSeconds seconds.`nLAST HEALTH ERROR:`n$lastHealthError`nSTDOUT:`n$stdoutTail`nSTDERR:`n$stderrTail`nSTRUCTURED LOG:`n$structuredTail"
    }

    $unauthorized = Invoke-LoopbackHttpRequest `
        -Client $loopbackClient `
        -Method GET `
        -Uri "$baseUrl/"
    if ($unauthorized.StatusCode -ne 403) {
        throw "Desktop root was available without bootstrap session"
    }

    $bootstrap = Invoke-LoopbackHttpRequest `
        -Client $loopbackClient `
        -Method GET `
        -Uri "$baseUrl/desktop/bootstrap?token=$sessionSecret"
    if ($bootstrap.StatusCode -ne 200) {
        throw "Desktop bootstrap did not reach the SPA"
    }
    $root = Invoke-LoopbackHttpRequest `
        -Client $loopbackClient `
        -Method GET `
        -Uri "$baseUrl/"
    if ($root.StatusCode -ne 200 -or $root.Content -notmatch "SearchCar Desktop") {
        throw "Desktop SPA response is invalid"
    }

    $shutdown = Invoke-LoopbackHttpRequest `
        -Client $loopbackClient `
        -Method POST `
        -Uri "$baseUrl/desktop/shutdown?token=$sessionSecret"
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
    if ($loopbackClient) {
        $loopbackClient.Dispose()
    }
    elseif ($loopbackHandler) {
        $loopbackHandler.Dispose()
    }
    $env:SEARCHCAR_DESKTOP_SESSION_SECRET = $previousSecret
}

Get-Content (Join-Path $outputPath.FullName "compiled-smoke.json")
