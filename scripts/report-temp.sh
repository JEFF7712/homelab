#!/bin/bash

TEMP=$(sensors | awk '/Package id 0/ {gsub(/\+/,"",$4); gsub(/° C/,"",$4); print int($4); exit}')

DATE_STR=$(date +"%Y-%m-%d %H:%M:%S")

echo "[$DATE_STR] Current server temperature: $TEMP °C"
