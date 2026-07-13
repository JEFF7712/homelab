# Repository Guidelines

## Project Structure & Module Organization

This repository declaratively manages a Talos Kubernetes homelab. `apps/` contains Argo CD `Application` resources, while `infrastructure/` holds the Kubernetes manifests they reconcile. Put cluster bootstrap resources in `bootstrap/`, provisioning code in `terraform/`, and configuration-management playbooks in `ansible/playbooks/`. Supporting validation or maintenance utilities belong in `scripts/`. Store encrypted secret manifests in `secrets/`; never commit plaintext credentials. Architecture notes and implementation records live under `docs/` and `plans/`.

## Build, Test, and Development Commands

Enter the reproducible tool environment with `nix develop`. Before submitting changes, run the checks relevant to the files touched:

- `yamllint .` validates YAML across the repository.
- `python scripts/validate_argocd_apps.py` checks that local Argo CD source paths exist and are safe.
- `terraform fmt -check -recursive terraform` and `terragrunt hcl format --check --working-dir terraform` verify IaC formatting.
- `cd ansible && ansible-lint` checks playbook style.
- `kubeconform -summary -strict -ignore-missing-schemas -kubernetes-version 1.35.0 <manifest>` validates Kubernetes resources.
- `gitleaks detect --source . --redact` checks for leaked credentials.

GitLab CI runs the complete validation and security suite defined in `.gitlab-ci.yml`.

## Coding Style & Naming Conventions

Follow `.yamllint.yml` and preserve the existing two-space YAML indentation. Use lowercase kebab-case for manifest names, such as `cluster-issuer.yaml`, and group service resources under `infrastructure/<service>/`. Keep Kubernetes resource names stable because Argo CD tracks them declaratively. Format Terraform before committing. Python utilities should use type hints, `snake_case`, and focused functions, matching `scripts/validate_argocd_apps.py`.

## Testing Guidelines

This repository uses static validation rather than a unit-test suite. Test the smallest relevant scope locally, then run broader checks for changes affecting shared manifests, Argo CD application wiring, or Terraform modules. Do not apply resources manually to compensate for uncommitted configuration; Git and Argo CD are the deployment path.

## Commit & Merge Request Guidelines

Match the concise history style: `feat: add ...`, `deploy: migrate ...`, or a scoped form such as `build(nixagent): update ...`. Keep each commit to one logical infrastructure change. Merge requests should explain the operational impact, list validation performed, link related issues or plans, and include screenshots only for user-visible dashboards. Call out migrations, secret requirements, rollback steps, and any expected Argo CD reconciliation behavior.
