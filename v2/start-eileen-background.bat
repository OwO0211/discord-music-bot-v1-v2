@echo off
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" bot.py >> "logs\console.log" 2>&1
