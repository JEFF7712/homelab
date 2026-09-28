# Homelab Foundation Implementation Plan

Status: historical (implemented; retained as a dated implementation record, not active state).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Establish the reproducible repository, local validation environment, and CI contract required before host, firewall, NAS, and cluster work.

**Architecture:** This workstream performs no remote mutation. A Nix flake supplies development and CI tools, Python contract tests protect the repository boundary, and GitLab CI runs the same commands. Follow-on workstreams are OPNsense control plane, the 5070 Standard appliance, NAS services, k3s plus Cilium, and Flux migration.

**Tech Stack:** Nix flakes, nixpkgs, Python unittest, GitLab CI, OpenTofu, SOPS, age, kubeconform, yamllint, gitleaks.

---

## File structure

- .gitignore: exclude local identities, decrypted material, Nix results, and state.
- README.md: state ownership and permitted deployment paths.
- flake/flake.nix: formatter, development shell, and Nix check.
- flake/flake.lock: pin the toolchain.
- tests/test_repository_contract.py: validate repository safety files.
- tests/test_flake_contract.py: validate required flake attributes and tools.
- .gitlab-ci.yml: run the local validation contract.
- docs/superpowers/plans/2026-08-30-homelab-workstream-map.md: record the following independent plans.

### Task 1: Protect the repository boundary

**Files:**
- Create: tests/test_repository_contract.py
- Create: .gitignore
- Create: README.md

- [ ] **Step 1: Write the failing test**

Create tests/test_repository_contract.py:

~~~python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_repository_has_required_documents(self) -> None:
        for relative_path in (
            ".gitignore",
            "README.md",
            "docs/superpowers/specs/2026-08-30-nixos-homelab-rebuild-design.md",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())

    def test_gitignore_excludes_private_material(self) -> None:
        ignored = (ROOT / ".gitignore").read_text()
        for pattern in ("secrets/keys/", "*.decrypted.yaml", ".direnv/", "result"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignored)


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Step 2: Run the test to verify it fails**

Run: python -m unittest tests/test_repository_contract.py -v

Expected: FAIL because .gitignore and README.md do not exist.

- [ ] **Step 3: Implement the minimal repository boundary**

Create .gitignore:

~~~gitignore
.direnv/
result
result-*
secrets/keys/
*.decrypted.yaml
*.decrypted.yml
*.decrypted.json
.env
.envrc
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.terraform/
*.tfstate
*.tfstate.*
~~~

Create README.md:

~~~markdown
# homelab

NixOS homelab desired state.

## Ownership

- flake/ owns NixOS hosts, disks, host networking, k3s installation, and host secrets.
- tofu/ owns API-managed external systems and supported OPNsense resources.
- opnsense-reconciler/ owns OPNsense interface and FRR settings absent from the OpenTofu provider.
- gitops/ owns all Kubernetes objects through Flux.
- secrets/ contains only SOPS-encrypted material.

## Deployment

First installation uses nixos-anywhere. Subsequent NixOS activation uses deploy-rs from the NAS-hosted GitLab runner. OpenTofu applies and OPNsense reconciliation run only in CI. Flux reconciles Kubernetes state from Git.

Do not apply infrastructure from a laptop.
~~~

- [ ] **Step 4: Run the test to verify it passes**

Run: python -m unittest tests/test_repository_contract.py -v

Expected: PASS with two tests.

- [ ] **Step 5: Commit**

~~~bash
git add .gitignore README.md tests/test_repository_contract.py
git commit -m "build: add homelab repository contract"
~~~

### Task 2: Add the reproducible Nix toolchain

**Files:**
- Create: tests/test_flake_contract.py
- Create: flake/flake.nix
- Create: flake/flake.lock

- [ ] **Step 1: Write the failing test**

Create tests/test_flake_contract.py:

~~~python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FlakeContractTests(unittest.TestCase):
    def test_flake_declares_required_tools(self) -> None:
        flake_path = ROOT / "flake/flake.nix"
        self.assertTrue(flake_path.is_file())
        flake = flake_path.read_text()
        for package in (
            "age",
            "sops",
            "opentofu",
            "yamllint",
            "kubeconform",
            "gitleaks",
            "pyright",
            "ruff",
        ):
            with self.subTest(package=package):
                self.assertIn(package, flake)

    def test_flake_exposes_formatter_and_shell(self) -> None:
        flake_path = ROOT / "flake/flake.nix"
        self.assertTrue(flake_path.is_file())
        flake = flake_path.read_text()
        self.assertIn("formatter =", flake)
        self.assertIn("devShells.default =", flake)

    def test_flake_uses_current_nixfmt_attribute(self) -> None:
        flake = (ROOT / "flake/flake.nix").read_text()
        self.assertIn("formatter = pkgs.nixfmt;", flake)

    def test_flake_check_runs_repository_tests(self) -> None:
        flake = (ROOT / "flake/flake.nix").read_text()
        self.assertIn("python -m unittest discover -s tests", flake)


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Step 2: Run the test to verify it fails**

Run: python -m unittest tests/test_flake_contract.py -v

Expected: FAIL because flake/flake.nix does not exist.

- [ ] **Step 3: Implement the Nix flake**

Create flake/flake.nix:

~~~nix
{
  description = "NixOS homelab development and validation tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in {
        formatter = pkgs.nixfmt;

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            age
            git
            gitleaks
            kubeconform
            kubectl
            kubernetes-helm
            nixfmt
            opentofu
            pyright
            python313
            ruff
            sops
            yamllint
          ];
        };

        checks.repository-contract = pkgs.runCommand "repository-contract" {
          nativeBuildInputs = [ pkgs.python313 ];
        } ''
          cd ${../.}
          python -m unittest discover -s tests
          touch $out
        '';
      });
}
~~~

- [ ] **Step 4: Generate the lock file and validate**

Run:

~~~bash
git add flake/flake.nix
nix flake lock ./flake
git add flake/flake.lock
nix develop ./flake -c python -m unittest discover -s tests -v
(cd flake && nix fmt -- flake.nix && nix fmt -- --check flake.nix)
nix flake check ./flake
~~~

Expected: both test modules pass, the formatter makes no remaining changes, and Nix evaluates the check. Nix reads a Git repository through its index, so stage the flake before every Nix command that evaluates it.

- [ ] **Step 5: Commit**

~~~bash
git add flake/flake.nix flake/flake.lock tests/test_flake_contract.py
git commit -m "build: add reproducible homelab toolchain"
~~~

### Task 3: Make GitLab CI enforce the local contract

**Files:**
- Create: .gitlab-ci.yml
- Modify: tests/test_repository_contract.py

- [ ] **Step 1: Extend the failing test**

Add this method inside RepositoryContractTests in tests/test_repository_contract.py:

~~~python
    def test_ci_uses_the_repository_flake(self) -> None:
        pipeline = (ROOT / ".gitlab-ci.yml").read_text()
        self.assertIn("nix develop ./flake", pipeline)
        self.assertIn("nix flake check ./flake", pipeline)
~~~

Also add .gitlab-ci.yml to the test_repository_has_required_documents tuple.

- [ ] **Step 2: Run the test to verify it fails**

Run: nix develop ./flake -c python -m unittest tests/test_repository_contract.py -v

Expected: FAIL because .gitlab-ci.yml does not exist.

- [ ] **Step 3: Implement the pipeline**

Create .gitlab-ci.yml:

~~~yaml
image: nixos/nix:latest

stages:
  - lint
  - test
  - security

variables:
  NIX_CONFIG: "experimental-features = nix-command flakes"

nix_format:
  stage: lint
  script:
    - cd flake && nix fmt -- --check flake.nix

repository_tests:
  stage: test
  script:
    - nix develop ./flake -c python -m unittest discover -s tests -v
    - nix flake check ./flake

yaml_schema:
  stage: test
  script:
    - nix develop ./flake -c yamllint .

secret_scan:
  stage: security
  script:
    - nix develop ./flake -c gitleaks detect --source . --redact
~~~

- [ ] **Step 4: Run the local equivalents**

Run:

~~~bash
(cd flake && nix fmt -- --check flake.nix)
nix develop ./flake -c python -m unittest discover -s tests -v
nix flake check ./flake
nix develop ./flake -c yamllint .
nix develop ./flake -c gitleaks detect --source . --redact
~~~

Expected: every command exits with status 0.

- [ ] **Step 5: Commit**

~~~bash
git add .gitlab-ci.yml tests/test_repository_contract.py
git commit -m "ci: validate homelab repository contract"
~~~

### Task 4: Record independent workstreams

**Files:**
- Create: docs/superpowers/plans/2026-08-30-homelab-workstream-map.md

- [ ] **Step 1: Create the map**

Create docs/superpowers/plans/2026-08-30-homelab-workstream-map.md:

~~~markdown
# Homelab Workstream Map

1. OPNsense control plane: import, provider-backed DHCP/firewall/Unbound state, typed API reconciliation for interface and FRR-global state, read-after-write checks, and encrypted pre-change backups.
2. AdGuard and NetBird appliance: NixOS disko layout, impermanence, SOPS bootstrap, AdGuard data migration, NetBird routing policy, and live DNS and remote-access checks.
3. NAS platform: disk burn-in, mirrored NVMe system pool, ZFS datasets and retention, NFS export, Attic, GitLab runner, and restore verification.
4. k3s and Cilium: three-server NixOS roles, embedded etcd, custom-CNI bootstrap, Cilium BGP control plane v2, LoadBalancer canary, and OPNsense route verification.
5. Flux platform and migration: GitLab bootstrap, SOPS and ESO dependency chain, storage classes, ingress, observability, application migration, and decommission gates.
~~~

- [ ] **Step 2: Check coverage**

Run:

~~~bash
rg -n "OPNsense|AdGuard|NAS|k3s|Flux"   docs/superpowers/specs/2026-08-30-nixos-homelab-rebuild-design.md   docs/superpowers/plans/2026-08-30-homelab-workstream-map.md
~~~

Expected: every major platform component from the architecture specification appears in the workstream map.

- [ ] **Step 3: Commit**

~~~bash
git add docs/superpowers/plans/2026-08-30-homelab-workstream-map.md
git commit -m "docs: map homelab implementation workstreams"
~~~

## Final verification

- [ ] Run:

~~~bash
(cd flake && nix fmt -- --check flake.nix)
nix develop ./flake -c python -m unittest discover -s tests -v
nix flake check ./flake
nix develop ./flake -c yamllint .
nix develop ./flake -c gitleaks detect --source . --redact
git status --short
~~~

Expected: all validations pass and git status --short has no output.
