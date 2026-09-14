<#
.SYNOPSIS
  One-shot setup for an EHN basestation on Windows: venv, install, layout, schemas, example seeds.

.DESCRIPTION
  Idempotent. Safe to re-run. Does not overwrite existing .env files or databases.
  After it finishes, edit dev\windows\env\*.env for your Pi address, gateway id, and token,
  then start the services with the commands it prints.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File dev\windows\bootstrap-windows.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipSeeds,
    [switch]$RunTests
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

Step "Python"
$pyVersion = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ([version]$pyVersion -lt [version]"3.11") { throw "Python 3.11+ required, found $pyVersion ($Python)" }
Write-Host "using $Python ($pyVersion)"

Step "Virtual environment"
$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $Python -m venv (Join-Path $repoRoot ".venv") }
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -e .
Write-Host "installed ehn-basestation into .venv"

Step "Station profiles"
$envDir = Join-Path $repoRoot "dev\windows\env"
foreach ($name in 'local-station', 'authority-station') {
    $target = Join-Path $envDir "$name.env"
    $template = Join-Path $envDir "$name.env.example"
    if (Test-Path $target) { Write-Host "$name.env exists, leaving it alone"; continue }
    # Rewrite the template's example paths to this checkout so first run works without edits.
    (Get-Content $template) -replace 'C:\\ehn\\ehn-basestation', $repoRoot | Set-Content $target -Encoding UTF8
    Write-Host "created $name.env from template (edit Pi address, gateway id, and token before real use)"
}

$env:PYTHONPATH = Join-Path $repoRoot "src"
$cli = @('-m', 'ehn_basestation.app.cli')
$localEnv = Join-Path $envDir 'local-station.env'
$authorityEnv = Join-Path $envDir 'authority-station.env'

Step "Data layout and schemas"
& $venvPy @cli --env-file $localEnv init-dev-layout | Out-Null
& $venvPy @cli --env-file $authorityEnv init-authority-directory-schema | Out-Null
Write-Host "var/ layout and databases initialised"

if (-not $SkipSeeds) {
    Step "Example seeds"
    & $venvPy @cli --env-file $authorityEnv seed-directory-users --target authority --input (Join-Path $repoRoot 'dev\windows\data\example-users.json')
    & $venvPy @cli --env-file $localEnv seed-sinks --input (Join-Path $repoRoot 'dev\windows\data\sinks-seed.json')
}

if ($RunTests) {
    Step "Unit tests"
    & $venvPy -m unittest discover -s tests -p 'test_*.py'
}

Step "Done"
Write-Host @"
Next:
  1. Edit dev\windows\env\local-station.env:
       BASESTATION_MESHCORE_LINK_ENDPOINT=<pi-ip>:7463
       BASESTATION_SMS_GATEWAY_ID=EHN-XXX
       BASESTATION_SMS_ANDROID_AUTH_TOKEN=<your secret>
  2. Start the authority console (loopback) and the workbench (LAN):
       `$env:PYTHONPATH='$($env:PYTHONPATH)'
       $venvPy -m ehn_basestation.app.cli --env-file $authorityEnv run-authority-console --root . --host 127.0.0.1 --port 8105
       $venvPy -m ehn_basestation.app.cli --env-file $localEnv run-workbench-preview --host 0.0.0.0 --port 18098
  3. Open http://127.0.0.1:8105 and replace the example members with real ones.
  4. Point the Android SMS gateway app at http://<this-host>:18098 with the same token.
"@
