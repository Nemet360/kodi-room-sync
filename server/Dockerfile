FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WATCHSYNC_DB=/data/watchsync.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN addgroup --system watchsync \
    && adduser --system --ingroup watchsync watchsync \
    && mkdir -p /data \
    && chown watchsync:watchsync /data

USER watchsync
EXPOSE 8844
VOLUME ["/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8844"]
