# Forward to the ETL CLI. Every subcommand must be run with etl/ as cwd so that
# `python -m onco_etl` resolves without an install step -- this script does that for you.
# Usage: ops\etl.ps1 seed-sources        ops\etl.ps1 probe --code mondo --only-headers
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'etl')
python -m onco_etl @args
$code = $LASTEXITCODE
Pop-Location
exit $code
