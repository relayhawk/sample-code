FROM python:3.11-slim

WORKDIR /app

RUN mkdir -p /app/vcon_app /app/captured

COPY requirements.txt /app/vcon_app/
RUN pip install --no-cache-dir -r /app/vcon_app/requirements.txt

COPY . /app/vcon_app/

ENV HOST=0.0.0.0 \
    PORT=5002 \
    LOG_LEVEL=info \
    LIMIT_CONCURRENCY=1000 \
    BACKLOG=2048 \
    TIMEOUT_KEEP_ALIVE=5 \
    RELOAD=false

CMD ["sh", "-c", "uvicorn vcon_app.main:app \
    --host ${HOST} \
    --port ${PORT} \
    --log-level ${LOG_LEVEL} \
    --forwarded-allow-ips '*' \
    --limit-concurrency ${LIMIT_CONCURRENCY} \
    --backlog ${BACKLOG} \
    --timeout-keep-alive ${TIMEOUT_KEEP_ALIVE} \
    --proxy-headers \
    $([ \"${RELOAD}\" = \"true\" ] && echo '--reload')"]

EXPOSE 5002
