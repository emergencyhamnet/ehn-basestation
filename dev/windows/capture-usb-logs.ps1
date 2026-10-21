param(
    [string]$DeviceSerial = "",
    [string]$OutputPath = "",
    [string]$AdbPath = "",
    [switch]$Follow
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
if (-not $OutputPath) {
    $root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $OutputPath = Join-Path $root "var\usb-logs\relay-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Path (Split-Path $OutputPath -Parent) -Force | Out-Null
if ($Follow) {
    Write-Output "Following sanitized EHNRelayLink logs; Ctrl+C stops capture."
    & $AdbPath -s $DeviceSerial logcat -v threadtime -s 'EHNRelayLink:I' '*:S' | Tee-Object -FilePath $OutputPath
} else {
    & $AdbPath -s $DeviceSerial logcat -d -v threadtime -s 'EHNRelayLink:I' '*:S' | Out-File -FilePath $OutputPath -Encoding utf8
}
if ($LASTEXITCODE -ne 0) { throw "ADB log capture failed." }
Write-Output "Saved sanitized gateway diagnostics to $OutputPath"