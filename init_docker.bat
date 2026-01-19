@echo off
echo ===========================================
echo   Initializing Docker Setup
echo ===========================================

:: 1. Ensure Files Exist (to prevent Docker creating directories)
if not exist .env (
    echo Creating empty .env...
    type nul > .env
)
if not exist withings_tokens.pkl (
    echo Creating placeholder withings_tokens.pkl...
    type nul > withings_tokens.pkl
)

:: 2. Build Container
echo.
echo Building Docker container...
docker-compose build
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker build failed. Is Docker Desktop running?
    pause
    exit /b %ERRORLEVEL%
)

:: 3. Run Setup Script in Container
echo.
echo Launching interactive setup...
docker-compose run -it --rm garmin-sync python setup.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Setup script failed. See above for details.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===========================================
echo   Setup Finished
echo ===========================================
echo To start the server, run: docker-compose up -d
echo.
pause
