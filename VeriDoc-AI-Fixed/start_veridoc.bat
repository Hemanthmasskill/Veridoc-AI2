@echo off
setlocal enabledelayedexpansion
title VeriDoc Launcher
cd /d "%~dp0"

echo ==================================================
echo   VeriDoc - Document Verification System
echo ==================================================
echo.

REM ---------- 1. Check Node.js ----------
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo Install Node.js 18 or 20 LTS from https://nodejs.org and run this again.
    pause
    exit /b 1
)

REM ---------- 2. Find Python (3.11 preferred) ----------
set "PYCMD="
py -3.11 --version >nul 2>nul && set "PYCMD=py -3.11"
if not defined PYCMD (
    where python >nul 2>nul && set "PYCMD=python"
)

REM ---------- 3. Free Port 3000 if already occupied ----------
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":3000 .*LISTENING"') do (
    if not "%%P"=="0" (
        echo [INFO] Port 3000 is occupied by PID %%P. Releasing port...
        taskkill /PID %%P /T /F >nul 2>nul
    )
)

REM ---------- 4. Node dependencies ----------
if not exist "node_modules" (
    echo [1/3] Installing Node.js dependencies ^(first run only^)...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed. Check your internet connection.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Node.js dependencies OK.
)

REM ---------- 5. Python virtual environment ----------
if not exist "ml\venv\Scripts\activate.bat" (
    if not defined PYCMD (
        echo [ERROR] Python is not installed. Install Python 3.11 from https://python.org
        echo Tick "Add Python to PATH" during install, then run this again.
        pause
        exit /b 1
    )
    echo [2/3] Creating Python virtual environment ^(first run only^)...
    %PYCMD% -m venv ml\venv
    if errorlevel 1 (
        echo [ERROR] Could not create virtual environment.
        pause
        exit /b 1
    )
)

call "ml\venv\Scripts\activate.bat"

REM Check whether core packages are already installed (instant find_spec check)
python -c "import importlib.util as u,sys; m=['cv2','rapidocr_onnxruntime','fitz','sklearn','pypdf','PIL','numpy']; sys.exit(0 if all(u.find_spec(x) for x in m) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [2/3] Installing Python packages ^(first run only, may take 1-3 minutes^)...
    python -m pip install --upgrade pip
    pip install -r ml\requirements.txt
    if errorlevel 1 (
        echo [WARN] Full requirements install encountered an issue. Installing core OCR bundle...
        pip install "numpy>=1.24.0,<2.0.0" "opencv-python>=4.8.0" "Pillow>=10.0.0" "pypdf>=4.0.0" "PyMuPDF>=1.23.0" "scikit-learn>=1.3.0" "rapidocr-onnxruntime>=1.2.0" "pytesseract>=0.3.10"
        if errorlevel 1 (
            echo [ERROR] Python package installation failed.
            pause
            exit /b 1
        )
    )
) else (
    echo [2/3] Python environment OK.
)

REM ---------- 6. Start server and open browser ----------
echo [3/3] Starting VeriDoc on http://localhost:3000 ...
echo Close this window or run stop_veridoc.bat to stop the server.
echo.

start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:3000"

call npm start

echo.
echo Server stopped.
pause
endlocal