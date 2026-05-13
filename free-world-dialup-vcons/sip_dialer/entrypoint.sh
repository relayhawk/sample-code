#!/bin/sh
set -e

TARGET="${1:-${DIAL_TARGET}}"
DURATION="${CALL_DURATION_SECONDS:-30}"

if [ -z "$TARGET" ]; then
  echo "ERROR: dial target required (arg or DIAL_TARGET env var)" >&2
  exit 1
fi
if [ -z "$SIP_SERVER" ] || [ -z "$SIP_LOGIN" ] || [ -z "$SIP_PASSWORD" ]; then
  echo "ERROR: SIP_SERVER, SIP_LOGIN, SIP_PASSWORD are required" >&2
  exit 1
fi

BARESIP_DIR="/root/.baresip"
mkdir -p "$BARESIP_DIR"

# Write SIP account (baresip codec format is name/rate/channels)
cat > "$BARESIP_DIR/accounts" <<EOF
<sip:${SIP_LOGIN}@${SIP_SERVER}>;auth_pass=${SIP_PASSWORD};outbound="sip:${SIP_SERVER};transport=udp"
EOF

# Write baresip config
cat > "$BARESIP_DIR/config" <<EOF
# Core
sip_trans_bsize      128
log_level            info
listen_addr          0.0.0.0

# Module search path
module_path          /usr/lib/baresip/modules

# Audio — play WAV file as source; discard received audio
audio_source         aufile,/audio/test-call.wav
audio_player         aufile,/dev/null
audio_alert          aufile,/dev/null

# Modules (UDP transport is built-in)
module               account.so
module               aufile.so
module               g711.so
module               opus.so
module               stun.so
module               turn.so
module               menu.so
EOF

echo "==> SIP account: sip:${SIP_LOGIN}@${SIP_SERVER}"
echo "==> Dialing: ${TARGET}"
echo "==> Call duration: ${DURATION}s (then auto-hang-up)"

# Run baresip in foreground:
#   -e "/dial ..."   executes the dial command after startup + registration
#   -t <sec>         auto-quit after N seconds (this ends/drops the call too)
#   -v               verbose SIP output so we can see REGISTER + INVITE responses
QUIT_AFTER=$(( DURATION + 15 ))

baresip \
  -f "$BARESIP_DIR" \
  -e "/dial sip:${TARGET}@${SIP_SERVER}" \
  -t "$QUIT_AFTER" \
  -v \
  2>&1

echo "==> baresip exited. Call should be complete."
