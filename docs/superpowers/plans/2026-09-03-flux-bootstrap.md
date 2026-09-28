# Flux Bootstrap Implementation Plan

Status: historical (implemented; retained as a dated implementation record, not active state).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap Flux from `gitops/clusters/homelab-01` and prove ESO syncs one GitLab variable, stopping before storage classes.

**Architecture:** `flux bootstrap gitlab` with a one-time project token creates the read-only deploy credential; four ordered Kustomization layers (platform, secrets, eso, apps-stub) reconcile namespaces, pinned ESO, one SOPS-encrypted GitLab token, and a canary ExternalSecret.

**Tech Stack:** fluxcd 2.9, ESO Helm chart (pinned), SOPS + age, GitLab project tokens, k3s 1.35.

---

### Task 1: Contract-test the Flux repository shape

**Files:**
- Create: `tests/test_flux_bootstrap_contract.py`

- [ ] **Step 1: Write the failing contract test**

```python
class FluxBootstrapContractTests(unittest.TestCase):
    def test_flux_system_bootstrap_files_exist(self) -> None:
        for relative_path in (
            "gitops/clusters/homelab-01/flux-system/gotk-components.yaml",
            "gitops/clusters/homelab-01/flux-system/gotk-sync.yaml",
            "gitops/clusters/homelab-01/flux-system/kustomization.yaml",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())

    def test_platform_layer_declares_pinned_eso(self) -> None:
        release = (ROOT / "gitops/platform/external-secrets/release.yaml").read_text()
        self.assertIn("kind: HelmRelease", release)
        self.assertIn("chart: external-secrets", release)
        self.assertIn("version: ", release)

    def test_layers_chain_with_depends_on(self) -> None:
        eso = (ROOT / "gitops/eso/kustomization.yaml").read_text()
        self.assertIn("dependsOn", eso)
        self.assertIn("name: secrets", eso)

    def test_sops_policy_names_single_age_recipient(self) -> None:
        policy = (ROOT / ".sops.yaml").read_text()
        self.assertIn("age:", policy)
        self.assertNotIn("TODO", policy)

    def test_no_plaintext_tokens_in_gitops(self) -> None:
        for path in (ROOT / "gitops").rglob("*.yaml"):
            with self.subTest(path=str(path)):
                self.assertNotRegex(path.read_text(), r"glpat-[A-Za-z0-9_-]{20,}")
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m unittest tests.test_flux_bootstrap_contract -v`

Expected: failures for every missing path.

- [ ] **Step 3: Commit the failing contract**

```bash
git add tests/test_flux_bootstrap_contract.py
git commit -m "test: define Flux bootstrap contract"
```

### Task 2: Bootstrap Flux controllers

**Files:**
- Generated: `gitops/clusters/homelab-01/flux-system/`

- [ ] **Step 1: Run the bootstrap from the repo root**

```bash
export GITLAB_TOKEN="$(glab api --method POST projects/85910419/access_tokens --header 'Content-Type: application/json' --input token-request.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
nix run nixpkgs#fluxcd -- bootstrap gitlab \
  --owner=JEFF7712 --repository=homelab --branch=main \
  --path=gitops/clusters/homelab-01 --deploy-token-auth
```

Expected: controllers installed, `flux-system` manifests committed locally by flux.

- [ ] **Step 2: Verify and revoke**

Run: `nix run nixpkgs#fluxcd -- check`

Expected: all controllers Ready. Then revoke the one-time token:
`glab api --method DELETE projects/85910419/access_tokens/<id>`.

- [ ] **Step 3: Push and confirm sync**

Push `main`; verify `GitRepository` and `flux-system` Kustomization Ready.

### Task 3: Platform layer with pinned ESO

**Files:**
- Create: `gitops/platform/namespace.yaml`
- Create: `gitops/platform/external-secrets/repository.yaml`
- Create: `gitops/platform/external-secrets/release.yaml`
- Create: `gitops/platform/kustomization.yaml`
- Modify: `gitops/clusters/homelab-01/sync.yaml` (add platform Kustomization)

- [ ] **Step 1: Pin the ESO chart version**

Run: `helm repo add external-secrets https://charts.external-secrets.io && helm search repo external-secrets/external-secrets --versions | head -3`

Use the newest non-RC version in `release.yaml` with `interval: 1h`,
`installCRDs: createReplace`, chart sourceRef to the HelmRepository.

- [ ] **Step 2: Wire dependsOn and verify**

`platform` dependsOn `flux-system`. Push; require the HelmRelease Ready and
`kubectl get crd secretstores.external-secrets.io` present.

### Task 4: SOPS age key and encrypted ESO token

**Files:**
- Create: `.sops.yaml`
- Create: `gitops/secrets/eso-gitlab-token.sops.yaml`
- Create: `gitops/secrets/kustomization.yaml`

- [ ] **Step 1: Generate the age key**

```bash
umask 077
age-keygen -o secrets/keys/flux-age.key
```

Add the public recipient to `.sops.yaml` scoped to `gitops/secrets/*.sops.yaml`.
`secrets/keys/` is already ignored; verify with `git status`.

- [ ] **Step 2: Create the ESO GitLab token and encrypt it**

Create a project access token (`api` scope, 365-day expiry) for ESO, then:

```bash
kubectl create secret generic eso-gitlab-token -n external-secrets \
  --from-literal=token=<TOKEN> --dry-run=client -o yaml | \
  sops --encrypt --in-place /dev/stdin > gitops/secrets/eso-gitlab-token.sops.yaml
```

Use a memory-only token path (process substitution or file with 0600 + shred).
Never print the token.

- [ ] **Step 3: Create the sops-age decryption secret out of band**

```bash
kubectl create secret generic sops-age -n flux-system \
  --from-file=age.agekey=secrets/keys/flux-age.key
```

- [ ] **Step 4: Push and require the secrets Kustomization Ready**

`secrets` dependsOn `platform`. Confirm decrypt succeeds (no sops errors in
kustomize-controller logs).

### Task 5: ESO store and canary proof

**Files:**
- Create: `gitops/eso/secretstore.yaml`
- Create: `gitops/eso/canary.yaml`
- Create: `gitops/eso/kustomization.yaml`

- [ ] **Step 1: Create the canary GitLab variable**

```bash
glab api --method POST projects/85910419/variables --field key=FLUX_ESO_CANARY --field value=<random> --field masked=true
```

- [ ] **Step 2: Declare store and ExternalSecret**

`SecretStore` in the `external-secrets` namespace (provider auth secretRefs
are namespace-local): provider gitlab, projectID `85910419`, auth secretRef
to the SOPS-managed `eso-gitlab-token` in the same namespace. `ExternalSecret`
in the same namespace maps `FLUX_ESO_CANARY` with `creationPolicy: Owner`,
`refreshInterval: 1h`.

- [ ] **Step 3: Prove sync and prune**

Require `ExternalSecret` Synced and the output Secret value hash equals the
variable hash. Then delete the canary from Git, push, and require the output
Secret pruned. Restore the canary afterwards only if the layer should keep it;
otherwise leave ESO proven-empty.

## Final verification

Run: `python -m unittest discover -s tests -v && nix flake check ./flake && gitleaks detect --source . --redact`
