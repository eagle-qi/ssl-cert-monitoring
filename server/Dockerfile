FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（包括 curl 用于健康检查，libpq-dev 用于 psycopg2）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
RUN pip install --no-cache-dir \
    flask \
    requests \
    gunicorn \
    cryptography \
    psycopg2-binary

# 复制应用
COPY agent_server.py /app/
COPY db.py /app/
COPY migrate_json_to_db.py /app/
COPY requirements.txt /app/

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 8090 8092

# 健康检查（使用 HTTPS）
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -kf https://localhost:8092/health || curl -f http://localhost:8090/health

# 启动命令（-u 不缓冲 stdout/stderr）
CMD ["python", "-u", "agent_server.py"]
