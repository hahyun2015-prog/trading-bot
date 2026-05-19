@echo off
title AI Factory Optimization Loop (Claude Code)
echo ====================================================
echo      AI Factory Batch Optimizer Loop Started
echo ====================================================
:LOOP
echo [%date% %time%] Running Batch Optimizer...
python batch_optimizer.py
echo [%date% %time%] Optimization Complete. Waiting for 1 hour...
timeout /t 3600 /nobreak
goto LOOP
