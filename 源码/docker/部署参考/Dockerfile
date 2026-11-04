FROM python:3.10-slim

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置运行环境
ENV ENV=test

WORKDIR /app

# 安装系统依赖（如需可扩展）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

EXPOSE 8000

# 启动服务
CMD ["python", "run.py"]

# Remote Build & Push commands:
# docker -H tcp://127.0.0.1:2375 build -t docker-hosted.example.invalid/nstl/smartbi/service-backend:v1.0.0 .
# docker -H tcp://127.0.0.1:2375 push docker-hosted.example.invalid/nstl/smartbi/service-backend:v1.0.0
