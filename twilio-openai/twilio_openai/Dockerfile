FROM python:3.11-slim

# Set working directory to the app directory
WORKDIR /app

# Create twilio_openai directory
RUN mkdir -p /app/twilio_openai

# Install Python dependencies
COPY requirements.txt /app/twilio_openai/
RUN pip install --no-cache-dir -r /app/twilio_openai/requirements.txt

# Install uvicorn
RUN pip install uvicorn

# Copy all twilio_openai code
COPY . /app/twilio_openai/

# Default environment variables
ENV HOST=0.0.0.0 \
    PORT=5001 \
    LOG_LEVEL=info \
    LIMIT_CONCURRENCY=1000 \
    BACKLOG=2048 \
    TIMEOUT_KEEP_ALIVE=5 \
    WS_PING_INTERVAL=30 \
    WS_PING_TIMEOUT=30 \
    WS_MAX_SIZE=1048576 \
    WS_MAX_QUEUE=64 \
    RELOAD=false

CMD ["sh", "-c", "uvicorn twilio_openai.main:app \
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

EXPOSE 5001 