@echo off
rem MGM Network Vault - Run IIS Setup with Administrator privileges
cd /d "%~dp0\..\.."
echo Running IIS Setup for networkvault.mgmhealthcare.in...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-iis.ps1"
pause
