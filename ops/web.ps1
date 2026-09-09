# Forward to the front-end toolchain (npm run dev / build / preview). Every subcommand runs
# with frontend/ as cwd so node_modules resolution and the vite config path stay correct no
# matter where you call it from. First run installs dependencies automatically.
# Usage: ops\web.ps1 dev        ops\web.ps1 build
#   dev needs the API already listening on 127.0.0.1:8001 (ops\api.ps1 serve) -- vite only
#   proxies /api.   build writes frontend/dist, which api.ps1 serve then hosts on the same
#   port as the API itself.
# Keep this file ASCII-only: without a BOM, Windows PowerShell 5.1 decodes a .ps1 as the
# system ANSI codepage, so non-ASCII comments break the parser and $args silently arrives empty.
# Call npm as npm.cmd, not bare `npm`: the bare name resolves to nodejs's own npm.ps1, and
# splatting an array into a .ps1 binds it as named parameters instead of positional arguments.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'frontend')
if (-not (Test-Path 'node_modules')) { npm.cmd install }
# Copy $args first: splatting the automatic array straight in (`npm run @args`) drops it.
$forward = @($args)
& npm.cmd run @forward
$code = $LASTEXITCODE
Pop-Location
exit $code
