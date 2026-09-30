# Single source of truth for the zot binary.
#
# The registry serves this exact build, and the garbage-collection fixture
# asserts the version it runs, so both import this file rather than repeating
# the URL and hash. Duplicating them would let the fixture quietly test a
# different binary than the one holding the images.
{ pkgs, lib }:
let
  release = {
    version = "2.1.20";
    hashes = {
      x86_64-linux = "sha256-oy5C0ELR8XtbExflXMGkFadEyHPc0FwlxWtmVHgli8s=";
      aarch64-linux = "sha256-1qOUdVh74Y7D1C4NK/pQ9cUGTLvNwiLb2I5V7Pad2Ok=";
    };
  };

  zotPlatform =
    if pkgs.stdenv.hostPlatform.isx86_64 then
      "amd64"
    else if pkgs.stdenv.hostPlatform.isAarch64 then
      "arm64"
    else
      null;
in
rec {
  inherit release;

  version = release.version;

  # False where no upstream release binary is published.
  supported = zotPlatform != null;

  zotPackage = pkgs.stdenvNoCC.mkDerivation {
    pname = "zot";
    inherit (release) version;
    nativeBuildInputs = [ pkgs.patchelf ];
    src = pkgs.fetchurl {
      url = "https://github.com/project-zot/zot/releases/download/v${release.version}/zot-linux-${zotPlatform}";
      hash = release.hashes.${pkgs.stdenv.hostPlatform.system};
    };
    dontUnpack = true;
    installPhase = ''
      runHook preInstall
      install -Dm755 "$src" "$out/bin/zot"
      patchelf --set-interpreter ${pkgs.stdenv.cc.bintools.dynamicLinker} "$out/bin/zot"
      runHook postInstall
    '';
    meta = {
      description = "OCI-native container registry";
      homepage = "https://zotregistry.dev/";
      license = lib.licenses.asl20;
      mainProgram = "zot";
      platforms = builtins.attrNames release.hashes;
      sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    };
  };
}
