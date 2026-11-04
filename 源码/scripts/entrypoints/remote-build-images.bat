@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ContentAssessment - Remote Build Images on 45.214

cd /d "%~dp0..\.."

echo ============================================
echo   ContentAssessment remote Docker image build
echo ============================================
echo.
echo Remote Docker host:
echo   tcp://127.0.0.1:2375
echo.
echo This will remove old remote images, build new
echo backend/frontend images on 45.214.
echo.
echo Push is intentionally skipped. Push manually
echo from the remote computer after logging in.
echo.
echo Optional env overrides:
echo   REMOTE_DOCKER_HOST, REGISTRY, IMAGE_NAMESPACE, IMAGE_TAG
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\remote-build-images.ps1" -SkipPush
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Remote build finished successfully. Push was skipped.
    echo.
    echo Manual push commands on the remote computer:
    echo   docker push docker-hosted.example.invalid/nstl/content-assessment/content-assessment-backend:v1.1.0
    echo   docker push docker-hosted.example.invalid/nstl/content-assessment/content-assessment-frontend:v1.1.0
    echo.
    echo Test server compose files:
    echo   docker\docker-compose.test.yml (Portainer Stack source)
) else (
    echo Remote build failed. Exit code: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
