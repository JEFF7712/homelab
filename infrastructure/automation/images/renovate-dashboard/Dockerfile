FROM alpine:3.23

RUN apk add --no-cache \
    ca-certificates \
    python3 \
    tzdata \
  && addgroup -S dashboard \
  && adduser -S -G dashboard -u 65532 dashboard

COPY app.py /app/app.py

USER 65532:65532
WORKDIR /app

ENTRYPOINT ["python3", "/app/app.py"]
