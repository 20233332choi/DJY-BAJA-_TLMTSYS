$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "Starting HTTPS tunnel for DJY Baja GPS..."
Write-Host "Run app.py first, then open the HTTPS /phone URL shown below on iPhone."
Write-Host ""

$ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrok) {
    Write-Host "ngrok not found."
    Read-Host "Press Enter to close"
    exit 1
}

cmd /c start "DJY Baja HTTPS" /min "$($ngrok.Source)" http 8765
Start-Sleep -Seconds 4

try {
    $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 3
    $https = $tunnels.tunnels | Where-Object { $_.public_url -like "https://*" } | Select-Object -First 1
    if ($https) {
        Write-Host ""
        Write-Host "Open this on iPhone:"
        Write-Host "$($https.public_url)/phone"
    } else {
        Write-Host "ngrok started, but HTTPS URL was not found yet."
        Write-Host "Open http://127.0.0.1:4040 and copy the https URL, then add /phone."
    }
} catch {
    Write-Host "ngrok started, but local ngrok API did not answer."
    Write-Host "Open http://127.0.0.1:4040 and copy the https URL, then add /phone."
}

Write-Host ""
Read-Host "Press Enter to close"
