# 워치독 콘솔 팝업 제거 — 관리자 권한으로 실행할 것
#
# 배경: "AMATS Watchdog" 태스크가 cmd.exe /c 로 배치를 직접 실행하는데
#       LogonType=Interactive 라서 2분마다 콘솔 창이 화면에 떴다.
#       wscript.exe + watchdog_hidden.vbs 로 바꾸면 사용자·세션·권한은
#       그대로 두고 창만 숨긴다(WScript.Shell.Run 의 window style 0).
#
# RunLevel=Highest 태스크라 수정에 관리자 권한이 필요하다.
# 실행: PowerShell을 "관리자 권한으로 실행" 후
#       powershell -ExecutionPolicy Bypass -File apply_watchdog_hidden.ps1

$ErrorActionPreference = 'Stop'
$TaskName = 'AMATS Watchdog'
$Vbs      = 'C:\Antigravity\AI_T_Agent\watchdog_hidden.vbs'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[중단] 관리자 권한이 아닙니다. PowerShell을 관리자로 실행한 뒤 다시 시도하세요." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Vbs)) {
    Write-Host "[중단] VBS 파일이 없습니다: $Vbs" -ForegroundColor Red
    exit 1
}

$t = Get-ScheduledTask -TaskName $TaskName
Write-Host ("변경 전 : {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
Write-Host ("권한    : {0} / {1} / {2}" -f $t.Principal.UserId, $t.Principal.RunLevel, $t.Principal.LogonType)

# 되돌릴 수 있도록 현재 정의를 백업
$stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = "C:\Antigravity\AI_T_Agent\watchdog_task_backup_$stamp.xml"
Export-ScheduledTask -TaskName $TaskName | Out-File -FilePath $backup -Encoding utf8
Write-Host "백업    : $backup"

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $Vbs + '"')
$t.Settings.Hidden = $true
Set-ScheduledTask -TaskName $TaskName -Action $action -Settings $t.Settings | Out-Null

$v = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host ("변경 후 : {0} {1}" -f $v.Actions[0].Execute, $v.Actions[0].Arguments)
Write-Host ("Hidden  : {0}" -f $v.Settings.Hidden)
Write-Host ("권한    : {0} / {1} / {2}  (변경 없어야 정상)" -f $v.Principal.UserId, $v.Principal.RunLevel, $v.Principal.LogonType)
Write-Host ("주기    : {0}  (변경 없어야 정상)" -f $v.Triggers[0].Repetition.Interval)

Write-Host ""
Write-Host "동작 확인 — 지금 한 번 실행합니다 (창이 뜨지 않아야 정상)"
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 6
$i = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ("  마지막 실행 {0}  결과 {1}" -f $i.LastRunTime, $i.LastTaskResult)
if ($i.LastTaskResult -eq 0) {
    Write-Host "  정상. 앞으로 2분마다 창 없이 실행됩니다." -ForegroundColor Green
} else {
    Write-Host "  결과가 0이 아닙니다. 되돌리려면:" -ForegroundColor Yellow
    Write-Host ("    Register-ScheduledTask -Xml (Get-Content '{0}' -Raw) -TaskName '{1}' -Force" -f $backup, $TaskName)
}
