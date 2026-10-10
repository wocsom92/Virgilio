FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt /tmp/requirements.txt
# Every dependency ships aarch64/x86_64 wheels, so no compiler is installed in the image.
RUN pip install --prefix=/install --only-binary=:all: -r /tmp/requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# iputils-ping lets site-monitor ping checks use ICMP instead of the TCP fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /install /usr/local
COPY backend/__init__.py /app/backend/__init__.py
COPY backend/app /app/backend/app
COPY backend/scripts /app/backend/scripts

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["python", "-m", "backend.app.run"]
