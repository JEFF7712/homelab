{
  pkgs,
  kernelModulesOverlay,
}:
let
  fixedPkgs = pkgs.extend kernelModulesOverlay;
  depmod = pkgs.writeShellScriptBin "depmod" ''
    set -euo pipefail
    test "$1" = -b
    base="$2"
    version="''${@: -1}"
    printf 'kernel/builtin.ko:\nextra/external.ko:\n' > "$base/lib/modules/$version/modules.dep"
    touch "$base/depmod-ran"
  '';
  original = (pkgs.aggregateModules [ ]).override { kmod = depmod; };
  fixed = (fixedPkgs.aggregateModules [ ]).override { kmod = depmod; };
  script =
    name: aggregation:
    pkgs.writeText name ''
      set -euo pipefail
      # The harness supplies the build tools and output variable. Exercise the
      # real aggregation postBuild without sourcing another derivation's setup.
      source() { :; }
      ${aggregation.postBuild}
    '';
  originalScript = script "original-module-aggregation.sh" original;
  fixedScript = script "fixed-module-aggregation.sh" fixed;
in
pkgs.runCommand "kernel-modules-aggregation-contract" { } ''
  make_input() {
    local base="$1/lib/modules/fixture"
    mkdir -p "$base/kernel"
    printf 'kernel certificate\n' > "$base/modules.cer"
    printf 'kernel/builtin.ko\n' > "$base/modules.order"
    printf 'original dependency map\n' > "$base/modules.dep"
    printf 'builtin module\n' > "$base/kernel/builtin.ko"
  }

  digest_input() {
    find "$1" -type f -print0 | sort -z | xargs -0 sha256sum
  }

  # A writable input models root's permission bypass in unsandboxed CI. Link
  # an ancestor directory, as buildEnv does for a single module source.
  make_input "$TMPDIR/original-input"
  mkdir "$TMPDIR/original-output"
  ln -s "$TMPDIR/original-input/lib" "$TMPDIR/original-output/lib"
  if out="$TMPDIR/original-output" bash ${originalScript} > original.log 2>&1; then
    echo 'expected the pinned upstream guard to fail on a writable linked input' >&2
    exit 1
  fi
  grep -F 'are the same file' original.log

  make_input "$TMPDIR/input"
  digest_input "$TMPDIR/input" > input-before
  mkdir "$TMPDIR/linked-output"
  ln -s "$TMPDIR/input/lib" "$TMPDIR/linked-output/lib"
  # Checking only the last path component for a symlink would miss this case.
  test ! -L "$TMPDIR/linked-output/lib/modules/fixture"
  out="$TMPDIR/linked-output" bash ${fixedScript}
  test ! -e "$TMPDIR/linked-output/depmod-ran"
  digest_input "$TMPDIR/input" > input-after
  diff -u input-before input-after

  # An output-owned merged tree must still regenerate maps and concatenate
  # certificates, replacing the certificate symlink without editing inputs.
  merged="$TMPDIR/merged-output"
  moduleDir="$merged/lib/modules/fixture"
  mkdir -p "$moduleDir/kernel" "$moduleDir/extra" "$TMPDIR/external"
  printf 'external module\n' > "$TMPDIR/external/external.ko"
  printf 'external certificate\n' > "$TMPDIR/external/external.ko.cer"
  ln -s "$TMPDIR/input/lib/modules/fixture/modules.cer" "$moduleDir/modules.cer"
  ln -s "$TMPDIR/input/lib/modules/fixture/modules.order" "$moduleDir/modules.order"
  ln -s "$TMPDIR/input/lib/modules/fixture/kernel/builtin.ko" "$moduleDir/kernel/builtin.ko"
  ln -s "$TMPDIR/external/external.ko" "$moduleDir/extra/external.ko"
  ln -s "$TMPDIR/external/external.ko.cer" "$moduleDir/extra/external.ko.cer"
  out="$merged" bash ${fixedScript}
  test -f "$merged/depmod-ran"
  test ! -L "$moduleDir/modules.cer"
  cat "$TMPDIR/input/lib/modules/fixture/modules.cer" "$TMPDIR/external/external.ko.cer" > expected-certificates
  cmp expected-certificates "$moduleDir/modules.cer"
  digest_input "$TMPDIR/input" > input-after-merge
  diff -u input-before input-after-merge
  grep -Fx 'kernel/builtin.ko:' "$moduleDir/modules.dep"
  grep -Fx 'extra/external.ko:' "$moduleDir/modules.dep"

  mkdir "$TMPDIR/empty-output"
  out="$TMPDIR/empty-output" bash ${fixedScript}
  mkdir -p "$TMPDIR/mixed-output/lib/modules/one" "$TMPDIR/mixed-output/lib/modules/two"
  if out="$TMPDIR/mixed-output" bash ${fixedScript} > mixed.log 2>&1; then
    echo 'expected inconsistent kernel versions to fail' >&2
    exit 1
  fi
  grep -F 'inconsistent kernel versions:' mixed.log

  mkdir "$out"
  cp original.log mixed.log "$out/"
  touch "$out/passed"
''
