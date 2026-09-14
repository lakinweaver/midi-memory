#!/bin/sh
# Run the server as whoever owns the mounted data directory.
#
# A container that hard-codes its own uid only works when the host directory
# happens to belong to that uid, which on a NAS it never does: a DSM or unRAID
# share belongs to a real user there, with ACLs on top. So the image starts as
# root, works out who it should be, and drops to them before exec'ing the
# server. Nothing runs as root but this script.
#
# Leave PUID/PGID unset and it adopts whoever already owns the data directory.
# That is the right answer almost everywhere and needs no configuration: the
# files stay owned by the user who owns the share, and nothing is chowned out
# from under them. Set them explicitly to override.
set -eu

DATA_DIR="${MIDI_MEMORY_DATA_DIR:-/data}"
FALLBACK_UID=10001
FALLBACK_GID=10001

log() { printf '%s entrypoint: %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# Already dropped -- someone set `user:` in compose, or `docker run --user`.
# Their choice; just check it can do its job before the traceback would.
if [ "$(id -u)" != "0" ]; then
  if [ ! -w "$DATA_DIR" ]; then
    log "WARNING: running as uid $(id -u) but $DATA_DIR is not writable by it."
    log "Drop the 'user:' override and let the entrypoint pick the owner, or"
    log "fix the directory's ownership on the host."
  fi
  exec "$@"
fi

mkdir -p "$DATA_DIR"
owner_uid="$(stat -c '%u' "$DATA_DIR" 2>/dev/null || echo "$FALLBACK_UID")"
owner_gid="$(stat -c '%g' "$DATA_DIR" 2>/dev/null || echo "$FALLBACK_GID")"

# An unset PUID means "whoever owns it already". Root ownership is the exception:
# that is a fresh named volume rather than somebody's share, and running the
# server as root to write it would be a poor trade.
if [ -z "${PUID:-}" ]; then
  if [ "$owner_uid" != "0" ]; then
    PUID="$owner_uid"
    log "Adopting the owner of $DATA_DIR: uid $PUID (set PUID to override)"
  else
    PUID="$FALLBACK_UID"
  fi
fi
if [ -z "${PGID:-}" ]; then
  if [ "$owner_gid" != "0" ]; then
    PGID="$owner_gid"
  else
    PGID="$FALLBACK_GID"
  fi
fi

# Recurse only when the top level is wrong, which is a first start against an
# empty volume or a deliberate PUID change. Every other start is a single stat.
if [ "$owner_uid:$owner_gid" != "$PUID:$PGID" ]; then
  log "Taking ownership of $DATA_DIR for $PUID:$PGID (was $owner_uid:$owner_gid)"
  if ! chown -R "$PUID:$PGID" "$DATA_DIR"; then
    log "WARNING: could not change ownership of $DATA_DIR."
    log "If it is a network share (NFS, SMB, a NAS mount), chown there is often"
    log "refused -- set PUID/PGID to the ids that already own it instead of"
    log "expecting the container to change them."
  fi
fi

exec gosu "$PUID:$PGID" "$@"
