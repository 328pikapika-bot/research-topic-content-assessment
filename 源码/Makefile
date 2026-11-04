.PHONY: help install format lint clean serve docker-build docker-up docker-down docker-down-vol info

# 默认目标
.DEFAULT_GOAL := help

# 颜色定义
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

# ==================== 帮助信息 ====================
help: ## 显示帮助信息
	@echo "$(BLUE)内容评估智能体 (ContentAssessment Agent) 命令$(NC)"
	@echo ""
	@echo "$(GREEN)可用命令:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'

# ==================== 安装依赖 ====================
install: ## 安装项目依赖
	@echo "$(BLUE)安装后端依赖...$(NC)"
	cd backend && pip install -r requirements.txt
	@echo "$(BLUE)安装前端依赖...$(NC)"
	cd frontend/web && npm install --registry=https://registry.npmmirror.com
	@echo "$(GREEN)安装完成！$(NC)"

# ==================== 代码质量 ====================
format: ## 格式化代码 (ruff)
	@echo "$(BLUE)格式化代码...$(NC)"
	ruff format backend/ --line-length 100
	@echo "$(GREEN)格式化完成！$(NC)"

lint: ## 运行代码检查 (ruff)
	@echo "$(BLUE)运行代码检查...$(NC)"
	ruff check backend/
	@echo "$(GREEN)检查完成！$(NC)"

lint-fix: ## 自动修复代码问题
	@echo "$(BLUE)自动修复代码问题...$(NC)"
	ruff check --fix backend/
	@echo "$(GREEN)修复完成！$(NC)"

# ==================== 清理 ====================
clean: ## 清理临时文件
	@echo "$(BLUE)清理临时文件...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)清理完成！$(NC)"

# ==================== 服务启动 ====================
serve: ## 启动后端开发服务 (端口 17607)
	@echo "$(BLUE)启动后端服务...$(NC)"
	cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 17607 --reload

serve-frontend: ## 启动前端开发服务 (Vite, 端口 18093)
	@echo "$(BLUE)启动前端服务...$(NC)"
	cd frontend/web && npm run dev

# ==================== Docker ====================
docker-build: ## 构建 Docker 镜像
	@echo "$(BLUE)构建后端镜像...$(NC)"
	docker build -f docker/Dockerfile.backend -t datahub-agentkit/content-assessment-backend:latest .
	@echo "$(BLUE)构建前端镜像...$(NC)"
	docker build -f docker/Dockerfile.frontend -t datahub-agentkit/content-assessment-frontend:latest .
	@echo "$(GREEN)构建完成！$(NC)"

docker-up: ## 启动 Docker Compose 服务
	@echo "$(BLUE)启动 Docker 服务...$(NC)"
	cd docker && docker compose up -d
	@echo "$(GREEN)服务已启动！$(NC)"

docker-down: ## 停止 Docker Compose 服务
	@echo "$(BLUE)停止 Docker 服务...$(NC)"
	cd docker && docker compose down
	@echo "$(GREEN)服务已停止！$(NC)"

docker-down-vol: ## 停止并清理数据卷
	@echo "$(YELLOW)警告: 将删除数据库数据！$(NC)"
	cd docker && docker compose down -v
	@echo "$(GREEN)已清理！$(NC)"

# ==================== 信息 ====================
info: ## 显示项目信息
	@echo "$(BLUE)项目信息:$(NC)"
	@echo "  名称: content-assessment-agent"
	@echo "  版本: 0.1.0"
	@echo "  后端端口: 17607"
	@echo "  前端端口: 18093"
	@echo "  Python: $(shell python --version)"
	@echo ""
	@echo "$(BLUE)模块结构:$(NC)"
	@find backend/app -type f -name "*.py" | sort | head -20
