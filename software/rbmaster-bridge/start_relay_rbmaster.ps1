$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$testDirectory = Join-Path $repoRoot '.local\rbmaster\RBMasterOfficial_COM8_3.0.5.5'
$bridgeSource = Join-Path $PSScriptRoot 'build\DSDEVICE.DLL'
$bridgeTarget = Join-Path $testDirectory 'DSDEVICE.DLL'
$rbMaster = Join-Path $testDirectory 'RBMASTERPRO.EXE'

if (-not (Test-Path -LiteralPath $rbMaster)) {
    throw "RB Master test executable not found: $rbMaster"
}
if (-not (Test-Path -LiteralPath $bridgeSource)) {
    throw "Relay bridge DLL not found: $bridgeSource"
}

Get-Process -Name RBMASTERPRO -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Copy-Item -LiteralPath $bridgeSource -Destination $bridgeTarget -Force

$env:RB_ESP32_RELAY = '127.0.0.1:8890'
Set-Location -LiteralPath $testDirectory
Start-Process -FilePath $rbMaster -WorkingDirectory $testDirectory -WindowStyle Normal
