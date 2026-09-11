@echo off
TITLE ALGO TRADE PRO - System Shutdown
COLOR 0C
CLS

echo ==============================================================================
echo       Stopping ALGO TRADE PRO Workstation & Background Services...
echo ==============================================================================
echo.

echo [1/2] Terminating FastAPI Backend processes on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000') do taskkill /f /pid %%a >nul 2>&1

echo [2/2] Terminating Next.js Frontend processes on port 3000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000') do taskkill /f /pid %%a >nul 2>&1

echo.
echo [SUCCESS] All trading system services safely terminated.
echo.
timeout /t 3 /nobreak >nul
exit
