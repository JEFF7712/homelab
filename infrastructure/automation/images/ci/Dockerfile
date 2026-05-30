FROM nixos/nix:2.34.7

# Image runs as root: nixos/nix ships /nix/store owned by root and switching
# to a non-root USER would require chowning the store and reworking the
# multi-user daemon assumption. DS-0002 is suppressed for this path in
# .trivyignore.yaml.

# Enable flakes and the new nix command.
# filter-syscalls=false is required for Nix to run inside kaniko's user-namespace container.
# substituters: cache.nixos.org for free pkgs, homelab Attic for unfree/rebuilt closures (terraform).
RUN echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf \
 && echo "filter-syscalls = false" >> /etc/nix/nix.conf \
 && echo "substituters = https://cache.nixos.org/ http://10.0.20.190:8080/homelab" >> /etc/nix/nix.conf \
 && echo "trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= homelab:s17u8G3szjlQ6UmMAPsszVS/J1jaw6gDwSDM9+/QeNQ=" >> /etc/nix/nix.conf

# Copy only the files that affect what gets installed.
WORKDIR /etc/ci-flake
COPY flake.nix flake.lock ./

# Pre-build the CI devShell so its closure lives in /nix/store and `nix develop`
# at job runtime is instant (no eval, no network). Capture the store path
# of the dev shell to /etc/ci-flake/closure-root so push_ci_closure_to_attic
# knows exactly what to upload (NOT system gc-roots, which include
# unrelated stuff like base-system).
RUN nix build --no-link --print-out-paths '.#devShells.x86_64-linux.ci' > /etc/ci-flake/closure-root

# Sanity check: drop into the shell and run trivial command. Fails the build
# early if any package in ciPackages is broken on the pinned nixpkgs.
RUN nix develop '.#ci' --command true

WORKDIR /work

# Informational: GitLab's Kubernetes executor overrides ENTRYPOINT on the build
# container, so jobs explicitly prefix scripts with `nix develop /etc/ci-flake#ci -c …`.
ENTRYPOINT ["nix", "develop", "/etc/ci-flake#ci", "--command"]
CMD ["bash"]




