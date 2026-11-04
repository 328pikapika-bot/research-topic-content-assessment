@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ContentAssessment Agent - Dev Startup

echo ============================================
echo   ContentAssessment Agent Dev Startup
echo ============================================
echo.

cd /d "%~dp0"

set "BACKEND_PORT=%CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT%"
if not defined BACKEND_PORT set "BACKEND_PORT=17607"

set "FRONTEND_PORT=%CONTENT_ASSESSMENT_LOCAL_FRONTEND_PORT%"
if not defined FRONTEND_PORT set "FRONTEND_PORT=18093"

set "LOCAL_APP_URL=https://www.nstl.gov.cn/ai/agent/content_assessment/"

set "MONGO_ENV="
set "MONGO_LOCAL_HOST="
set "MONGO_TEST_HOST="
set "MONGO_LOCAL_PORT="
set "MONGO_TEST_PORT="
set "MONGO_HOST="
set "MONGO_PORT="
set "MONGO_DATABASE="
set "MONGO_USERNAME="
set "MONGO_AUTH_SOURCE="

set "MONGO_PASSWORD="
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if /I "%%a"=="MONGO_ENV" if not defined MONGO_ENV set "MONGO_ENV=%%b"
        if /I "%%a"=="MONGO_LOCAL_HOST" set "MONGO_LOCAL_HOST=%%b"
        if /I "%%a"=="MONGO_TEST_HOST" set "MONGO_TEST_HOST=%%b"
        if /I "%%a"=="MONGO_LOCAL_PORT" set "MONGO_LOCAL_PORT=%%b"
        if /I "%%a"=="MONGO_TEST_PORT" set "MONGO_TEST_PORT=%%b"
        if /I "%%a"=="MONGO_HOST" set "MONGO_HOST=%%b"
        if /I "%%a"=="MONGO_PORT" set "MONGO_PORT=%%b"
        if /I "%%a"=="MONGO_DATABASE" set "MONGO_DATABASE=%%b"
        if /I "%%a"=="MONGO_USERNAME" set "MONGO_USERNAME=%%b"
        if /I "%%a"=="MONGO_PASSWORD" set "MONGO_PASSWORD=%%b"
        if /I "%%a"=="MONGO_AUTH_SOURCE" set "MONGO_AUTH_SOURCE=%%b"
        if /I "%%a"=="MONGO_INSTANCE_ID" set "MONGO_INSTANCE_ID=%%b"
    )
) else (
    echo [ERROR] Missing project .env. Copy .env.example to .env and configure local values.
    exit /b 1
)
if not defined MONGO_ENV (
    echo [ERROR] MONGO_ENV is missing from .env.
    exit /b 1
)
if not defined MONGO_DATABASE (
    echo [ERROR] MONGO_DATABASE is missing from .env.
    exit /b 1
)
set "SELECTED_MONGO_HOST=%MONGO_HOST%"
set "SELECTED_MONGO_PORT=%MONGO_PORT%"
if /I "%MONGO_ENV%"=="local" set "SELECTED_MONGO_HOST=%MONGO_LOCAL_HOST%"
if /I "%MONGO_ENV%"=="local" set "SELECTED_MONGO_PORT=%MONGO_LOCAL_PORT%"
if /I "%MONGO_ENV%"=="test" set "SELECTED_MONGO_HOST=%MONGO_TEST_HOST%"
if /I "%MONGO_ENV%"=="test" set "SELECTED_MONGO_PORT=%MONGO_TEST_PORT%"
if not defined SELECTED_MONGO_HOST (
    echo [ERROR] Mongo host for MONGO_ENV "%MONGO_ENV%" is missing from .env.
    exit /b 1
)
if not defined SELECTED_MONGO_PORT (
    echo [ERROR] Mongo port for MONGO_ENV "%MONGO_ENV%" is missing from .env.
    exit /b 1
)
if not defined MONGO_INSTANCE_ID if /I "%MONGO_ENV%"=="local" set "MONGO_INSTANCE_ID=test_local"
if not defined MONGO_INSTANCE_ID if /I "%MONGO_ENV%"=="test" set "MONGO_INSTANCE_ID=test"

set "MONGO_COMPAT_DIR=%~dp0.runtime\python-mongo-compat"
set "BACKEND_PYTHONPATH=%MONGO_COMPAT_DIR%;%~dp0backend"

echo [1/6] Clearing previous ports...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%BACKEND_PORT%" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
    echo   Closed backend PID %%a on port %BACKEND_PORT%
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%FRONTEND_PORT%" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
    echo   Closed frontend PID %%a on port %FRONTEND_PORT%
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$processIds = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*nstl-www-https-proxy.mjs*' } | Select-Object -ExpandProperty ProcessId; foreach ($processId in $processIds) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue; Write-Host ('  Closed NSTL local gateway PID ' + $processId) }"
timeout /t 2 /nobreak >nul
echo.

echo [2/6] Checking NSTL certificate...
if exist "frontend\web\certs\www.nstl.gov.cn.pfx" (
    echo   NSTL certificate is ready.
) else (
    echo   Certificate not found. Trying to generate it. Administrator permission may be required.
    cd frontend\web
    call npm run setup:nstl-local
    cd ..\..
)
echo.

echo [3/6] Preparing MongoDB compatibility...
if not exist "%MONGO_COMPAT_DIR%\pymongo" (
    python -m pip install --target "%MONGO_COMPAT_DIR%" "pymongo>=3.13,<4.0"
    if errorlevel 1 (
        echo [ERROR] Failed to install PyMongo 3.x compatibility package.
        pause >nul
        exit /b 1
    )
) else (
    echo   PyMongo compatibility package is ready.
)
echo.

if /I "%MONGO_ENV%"=="local" (
    echo [4/6] Starting local MongoDB container...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-mongo.ps1"
    if errorlevel 1 (
        echo [ERROR] Failed to start local MongoDB container.
        pause >nul
        exit /b 1
    )
)

echo [4/6] Starting local backend on port %BACKEND_PORT% with %MONGO_ENV% MongoDB...
start "ContentAssessment-Backend-Local" /min cmd /c "cd /d %~dp0backend && set PYTHONPATH=%BACKEND_PYTHONPATH%&& set BACKEND_PORT=%BACKEND_PORT%&& set USE_MEMORY_STORE=false&& set MONGO_ENV=%MONGO_ENV%&& set MONGO_LOCAL_HOST=%MONGO_LOCAL_HOST%&& set MONGO_TEST_HOST=%MONGO_TEST_HOST%&& set MONGO_LOCAL_PORT=%MONGO_LOCAL_PORT%&& set MONGO_TEST_PORT=%MONGO_TEST_PORT%&& set MONGO_HOST=%SELECTED_MONGO_HOST%&& set MONGO_PORT=%SELECTED_MONGO_PORT%&& set MONGO_DATABASE=%MONGO_DATABASE%&& set MONGO_USERNAME=%MONGO_USERNAME%&& set MONGO_PASSWORD=%MONGO_PASSWORD%&& set MONGO_AUTH_SOURCE=%MONGO_AUTH_SOURCE%&& set MONGO_INSTANCE_ID=%MONGO_INSTANCE_ID%&& set CONTENT_ASSESSMENT_TASK_COLLECTION=content_assessment_tasks&& set ALLOW_DEFAULT_USER_SCOPE=true&& python -m uvicorn app.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"
echo   Backend is starting...

set ok=0
for /l %%i in (1,1,15) do (
    timeout /t 1 /nobreak >nul
    curl -f -s -o nul http://127.0.0.1:%BACKEND_PORT%/system/health 2>nul
    if not errorlevel 1 (
        echo   Backend ready.
        set ok=1
        goto :backend_ok
    )
    <nul set /p =.
)
:backend_ok
echo.

echo [5/6] Starting local frontend on port %FRONTEND_PORT%, proxying to backend %BACKEND_PORT%...
start "ContentAssessment-Frontend-Local" /min cmd /c "cd /d %~dp0frontend\web && set VITE_PROXY_BACKEND=http://127.0.0.1:%BACKEND_PORT%&& npm run dev:nstl -- --port %FRONTEND_PORT%"
echo   Frontend is starting. Waiting for Vite...
timeout /t 8 /nobreak >nul
echo.

echo [6/6] Starting local NSTL HTTPS gateway and opening browser...
start "ContentAssessment-NSTL-Gateway-Local" /min cmd /c "cd /d %~dp0frontend\web && set NSTL_PROXY_LOCAL_FRONTEND_PORT=%FRONTEND_PORT%&& node scripts\nstl-www-https-proxy.mjs"
timeout /t 2 /nobreak >nul
echo ============================================
echo   Startup complete.
echo ============================================
echo.
echo   Content assessment entry: %LOCAL_APP_URL%
echo   Vite internal frontend:   http://127.0.0.1:%FRONTEND_PORT% (internal only)
echo   HTTPS gateway:             https://www.nstl.gov.cn (public site root)
echo   Local backend:             http://127.0.0.1:%BACKEND_PORT% (internal only)
echo   Database:        %MONGO_ENV% MongoDB (%SELECTED_MONGO_HOST%:%SELECTED_MONGO_PORT%)
echo.
timeout /t 2 /nobreak >nul
start "" %LOCAL_APP_URL%

echo.
echo   Services are running in the background. You can close this window.
echo   Press any key to exit...
pause >nul
