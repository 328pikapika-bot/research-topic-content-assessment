@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ContentAssessment - Push Test Images

cd /d "%~dp0.."

echo ============================================
echo   ContentAssessment push test images
echo ============================================
echo.
echo Default registry:
echo   docker-hosted.example.invalid/nstl/content-assessment
echo.
echo Optional environment variables:
echo   HARBOR_USER / HARBOR_PASS
echo   IMAGE_TAG
echo   DOCKER_HOST
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docker-push-test.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Push finished successfully.
) else (
    echo Push failed. Exit code: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
