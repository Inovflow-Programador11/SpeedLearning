<#
.SYNOPSIS
  Remove a tarefa agendada e a regra de firewall da PC Lock API.
  Correr numa PowerShell como Administrador (com o PC desbloqueado).
#>
$ErrorActionPreference = "Continue"
Stop-ScheduledTask -TaskName "PCLockAPI" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "PCLockAPI" -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "PC Lock API" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*main.py*" -and $_.CommandLine -like "*$PSScriptRoot*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "PC Lock API removida."
