@echo off
TITLE ALGO TRADE PRO - Environment Setup
COLOR 0B
CLS

echo ==============================================================================
echo       ALGO TRADE PRO - Automated Environment Installation
echo ==============================================================================
echo.

echo [1/3] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.10+ is not installed or not in PATH!
    echo Please install Python from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python is available.

echo.
echo [2/3] Installing Backend Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Python dependencies.
    pause
    exit /b 1
)
echo [OK] Python packages installed successfully.

echo.
echo [3/3] Installing Frontend Node.js dependencies...
cd frontend
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Node.js packages.
    cd ..
    pause
    exit /b 1
)
echo [OK] Frontend packages installed successfully.
cd ..

echo.
echo [4/4] Verifying database tables and migrations...
python scripts/migrate_db.py

echo.
echo ==============================================================================
echo [SUCCESS] Environment Setup Complete!
echo You can now launch the system anytime using: start_trading_system.bat
echo ==============================================================================
echo.
pause
