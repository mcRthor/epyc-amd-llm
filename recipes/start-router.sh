#!/usr/bin/env bash
# start-router.sh - start llama-server in router mode (all models behind one port, swapped on demand)
#
# Used by the locai-router systemd service, but you can run it by hand too:  ./start-router.sh
# Defaults (override with environment variables, or in ~/locai/llama/router.env for the service):
#   ROUTER_HOST=0.0.0.0        listen on all interfaces (local and LAN)
#   ROUTER_PORT=9700
#   ROUTER_GPU=1               HIP index of the GPU to use (1 = R9700)
#   ROUTER_MODELS_MAX=1        models kept loaded at once
#   ROUTER_PRESET=~/locai/models/router.ini
#   ROUTER_KEY_FILE=~/locai/llama/api-key.txt
#   ROUTER_NO_KEY=1            run WITHOUT an API key (then rely on the firewall)
#   LLAMA_HOME=~/locai/llama
#   LLAMA_BIN=$LLAMA_HOME/bin  folder with llama-server; point it at another build to test it
#                              (e.g. LLAMA_BIN=~/locai/llama/b1359 on a test router)
set -euo pipefail

LLAMA_HOME="${LLAMA_HOME:-$HOME/locai/llama}"
PRESET="${ROUTER_PRESET:-$HOME/locai/models/router.ini}"
KEY_FILE="${ROUTER_KEY_FILE:-$HOME/locai/llama/api-key.txt}"
HOST="${ROUTER_HOST:-0.0.0.0}"
PORT="${ROUTER_PORT:-9700}"
GPU="${ROUTER_GPU:-1}"
MAX="${ROUTER_MODELS_MAX:-1}"
LLAMA_BIN="${LLAMA_BIN:-$LLAMA_HOME/bin}"

[ -x "$LLAMA_BIN/llama-server" ] || { echo "llama-server not found in $LLAMA_BIN - unpack a llama.cpp ROCm build there (see recipes/README.md)" >&2; exit 1; }
[ -f "$PRESET" ] || { echo "preset file not found: $PRESET" >&2; exit 1; }

ARGS=(--models-preset "$PRESET" --models-max "$MAX" --jinja --host "$HOST" --port "$PORT")
if [ -z "${ROUTER_NO_KEY:-}" ]; then
  [ -s "$KEY_FILE" ] || { echo "API key file missing or empty: $KEY_FILE (create it, or set ROUTER_NO_KEY=1)" >&2; exit 1; }
  ARGS+=(--api-key "$(cat "$KEY_FILE")")
else
  echo "WARNING: starting without an API key; only the firewall limits access" >&2
fi

export LD_LIBRARY_PATH="$LLAMA_BIN:${LD_LIBRARY_PATH:-}"
echo "router: host $HOST, port $PORT, GPU index $GPU, max loaded models $MAX, presets $PRESET, llama.cpp $LLAMA_BIN" >&2
HIP_VISIBLE_DEVICES="$GPU" exec "$LLAMA_BIN/llama-server" "${ARGS[@]}"
