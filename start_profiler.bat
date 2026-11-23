@echo off
rem Starts the ICC Profiler web UI on native Windows.
rem Uses its own venv (.venv-win) so it doesn't clash with the WSL/Linux .venv.
rem The venv is created and the requirements installed on first run.
rem Any extra arguments are passed straight through to uvicorn,
rem e.g. start_profiler.bat --port 8080
setlocal
cd /d "%~dp0"

set "VENV=.venv-win"

if exist "%VENV%\Scripts\python.exe" goto run

echo Creating Windows virtual environment in %VENV% ...
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m venv "%VENV%"
) else (
    python -m venv "%VENV%"
)
if not exist "%VENV%\Scripts\python.exe" (
    echo error: could not create the venv. Install Python 3.10+ from python.org
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo error: installing the requirements failed.
    rmdir /s /q "%VENV%"
    pause
    exit /b 1
)

:run
echo Starting ICC Profiler on http://localhost:8000 (Ctrl+C to stop)
"%VENV%\Scripts\python.exe" -m uvicorn icc_profiler.webapp.app:app --reload %*
if errorlevel 1 pause
endlocal
