<#
  MGM Network Vault - remove the background service and firewall rule.
  The app folder, config.toml and the data folder are NOT deleted.

      powershell -ExecutionPolicy Bypass -File deploy\uninstall-windows.ps1
#>
param([string]$TaskName = "MGM Network Vault")
$ErrorActionPreference = "Stop"
$App = Split-Path -Parent $PSScriptRoot

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'"
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$App*run.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped process $($_.ProcessId)" }
Get-NetFirewallRule -DisplayName "MGM Network Vault (TCP *)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Done. Data and configuration were kept in $App."
