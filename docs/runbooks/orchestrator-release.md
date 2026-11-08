# Orchestrator image release

The persistent-agent controller and worker images are Nix packages
(`packages.x86_64-linux.orchestrator-controller-image` and
`orchestrator-worker-image`, from `flake/packages/orchestrator-images.nix`).
The protected `orchestrator-publish` operation publishes both from a
validated main commit:

1. builds each image and pushes it to `registry.rupan.dev/apps/orchestrator`
   or `apps/orchestrator-worker` under the pipeline tag `ci-<pipeline>-<sha12>`,
   using only that repository's scoped publisher (`publisher-orchestrator`,
   `publisher-orchestrator-worker`);
2. verifies the published runtime contract (user `10001:10001`, workdir
   `/app`, linux/amd64) and the digest lookup, and adds the
   `retention-candidate-*` tag;
3. prints a promotion patch that pins both digests in
   `config/orchestrator/images.json` and inserts or updates their
   first-party records in `registry/images.lock.json` and
   `registry/images.inventory.json`. `just ship` merges it as its own PR.

Both repositories are listed in `RESERVED_FIRST_PARTY`
(`scripts/registry/core.py`), so the registry access policy grants each one
to its publisher before its first lock record exists. Publishing deploys
nothing: the orchestrator's manifests are rendered from the pins when a
deployment is requested.

## First publication

1. Deploy the access policy: `just ship --resume <merge-sha> --deploy deploy-registry-platform`.
2. The operator provisions both publishers from a machine with sudo SSH to
   the registry host and an admin Woodpecker token:

   ```sh
   just registry-provision-publisher apps/orchestrator apps/orchestrator-worker
   ```

   This creates the Woodpecker secrets
   `registry_orchestrator_publisher_auth_file` and
   `registry_orchestrator_worker_publisher_auth_file`.
3. Publish: `just ship --resume <main-sha> --deploy orchestrator-publish`.
   Ship merges the promotion PR it prints.
4. Set the `orchestrator-images` route in `config/ci/ship-routes.json` to
   `"auto": true`, so later changes to `scripts/orchestrator/` or the image
   package publish on merge.

## Failures

- `Supply the protected publisher-... OCI auth file`: the secret is missing or
  belongs to another identity; rerun step 2.
- `Registry read failed with HTTP 401/403`: the access policy is not
  deployed (step 1) or the htpasswd entry is missing.
- `Pipeline tag already exists`: a previous run published this pipeline's
  tag; inspect `artifacts/orchestrator-release/receipts.json` from that run
  before retrying with a new pipeline.
- `published runtime contract differs`: the image package changed its user,
  workdir or platform; fix `flake/packages/orchestrator-images.nix`.
