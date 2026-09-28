# Flux and External Secrets, GitLab control plane

Research date: 2026-08-30. Sources below are current official Flux and External Secrets Operator documentation.

## Recommended supported shape

- Bootstrap the private mono-repo once with `flux bootstrap gitlab`, using a cluster-specific path such as `gitops/clusters/cluster-01`. The command installs Flux controllers, commits the Flux manifests, configures the cluster source, and lets later controller upgrades and cluster changes arrive by Git push. For an existing repository on branch `main`, use the documented form:

  ```sh
  flux bootstrap gitlab \\
    --owner=<gitlab-group-or-user> \\
    --repository=homelab \\
    --branch=main \\
    --path=gitops/clusters/cluster-01 \\
    --deploy-token-auth
  ```

  `--deploy-token-auth` is appropriate when Flux only reads Git: it creates a project deploy token and stores the resulting credentials in `flux-system/flux-system`. It is read-only, so do not use it if Flux image automation must push commits. In that case use an SSH deploy key with explicit write permission, preferably scoped only to this repository.

  Source: <https://fluxcd.io/flux/installation/bootstrap/gitlab/>.

- The human/bootstrap identity needs cluster-admin access and GitLab project ownership or GitLab-group admin rights. `flux bootstrap gitlab` requires a GitLab PAT with complete GitLab API read/write access to create or configure the project and deploy credential. This high-privilege PAT is a one-time workstation credential, not a GitOps or Kubernetes runtime secret.

  Source: <https://fluxcd.io/flux/installation/bootstrap/gitlab/>.

- Let the generated `GitRepository` be the single source for the mono-repo, then split reconciliation into Flux `Kustomization` layers. Set `prune: true` on the layers that own disposable declarative resources. Flux garbage-collects objects removed from the source and, by default, also when the owning Kustomization is deleted. Use `dependsOn` and health checks to make CRDs/controllers ready before their custom resources. Avoid circular dependencies.

  Source: <https://fluxcd.io/flux/components/kustomize/kustomizations/>.

## Safe ordering

1. Provision k3s and confirm API access. Create the private GitLab project and its initial `gitops/clusters/cluster-01` bootstrap path.
2. From an administrator workstation, run `flux bootstrap gitlab` with a short-lived/revocable PAT, then verify `flux check` and the initial source/Kustomization readiness. Do not put that bootstrap PAT in GitLab CI variables, SOPS, or the cluster.
3. Reconcile a `platform` layer for namespaces, CRDs, Helm repositories/releases, and controllers. Install External Secrets Operator before any `SecretStore` or `ExternalSecret`: it owns those CRDs. The Helm chart normally installs and manages its CRDs, but that behavior must be chosen deliberately and pinned in the release configuration.
4. Reconcile the External Secrets access-token Secret first, then the GitLab `SecretStore`, then `ExternalSecret` resources and finally workloads that consume their output. The access token is a bootstrap secret: it cannot itself be fetched from GitLab Variables by the GitLab provider. Manage it outside that circular dependency, for example as a SOPS-encrypted Kubernetes Secret decrypted by Flux, with a GitLab project/group token limited to read API access for the required variables.
5. Put applications behind a separate Flux layer that `dependsOn` the platform/External Secrets layer and uses health checks. For individual apps, gate startup on the generated Kubernetes Secret where the workload/controller supports that behavior. An ESO sync failure must not be treated as a successful application deployment.

ESO installation/CRD source: <https://external-secrets.io/latest/introduction/getting-started/>.

## GitLab Variables provider boundaries

- ESO's GitLab provider reads GitLab project variables and can also read group variables. A `SecretStore` supplies the GitLab URL (GitLab.com by default), numeric `projectID`, optional group selection, optional environment scope, and an `accessToken` Secret reference. `ClusterSecretStore` requires the namespace on that token reference. Use namespace-local `SecretStore` objects by default to limit blast radius.
- The GitLab access token must have the `api` scope. Scope the token to the minimum project/group and use an expiry/rotation process. Do not use the Flux repository deploy token for this: it is for Git transport and does not establish GitLab Variables API access.
- An `ExternalSecret` maps named GitLab variables into a Kubernetes Secret. Use `target.creationPolicy: Owner`, explicit `data` mappings, and a bounded `refreshInterval` such as `1h`. `Periodic` is the default refresh policy. Avoid broad `dataFrom.find` imports unless the variable namespace is intentionally dedicated to one Kubernetes namespace.
- GitLab environment scopes are supported. A configured environment first resolves an exact-scoped variable, then falls back to `*`; use distinct environment scopes deliberately so production values cannot be accidentally selected by an unscoped store.
- For private/self-hosted GitLab with a private CA, configure `caProvider` rather than embedding a CA bundle directly in the store when practical.

Provider and ExternalSecret examples: <https://external-secrets.io/main/provider/gitlab-variables/>.

## Credential and ownership caveats

- Flux's Git source credentials live in a Kubernetes Secret referenced by the `GitRepository`. For GitLab HTTPS OAuth/PAT authentication, Flux documents basic auth (`username` plus token as `password`), not bearer auth. The bootstrap-generated deploy credential avoids hand-authoring this secret for the initial source.

  Source: <https://fluxcd.io/flux/components/source/gitrepositories/>.

- Do not store plaintext or base64-only Kubernetes secrets in the private Git repository. Private visibility is not encryption. Keep the Flux source credential bootstrap-managed; use SOPS-encrypted manifests for the ESO GitLab API token and other bootstrap secrets, then ESO for application runtime values held in GitLab Variables.
- Treat GitLab CI variables as a secret distribution backend, not the source of truth for host bootstrap, GitLab admin access, or Flux Git authentication. An ESO controller compromise can read every variable its API token can read, so use separate GitLab projects/groups and separate access tokens for separate trust domains if applications require different secrets.

Flux secret-storage warning and Git authentication formats: <https://fluxcd.io/flux/components/kustomize/kustomizations/>, <https://fluxcd.io/flux/components/source/gitrepositories/>.
