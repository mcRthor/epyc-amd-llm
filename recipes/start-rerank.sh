#!/bin/bash
# Reranker on the RX 9060 XT (HIP index 0), port 9703: Qwen3-Reranker-0.6B for /v1/rerank.
# Shares the 9060 XT with Quick (9701) and embed (9702); does not touch the router on 9700.
# Each query + document pair can be up to 2048 tokens (ctx 4096 / 2 slots); longer ones get HTTP 400.
# Tested 2026-10-27: about 1.4 GB VRAM, 30 documents reranked in 0.8 s, scores 0-1.
set -euo pipefail
L="$HOME/locai"
MODEL="$L/models/qwen3-reranker-0.6b/qwen3-reranker-0.6b-q8_0.gguf"
KEYFILE="$L/llama/api-key.txt"
[ -f "$MODEL" ]   || { echo "model file missing: $MODEL" >&2; exit 1; }
[ -f "$KEYFILE" ] || { echo "key file missing: $KEYFILE" >&2; exit 1; }
export LD_LIBRARY_PATH="$L/llama/bin:${LD_LIBRARY_PATH:-}"
export HIP_VISIBLE_DEVICES=0
exec "$L/llama/bin/llama-server" \
  -m "$MODEL" --alias rerank-qwen --reranking -ngl 99 \
  -c 4096 --parallel 2 -b 2048 -ub 2048 \
  --host 0.0.0.0 --port 9703 --api-key "$(cat "$KEYFILE")"
