FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# curl 供容器内健康检查使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 构建上下文是仓库根目录，因此路径都带 app/ 前缀。
# 这样前端 web/ 也能进镜像，且可以和 app/ 一起 COPY。
COPY app/pyproject.toml ./pyproject.toml
COPY app/src ./src
COPY app/migrations ./migrations
COPY web ./web

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data/uploads /app/data/books

EXPOSE 8000

CMD ["python", "-m", "mistakebook.main"]