FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    baresip \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /root/.baresip /audio

COPY audio/ /audio/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
