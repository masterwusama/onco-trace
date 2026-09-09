# Forward to the API CLI. Every subcommand must run with api/ as cwd so that
# `python -m onco_api` resolves without an install step -- this script does that for you.
# Usage: ops\api.ps1 serve --reload      ops\api.ps1 routes
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'api')
python -m onco_api @args
$code = $LASTEXITCODE
Pop-Location
exit $code
