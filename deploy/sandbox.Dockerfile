FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
COPY deploy/sandbox/requirements.lock /opt/famsun/requirements.lock
RUN --mount=type=secret,id=corporate_ca if [ -s /run/secrets/corporate_ca ]; then \
    cp /run/secrets/corporate_ca /usr/local/share/ca-certificates/site-ca.crt && update-ca-certificates; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt UV_SYSTEM_CERTS=true SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
RUN pip install --no-cache-dir uv==0.13.9 && uv pip install --system --no-cache -r /opt/famsun/requirements.lock && \
    groupadd --gid 10001 worker && useradd --uid 10001 --gid worker --create-home worker && \
    install -d -o worker -g worker /workspace && \
    uv venv --system-site-packages /home/worker/.venv && chown -R worker:worker /home/worker
# Support native Python extensions, source distributions and HTTPS VCS installs.
RUN sed -i 's@URIs: http://deb.debian.org@URIs: https://deb.debian.org@g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends build-essential curl git pkg-config && \
    rm -rf /var/lib/apt/lists/*
ENV HOME=/home/worker VIRTUAL_ENV=/home/worker/.venv PATH=/home/worker/.venv/bin:$PATH
WORKDIR /workspace
USER 10001:10001

FROM runtime
COPY deploy/sandbox/artifacts.py deploy/sandbox/proxy.py /opt/famsun/
COPY famsun/skills /opt/famsun/skills
USER root
RUN chmod -R a+rX /opt/famsun
USER 10001:10001
CMD ["sleep", "infinity"]
