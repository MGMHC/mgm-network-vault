@echo off
rem MGM Network Vault - start the web app (used directly, and by the Windows service task)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv .venv || goto :err
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :err
)
if not exist config.toml copy /y config.example.toml config.toml >nul

:run
.venv\Scripts\python.exe run.py
rem exit code 3 = restart requested (e.g. after restoring an export)
if %errorlevel%==3 (
  echo Restarting...
  goto :run
)
goto :eof

:err
echo Setup failed. Install Python 3.12+ from python.org (tick "Add python.exe to PATH") and try again.
pause
