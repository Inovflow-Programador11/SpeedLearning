<#
.SYNOPSIS
  Instala a PC Lock API para arrancar automaticamente quando o utilizador inicia sessão.

.DESCRIPTION
  - Instala as dependências Python.
  - Cria uma tarefa agendada "PCLockAPI" (no início de sessão, privilégios elevados,
    reinício automático se o processo morrer).
  - Abre a porta da API na firewall do Windows.

  Correr numa PowerShell COMO ADMINISTRADOR, a partir desta pasta:
      powershell -ExecutionPolicy Bypass -File .\install.ps1

  A API TEM de correr dentro da sessão do utilizador (não como serviço do Windows),
  porque os serviços correm na "sessão 0" e não conseguem mostrar janelas no ecrã.
#>
param(
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [int]$Port = 0,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "PCLockAPI"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Execute este script numa PowerShell como Administrador."
}

if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
$PythonW = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (-not (Test-Path $PythonW)) { throw "Não encontrei pythonw.exe junto de $Python" }

$Config = Join-Path $AppDir "config.json"
if (-not (Test-Path $Config)) {
    $token = & $Python (Join-Path $AppDir "main.py") --generate-token
    $cfg = Get-Content (Join-Path $AppDir "config.example.json") -Raw | ConvertFrom-Json
    $cfg.token = $token.Trim()
    $cfg | ConvertTo-Json | Set-Content -Path $Config -Encoding UTF8
    Write-Host "Criado config.json com um token novo:" -ForegroundColor Yellow
    Write-Host "    $($cfg.token)" -ForegroundColor Yellow
    Write-Host "Guarde-o: é necessário para chamar a API." -ForegroundColor Yellow
}
if ($Port -eq 0) { $Port = [int]((Get-Content $Config -Raw | ConvertFrom-Json).port) }
if ($Port -eq 0) { $Port = 8765 }

Write-Host "A instalar dependências..."
& $Python -m pip install --quiet -r (Join-Path $AppDir "requirements.txt")

Write-Host "A criar a tarefa agendada '$TaskName' para $User..."
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$AppDir\main.py`"" -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $taskPrincipal -Settings $settings -Force | Out-Null

Write-Host "A abrir a porta $Port na firewall..."
Get-NetFirewallRule -DisplayName "PC Lock API" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "PC Lock API" -Direction Inbound -Protocol TCP `
    -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Instalado. A API está a arrancar na porta $Port (log em $AppDir\pclock.log)." -ForegroundColor Green
