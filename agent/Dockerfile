FROM python:3.11-slim

LABEL maintainer="SSL Cert Monitoring"
LABEL description="SSL Certificate Agent for internal network monitoring (Push/Pull/Dual Mode)"

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY ssl_cert_agent.py .

# 创建数据目录
RUN mkdir -p /app/data

# 拉取/双模式暴露端口，推送模式不需要
EXPOSE 8091

# 健康检查 - 自适应模式
# push 模式: 检查进程是否存活（无 HTTP 端口）
# pull/dual 模式: 检查 HTTP 健康接口
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8091/health 2>/dev/null || ps aux | grep ssl_cert_agent.py | grep -v grep > /dev/null 2>&1 || exit 1

# 默认环境变量 - 推送模式
ENV AGENT_MODE=push
ENV SERVER_URL=http://ssl-agent-server:8090
ENV AGENT_LISTEN_PORT=8091

# 启动命令
CMD ["python", "ssl_cert_agent.py"]
