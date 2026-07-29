FROM node:22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build


FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.9.2
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY config ./config
COPY src ./src
COPY --from=frontend-build /frontend/dist ./frontend/dist
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 observatory \
    && mkdir -p /app/var/maas-observatory \
    && chown -R observatory:observatory /app

USER observatory
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
VOLUME ["/app/var/maas-observatory"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

CMD ["maas-observatory", "serve"]
