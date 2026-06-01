@echo off
rem chcp 65001 (Disabled to prevent CMD UTF-8 parser bug)
echo ========================================
echo   ERA 에러 진단 시작
echo ========================================
echo.
"c:\Antigravity\AI_T_Agent\venv32\Scripts\python.exe" "c:\Antigravity\AI_T_Agent\test_era.py"
echo.
echo ========================================
pause
