# Recipes

How the models are served on this build: one llama.cpp **router** on the R9700 holding the big models
(one loaded at a time), and three small always-on servers on the RX 9060 XT.

| Port | Card (HIP index) | Script | What |
|---|---|---|---|
| 9700 | R9700 (1) | [`start-router.sh`](start-router.sh) + [`router.ini`](router.ini) | router: chat, coding, agents, vision; swaps models on demand |
| 9701 | 9060 XT (0) | [`start-quick.sh`](start-quick.sh) | Qwen3.5 9B, fast small chat |
| 9702 | 9060 XT (0) | [`start-embed.sh`](start-embed.sh) | Qwen3-Embedding-4B, `/v1/embeddings` |
| 9703 | 9060 XT (0) | [`start-rerank.sh`](start-rerank.sh) | Qwen3-Reranker-0.6B, `/v1/rerank` |

All four speak the OpenAI-compatible API and require an API key.

## Setup

The scripts expect this layout (change the paths at the top of each script and in `router.ini` to suit):

```
~/locai/
  llama/
    bin/            llama.cpp ROCm build (llama-server and its bundled libraries)
    api-key.txt     one line, the API key clients must send (chmod 600)
    start-*.sh      from this folder
  models/
    router.ini      from this folder
    <model>/...gguf
```

1. **llama.cpp:** download a `ubuntu-rocm-gfx120X-x64` release from
   [lemonade-sdk/llamacpp-rocm](https://github.com/lemonade-sdk/llamacpp-rocm/releases) (b1359 or newer)
   and unzip it into `~/locai/llama/bin/`. It bundles its own ROCm libraries; the scripts set
   `LD_LIBRARY_PATH` to that folder.
2. **API key:** `openssl rand -hex 32 > ~/locai/llama/api-key.txt && chmod 600 ~/locai/llama/api-key.txt`
3. **Models:** download the GGUF files named in `router.ini` and the start scripts (Unsloth's `UD-Q4_K_XL`
   quants from Hugging Face for most of them).
4. **Check the GPU indexes** before anything else; on this board HIP index 0 is the 9060 XT and 1 is the
   R9700 (see [docs/HARDWARE.md](../docs/HARDWARE.md#pcie-layout)). Change `ROUTER_GPU` and
   `HIP_VISIBLE_DEVICES` if yours differ.
5. Run a script by hand first, then install the services.

## Services

Unit files are in [`systemd/`](systemd/); edit `User=` and the paths, then:

```bash
sudo cp systemd/locai-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now locai-router locai-quick locai-embed locai-rerank
```

## Router notes

- `--models-max 1`: only one model is loaded on the R9700; asking for another unloads the current one
  (a swap takes from a few seconds to about 30 s for the biggest files).
- **Don't poll a model by name** (`/slots?model=X`): the router loads X to answer.
- Per-model settings (context, sampling, draft model, reasoning budget) live in `router.ini`; the comments
  there say why each value was chosen.
- Request settings override `router.ini`: for example send `max_tokens: 32000` to reasoning models on hard
  problems, and `chat_template_kwargs: {"enable_thinking": false}` to turn thinking off where supported.

## R9700 power limit

[`power-cap/`](power-cap/) sets every R9700 to a fixed limit at boot (the card forgets it at every reboot):

```bash
sudo install -o root -g root -m 0755 power-cap/locai-gpu-powercap /usr/local/sbin/
sudo install -o root -g root -m 0644 power-cap/locai-gpu-powercap@.service /etc/systemd/system/
sudo systemctl enable --now locai-gpu-powercap@250.service
```

Start another instance (`sudo systemctl start locai-gpu-powercap@300`) to change it until the next reboot.
The limit is clamped to the card's own range (210-300 W).
