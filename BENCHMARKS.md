# Benchmarks

Every number measured on this one machine: Huananzhi H12D-8D, EPYC 7402, 128 GB DDR4-2400 (8 channels),
Radeon AI PRO R9700 32 GB, Ubuntu 26.04, kernel 7.0, llama.cpp ROCm builds from
[lemonade-sdk/llamacpp-rocm](https://github.com/lemonade-sdk/llamacpp-rocm) (gfx120X).
All router models ran on the **R9700**, one at a time. Results from October 23 to November 3, 2026.

- [Coding: hard set](#coding-hard-set): how often each model writes correct code, and how fast
- [Coding: basic set](#coding-basic-set)
- [Speed and VRAM](#speed-and-vram)
- [Speculative decoding (DFlash)](#speculative-decoding-dflash)
- [R9700 power limit](#r9700-power-limit)
- [RX 9060 XT services](#rx-9060-xt-services)

## How the coding tests work

[`bench/code_test.py`](bench/code_test.py) gives every model the same Python tasks, takes the code out of
its reply and runs it against hidden checks in a sandbox (no network, 1 GB memory, time limits). Stdlib only.

- **Hard set:** 7 tasks (`eval_expr`, `parse_csv_line`, `semver_compare`, `keys_maze`, `lfu_cache`,
  `diff_lines`, `fix_spiral`), **3 attempts each** = 21 answers, **309 checks**.
- **Basic set:** 6 easier tasks, 1 attempt each, 67 checks.
- **Checks** = checks passed out of all checks. **Solved** = answers that passed every check.
  **Cut off** = answers that hit the token limit before finishing (they score what they got, usually 0).
  An answer with no code, or code that fails to run (syntax or import error), scores 0.
- Sampling is each model's own (from [`recipes/router.ini`](recipes/router.ini)) unless noted.
- "Thinking off" means the request asked the model not to reason first; models that always reason
  (Glimmer) reason anyway.

```bash
python3 bench/code_test.py --set hard --repeat 3 --models code-flint --thinking --max-tokens 32000
```

## Coding: hard set

7 tasks × 3 attempts, 309 checks. Sorted by checks passed.

| Model | Size / quant | Thinking | Checks | Solved | Cut off | Time | Gen tok/s | Date | llama.cpp |
|---|---|---|---|---|---|---|---|---|---|
| Meta Muse-Glimmer 30B ⁴ | dense, UD-Q4_K_XL | always | **302/309 (97.7%)** | 19/21 | 0 | 4,146 s | 31.7 | 10-23 | b1350 |
| Recursive Flint 40B-A2.5B | MoE, UD-Q4_K_XL | on | **299/309 (96.8%)** | 19/21 | 0 | 1,828 s | 73.7 | 10-31 | b1359 |
| Meta Muse-Glimmer 30B | dense, UD-Q6_K_XL | always | 299/309 (96.8%) | 16/21 | 0 | 6,336 s | 21.4 | 10-27 | b1350 |
| Qwen3.6 27B | dense, UD-Q6_K_XL | off | 283/309 (91.6%) | 15/21 | 0 | 959 s | 20.8 | 10-27 | b1350 |
| Qwen3.6 27B | dense, UD-Q4_K_XL | off | 278/309 (90.0%) | 14/21 | 0 | 747 s | 27.8 | 10-23 | b1350 |
| Nemotron 3.5 Super VL 120B-A12B ¹ ³ | MoE, experts in RAM | off | 276/309 (89.3%) | 13/21 | 0 | 2,275 s | 6.0 | 10-27 | b1350 |
| Qwen3.8 27B | dense, UD-Q6_K_XL | off | 267/309 (86.4%) | 12/21 | 0 | 705 s | 21.1 | 10-27 | b1350 |
| Muse-Glimmer 30B + DFlash 15 ² | dense, UD-Q4_K_XL | always | 264/309 (85.4%) | 16/21 | 2 | 2,604 s | 62.3 | 10-30 | b1350 |
| Nemotron 3.5 Bolt 74B-A9B "24GB" + DFlash 6 | MoE, NVIDIA 2-bit | on | 259/309 (83.8%) | 19/21 | 2 | 2,461 s | 100.2 | 11-01 | b1359 |
| Qwen3.8 27B ³ | dense, UD-Q4_K_XL | off | 256/309 (82.8%) | 15/21 | 0 | 646 s | 27.9 | 10-23 | b1350 |
| Poolside Laguna XS 2.1 33B-A3B | MoE, Q4_K_M | off | 242/309 (78.3%) | 6/21 | 2 | 290 s | 99.6 | 10-27 | b1350 |
| Poolside Laguna S 2.1 118B-A8B ¹ ³ | MoE, experts in RAM | off | 241/309 (78.0%) | 10/21 | 1 | 1,600 s | 13.2 | 10-27 | b1350 |
| Nemotron 3.5 Bolt 74B-A9B ¹ | MoE, UD-Q4_K_XL, experts in RAM | off | 233/309 (75.4%) | 10/21 | 0 | 823 s | 19.1 | 10-27 | b1350 |
| Ornith 1.5 122B ¹ (temp 0.2) | MoE, experts in RAM | off | 204/309 (66.0%) | 6/21 | 0 | 970 s | 11.5 | 10-27 | b1350 |
| Nemotron 3.5 Lightning 30B-A3B ³ | MoE, UD-Q4_K_XL | off | 178/309 (57.6%) | 5/21 | 0 | 179 s | 107.2 | 10-27 | b1350 |

¹ Too big for 32 GB: expert weights partly in system RAM (`n-cpu-moe`), so generation speed is limited by
the 8-channel DDR4-2400 (about 154 GB/s in theory).

² Same model as the top row with a DFlash draft, answers limited to 16,000 tokens. Answers ran about a
third longer with the draft, and two were cut off. A 32,000-token limit helps but does not fix it; see
[DFlash](#speculative-decoding-dflash).

³ Includes answers scored 0 because the code did not run (syntax or import error) or the reply had no code:
Qwen3.8 1, Nemotron Super 1, Laguna S 1, Lightning 2.

⁴ On October 23 the prompt did not yet say "use only the Python standard library", and all three of Glimmer's
`diff_lines` answers imported `numpy`, which the sandbox lacks. That line was added to the prompt and those three
answers re-run (22/27); the other 18 are from the first run. Without the re-run: 280/309. Every run from
October 27 on used the new prompt; the Qwen 27B rows of October 23 did not, but they did not use `numpy`.

**What it shows**

- **Flint scores like Glimmer in less than half the time.** Same 19/21 solved; 1,828 s against about 4,150 s.
- **6-bit quants gave no clear gain** over 4-bit for Glimmer, Qwen3.6 and Qwen3.8, at 25-33% lower speed.
- **Bolt "24GB" solves 19/21 at 100 tok/s** but sometimes loops on an edge-case checklist until the token
  limit (`eval_expr`). A reasoning budget fixes it: with `reasoning-budget 24000`, 4 of 4 `eval_expr`
  attempts finished (95/100 checks), against 1 of 2 with a 64,000-token limit and no budget.
- **Models with experts in RAM** work (120B on a 32 GB card) but run at 6-19 tok/s.

## Coding: basic set

6 tasks, 1 attempt each, 67 checks. 2026-10-23, llama.cpp b1350.

| Model | Thinking off | Time | Thinking on | Time |
|---|---|---|---|---|
| Meta Muse-Glimmer 30B | 67/67 | 404 s | 67/67 | 431 s |
| Qwen3.6 27B | 67/67 | 96 s | 67/67 | 874 s |
| Qwen3.8 27B | 67/67 | 84 s | 53/67 (1 cut off) | 1,292 s |
| Qwen3.6 35B-A3B | 64/67 | 44 s | 67/67 | 595 s |
| Devstral Small 2 24B | 63/67 | 47 s | 65/67 | 48 s |

The basic set is too easy to separate the good models; the hard set does.

## Speed and VRAM

Single request on the R9700: load checks when each model was added, or the average over a coding run.
"Prompt" is prompt processing. Empty cells were not measured.

| Model | Quant | Context | VRAM used | Gen tok/s | Prompt tok/s |
|---|---|---|---|---|---|
| Nemotron 3.5 Lightning 30B-A3B | UD-Q4_K_XL | 49,152 | 24.2 GB | 110 | 3,600 |
| Poolside Laguna XS 2.1 33B-A3B | Q4_K_M | 49,152 | 22.1 GB | 105 | 2,800 |
| Nemotron 3.5 Bolt 74B-A9B "24GB" + DFlash | NVIDIA 2-bit | 131,072 | 30.1 GB | 74 prose / 122 code | |
| Recursive Flint 40B-A2.5B | UD-Q4_K_XL | 65,536 | 26.2 GB | 74 | |
| Muse-Glimmer 30B + DFlash 15 (300 W) | UD-Q4_K_XL | 49,152 | | 67.6 | 1,044 (9.7K-token prompt) |
| Qwen3.6 35B-A3B | UD-Q4_K_XL | 49,152 | | 65 | |
| Muse-Glimmer 30B, no draft | UD-Q4_K_XL | 49,152 | | 31.6 | |
| Qwen3.6 / Qwen3.8 27B | UD-Q4_K_XL | 49,152 | | 28 | |
| Nemotron Super 49B v1.5 | UD-Q3_K_XL, KV q8_0 | 32,768 | 27.1 GB | 21.7 | |
| Muse-Glimmer 30B | UD-Q6_K_XL | 49,152 | 25.3 GB | 21.7 | |
| Qwen3.6 27B / Qwen3.8 27B | UD-Q6_K_XL | 49,152 | 27.6 / 27.1 GB | 21.2 / 21.6 | |
| Nemotron 3.5 Bolt 74B-A9B ¹ | UD-Q4_K_XL, n-cpu-moe 52 | 49,152 | 29.3 GB | 19.4 | 538 |
| Poolside Laguna S 2.1 118B-A8B ¹ | UD-Q4_K_XL, n-cpu-moe 32 | 49,152 | 30.5 GB | 13.4 | 372 |
| Ornith 1.5 122B ¹ | UD-Q4_K_XL, n-cpu-moe 33 | 49,152 | | 11.3 | 293 |
| Nemotron 3.5 Super VL 120B-A12B ¹ | n-cpu-moe 72 | 49,152 | 27.5 GB | 6.1 | 234 (39K-token prompt) |

¹ Experts partly in system RAM.

## Speculative decoding (DFlash)

Muse-Glimmer 30B with Meta's 1.6 GB DFlash drafter, tasks `eval_expr` + `lfu_cache`, 2026-10-30:

| Draft tokens per step | Checks | Time | Gen tok/s |
|---|---|---|---|
| none | 34/34 | 676 s | 31.2 |
| 3 (default) | 34/34 | 360 s | 47.8 |
| 8 | 33/34 | 277 s | 58.8 |
| 15 | 34/34 | 247 s | **59.7** |

About **1.9x faster** at 15. But answers with the draft ran about a third longer, and some never finish.
With a 32,000-token limit (`diff_lines` + `eval_expr`, 3 attempts each, 2026-10-30):

| | Checks | Cut off | Time | Gen tok/s |
|---|---|---|---|---|
| no draft | 100/102 | 0 of 6 | 1,892 s | 31.5 |
| DFlash 15 | 91/102 | 1 of 6 | 1,584 s | 59.7 |

So the draft doubles the speed but costs some reliability on long reasoning; this build keeps it on for
everyday use and sends a large `max_tokens`.

**Measuring tip:** with a draft model, generation speed depends on how many drafted tokens are accepted,
which depends on the exact text. Giving each run a different prompt prefix made generation speed swing
between 56 and 88 tok/s. Send the identical prompt each run (`cache_prompt: false`) and compare draft acceptance.

## R9700 power limit

Same 9,691-token prompt and 512-token answer, Muse-Glimmer 30B + DFlash, greedy, 3 runs per limit,
2026-11-03:

| Limit | Prompt tok/s | Gen tok/s | Avg power (prompt / gen) | Peak junction |
|---|---|---|---|---|
| 300 W (default) | 1,044 | 67.6 | 245 / 275 W | 91 °C |
| 285 W | 1,036 (−0.8%) | 67.6 | 231 / 277 W | 89 °C |
| 250 W | 1,007 (−3.5%) | 66.9 (−1.0%) | 207 / 248 W | 85 °C |

**250 W costs about 3.5% prompt speed and 1% generation speed, for 10-15% less power and a 6 °C lower peak junction temperature.**
This build runs at 250 W ([`recipes/power-cap/`](recipes/power-cap/)). The card accepts 210-300 W.
Dense models that keep the GPU busier may lose more; measure your own.

## RX 9060 XT services

The 16 GB card runs the small always-on services, measured 2026-10-27:

| Service | Model | VRAM | Result |
|---|---|---|---|
| Embeddings (port 9702) | Qwen3-Embedding-4B Q8_0 | about 5.2 GB | 32 chunks of ~380 tokens in 3.1 s, 2,560-dim vectors |
| Reranker (port 9703) | Qwen3-Reranker-0.6B Q8_0 | about 1.4 GB | 30 documents reranked in 0.8 s |
| Quick chat (port 9701) | Qwen3.5 9B UD-Q4_K_XL | | not benchmarked yet |
