#!/bin/sh
# Run the server as whoever owns the mounted data directory.
#
# A container that hard-codes its own uid only works when the host directory
# happens to belong to that uid, which on a NAS it never does: a DSM or unRAID
# share belongs to a real user there, with ACLs on top. So the image starts as
# root, takes ownership of /data for the ids you asked for, and drops to them
# before exec'ing the server. Nothing runs as root but this script.
set -eu

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"
DATA_DIR="${MIDI_MEMORY_DATA_DIR:-/data}"

log() { printf '%s entrypoint: %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# Already dropped -- someone set `user:` in compose, or `docker run --user`.
# Their choice; just check it can do its job before the traceback would.
if [ "$(id -u)" != "0" ]; then
  if [ ! -w "$DATA_DIR" ]; then
    log "WARNING: running as uid $(id -u) but $DATA_DIR is not writable by it."
    log "Drop the 'user:' override and set PUID/PGID instead, or fix the"
    log "directory's ownership on the host."
  fi
  exec "$@"
fi

mkdir -p "$DATA_DIR"

# Recurse only when the top level is wrong, which is the first start and an
# adopted library. Every restart after that is a single stat.
owner="$(stat -c '%u:%g' "$DATA_DIR" 2>/dev/null || echo 'unknown')"
if [ "$owner" != "$PUID:$PGID" ]; then
  log "Taking ownership of $DATA_DIR for $PUID:$PGID (was $owner)"
  if ! chown -R "$PUID:$PGID" "$DATA_DIR"; then
    log "WARNING: could not change ownership of $DATA_DIR."
    log "If it is a network share (NFS, SMB, a NAS mount), chown there is often"
    log "refused -- set PUID/PGID to the ids that already own it instead of"
    log "expecting the container to change them."
  fi
fi

exec gosu "$PUID:$PGID" "$@"
