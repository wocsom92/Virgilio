FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY monitor/requirements.txt /tmp/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /install /usr/local

COPY monitor/app /app/monitor/app

EXPOSE 9000

CMD ["uvicorn", "monitor.app.main:app", "--host", "0.0.0.0", "--port", "9000"]
