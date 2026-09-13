$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envFile = Join-Path $PSScriptRoot 'env\local-station.env'
$seedFile = Join-Path $PSScriptRoot 'data\sinks-seed.json'

Set-Location $repoRoot
$env:PYTHONPATH = 'src'
python -m ehn_basestation.app.cli --env-file $envFile seed-sinks --root . --input $seedFile
python -m ehn_basestation.app.cli --env-file $envFile show-sinks --root .
