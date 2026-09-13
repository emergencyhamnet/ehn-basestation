$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envFile = Join-Path $PSScriptRoot 'env\authority-station.env'
# Prefer a local (git-ignored) real member file; fall back to the committed example bundle.
$seedFile = Join-Path $PSScriptRoot 'data\real-test-users.json'
if (-not (Test-Path $seedFile)) { $seedFile = Join-Path $PSScriptRoot 'data\example-users.json' }

Set-Location $repoRoot
$env:PYTHONPATH = 'src'
python -m ehn_basestation.app.cli --env-file $envFile seed-directory-users --root . --target authority --input $seedFile