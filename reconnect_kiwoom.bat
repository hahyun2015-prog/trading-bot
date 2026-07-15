@echo off
title AMATS Kiwoom Reconnect
echo Starting Kiwoom & ERA Auto-Reconnection...
rem RunLevel=Highest로 등록된 예약 작업을 트리거해 UAC 동의창 없이 조용히 승격 실행한다
schtasks /run /tn "AMATS ERA Reconnect"
exit
