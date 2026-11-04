@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ContentAssessment - Local Docker One-Click Deploy

cd /d "%~dp0.."

echo.
echo ============================================
echo   ContentAssessment 本地 Docker 一键部署
echo ============================================
echo.
echo 将依次执行：
echo   1. 构建后端镜像
echo   2. 构建前端镜像
echo   3. 停止旧容器 + 准备网络
echo   4. 启动本地测试容器
echo.
echo ============================================
echo.

REM ── 1. 构建后端镜像 ──────────────────────────────────
echo [1/4] 构建后端镜像...
echo.
docker build -f docker/Dockerfile.backend -t docker-hosted.example.invalid/nstl/content-assessment/content-assessment-backend:v1.0.1 .
if %ERRORLEVEL% neq 0 (
    echo.
    echo [FAIL] 后端镜像构建失败！
    pause
    exit /b 1
)
echo.
echo [OK] 后端镜像构建完成。
echo.

REM ── 2. 构建前端镜像 ──────────────────────────────────
echo [2/4] 构建前端镜像...
echo.
docker build -f docker/Dockerfile.frontend -t nstl/content-assessment/content-assessment-frontend:v1.0.1 .
if %ERRORLEVEL% neq 0 (
    echo.
    echo [FAIL] 前端镜像构建失败！
    pause
    exit /b 1
)
echo.
echo [OK] 前端镜像构建完成。
echo.

REM ── 3. 停止旧容器、创建网络 ────────────────────────────
echo [3/4] 停止旧容器并准备网络...
echo.
docker compose -f docker/docker-compose.test.yml -f docker/docker-compose.test-local.yml down 2>nul
docker network create NSTL-overlay 2>nul
echo [OK] 网络就绪。
echo.

REM ── 4. 启动容器 ──────────────────────────────────────
echo [4/4] 启动本地测试容器...
echo.
docker compose -f docker/docker-compose.test.yml -f docker/docker-compose.test-local.yml up -d
if %ERRORLEVEL% neq 0 (
    echo.
    echo [FAIL] 容器启动失败！
    pause
    exit /b 1
)

echo.
echo 等待容器健康检查（约 20 秒）...
timeout /t 20 /nobreak >nul

REM ── 验证容器状态 ─────────────────────────────────────
echo.
echo ============================================
echo   容器状态
echo ============================================
docker ps --filter "name=content-assessment" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo.

REM ── 输出访问链接 ─────────────────────────────────────
echo ============================================
echo   访问链接
echo ============================================
echo.
echo   前端页面:      http://localhost:18093
echo   后端 API:      http://localhost:17607
echo   健康检查:      http://localhost:17607/system/health
echo   队列健康:      http://localhost:17607/system/health/queue
echo   API 文档:      http://localhost:17607/docs
echo.
echo ============================================
echo   登录账号
echo ============================================
echo.
echo   Please use NSTL unified login. Mock accounts are disabled.
echo.
echo ============================================
echo   常用命令
echo ============================================
echo.
echo   查看日志:
echo     docker compose -f docker/docker-compose.test.yml -f docker/docker-compose.test-local.yml logs -f
echo.
echo   停止服务:
echo     docker compose -f docker/docker-compose.test.yml -f docker/docker-compose.test-local.yml down
echo.
echo   查看指定服务日志:
echo     docker logs -f content-assessment-celery-worker-test
echo     docker logs -f content-assessment-backend-test
echo     docker logs -f content-assessment-frontend-test
echo.
echo ============================================
echo   一键部署完成！
echo ============================================
echo.
pause
