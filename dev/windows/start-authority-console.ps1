$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envFile = Join-Path $PSScriptRoot 'env\authority-station.env'
$repoPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$sharedPython = 'D:\Projects\meshcore-pi-port\.venv\Scripts\python.exe'
$pythonExe = if (Test-Path $repoPython) { $repoPython } elseif (Test-Path $sharedPython) { $sharedPython } else { 'python' }

Set-Location $repoRoot
$env:PYTHONPATH = Join-Path $repoRoot 'src'
& $pythonExe -m ehn_basestation.app.cli --env-file $envFile run-authority-console --root . --host 127.0.0.1 --port 8105