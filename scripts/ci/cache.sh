#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
flake=${1:-./flake}
target_set=${2:-homelab}

cache_work=$(mktemp -d "${TMPDIR:-/tmp}/attic-ci-XXXXXX")
trap 'rm -rf "$cache_work"' EXIT
export XDG_CONFIG_HOME="$cache_work/config"
mkdir -p "$XDG_CONFIG_HOME"

if [[ "$target_set" == "homelab" ]]; then
  build_targets=(
    "$flake#nixosConfigurations.nas-01.config.system.build.toplevel"
    "$flake#nixosConfigurations.adguard-netbird-01.config.system.build.toplevel"
    "$flake#nixosConfigurations.homelab-01.config.system.build.toplevel"
    "$flake#nixosConfigurations.homelab-02.config.system.build.toplevel"
    "$flake#nixosConfigurations.homelab-03.config.system.build.toplevel"
    "$flake#nixosConfigurations.homelab-04.config.system.build.toplevel"
    "$flake#nixosConfigurations.homelab-05.config.system.build.toplevel"
    "$flake#checks.x86_64-linux.repository-contract"
    "$flake#checks.x86_64-linux.agent-workspace-network"
    "$flake#checks.x86_64-linux.agent-workspace-packet-flow"
    "$flake#checks.x86_64-linux.agent-workspace-host-reservations"
    "$flake#checks.x86_64-linux.agent-workspace-libvirt-normalization"
    "$flake#checks.x86_64-linux.zot-registry"
    "$flake#checks.x86_64-linux.zot-registry-service"
    "$flake#checks.x86_64-linux.opnsense-types"
    "$flake#checks.x86_64-linux.opnsense-config"
    "$flake#checks.x86_64-linux.fleet-inventory"
    "$flake#checks.x86_64-linux.kiosk-tty1"
    "$flake#checks.x86_64-linux.topology"
    "$flake#devShells.x86_64-linux.default"
    "$flake#devShells.x86_64-linux.ci"
    "$flake#devShells.x86_64-linux.ops"
  )
elif [[ "$target_set" == "nixos-laptop" ]]; then
  build_targets=(
    "$flake#nixosConfigurations.laptop.config.system.build.toplevel"
  )
elif [[ "$target_set" == "nixos-iso" ]]; then
  build_targets=(
    "$flake#nixosConfigurations.iso.config.system.build.isoImage"
  )
else
  echo "Unknown target set: $target_set" >&2
  exit 2
fi
builder=${HOMELAB_CACHE_BUILDER:-native}
started=$SECONDS
case "$builder" in
  fast)
    selection='flake: {'
    for index in "${!build_targets[@]}"; do
      selection+=" target${index} = flake.${build_targets[index]#*#};"
    done
    selection+=' }'
    nix-fast-build --flake "$flake#" --select "$selection" \
      --eval-workers 2 --eval-max-memory-size 2048 --max-jobs 2 \
      --no-nom --out-link "$cache_work/result" \
      --result-file "$cache_work/results.json"
    python -m scripts.ci.cache_results "$cache_work/results.json" \
      "${#build_targets[@]}" "$cache_work/paths" "$cache_work/derivations"
    ;;
  native)
    nix build --no-link --print-out-paths "${build_targets[@]}" > "$cache_work/paths"
    ;;
  *)
    echo "Unknown cache builder: $builder (expected fast or native)" >&2
    exit 2
    ;;
esac
echo "Cache build: builder=$builder targets=${#build_targets[@]} elapsed_seconds=$((SECONDS - started))"

if [[ -z "${ATTIC_TOKEN:-}" ]]; then
  echo "Build succeeded; ATTIC_TOKEN is not set, so upload is skipped."
  exit 0
fi
nix flake archive --json --no-write-lock-file "$flake" > "$cache_work/archive.json"
python -m scripts.ci.cache_sources "$cache_work/archive.json" >> "$cache_work/paths"
if [[ "$builder" == native ]]; then
  nix path-info --derivation "${build_targets[@]}" > "$cache_work/derivations"
fi
mapfile -t build_derivations < "$cache_work/derivations"
nix-store --query --requisites --include-outputs "${build_derivations[@]}" >> "$cache_work/paths"
attic login local http://10.0.30.20:8080/ "$ATTIC_TOKEN"
awk '!seen[$0]++' "$cache_work/paths" > "$cache_work/unique"

# One path Attic rejects (it has refused single NARs with "Bad NAR Hash or
# Size") aborts a whole push, so push in batches and retry a failed batch one
# path at a time. Skipped paths are reported; only a push where nothing at all
# succeeds fails the job.
push() {
  attic push local:homelab --ignore-upstream-cache-filter --jobs 8 --stdin
}
split -l 200 "$cache_work/unique" "$cache_work/batch."
pushed=0
skipped=()
for batch in "$cache_work"/batch.*; do
  if push < "$batch"; then
    pushed=$((pushed + 1))
    continue
  fi
  while read -r path; do
    if push <<< "$path"; then
      pushed=$((pushed + 1))
    else
      skipped+=("$path")
    fi
  done < "$batch"
done
if ((${#skipped[@]})); then
  printf 'Attic rejected %d path(s); they stay uncached:\n' "${#skipped[@]}" >&2
  printf '  %s\n' "${skipped[@]}" >&2
  if ((pushed == 0)); then
    exit 1
  fi
fi
