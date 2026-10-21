param(
    [string]$SerialPort = "COM12",
    [ValidateRange(1, 65535)][int]$Port = 7465,
    [switch]$AllowTransmit
)

$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Project Python environment not found. Run the Windows bootstrap first." }
$arguments = @("-m", "ehn_basestation.gateways.meshcore.usb_gateway", "--serial-port", $SerialPort, "--port", "$Port")
if ($AllowTransmit) { $arguments += "--allow-transmit" }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "MeshCore USB gateway exited with code $LASTEXITCODE." }