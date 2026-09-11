@echo off
TITLE ALGO TRADE PRO - System Launcher
COLOR 0A
CLS

echo ==============================================================================
echo       ALGO TRADE PRO - SEBI-Compliant Trading Workstation v2.0
echo ==============================================================================
echo.
echo [1/3] Starting Backend API Server on http://127.0.0.1:8000 ...
start /B python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > logs\backend_startup.log 2>&1

echo [2/3] Starting Frontend Workstation on http://localhost:3000 ...
cd frontend
start /B npm run dev > ..\logs\frontend_startup.log 2>&1
cd ..

echo [3/3] Waiting for services to initialize...
timeout /t 6 /nobreak >nul

echo.
echo [SUCCESS] Workstation is live! Opening browser...
start http://localhost:3000

echo.
echo ==============================================================================
echo  Workstation running in background.
echo  To safely stop the system at market close, run: stop_trading_system.bat
echo ==============================================================================
echo.
pause
