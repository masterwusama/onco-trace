# One-shot launcher for the site. Two shapes:
#   ops\up.ps1 dev    backend on the .env API_PORT + vite dev on 5174, each in its own window
#   ops\up.ps1 site   npm run build, then serve API and site on the same port (site at /)
# Usage: ops\up.ps1 dev        ops\up.ps1 site
#
# Before starting anything this script clears the ground: the .env settings come from
# api/onco_api/config.py (so there is only ever one parser of that file), MySQL has to be
# listening, and any process still holding a port it is about to use gets stopped. That last
# one is the point of the whole script: a backend still running with yesterday's code answers
# requests fine and shows numbers that no longer match the repository, which looks like a data
# bug and is not one. It only clears processes it can identify as this stack (python on the
# API port, node on the vite port) and refuses anything else by PID and name.
#
# What it deliberately does NOT do: apply migrations (a schema change does not belong in
# "start" -- run python db/tests/run.py migrate) or run the gates.
# Keep this file ASCII-only: see the note in ops\web.ps1.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$apiPs = Join-Path $PSScriptRoot 'api.ps1'
$webPs = Join-Path $PSScriptRoot 'web.ps1'
$vitePort = 5174   # fixed by frontend/vite.config.js with strictPort; 5173 belongs to another project

function Die([string]$msg) {
    Write-Host "up: $msg" -ForegroundColor Red
    exit 2
}

function Test-Tcp([string]$computer, [int]$port, [int]$timeoutMs) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $waited = $client.ConnectAsync($computer, $port).Wait($timeoutMs)
        return ($waited -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortOwners([int]$port) {
    @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

# Clearing a port is the only destructive thing this script does, so it is identity-checked:
# the owner's process name has to be one this stack actually runs ($owners: python for the API,
# node for vite). Anything else - a database, another project, a port mapper - is reported by
# PID and name and left alone, because guessing which process a person wanted killed is not
# this script's call.
function Clear-Port([int]$port, [string]$what, [string[]]$owners) {
    $held = Get-PortOwners $port
    if ($held.Count -eq 0) {
        Write-Host ('  port {0,-5} free     ({1})' -f $port, $what) -ForegroundColor Green
        return
    }
    foreach ($procId in $held) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { '?' }
        if ($owners -notcontains $name) {
            $refused = ('{0} port {1} is held by PID {2} ({3}), which is not one of [{4}] - this ' +
                        'script only clears processes it recognises as this stack. ' +
                        'Stop that one yourself.') -f $what, $port, $procId, $name, ($owners -join ', ')
            Die $refused
        }
        Write-Host ('  port {0,-5} held by  {1} PID {2} -> stopping' -f $port, $name, $procId) -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    # a killed process releases its listener asynchronously; 10 s covers the pathological case
    for ($try = 0; $try -lt 40; $try++) {
        if ((Get-PortOwners $port).Count -eq 0) {
            Write-Host ('  port {0,-5} cleared  ({1})' -f $port, $what) -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 250
    }
    Die ('port {0} ({1}) is still listening 10 s after stopping its owner' -f $port, $what)
}

if (@('dev', 'site') -notcontains $args[0]) {
    Write-Host 'usage: ops\up.ps1 dev | site' -ForegroundColor Yellow
    Write-Host '  dev    backend + vite dev server, one window each (hot reload)'
    Write-Host '  site   build frontend/dist, then serve API and site on one port'
    exit 2
}
$mode = $args[0]

# --- 1. settings: ask the backend's own config module rather than parse .env a second time ---
# Native commands write progress on stderr, which PowerShell 5.1 turns into a thrown error
# under 'Stop'; relax around those calls and judge them by $LASTEXITCODE only.
$env:PYTHONIOENCODING = 'utf-8'
# Single quotes only inside the probe: PowerShell 5.1 drops the double quotes when it hands an
# argument to a native program, so "api" would arrive at python as a bare name.
$probe = @'
import sys
sys.path.insert(0, 'api')
from onco_api.config import load_settings
s = load_settings()
print(s.api_host, s.api_port, s.host, s.port, s.database)
'@
$preference = $ErrorActionPreference
Push-Location $root
try {
    $ErrorActionPreference = 'Continue'
    # No 2>&1 on this call: merging stderr turns python's traceback into PowerShell error
    # records and dresses it in mojibake. stdout is all this script parses.
    $captured = & python -c $probe
    $probeCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $preference
    Pop-Location
}
$report = ($captured | Out-String).Trim()
if ($probeCode -ne 0) { Die "cannot read settings from .env (exit $probeCode)`n$report" }
$fields = $report -split '\s+'
if ($fields.Count -ne 5) { Die "unexpected settings output, expected 5 fields:`n$report" }
$apiHost, $apiPort, $dbHost, $dbPort, $dbName = $fields

Write-Host ('up ({0})  db={1}@{2}:{3}  api={4}:{5}' -f $mode, $dbName, $dbHost, $dbPort, $apiHost, $apiPort)
Write-Host '  preflight:' -ForegroundColor DarkGray

# --- 2. MySQL first: the API starts happily without it and then 500s on every request --------
if (Test-Tcp $dbHost ([int]$dbPort) 3000) {
    Write-Host ('  MySQL {0}:{1,-5} listening' -f $dbHost, $dbPort) -ForegroundColor Green
} else {
    Die ('MySQL at {0}:{1} is not reachable. Start that service first (DB_* in .env).' -f $dbHost, $dbPort)
}
Clear-Port ([int]$apiPort) 'backend' @('python')

if ($mode -eq 'dev') {
    # vite would refuse a taken port anyway (strictPort); clearing it first is what makes the
    # second window come up on the port the /api proxy is configured for
    Clear-Port $vitePort 'vite dev' @('node')
    Write-Host ''
    Write-Host ('  backend    http://{0}:{1}/api/docs   (new window)' -f $apiHost, $apiPort)
    Start-Process powershell -ArgumentList '-NoProfile', '-NoExit', '-File', $apiPs, 'serve'
    Write-Host ('  vite dev   http://127.0.0.1:{0}   (new window, /api proxied to {1}:{2})' -f $vitePort, $apiHost, $apiPort)
    Start-Process powershell -ArgumentList '-NoProfile', '-NoExit', '-File', $webPs, 'dev'
    Write-Host ''
    Write-Host '  Ctrl+C (or close) each window to stop; this window is free again.' -ForegroundColor DarkGray
    exit 0
}

# --- site: build in this window so the output is visible, then serve in the foreground -------
Write-Host '  building frontend (npm run build) ...'
$ErrorActionPreference = 'Continue'
& $webPs build
$buildCode = $LASTEXITCODE
$ErrorActionPreference = $preference
if ($buildCode -ne 0) {
    Die ('frontend build failed (exit {0}) - nothing was started on port {1}' -f $buildCode, $apiPort)
}
$index = Join-Path $root 'frontend\dist\index.html'
if (-not (Test-Path $index)) { Die "build succeeded but $index is missing" }
Write-Host ''
Write-Host ('  serving API and site on http://{0}:{1}   (Ctrl+C to stop)' -f $apiHost, $apiPort)
& $apiPs serve
exit $LASTEXITCODE
