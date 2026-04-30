FROM python:3.11-slim

WORKDIR /app

RUN mkdir -p /app/relay_app

COPY requirements.txt /app/relay_app/
RUN pip install --no-cache-dir -r /app/relay_app/requirements.txt

COPY . /app/relay_app/

ENV HOST=0.0.0.0 \
    PORT=5002 \
    LOG_LEVEL=info \
    LIMIT_CONCURRENCY=1000 \
    BACKLOG=2048 \
    TIMEOUT_KEEP_ALIVE=5 \
    WS_PING_INTERVAL=30 \
    WS_PING_TIMEOUT=30 \
    WS_MAX_SIZE=1048576 \
    WS_MAX_QUEUE=64 \
    RELOAD=false

CMD ["sh", "-c", "uvicorn relay_app.main:app \
    --host ${HOST} \
    --port ${PORT} \
    --log-level ${LOG_LEVEL} \
    --forwarded-allow-ips '*' \
    --limit-concurrency ${LIMIT_CONCURRENCY} \
    --backlog ${BACKLOG} \
    --timeout-keep-alive ${TIMEOUT_KEEP_ALIVE} \
    --proxy-headers \
    --ws websockets \
    --ws-ping-interval ${WS_PING_INTERVAL} \
    --ws-ping-timeout ${WS_PING_TIMEOUT} \
    --ws-max-size ${WS_MAX_SIZE} \
    --ws-max-queue ${WS_MAX_QUEUE} \
    $([ \"${RELOAD}\" = \"true\" ] && echo '--reload')"]

EXPOSE 5002
