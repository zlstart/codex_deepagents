FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS converter
RUN --mount=type=secret,id=corporate_ca if [ -s /run/secrets/corporate_ca ]; then \
    cp /run/secrets/corporate_ca /usr/local/share/ca-certificates/site-ca.crt && update-ca-certificates; fi
RUN sed -i 's@URIs: http://deb.debian.org@URIs: https://deb.debian.org@g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress python3-uno python3-openpyxl python3-xlrd \
    fonts-liberation2 fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp/office LANG=C.UTF-8 SAL_USE_VCLPLUGIN=svp
WORKDIR /tmp
USER 10001:10001
FROM converter
COPY --chmod=0555 deploy/preview/ /opt/famsun/
ENTRYPOINT ["/usr/bin/python3", "-I", "/opt/famsun/convert.py"]
