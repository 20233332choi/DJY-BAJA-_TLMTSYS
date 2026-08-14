$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$output = Join-Path $PSScriptRoot "ftdi_capture_$stamp.log"

Write-Host "Capturing passive UART traffic from COM8. Press Ctrl+C to stop."
Write-Host "Output: $output"
& mpremote connect COM8 repl 2>&1 | Tee-Object -FilePath $output
