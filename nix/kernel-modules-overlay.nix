# aggregateModules may link its entire tree to an input when there are no
# external modules. Root builds consider that read-only store input writable,
# so the upstream -w guard can run depmod and certificate merging in the input.
# Only a directory resolved inside this derivation's output may be rewritten.
_final: prev: {
  aggregateModules =
    modules:
    (prev.aggregateModules modules).override {
      buildEnv =
        args:
        prev.buildEnv (
          args
          // {
            postBuild =
              let
                oldGuard = "if test -w $out/lib/modules/$kernelVersion; then";
                ownedGuard = ''if [[ $(readlink -f "$out/lib/modules/$kernelVersion") == "$out/"* ]]; then'';
              in
              assert prev.lib.assertMsg (prev.lib.hasInfix oldGuard args.postBuild)
                "Nixpkgs aggregateModules changed; review the output-ownership workaround";
              prev.lib.replaceStrings [ oldGuard ] [ ownedGuard ] args.postBuild;
          }
        );
    };
}
