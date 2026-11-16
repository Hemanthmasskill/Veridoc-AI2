@echo off
setlocal enabledelayedexpansion
title Stop VeriDoc
set "PORT=3000"
set "FOUND="

echo Stopping VeriDoc on port %PORT% ...

REM Find every process listening on the port and kill it with its child processes
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        set "FOUND=1"
        echo Killing process ID %%P
        taskkill /PID %%P /T /F >nul 2>nul
    )
)

REM Close the launcher window if it is still open
taskkill /FI "WINDOWTITLE eq VeriDoc Launcher*" /F >nul 2>nul

if defined FOUND (
    echo.
    echo VeriDoc has been stopped.
) else (
    echo.
    echo VeriDoc was not running on port %PORT%.
)

timeout /t 3 >nul
endlocal