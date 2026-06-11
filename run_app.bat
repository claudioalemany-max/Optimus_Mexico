@echo off
rem Optimus Mexico launcher — double-click to start Streamlit.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "PORT=8501"

rem Prefer py launcher (avoids broken Windows Store python stub).
set "PY_BOOT=py -3"
where py >nul 2>&1 || set "PY_BOOT=python"

if not exist "%VENV_PY%" (
    echo Creating virtual environment...
    %PY_BOOT% -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create .venv
        echo Install Python 3.11+ from https://www.python.org/downloads/
        echo and ensure py or python works in a terminal.
        pause
        exit /b 1
    )
)

if not exist "%VENV_PY%" (
    echo ERROR: Virtual environment missing at .venv\Scripts\python.exe
    pause
    exit /b 1
)

echo Checking dependencies...
"%VENV_PY%" -c "import streamlit" 2>nul
if errorlevel 1 (
    echo Installing dependencies - first run may take a few minutes...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        exit /b 1
    )
)

rem If Streamlit is already running, open the browser instead of failing silently.
netstat -ano | findstr ":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Streamlit already running on port %PORT%.
    echo Opening http://localhost:%PORT% in your browser...
    start "" "http://localhost:%PORT%"
    pause
    exit /b 0
)

echo Starting Optimus Mexico on http://localhost:%PORT% ...
start "" "http://localhost:%PORT%"
"%VENV_PY%" -m streamlit run app.py --server.port %PORT% --browser.gatherUsageStats false
if errorlevel 1 (
    echo.
    echo Streamlit exited with an error. See messages above.
)
pause
