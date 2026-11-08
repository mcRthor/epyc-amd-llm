#!/usr/bin/env bash
# report.sh - print a Markdown summary of an AMD GPU LLM machine: board, BIOS, CPU, memory, GPUs (with the
# real PCIe link to the CPU), storage and software. Run as a normal user; run with sudo to add the DIMM
# details (dmidecode needs root). Prints no serial numbers, UUIDs, MAC addresses or host names.
#
#   bash scripts/report.sh > my-rig.md
#   LLAMA_BIN=~/locai/llama/bin bash scripts/report.sh     # also report the llama.cpp build
set -u
rd() { cat "$1" 2>/dev/null | tr -d '\n'; }
dmi() { rd "/sys/class/dmi/id/$1"; }
# summarise `dmidecode -t memory` (stdin): sizes, part numbers, speeds and slots, never serial numbers
dimms() {
    awk -F': ' '
        /^Memory Device/ {d=1; size=""; loc=""; type=""; speed=""; cfg=""; man=""; part=""; rank=""; next}
        d && /^\tSize:/ {size=$2} d && /^\tLocator:/ {loc=$2} d && /^\tType:/ {type=$2}
        d && /^\tSpeed:/ {speed=$2} d && /^\tConfigured Memory Speed:/ {cfg=$2}
        d && /^\tManufacturer:/ {man=$2} d && /^\tPart Number:/ {gsub(/ +$/,"",$2); part=$2} d && /^\tRank:/ {rank=$2}
        d && /^$/ {d=0; if (size !~ /No Module/ && size != "") { n++; key=size" "type" "man" "part" rank "rank" @ "cfg; c[key]++; slots=slots" "loc } }
        END {for (k in c) printf "- DIMMs: %d x %s\n", c[k], k; printf "- Populated slots (%d):%s\n", n, slots}'
}

echo "## System"
. /etc/os-release 2>/dev/null
echo "- OS: ${PRETTY_NAME:-unknown}"
echo "- Kernel: $(uname -r)"
echo "- Board: $(dmi board_vendor) $(dmi board_name) (rev $(dmi board_version))"
echo "- BIOS: $(dmi bios_vendor) $(dmi bios_version) ($(dmi bios_date))"

echo
echo "## CPU and memory"
echo "- CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')"
echo "- Cores / threads: $(lscpu -p=CORE 2>/dev/null | grep -v '^#' | sort -u | wc -l) / $(nproc --all)"
echo "- NUMA nodes: $(lscpu 2>/dev/null | awk -F: '/^NUMA node\(s\)/{gsub(/ /,"",$2);print $2}')"
echo "- RAM: $(free -g | awk '/^Mem:/{print $2}') GiB"
if [ "$(id -u)" = 0 ] && command -v dmidecode >/dev/null; then
    dmidecode -t memory | dimms
else
    echo "- DIMMs: run with sudo to list them"
fi

echo
echo "## GPUs"
echo
echo "| GPU | PCI | VRAM | VBIOS | Power limit | Link to CPU |"
echo "|---|---|---|---|---|---|"
for dev in /sys/bus/pci/devices/*; do
    [ "$(rd "$dev/vendor")" = 0x1002 ] || continue
    [ -e "$dev/mem_info_vram_total" ] || continue
    bdf=$(basename "$dev")
    name=$(lspci -s "$bdf" 2>/dev/null | sed 's/^[^:]*: *[^:]*: *//; s/^Advanced Micro Devices, Inc. \[AMD\/ATI\] *//')
    vram=$(( ($(rd "$dev/mem_info_vram_total") + 536870912) / 1073741824 ))
    cap=$(rd "$(ls "$dev"/hwmon/hwmon*/power1_cap 2>/dev/null | head -n1)")
    capmax=$(rd "$(ls "$dev"/hwmon/hwmon*/power1_cap_max 2>/dev/null | head -n1)")
    # walk up to the CPU root port: the GPU's own link is often its internal bridge, not the real slot link
    p=$(readlink -f "$dev"); link=""
    while [ -e "$p/current_link_speed" ]; do link="$(rd "$p/current_link_speed" | sed 's/ PCIe//') x$(rd "$p/current_link_width") (port $(basename "$p"))"; p=$(dirname "$p"); done
    echo "| ${name:-AMD GPU} | \`$bdf\` | ${vram} GB | $(rd "$dev/vbios_version") | ${cap:+$((cap/1000000)) W}${capmax:+ (max $((capmax/1000000)) W)} | $link |"
done
echo
echo "HIP device order: check with \`HIP_VISIBLE_DEVICES\` and a short load; it may differ from the PCI order above."

echo
echo "## Storage"
for n in /sys/class/nvme/nvme*; do
    [ -e "$n/model" ] || continue
    echo "- $(rd "$n/model" | sed 's/ *$//') (firmware $(rd "$n/firmware_rev" | sed 's/ *$//')): $(rd "$n/device/current_link_speed" | sed 's/ PCIe//') x$(rd "$n/device/current_link_width")"
done

echo
echo "## Software"
if command -v amd-smi >/dev/null; then
    echo "- $(amd-smi version 2>/dev/null | head -n1 | cut -d'|' -f1-3 | sed 's/ *$//')"
fi
[ -r /opt/rocm/.info/version ] && echo "- /opt/rocm: $(rd /opt/rocm/.info/version)"
v=$(modinfo -F version amdgpu 2>/dev/null); echo "- amdgpu: ${v:-in-tree driver} (kernel $(uname -r))"
if [ -n "${LLAMA_BIN:-}" ] && [ -x "$LLAMA_BIN/llama-server" ]; then
    echo "- llama.cpp: $(LD_LIBRARY_PATH="$LLAMA_BIN" "$LLAMA_BIN/llama-server" --version 2>&1 | grep -m1 -i version)"
fi
