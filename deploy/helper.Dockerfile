# The control plane starts this image for short reads and per-session egress.
FROM python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
RUN --mount=type=secret,id=corporate_ca if [ -s /run/secrets/corporate_ca ]; then \
    cp /run/secrets/corporate_ca /usr/local/share/ca-certificates/site-ca.crt && update-ca-certificates; fi
COPY --chmod=0555 deploy/sandbox/artifacts.py deploy/sandbox/proxy.py /opt/famsun/
ENV PYTHONUNBUFFERED=1 SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
USER 10001:10001
