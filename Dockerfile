FROM python:3.15-slim AS base

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

# Without the [tts] extra: onnxruntime, which Piper needs, has no wheels for
# this Python yet. The image plays clips and serves the browser sink; for
# speech, run natively (docs/lattepanda.md) or add the extra once it installs.
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache

ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
ENV VERSION=${VERSION} COMMIT=${COMMIT} DATE=${DATE}

COPY sounds/ sounds/
COPY profile.yaml ./

# In a container there is rarely a speaker: the browser is the sink.
ENV SOUND_SINKS=browser \
    SOUNDS_DIR=/app/sounds \
    TTS_CACHE_DIR=/tmp/sound-catcher

RUN useradd --uid 65532 --no-create-home appuser
USER 65532

EXPOSE 8080

ENTRYPOINT ["sound-catcher"]
