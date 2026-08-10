@echo off
rem 야간선물 실시간 수집기(night_collector.pid) 정지 전용. 매일 새벽 05:15 작업 스케줄러가 호출.
rem
rem [인코딩 주의 - 2026-08-10] 이 파일은 반드시 CP949(ANSI 한국어)로 저장할 것.
rem   종전 파일은 UTF-8(무BOM)이었는데 cmd.exe는 이를 CP949로 읽는다. 그 결과 한글이
rem   깨지면서 괄호 블록의 파싱까지 무너져 taskkill 줄이 통째로 실행되지 않았고,
rem   작업 스케줄러는 결과 1을 반환했다. 정지에 실패해도 조용히 넘어가던 원인이다.
rem   (2026-08-10 21:46 실행 실패, 수동 종료로 대응한 뒤 원인 규명)
setlocal enabledelayedexpansion
cd /d "%~dp0"

if exist "night_collector.pid" (
    for /f "usebackq tokens=1 delims= " %%i in ("night_collector.pid") do (
        echo [KIS 수집기 정지] PID %%i 종료 시도
        taskkill /f /t /pid %%i >nul 2>&1
    )
    del /f /q "night_collector.pid" >nul 2>&1
) else (
    echo [KIS 수집기 정지] night_collector.pid 없음 - 잔여 프로세스만 확인합니다.
)

rem pid 파일에는 자식(실제 인터프리터) PID만 들어간다. venv 런처가 부모로 남거나
rem pid 파일이 낡은 경우까지 확실히 정리하려면 명령줄로 한 번 더 훑어야 한다.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*kis_night*' } | ForEach-Object { Write-Host ('[KIS 수집기 정지] 잔여 PID ' + $_.ProcessId + ' 종료'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [KIS 수집기 정지] 완료
endlocal
rem 이미 멈춰 있는 상태도 정상으로 본다 - 스케줄러가 실패로 표시하지 않도록 0을 반환.
exit /b 0
