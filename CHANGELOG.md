# Changelog

## v0.1 (2026-11-08)

First version.

- README with the build, photos and a results summary.
- `docs/HARDWARE.md`: parts, PCIe layout (both GPUs PCIe 4.0 x16 to the CPU), memory bandwidth, power,
  quirks (9060 XT SMU hang at boot, reversed HIP order).
- `BENCHMARKS.md`: coding hard and basic sets for 15 model configurations, speed and VRAM, DFlash draft
  sizes, R9700 power limit 250/285/300 W, 9060 XT services. Measured 2026-10-23 to 2026-11-03.
- `recipes/`: llama.cpp router presets, start scripts, systemd units, R9700 power-cap service.
- `bench/code_test.py`: the coding benchmark.
- `scripts/report.sh`: rig report for AMD GPU machines.

Not yet: BIOS settings, Qwen3.5 9B speed on the 9060 XT, results from other machines.
