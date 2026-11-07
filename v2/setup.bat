@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Setup complete. Copy .env.example to .env, fill DISCORD_BOT_TOKEN, then run start.bat.
pause
exit /b 0

:error
echo.
echo Setup failed. Check Python and network, then try again.
pause
exit /b 1
