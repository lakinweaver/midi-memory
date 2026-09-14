#!/usr/bin/env bash
#
# Install the MIDI Memory capture client as a system service on a Raspberry Pi.
#
#   git clone <repo> ~/midi-memory && cd ~/midi-memory
#   ./scripts/install_client_pi.sh
#
# This installs the recorder only. The library lives on the server, which runs
# somewhere else -- see docker-compose.yml. Once this finishes, open the address
# it prints and paste in the server address and the client secret.
#
# Safe to re-run: it upgrades an existing install in place.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="midi-memory-client"
DATA_DIR="${MIDI_MEMORY_DATA_DIR:-/var/lib/midi-memory-client}"
RUN_USER="${SUDO_USER:-$USER}"
PORT="${MIDI_MEMORY_PORT:-8081}"

say()  { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; STEP="$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

STEP="starting up"
on_error() {
  local code=$?
  warn "Install failed during: ${STEP} (exit ${code}, line ${BASH_LINENO[0]})"
  warn "Nothing is half-installed that re-running will not fix:"
  warn "    ./scripts/install_client_pi.sh"
  if [[ "$STEP" == *package* || "$STEP" == *virtualenv* ]]; then
    warn "That step needs the network. If this Pi is on flaky wifi, check with:"
    warn "    ping -c3 deb.debian.org && ping -c3 pypi.org"
  fi
  exit "$code"
}
trap on_error ERR

if [[ "$(uname -s)" != "Linux" ]]; then
  warn "This installer targets Linux (Raspberry Pi OS). For local development run:"
  warn "    python3 -m venv .venv && .venv/bin/pip install -e '.[dev,client]'"
  warn "    .venv/bin/python -m midi_memory.client"
  exit 1
fi

# Test the EFFECTIVE user, not $SUDO_USER. Running this under sudo sets
# SUDO_USER to your own name, so checking that would wave sudo straight through
# -- and then every file the script creates (.venv, .env, the egg-info) is owned
# by root inside your home directory, which the service, running as you, cannot
# read.
if [[ $EUID -eq 0 ]]; then
  warn "Do not run this with sudo or as root -- it calls sudo itself where needed."
  if [[ -n "${SUDO_USER:-}" ]]; then
    warn "Run it as yourself instead:"
    warn "    exit  # leave this root shell, or drop the sudo"
    warn "    cd ${APP_DIR} && ./scripts/install_client_pi.sh"
  fi
  exit 1
fi

# An earlier sudo run leaves root-owned files behind that this run cannot
# overwrite -- the usual symptom is a permission error on .env. Repair rather
# than dying, since we already use sudo for apt and systemd anyway.
if find "$APP_DIR" -maxdepth 2 ! -user "$RUN_USER" -print -quit 2>/dev/null | grep -q .; then
  say "Reclaiming files left owned by another user (likely an earlier sudo run)"
  sudo chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
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
if ! ./.venv/bin/pip install "${PIP_NET[@]}" -e '.[client,alsa]'; then
  warn "Could not install dependencies (see the pip output above)."
  warn "This is almost always a network problem; re-run the script to resume."
  exit 1
fi

# No piano samples here: the client has no player. That is two megabytes and one
# flaky-network step this install does not need.

# ------------------------------------------------------------------ storage --
say "Preparing the data directory at $DATA_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown -R "$RUN_USER":"$RUN_USER" "$DATA_DIR"

# -------------------------------------------------------------------- config --
if [[ -f .env && ! -w .env ]]; then
  warn ".env exists but is not writable by $RUN_USER:"
  ls -l .env >&2
  warn "Fix the ownership and re-run:"
  warn "    sudo chown $RUN_USER:$RUN_USER $APP_DIR/.env"
  exit 1
fi

if [[ ! -f .env ]]; then
  say "Creating .env"
  # The settings page can change the server address and holds this client's
  # secret, so it gets a password even though it only serves the LAN.
  PASSWORD="$(head -c 9 /dev/urandom | base64 | tr -d '/+=' | head -c 12)"
  cat > .env <<ENVEOF
MIDI_MEMORY_HOST=0.0.0.0
MIDI_MEMORY_PORT=$PORT
MIDI_MEMORY_PASSWORD=$PASSWORD
MIDI_MEMORY_DATA_DIR=$DATA_DIR

# Filled in from the settings page, or set here if you prefer.
MIDI_MEMORY_SERVER_URL=${MIDI_MEMORY_SERVER_URL:-}
MIDI_MEMORY_CLIENT_SECRET=${MIDI_MEMORY_CLIENT_SECRET:-}

MIDI_MEMORY_IDLE_SECONDS=45
MIDI_MEMORY_MIN_NOTES=4
MIDI_MEMORY_MIN_SECONDS=2
MIDI_MEMORY_DEVICE_MATCH=
MIDI_MEMORY_MIDI_SOURCE=auto
MIDI_MEMORY_KEEP_UPLOADED_DAYS=7
ENVEOF
  chmod 600 .env
  GENERATED_PASSWORD="$PASSWORD"
else
  say "Keeping the existing .env"
fi

# -------------------------------------------------------------------- service --
say "Installing the systemd service"
# An install upgrading from before the server/client split has the old unit
# still running and still recording. Stop it, or two recorders fight over the
# same keyboard port.
if systemctl list-unit-files 'midi-memory.service' 2>/dev/null | grep -q midi-memory.service; then
  say "Removing the pre-split midi-memory service"
  sudo systemctl disable --now midi-memory.service || true
  sudo rm -f /etc/systemd/system/midi-memory.service
fi

sed -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@DATA_DIR@|$DATA_DIR|g" \
    -e "s|@USER@|$RUN_USER|g" \
    scripts/midi-memory-client.service | sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null

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
PORT="${PORT:-8081}"
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
URL="http://${IP:-<pi-address>}:${PORT}/"

say "The capture client is running and recording"
echo
echo "  One thing left: point it at your server."
echo
echo "    1. Open the MIDI Memory server and go to Settings -> Capture clients."
echo "    2. Add a client, name it, and copy the secret it shows you."
echo "    3. Open ${URL} and paste the server address and that secret."
echo

# Always say something about the password, not just on the run that generated
# one. Re-running the installer is normal -- it is how you upgrade -- and a
# second run that says nothing leaves no clue that the page even has a password,
# let alone where it lives.
if [[ -n "${GENERATED_PASSWORD:-}" ]]; then
  echo "  The password for that page: ${GENERATED_PASSWORD}"
  echo "  Written to $APP_DIR/.env. Change it there and restart the service."
elif grep -qE '^MIDI_MEMORY_PASSWORD=.+$' .env 2>/dev/null; then
  # Deliberately not echoed: it is already on disk, and re-printing it on every
  # upgrade would scatter it through shell history and journal logs.
  echo "  That page has a password, set in $APP_DIR/.env. To see it:"
  echo "      grep MIDI_MEMORY_PASSWORD $APP_DIR/.env"
else
  echo "  That page has NO password -- anyone on your network can open it and"
  echo "  change where this client sends recordings. To set one:"
  echo "      nano $APP_DIR/.env        # set MIDI_MEMORY_PASSWORD=something"
  echo "      sudo systemctl restart $SERVICE_NAME"
fi
echo
echo "  Also at: http://$(hostname).local:${PORT}/"
echo
echo "  MIDI ports seen right now:"
./.venv/bin/python -m midi_memory.tools.ports 2>&1 | sed 's/^/    /'
echo
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Restart:  sudo systemctl restart $SERVICE_NAME"
