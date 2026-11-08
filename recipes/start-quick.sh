#!/bin/bash
# Quick model on the RX 9060 XT (HIP index 0), port 9701. Does not touch the router on 9700.
set -euo pipefail
L="$HOME/locai"
MODEL="$L/models/qwen3.5-9b/Qwen3.5-9B-UD-Q4_K_XL.gguf"
KEYFILE="$L/llama/api-key.txt"
[ -f "$MODEL" ]   || { echo "model file missing: $MODEL" >&2; exit 1; }
[ -f "$KEYFILE" ] || { echo "key file missing: $KEYFILE" >&2; exit 1; }
export LD_LIBRARY_PATH="$L/llama/bin:${LD_LIBRARY_PATH:-}"
export HIP_VISIBLE_DEVICES=0
exec "$L/llama/bin/llama-server" \
  -m "$MODEL" --alias quick-qwen3.5,quick --jinja -ngl 99 \
  -c 32768 --parallel 2 \
  --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --presence-penalty 1.5 \
  --reasoning off \
  --host 0.0.0.0 --port 9701 --api-key "$(cat "$KEYFILE")"
