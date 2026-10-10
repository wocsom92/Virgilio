FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY monitor/requirements.txt /tmp/requirements.txt
RUN pip install --prefix=/install --only-binary=:all: -r /tmp/requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY --from=builder /install /usr/local
COPY monitor/__init__.py /app/monitor/__init__.py
COPY monitor/app /app/monitor/app

EXPOSE 9000

CMD ["python", "-m", "monitor.app.run"]
