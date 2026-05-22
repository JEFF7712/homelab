FROM alpine:3.22

RUN apk add --no-cache \
    ca-certificates \
    git \
    py3-yaml \
    python3 \
  && addgroup -S renovate \
  && adduser -S -G renovate -u 65532 renovate

USER 65532:65532
WORKDIR /work

ENTRYPOINT ["python3", "/config/renovate-agent.py"]
