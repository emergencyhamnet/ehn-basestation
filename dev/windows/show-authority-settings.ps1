$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envFile = Join-Path $PSScriptRoot 'env\authority-station.env'

Set-Location $repoRoot
$env:PYTHONPATH = 'src'
python -m ehn_basestation.app.cli --env-file $envFile show-settings --root .