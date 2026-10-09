# Syntax: docker/dockerfile:1
FROM ghcr.io/hotio/lidarr:release

# Install runtime and build dependencies
RUN apk add --no-cache \
    ffmpeg \
    flac \
    jq \
    xq \
    python3 \
    py3-pip \
    imagemagick \
    opus-tools \
    opustags \
    tidyhtml \
    musl-locales \
    musl-locales-lang \
    parallel \
    uv \
    git \
    gcc \
    python3-dev \
    libc-dev \
    nodejs \
    npm \
 && apk add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing atomicparsley

# Install freyr client globally
RUN npm install -g miraclx/freyr-js

# Install Python audio and metadata packages
RUN uv pip install --system --no-cache-dir --break-system-packages \
    jellyfish \
    beautifulsoup4 \
    yt-dlp \
    beets \
    yq \
    pyxDamerauLevenshtein \
    pyacoustid \
    requests \
    colorama \
    python-telegram-bot \
    pylast \
    mutagen \
    r128gain \
    tidal-dl \
    deemix \
    langdetect \
    apprise \
    "tidaler==0.1.8"

# Install SMA (sickbeard_mp4_automator)
RUN git clone --depth 1 https://github.com/mdhiggins/sickbeard_mp4_automator.git /usr/local/sma \
 && uv pip install --system --no-cache-dir --break-system-packages -r /usr/local/sma/setup/requirements.txt \
 && touch /usr/local/sma/config/sma.log \
 && chgrp users /usr/local/sma/config/sma.log \
 && chmod g+w /usr/local/sma/config/sma.log

# Fetch and install arr-scripts into /custom-services.d
RUN mkdir -p /custom-services.d/python /config/extended \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/universal/services/QueueCleaner -o /custom-services.d/QueueCleaner \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/AutoConfig.service.bash -o /custom-services.d/AutoConfig \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/Video.service.bash -o /custom-services.d/Video \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/TidalVideoDownloader.bash -o /custom-services.d/TidalVideoDownloader \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/Audio.service.bash -o /custom-services.d/Audio \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/AutoArtistAdder.bash -o /custom-services.d/AutoArtistAdder \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/UnmappedFilesCleaner.bash -o /custom-services.d/UnmappedFilesCleaner \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/python/ARLChecker.py -o /custom-services.d/python/ARLChecker.py \
 && curl -sfL https://raw.githubusercontent.com/RandomNinjaAtk/arr-scripts/main/lidarr/ARLChecker -o /custom-services.d/ARLChecker \
 && chmod +x /custom-services.d/ARLChecker /custom-services.d/Audio

# Install tidal-dl compatibility shim for tidaler
COPY tidal-dl-shim /usr/local/bin/tidal-dl
RUN chmod 755 /usr/local/bin/tidal-dl

# Clean up build-only packages and cache
RUN apk del gcc python3-dev libc-dev git \
 && rm -rf /root/.cache /tmp/*
