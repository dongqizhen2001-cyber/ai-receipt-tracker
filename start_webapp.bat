@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python venv not found: .venv\Scripts\python.exe
    echo Please create venv first:
    echo   py -3.11 -m venv .venv
    pause
    exit /b 1
)

echo Starting Streamlit app...
set PORT=
for %%P in (8501 8502 8503 8504 8505 8506 8507 8508 8509 8510) do (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul
    if errorlevel 1 if not defined PORT set PORT=%%P
)

if not defined PORT set PORT=8511

echo URL: http://localhost:%PORT%
echo Press Ctrl+C in this window to stop the app.
start "" "http://localhost:%PORT%"

".venv\Scripts\python.exe" -m streamlit run app.py --server.port %PORT% --server.address 0.0.0.0

endlocal
