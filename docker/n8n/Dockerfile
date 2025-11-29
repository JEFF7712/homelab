# Based on the official n8n image
FROM n8nio/n8n:latest

USER root

RUN apk add --update --no-cache python3 docker-cli py3-pip build-base ffmpeg ttf-dejavu

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install edge-tts rvc-python

USER node
