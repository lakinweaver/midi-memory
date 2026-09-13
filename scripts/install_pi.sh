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

say()  { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; STEP="$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

STEP="starting up"
on_error() {
  local code=$?
  warn "Install failed during: ${STEP} (exit ${code}, line ${BASH_LINENO[0]})"
  warn "Nothing is half-installed that re-running will not fix:"
  warn "    ./scripts/install_pi.sh"
  if [[ "$STEP" == *package* || "$STEP" == *virtualenv* || "$STEP" == *samples* ]]; then
    warn "That step needs the network. If this Pi is on flaky wifi, check with:"
    warn "    ping -c3 deb.debian.org && ping -c3 pypi.org"
  fi
  exit "$code"
}
trap on_error ERR

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
sudo apt-get install -y --no-install-recommends python3 python3-venv alsa-utils

# Debian renamed the ALSA runtime in Trixie (libasound2 -> libasound2t64), so a
# hard-coded name aborts the install on newer Raspberry Pi OS. Take whichever
# exists; it is usually already present for some other audio package anyway.
say "Installing the ALSA runtime library"
if ! sudo apt-get install -y --no-install-recommends libasound2t64 2>/dev/null; then
  sudo apt-get install -y --no-install-recommends libasound2 \
    || warn "Could not install libasound2; MIDI may not work. Is it already present?"
fi

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
# Raspberry Pi OS marks the system Python as externally managed (PEP 668), so
# installing into it is refused unless you override -- and overriding risks
# breaking apt's own Python tooling. The venv is the supported route, and costs
# about 15 MB.
say "Creating the virtualenv"
cd "$APP_DIR"
[[ -d .venv ]] || python3 -m venv .venv

# Generous retries: a Pi on wifi drops PyPI connections far more than a laptop.
PIP_NET=(--timeout 60 --retries 5)
./.venv/bin/pip install "${PIP_NET[@]}" --quiet --upgrade pip
if ! ./.venv/bin/pip install "${PIP_NET[@]}" -e '.[alsa]'; then
  warn "Could not install dependencies (see the pip output above)."
  warn "This is almost always a network problem; re-run the script to resume."
  exit 1
fi

if [[ "${SKIP_SAMPLES:-0}" == "1" ]]; then
  say "Skipping piano samples (SKIP_SAMPLES=1); the player will use its synth"
else
  say "Fetching piano samples for browser playback (optional, ~2 MB)"
  ./.venv/bin/python scripts/fetch_samples.py || \
    warn "Samples unavailable; the player falls back to its built-in synth. \
Re-run scripts/fetch_samples.py later, or set SKIP_SAMPLES=1 to stop trying."
fi

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

say "Checking the service is up and answering"
sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  warn "The service did not start. Recent log:"
  sudo journalctl -u "$SERVICE_NAME" -n 30 --no-pager
  exit 1
fi

PORT="$(grep -E '^MIDI_MEMORY_PORT=' .env | cut -d= -f2)"
PORT="${PORT:-8080}"
HEALTH=""
for _ in 1 2 3 4 5; do
  if HEALTH="$(curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null)"; then
    break
  fi
  sleep 2
done
if [[ -z "$HEALTH" ]]; then
  warn "The service is running but is not answering on port ${PORT}. Recent log:"
  sudo journalctl -u "$SERVICE_NAME" -n 30 --no-pager
  exit 1
fi

# ----------------------------------------------------------------------- done --
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

say "MIDI Memory is running and answering on port ${PORT}"
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
