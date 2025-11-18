#!/usr/bin/env bash
set -e

ENV_FILE="/home/rupan/homelab/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
else
  echo "Warning: $ENV_FILE not found, using existing environment variables"
fi

MAX=20
ALERT_CMD="curl -s -X POST https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage -d chat_id=${TELEGRAM_CHAT_ID} -d text='WARNING: Server overheating! Shutting down now.'"

while true; do
    TEMP_RAW=$(sensors | grep 'Package id 0' | awk '{print $4}' | tr -d '+°C')
    TEMP=${TEMP_RAW%.*}
    if [ "$TEMP" != "" ] && [ "$TEMP" -gt "$MAX" ]; then
        echo "Laptop too hot: $TEMP C" | bash -c "$ALERT_CMD"
        sleep 10
        echo "SHUTDOWN"
    fi

    sleep 5
done 

