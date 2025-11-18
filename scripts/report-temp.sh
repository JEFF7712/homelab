#!/usr/bin/env bash
set -e

TEMP=$(sensors | awk '/Package id 0/ {gsub(/\+/,"",$4); gsub(/° C/,"",$4); print int($4); exit}')

DATE_STR=$(date +"%Y-%m-%d %H:%M:%S")

echo "[$DATE_STR] Current server temperature: $TEMP °C"

## ADD THIS IN crontab -e

#0 0,12 * * * TEMP=$(/usr/local/bin/report-temp.sh) && curl -s -X POST "https://api.telegram.org/<bot-API>/sendMessage" -d chat_id=<chat-id> -d text="$TEMP"
