#!/bin/sh
set -u

output=$(mktemp)
trap 'rm -f "$output"' EXIT HUP INT TERM
attempt=1
while :; do
    status=0
    timeout -k 5 -s TERM 60 "$@" >"$output" 2>&1 || status=$?
    cat "$output"
    if [ "$status" -eq 0 ]; then
        exit 0
    fi
    if [ "$attempt" -ge 3 ]; then
        exit "$status"
    fi
    if [ "$status" -ne 124 ] && ! grep -Eiq 'requested URL returned error: (429|500|502|503|504)|Could not resolve host|Failed to connect|Connection (refused|reset)|Connection timed out|Operation timed out|remote end hung up unexpectedly|TLS connection was non-properly terminated' "$output"; then
        exit "$status"
    fi
    printf 'Transient clone failure, retry %s/2 in %s seconds\n' "$attempt" "$((attempt * 5))" >&2
    sleep "$((attempt * 5))"
    attempt=$((attempt + 1))
done
