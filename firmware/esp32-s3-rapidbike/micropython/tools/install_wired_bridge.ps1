param(
    [string]$Port = 'COM8',
    [switch]$IncludeWifiConfig,
    [switch]$IncludeRelayConfig
)

$ErrorActionPreference = 'Stop'
$microPythonRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $microPythonRoot 'main.py'
$wifiConfig = Join-Path $microPythonRoot 'config\wifi_config.py'
$relayConfig = Join-Path $microPythonRoot 'config\relay_config.py'

Write-Host "Installing Rapid Bike UART bridge on $Port..."
& mpremote connect $Port fs cp $source ':main.py'
if ($LASTEXITCODE -ne 0) { throw "mpremote copy failed: $LASTEXITCODE" }

if ($IncludeWifiConfig) {
    if (-not (Test-Path -LiteralPath $wifiConfig)) {
        throw "Wi-Fi config not found: $wifiConfig"
    }
    & mpremote connect $Port fs cp $wifiConfig ':wifi_config.py'
    if ($LASTEXITCODE -ne 0) { throw "wifi_config.py copy failed: $LASTEXITCODE" }
}

if ($IncludeRelayConfig) {
    if (-not (Test-Path -LiteralPath $relayConfig)) {
        throw "Relay config not found: $relayConfig"
    }
    & mpremote connect $Port fs cp $relayConfig ':relay_config.py'
    if ($LASTEXITCODE -ne 0) { throw "relay_config.py copy failed: $LASTEXITCODE" }
}

Write-Host "Resetting ESP32. $Port will switch from REPL 115200 to bridge 9600."
& mpremote connect $Port reset
Write-Host 'Bridge installed.'
Write-Host 'Recovery: hold BOOT, press RST, then release BOOT.'
