<#
  MGM Network Vault - install on a Windows server as an always-on background service.

  Run in an elevated PowerShell (Run as Administrator) from the unzipped app folder:
      powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1
      powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Port 8443 -DataDir "D:\NetworkVault\data"

  What it does:
    * checks Python 3.12+ and Git
    * creates the Python environment and installs the dependencies
    * writes config.toml (port, data folder) if it does not exist yet
    * opens the port in Windows Firewall
    * registers the scheduled task "MGM Network Vault": starts at boot as SYSTEM (no one needs to be
      logged on), restarts automatically if it stops, and logs to logs\service.log
  Re-running it is safe (it updates the installation and keeps config + data).
#>
param(
    [int]$Port = 8080,
    [string]$DataDir = "data",
    [string]$TaskName = "MGM Network Vault"
)
$ErrorActionPreference = "Stop"
$App = Split-Path -Parent $PSScriptRoot

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in PowerShell started with 'Run as Administrator'."
}

Write-Host "Installing MGM Network Vault from $App" -ForegroundColor Cyan

# --- prerequisites -----------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python not found. Install Python 3.12+ from python.org for ALL USERS and tick 'Add python.exe to PATH'." }
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.12") { throw "Python $ver found - version 3.12 or newer is required." }
if ($py.Source -like "*\AppData\*") {
    Write-Warning "Python is installed for one user only ($($py.Source)). The service runs as SYSTEM; reinstall Python 'for all users' if the service fails to start."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warning "Git not found - everything works except GitHub sync. Install Git for Windows to use it."
}

# --- python environment ------------------------------------------------------
Push-Location $App
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "Creating Python environment..."
        & python -m venv .venv
    }
    Write-Host "Installing dependencies..."
    & .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (no internet access? set HTTPS_PROXY first)." }

    # --- configuration ------------------------------------------------------
    if (-not (Test-Path "config.toml")) {
        $cfg = Get-Content "config.example.toml" -Raw
        $cfg = $cfg -replace '(?m)^port = \d+', "port = $Port"
        $cfg = $cfg -replace '(?m)^data_dir = "[^"]*"', ('data_dir = "' + ($DataDir -replace '\\', '/') + '"')
        Set-Content -Path "config.toml" -Value $cfg -Encoding UTF8
        Write-Host "Wrote config.toml (port $Port, data $DataDir)"
    } else {
        Write-Host "Keeping existing config.toml"
        $m = Select-String -Path "config.toml" -Pattern '^\s*port\s*=\s*(\d+)' | Select-Object -First 1
        if ($m) { $Port = [int]$m.Matches[0].Groups[1].Value }
    }
    New-Item -ItemType Directory -Force "logs" | Out-Null
} finally { Pop-Location }

# --- firewall ----------------------------------------------------------------
$rule = "MGM Network Vault (TCP $Port)"
$localOnly = Select-String -Path (Join-Path $App "config.toml") -Pattern '^\s*host\s*=\s*"127\.0\.0\.1"' -Quiet
if ($localOnly) {
    Write-Host "Firewall: app listens on 127.0.0.1 only (published through IIS) - no rule for port $Port needed"
} elseif (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null
    Write-Host "Firewall: allowed inbound TCP $Port (Domain/Private networks)"
}

# --- service (scheduled task) ------------------------------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$App*run.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory $App `
    -Argument "/c `"`"$App\start.bat`" >> `"$App\logs\service.log`" 2>&1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$runAs = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $runAs `
    -Description "MGM Network Vault - switch configuration backups (web UI on port $Port)" | Out-Null
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 8
$up = $false
try { $up = (Invoke-WebRequest "http://localhost:$Port/login" -UseBasicParsing -TimeoutSec 10).StatusCode -eq 200 } catch {}
$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Host ""
if ($up) { Write-Host "MGM Network Vault is running." -ForegroundColor Green }
else { Write-Warning "The service was registered but is not answering yet - check $App\logs\service.log" }
Write-Host "Open:  http://$($env:COMPUTERNAME):$Port" -ForegroundColor Green
$ips | ForEach-Object { Write-Host "       http://$($_):$Port" }
Write-Host "First login: admin / admin (you will be asked to change it). Then use 'Export & migrate' to import your data."
Write-Host "Manage: Task Scheduler > '$TaskName'  |  logs: $App\logs\service.log"
