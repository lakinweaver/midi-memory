# The MIDI Memory library server.
#
# Capture clients are not in this image and never will be: they need USB and
# ALSA access on the machine beside the instrument, which is the whole reason
# the two are separate.
FROM python:3.13-slim AS build

WORKDIR /src
COPY pyproject.toml README.md ./
COPY midi_memory ./midi_memory

# Build a wheel so the runtime stage carries no build tooling.
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist


FROM python:3.13-slim

LABEL org.opencontainers.image.title="MIDI Memory" \
      org.opencontainers.image.description="Always-on MIDI notepad: the library server."

# curl for the healthcheck; gosu to drop privileges in the entrypoint.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gosu \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl "midi-memory[server]" \
 && rm -f /tmp/*.whl

# Bake the piano samples into the image. They are about 2 MB, and fetching them
# at runtime would make a first playback depend on the internet being up --
# which, for a machine whose job is to sit on a home network, it may not be.
# The Download button in Settings stays as a fallback either way.
RUN python -c "from midi_memory.server import samples; samples.download()"

# The default identity the server runs as. PUID/PGID override it at runtime,
# which is how a bind mount from a NAS share is made writable -- see
# docker/entrypoint.sh. The volume is declared so a plain `docker run` with no
# -v still keeps recordings somewhere sensible.
RUN useradd --create-home --uid 10001 midimemory \
 && mkdir -p /data && chown midimemory:midimemory /data
VOLUME ["/data"]

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Deliberately still root here: the entrypoint needs it to own the data
# directory, and drops privileges before the server ever runs.
#
# PUID/PGID are deliberately NOT given defaults. Unset is meaningful: it tells
# the entrypoint to adopt whoever already owns the data directory, which is what
# makes a NAS bind mount work with no configuration. A default here would look
# harmless and quietly chown somebody's share to a uid that does not exist on
# their box.
ENV MIDI_MEMORY_DATA_DIR=/data \
    MIDI_MEMORY_HOST=0.0.0.0 \
    MIDI_MEMORY_PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["midi-memory-server"]
