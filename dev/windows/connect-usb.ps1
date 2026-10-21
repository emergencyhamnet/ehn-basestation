param(
    [string]$DeviceSerial = "",
    [ValidateRange(1, 65535)][int]$Port = 18098,
    [string]$AdbPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $AdbPath) {
    $AdbPath = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
}
if (-not (Test-Path $AdbPath)) { throw "ADB was not found. Supply -AdbPath." }
if (-not $DeviceSerial) {
    $devices = @(& $AdbPath devices | ForEach-Object {
        if ($_ -match '^(\S+)\s+device\s*$') { $Matches[1] }
    })
    if ($devices.Count -ne 1) { throw "Connect and authorize one phone, or supply -DeviceSerial." }
    $DeviceSerial = $devices[0]
}
$state = & $AdbPath -s $DeviceSerial get-state
if ($LASTEXITCODE -ne 0 -or $state.Trim() -ne "device") { throw "The selected phone is not authorized." }
& $AdbPath -s $DeviceSerial reverse "tcp:$Port" "tcp:$Port"
if ($LASTEXITCODE -ne 0) { throw "ADB reverse failed." }
& $AdbPath -s $DeviceSerial reverse --list
if ($LASTEXITCODE -ne 0) { throw "Could not verify the USB tunnel." }
Write-Output "USB development tunnel ready: phone 127.0.0.1:$Port -> this host 127.0.0.1:$Port"