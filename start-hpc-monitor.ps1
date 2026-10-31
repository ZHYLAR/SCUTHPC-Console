param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$monitorUrl = 'http://127.0.0.1:8765'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'serve.py'
$stdoutPath = Join-Path $PSScriptRoot 'console.log'
$stderrPath = Join-Path $PSScriptRoot 'console.error.log'

function Test-ConsoleReady {
    try {
        $response = Invoke-WebRequest -Uri $monitorUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content -match '<title>SCUTHPC Console</title>'
    } catch {
        return $false
    }
}

try {
    if (-not (Test-ConsoleReady)) {
        if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
            throw 'Port 8765 is occupied and SCUTHPC Console is not ready. Check the existing service.'
        }
        if (-not (Test-Path -LiteralPath $pythonPath)) {
            throw 'Missing .venv\Scripts\python.exe. Install the project dependencies first.'
        }
        & $pythonPath -c 'import sys; assert sys.version_info >= (3, 11); import fastapi, uvicorn, slurmbox'
        if ($LASTEXITCODE -ne 0) {
            throw 'The Python environment is incomplete. Reinstall requirements.txt and skills/slurmbox.'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'config.json'))) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Destination (Join-Path $PSScriptRoot 'config.json')
            Write-Host 'Created config.json from the example. Review the local settings before using cluster features.'
        }
        $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList ('"' + $serverPath + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
        $deadline = (Get-Date).AddSeconds(60)
        $ready = $false
        do {
            $ready = Test-ConsoleReady
            if ($ready) { break }
            $serverProcess.Refresh()
            if ($serverProcess.HasExited) {
                throw "The server exited. Check $stderrPath"
            }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if (-not $ready) {
            throw "The server is not ready after 60 seconds. Check $stderrPath"
        }
    }
    Write-Host "SCUTHPC Console: $monitorUrl"
    if (-not $NoBrowser) { Start-Process $monitorUrl }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
