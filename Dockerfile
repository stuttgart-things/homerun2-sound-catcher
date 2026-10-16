FROM python:3.14-slim AS base

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

# Python 3.14, not the newest: onnxruntime, which Piper needs, has no wheels
# for 3.15 yet. renovate.json holds the image below 3.15 until it does.
RUN pip install --no-cache-dir ".[tts]" \
    && rm -rf /root/.cache

# The voice is baked in, so a pod speaks without a volume or a download at
# start. Another one: --build-arg VOICE=de_DE-kerstin-low
ARG VOICE=de_DE-thorsten-medium
RUN python -m piper.download_voices --data-dir /app/voices "${VOICE}" \
    && test -f "/app/voices/${VOICE}.onnx"

ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
ENV VERSION=${VERSION} COMMIT=${COMMIT} DATE=${DATE}

COPY sounds/ sounds/
COPY profile.yaml ./

# In a container there is no speaker: the browser is the sink.
ENV SOUND_SINKS=browser \
    SOUNDS_DIR=/app/sounds \
    TTS_VOICE=/app/voices/${VOICE}.onnx \
    TTS_CACHE_DIR=/tmp/sound-catcher

RUN useradd --uid 65532 --no-create-home appuser
USER 65532

EXPOSE 8080

ENTRYPOINT ["sound-catcher"]
