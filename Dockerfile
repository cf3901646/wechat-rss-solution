# ===============================================================
# 微信公众号知识库助手 - 生产级全内置容器镜像 (Dockerfile)
# 开箱即用：内置完整 Python 环境、中文字体、驱动与 Web 控制台
# ===============================================================

FROM python:3.11-slim-bookworm

# 基础环境变量：禁止字节码生成、实时标准输出、时区设置
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    HOST=0.0.0.0 \
    PORT=8002 \
    DEBIAN_FRONTEND=noninteractive

# 安装系统级必备依赖与中文字体支持 (确保在无宿主机中文字体环境下生成图片与文本不乱码)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖文件并安装，利用 Docker 缓存层加速二次构建
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# 复制项目核心代码、服务驱动与静态资源
COPY scripts/ /app/scripts/
COPY driver/ /app/driver/
COPY core/ /app/core/
COPY config/ /app/config/
COPY static/ /app/static/
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

# 预创建持久化目录并赋予权限
RUN mkdir -p /app/data /app/output/articles /app/logs && \
    chmod +x /app/docker/entrypoint.sh

# 暴露知识库助手 Web 服务端口
EXPOSE 8002

# 健康检查：自动探测管理门户状态
HEALTHCHECK --interval=30s --timeout=5s --start-period=8s --retries=3 \
    CMD curl -f http://127.0.0.1:8002/api/status || exit 1

# 入口脚本启动
ENTRYPOINT ["/app/docker/entrypoint.sh"]
