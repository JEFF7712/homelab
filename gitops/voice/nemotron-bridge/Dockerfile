FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates ninja-build python3-pip libsentencepiece-dev \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir cmake==3.31.6
WORKDIR /src
RUN git init . && git remote add origin https://github.com/NVIDIA/NeMo-Speech.cpp.git \
 && git fetch --depth 1 origin 07003daa7eefea542076310722ccaa89709ee3c3 \
 && git checkout --detach FETCH_HEAD \
 && git submodule update --init --depth 1 ggml \
 && bash scripts/apply-ggml-patches.sh
ARG BUILD_JOBS=2
RUN cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/nemo \
    -DCMAKE_INSTALL_LIBDIR=lib64 -DCMAKE_INSTALL_RPATH=/opt/nemo/lib64 \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DGGML_NATIVE=OFF \
    -DNEMO_SPEECH_GGML_PATCHED=ON -DNEMO_SPEECH_BUILD_ASR=ON \
    -DNEMO_SPEECH_BUILD_DIAR=OFF -DNEMO_SPEECH_BUILD_TTS=OFF \
    -DNEMO_SPEECH_BUILD_NMT=OFF -DNEMO_SPEECH_BUILD_CLI=OFF \
    -DNEMO_SPEECH_BUILD_TESTS=OFF -DBUILD_TESTING=OFF \
 && cmake --build build --parallel ${BUILD_JOBS} \
 && cmake --install build

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/nemo/lib64:/usr/local/cuda/lib64 \
    NEMO_LIB=/opt/nemo/lib64/libnemo_speech_asr_c.so
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv libgomp1 libsentencepiece0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=build /opt/nemo /opt/nemo
COPY bridge.py proxy.py verify_contract.py /app/
RUN python3 /app/verify_contract.py --abi-only
EXPOSE 10300 9100
USER 1000:1000
ENTRYPOINT ["python3", "/app/bridge.py"]
