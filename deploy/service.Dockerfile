FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS web-deps
WORKDIR /build
COPY web/package*.json ./
RUN --mount=type=secret,id=corporate_ca if [ -s /run/secrets/corporate_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/corporate_ca; fi; npm ci --no-audit --no-fund

FROM web-deps AS ui
COPY web/ ./
RUN npm run build

FROM docker:29-cli@sha256:34507eb5069d8be82c95dd3b6987d4250fd5fee49b82e2509b7d7a7030740d6d AS docker-cli
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS server-deps
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt UV_SYSTEM_CERTS=true
WORKDIR /app
RUN --mount=type=secret,id=corporate_ca if [ -s /run/secrets/corporate_ca ]; then \
    cp /run/secrets/corporate_ca /usr/local/share/ca-certificates/site-ca.crt && update-ca-certificates; fi
RUN pip --cert /etc/ssl/certs/ca-certificates.crt install --no-cache-dir uv==0.13.9
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache --no-install-project && \
    groupadd --gid 10002 service && useradd --uid 10002 --gid service --create-home service && \
    install -d -m 0700 -o service -g service /app/var
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

FROM server-deps
COPY famsun/ ./famsun/
COPY --from=ui /build/dist/ ./web/dist/
RUN chmod -R a+rX /app/famsun /app/web
USER 10002:10002
ENV PATH="/app/.venv/bin:${PATH}" FAMSUN_DATA_DIR=/app/var FAMSUN_STATIC_DIR=/app/web/dist
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]
CMD ["uvicorn","famsun.api:app","--host","0.0.0.0","--port","8000","--workers","1","--no-proxy-headers"]
