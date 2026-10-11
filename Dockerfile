# Keep the base image reproducible; update this digest deliberately when refreshing Python.
FROM python:3.11-slim@sha256:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca

ARG APP_VERSION=0.0.30
ARG IMAGE_REF=mcp_qianji:0.0.30

LABEL org.opencontainers.image.title="qianji-sdk" \
      org.opencontainers.image.description="Qianji API and MCP server" \
      org.opencontainers.image.ref.name="${IMAGE_REF}" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QIANJI_DB_PATH=/app/data/.qianji.db \
    QIANJI_HOST=0.0.0.0 \
    QIANJI_PORT=50003 \
    TZ=Asia/Shanghai

WORKDIR /app

# python:3.11-slim 不自带时区数据，安装 tzdata 让 TZ 生效
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

# 使用阿里云 pip 源
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com

# 先按 pyproject.toml 安装第三方依赖；源码小改不会让这一层失效
COPY pyproject.toml README.md ./
RUN python -c "import pathlib, tomllib; data = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(data['project']['dependencies']) + '\n')" && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# 复制源码并直接运行模块入口，避免构建本项目 wheel
COPY qianji/ qianji/
COPY scripts/ scripts/

# 预拉取分类图标到包内静态目录，随镜像层烘焙；图标走 /category-icons/ 同源代理，
# 运行时命中即可，无需访问 CDN。构建期无外网时仅告警，运行时回源兜底。
RUN python scripts/prefetch_icons.py || echo "icon prefetch skipped (offline build)"

# 非 root 用户运行，并准备数据目录
# UID/GID 默认与 docker-compose 的 PUID/PGID 对齐，避免命名卷属主不匹配导致 SQLite 无法写入
ARG PUID=1000
ARG PGID=1000
RUN mkdir -p /app/data && \
    groupadd --gid ${PGID} appuser && \
    useradd --create-home --uid ${PUID} --gid ${PGID} appuser && \
    chown -R ${PUID}:${PGID} /app
USER appuser

EXPOSE 50003

# python:slim 镜像没有 curl，用 urllib 打健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:50003/healthz', timeout=3).read()"]

CMD ["python", "scripts/run_api.py"]
