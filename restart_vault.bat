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
echo   Restarting MGM Network Vault task ^& IIS App Pool
echo ========================================================
echo.

echo [1/2] Restarting scheduled task: MGM Network Vault...
:: The task (deploy\install-windows.ps1) runs start.bat; ending it can leave python running, so stop that too.
powershell -NoProfile -Command "$t='MGM Network Vault'; if (-not (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue)) { Write-Host '  Task not found - run deploy\install-windows.ps1 as Administrator.' -ForegroundColor Red; exit 1 }; Stop-ScheduledTask -TaskName $t; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run\.py' -and $_.ExecutablePath -like '%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Start-Sleep -Seconds 2; Start-ScheduledTask -TaskName $t; Write-Host '  MGM Network Vault task restarted.' -ForegroundColor Green"

echo.
echo [2/2] Recycling IIS App Pool...
powershell -NoProfile -Command "Import-Module WebAdministration -ErrorAction SilentlyContinue; Restart-WebAppPool -Name 'DefaultAppPool' -ErrorAction SilentlyContinue; if ($?) { Write-Host '  IIS DefaultAppPool recycled.' -ForegroundColor Green }"

echo.
echo ========================================================
echo   Done. Check logs\service.log for "MGM Network Vault ... is running".
echo ========================================================
timeout /t 5
