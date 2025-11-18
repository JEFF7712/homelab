#!/bin/bash

MAX=90
ALERT_CMD="curl -s -X POST https://api.telegram.org/bot8566657112:AAFifQmpm32_Z4ZnpU_K-WfgRAqYnRcQvcE/sendMessage -d chat_id=7542293680 -d text='WARNING: Server overheating! Shutting down now.'"

while true; do
    TEMP_RAW=$(sensors | grep 'Package id 0' | awk '{print $4}' | tr -d '+°C')
    TEMP=${TEMP_RAW%.*}
    if [ "$TEMP" != "" ] && [ "$TEMP" -gt "$MAX" ]; then
        echo "Laptop too hot: $TEMP C" | bash -c "$ALERT_CMD"
        sleep 10
        shutdown -h now
    fi

    sleep 5
done 

