@echo off
rem Daily direct load (Task Scheduler "QMind Daily Load", 12:10 every day).
rem Log: data\daily_load\daily_load.log (git ignored). Details: research.md 2.5
cd /d %~dp0..
if not exist data\daily_load mkdir data\daily_load
echo ===== %date% %time% >> data\daily_load\daily_load.log
C:\Users\IKM\.virtualenvs\bok-ZkEKkwfv\Scripts\python.exe -X utf8 main.py direct-load-daily --write >> data\daily_load\daily_load.log 2>&1
echo exit %errorlevel% >> data\daily_load\daily_load.log
