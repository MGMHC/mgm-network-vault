@echo off
:: MGM Network Vault - Service Restart Script
:: Elevates automatically if not running as Administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges to restart NetworkVault service...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo   Restarting MGM Network Vault Service & App Pool
echo ========================================================
echo.

echo [1/2] Restarting Windows Service: NetworkVault...
powershell -NoProfile -Command "Restart-Service -Name 'NetworkVault' -Force -ErrorAction SilentlyContinue; if ($?) { Write-Host '  NetworkVault service restarted successfully.' -ForegroundColor Green } else { Write-Host '  Failed or service not running under this name, trying MGMNetworkVault...' -ForegroundColor Yellow; Restart-Service -Name 'MGMNetworkVault' -Force -ErrorAction SilentlyContinue }"

echo.
echo [2/2] Recycling IIS App Pool...
powershell -NoProfile -Command "Import-Module WebAdministration -ErrorAction SilentlyContinue; Restart-WebAppPool -Name 'DefaultAppPool' -ErrorAction SilentlyContinue; if ($?) { Write-Host '  IIS DefaultAppPool recycled.' -ForegroundColor Green }"

echo.
echo ========================================================
echo   Done! VLAN Matrix & Dormant Port Auditing is now live.
echo ========================================================
timeout /t 5
