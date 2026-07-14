FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PTBD_WEB_HOST=0.0.0.0 \
    PTBD_WEB_PORT=8899 \
    PTBD_WEB_MODE=local \
    PTBD_WEB_LOCAL_ROOT=/media \
    PTBD_WEB_CONFIG=/config/config.json \
    PTBD_CONTAINER_SAVE_DIR=/output \
    BDTOOL_DATA_DIR=/config/runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        ffmpeg \
        libbluray-bin \
        mediainfo \
        python3 \
        python3-numpy \
        python3-pil \
        tini \
        zip \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 ptbd \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin ptbd \
    && printf '%s\n' \
        '#!/usr/bin/env sh' \
        'set -eu' \
        'if [ "${1:-}" = "-w" ]; then shift; fi' \
        'target="${1:-}"' \
        '[ -n "$target" ] || { echo "usage: BDInfo <scan_target> [out_dir]" >&2; exit 2; }' \
        'exec bd_info "$target"' \
        > /usr/local/bin/BDInfo \
    && chmod 0755 /usr/local/bin/BDInfo

WORKDIR /opt/PT-BDtool

COPY bdtool bdtool-legacy.sh bdtool.sh ptbd-web.py ptbd_remote_backend.py ./
COPY lib/ui.sh ./lib/ui.sh
COPY scripts/audio-spectrum.py ./scripts/audio-spectrum.py
COPY ptbd_core ./ptbd_core
COPY docker ./docker

RUN python3 ptbd_core/runtime_assets.py validate --profile docker --source-root /opt/PT-BDtool \
    && chmod 0755 bdtool bdtool-legacy.sh bdtool.sh scripts/audio-spectrum.py docker/entrypoint.sh docker/healthcheck.py \
    && mkdir -p /media /output /config \
    && chown -R ptbd:ptbd /output /config

USER ptbd:ptbd

EXPOSE 8899
VOLUME ["/config", "/output"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python3", "/opt/PT-BDtool/docker/healthcheck.py"]

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/PT-BDtool/docker/entrypoint.sh"]
