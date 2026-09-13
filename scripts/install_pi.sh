#!/usr/bin/env bash
#
# Install MIDI Memory as a system service on a Raspberry Pi.
#
#   git clone <repo> ~/midi-memory && cd ~/midi-memory
#   ./scripts/install_pi.sh
#
# Safe to re-run: it upgrades an existing install in place.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="midi-memory"
DATA_DIR="${MIDI_MEMORY_DATA_DIR:-/var/lib/midi-memory}"
RUN_USER="${SUDO_USER:-$USER}"

say()  { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

if [[ "$(uname -s)" != "Linux" ]]; then
  warn "This installer targets Linux (Raspberry Pi OS). For local development run:"
  warn "    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/python -m app"
  exit 1
fi

if [[ "$RUN_USER" == "root" ]]; then
  warn "Run this as your normal user (it will call sudo where needed), not as root."
  exit 1
fi

# ---------------------------------------------------------------- packages --
say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  python3 python3-venv python3-dev libasound2 alsa-utils

# ALSA sequencer access needs group membership; it takes effect on next login,
# but the service gets it immediately via SupplementaryGroups.
if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx audio; then
  say "Adding $RUN_USER to the 'audio' group"
  sudo usermod -aG audio "$RUN_USER"
fi

# The sequencer module is what exposes USB keyboards as connectable ports.
if [[ ! -e /dev/snd/seq ]]; then
  say "Loading the ALSA sequencer kernel module"
  sudo modprobe snd-seq || warn "Could not load snd-seq; MIDI capture may not work"
  echo snd-seq | sudo tee /etc/modules-load.d/midi-memory.conf >/dev/null
fi

# ------------------------------------------------------------------- python --
say "Creating the virtualenv"
cd "$APP_DIR"
[[ -d .venv ]] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e '.[alsa]'

say "Fetching piano samples for browser playback (optional, ~2 MB)"
./.venv/bin/python scripts/fetch_samples.py || \
  warn "Samples unavailable; the player will fall back to its built-in synth."

# ------------------------------------------------------------------ storage --
say "Preparing the data directory at $DATA_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown -R "$RUN_USER":"$RUN_USER" "$DATA_DIR"

# -------------------------------------------------------------------- config --
if [[ ! -f .env ]]; then
  say "Creating .env"
  PASSWORD="$(head -c 9 /dev/urandom | base64 | tr -d '/+=' | head -c 12)"
  cat > .env <<ENVEOF
MIDI_MEMORY_HOST=0.0.0.0
MIDI_MEMORY_PORT=8080
MIDI_MEMORY_PASSWORD=$PASSWORD
MIDI_MEMORY_DATA_DIR=$DATA_DIR
MIDI_MEMORY_IDLE_SECONDS=45
MIDI_MEMORY_MIN_NOTES=4
MIDI_MEMORY_MIN_SECONDS=2
MIDI_MEMORY_DEVICE_MATCH=
MIDI_MEMORY_MIDI_SOURCE=auto
ENVEOF
  chmod 600 .env
  GENERATED_PASSWORD="$PASSWORD"
else
  say "Keeping the existing .env"
fi

# -------------------------------------------------------------------- service --
say "Installing the systemd service"
sed -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@DATA_DIR@|$DATA_DIR|g" \
    -e "s|@USER@|$RUN_USER|g" \
    scripts/midi-memory.service | sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  warn "The service did not start. Recent log:"
  sudo journalctl -u "$SERVICE_NAME" -n 30 --no-pager
  exit 1
fi

# ----------------------------------------------------------------------- done --
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT="$(grep -E '^MIDI_MEMORY_PORT=' .env | cut -d= -f2)"
PORT="${PORT:-8080}"

say "MIDI Memory is running"
echo "  Open:     http://${IP:-<pi-address>}:${PORT}/"
echo "  Hostname: http://$(hostname).local:${PORT}/"
if [[ -n "${GENERATED_PASSWORD:-}" ]]; then
  echo
  echo "  Password: ${GENERATED_PASSWORD}   (generated; change it in $APP_DIR/.env)"
fi
echo
echo "  MIDI ports seen right now:"
./.venv/bin/python -m app.tools.ports 2>&1 | sed 's/^/    /'
echo
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Restart:  sudo systemctl restart $SERVICE_NAME"
