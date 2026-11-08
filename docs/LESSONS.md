# Lessons learned

What building and testing this server taught us, beyond the hardware notes. Each point comes from a
measurement or a failure on this machine; the numbers are in [BENCHMARKS.md](../BENCHMARKS.md) and the
comments in [`recipes/router.ini`](../recipes/router.ini).

- [Hardware](#hardware)
- [Fitting models into 32 GB](#fitting-models-into-32-gb)
- [Running the router](#running-the-router)
- [Speed tricks](#speed-tricks)
- [Benchmarking](#benchmarking)

## Hardware

Covered in [HARDWARE.md](HARDWARE.md); the short version:

- **Don't trust the first PCIe number you see.** Linux reports 32 GT/s for each GPU, but that is the link
  inside the card; the real link to the CPU is PCIe 4.0 x16. Walk up to the root port.
- **The HIP device order is the reverse of the PCI order** (the 9060 XT is HIP 0). Check which card's VRAM
  fills before trusting an index.
- **The R9700 forgets its power limit at every boot.** Set it with a oneshot unit. 250 W instead of 300 W
  cost 3.5% prompt speed and 1% generation speed.
- **The 9060 XT's firmware hung once in 31 boots**; a reboot fixed it. Its fans stop at idle, so it idles
  warmer. Neither is a fault in the build.
- **The BIOS defaults already work**: Above 4G decoding, Re-Size BAR and IOMMU are on out of the box.

## Fitting models into 32 GB

- **Choose the quant from what is left after context.** Nemotron Super 49B at 4 bits is 30.4 GB, which
  leaves no room for context on a 32 GB card; Q3_K_XL (24.8 GB) with a q8_0 KV cache fits at 32k context
  in 27.1 GB.
- **A quantised KV cache needs flash attention.** `cache-type-v = q8_0` only works with `flash-attn = on`.
- **Hybrid Mamba models get long context almost for free.** Nemotron 3.5 Bolt uses attention in only 8 of
  its 88 layers, so 131k context costs about 1.1 GB of KV cache at f16; the whole model loads in 30.1 GB.
  Check how many layers use attention before cutting context or quantising the cache.
- **Bigger quants were not worth it.** 6-bit versions of Glimmer 30B, Qwen3.6 27B and Qwen3.8 27B gave no
  clear gain over 4-bit on the hard set and ran 25-33% slower.
- **Models bigger than VRAM do run**, with expert weights in system RAM (`n-cpu-moe`), but at 6-19 tok/s,
  limited by memory bandwidth. Tune `n-cpu-moe` by measuring: raise it until 1-2 GB of VRAM stays free.
  Ornith 122B fitted at 33 without its vision file (1.7 GB free); the 0.9 GB vision file needs room too.

## Running the router

- **Try every new model and every new llama.cpp build on a second router** on a spare port before touching
  the live one. llama.cpp b1350 could not load Recursive Flint at all; b1359 could. And a preset that
  points at a missing file stops the router's next restart.
- **Never ask the router about a model by name just to check on it.** `/slots?model=X` and any request
  naming X make the router load X, unloading whatever was running.
- **Keep small always-on models off the router.** With one model loaded at a time, a request for the 9B
  chat model through the router evicts the big model. Here the small models run as their own servers on
  the 9060 XT.
- **Embedding queries need the instruction prefix** (`Instruct: <task>\nQuery: <question>`); documents go
  in as they are. Inputs longer than one slot's context (2,048 tokens here) fail with HTTP 400.

## Speed tricks

- **Speculative decoding (DFlash) doubled Glimmer's speed** (31 → 60 tok/s) but made its answers about a
  third longer, and now and then one never finished. Send a large `max_tokens` (32,000) with it.
- **A reasoning budget stops endless thinking.** Bolt sometimes looped on an edge-case checklist until the
  token limit; `reasoning-budget = 24000` made it answer every time (4 of 4, against 1 of 2 without).
- **To measure a draft model's speed, send the identical prompt every run.** Speed depends on how many
  drafted tokens are accepted, which depends on the exact text: a different prompt prefix per run made
  generation speed swing between 56 and 88 tok/s.

## Benchmarking

- **Test the test first.** The first version of the coding prompt did not say "use only the Python standard
  library", and Glimmer answered one task with `numpy`, which the sandbox lacks: three zero scores that
  said nothing about the model. `code_test.py --validate` now runs every task's reference solution and an
  empty answer before any model is scored.
- **One attempt per task is not enough.** Bolt's three attempts at the same task scored 25/25, 0/25 and
  0/25. Ornith 122B, with one attempt per task, scored 117/170 with chat sampling and 159/170 with coding
  sampling, but 122/170 in another run with the same coding sampling: luck moved the score as much as the
  settings did. The hard set uses 3 attempts per task.
- **Separate "cut off" from "wrong".** An answer that hits the token limit scores 0 for a different reason
  than a wrong answer, and the fix is different (a larger limit or a reasoning budget).
- **Thinking is not always better.** On the basic set Qwen3.8 27B scored 67/67 with thinking off and 53/67
  with it on, taking 15 times as long.
- **Check the model card's sampling for the job.** Some cards give separate settings for chat and for code
  (Ornith's: presence penalty 1.5 for chat, 0 for code). A preset can hold only one set; send the other
  per request (`temperature`, `presence_penalty`), and note in the benchmark which one was used.

## Still to write up

Planned for a later version: the system monitor (HUD) that watches this server, TLS certificates for the
local services, firewall ports, and the Docker setup.
