#!/bin/bash
# Embedding model on the RX 9060 XT (HIP index 0), port 9702: Qwen3-Embedding-4B for /v1/embeddings.
# Shares the 9060 XT with Quick (9701) and rerank (9703); does not touch the router on 9700.
# Each input (chunk) can be up to 2048 tokens (ctx 4096 / 2 slots); longer ones get HTTP 400.
# Tested 2026-10-27: about 5.2 GB VRAM, 32 chunks of ~380 tokens embedded in 3.1 s, 2560-dim vectors.
# Queries need the instruction prefix "Instruct: <task>\nQuery: <question>"; documents go in as they are.
set -euo pipefail
L="$HOME/locai"
MODEL="$L/models/qwen3-embedding-4b/Qwen3-Embedding-4B-Q8_0.gguf"
KEYFILE="$L/llama/api-key.txt"
[ -f "$MODEL" ]   || { echo "model file missing: $MODEL" >&2; exit 1; }
[ -f "$KEYFILE" ] || { echo "key file missing: $KEYFILE" >&2; exit 1; }
export LD_LIBRARY_PATH="$L/llama/bin:${LD_LIBRARY_PATH:-}"
export HIP_VISIBLE_DEVICES=0
exec "$L/llama/bin/llama-server" \
  -m "$MODEL" --alias embed-qwen --embedding --pooling last -ngl 99 \
  -c 4096 --parallel 2 -b 2048 -ub 2048 \
  --host 0.0.0.0 --port 9702 --api-key "$(cat "$KEYFILE")"
