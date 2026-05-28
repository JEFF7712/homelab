FROM nixos/nix:2.31.2

# Enable flakes and the new nix command.
# filter-syscalls=false is required for Nix to run inside kaniko's user-namespace container.
RUN echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf \
 && echo "filter-syscalls = false" >> /etc/nix/nix.conf

# Copy only the files that affect what gets installed.
WORKDIR /etc/ci-flake
COPY flake.nix flake.lock ./

# Pre-build the CI devShell so its closure lives in /nix/store and `nix develop`
# at job runtime is instant (no eval, no network).
RUN nix build --no-link '.#devShells.x86_64-linux.ci' \
 && nix-store --gc --print-roots > /etc/ci-flake/gc-roots

# Sanity check: drop into the shell and run trivial command. Fails the build
# early if any package in ciPackages is broken on the pinned nixpkgs.
RUN nix develop '.#ci' --command true

WORKDIR /work

# Informational: GitLab's Kubernetes executor overrides ENTRYPOINT on the build
# container, so jobs explicitly prefix scripts with `nix develop /etc/ci-flake#ci -c …`.
ENTRYPOINT ["nix", "develop", "/etc/ci-flake#ci", "--command"]
CMD ["bash"]
