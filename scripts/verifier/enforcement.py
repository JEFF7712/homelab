"""The operator's gates: enforcement code and spending.

Agents decide everything else and the decision is recorded. The operator
approves, by exact change, only:

- spending money: `config/spend.json` holds the caps every paid consumer
  must read;
- enforcement code: the code that judges merges and deployments, holds
  operations for approval, stores the authorization baseline, decides who can
  decrypt secrets and which checks branch protection requires, and this list
  itself.

Without the second gate an agent could remove the first in one merge, so the
list is closed: it names the enforcement code and nothing else. Agent guards,
the operation catalog, review routing and service code are not on it.

Persistent-data destruction stays held separately (`DATA_PATHS`) until
restore drills publish evidence the deployment gate can check.
"""

from __future__ import annotations

ENFORCEMENT_PATHS = (
    # Merge decision, including this list.
    "scripts/verifier/",
    "flake/modules/merge-policy.nix",
    # Deployment decision, the held-operation runtime and the baseline store.
    "scripts/ci/policy.py",
    "scripts/ci/operation.py",
    "scripts/ci/deployment_risk.py",
    "scripts/ci/state.py",
    "flake/modules/tofu-state.nix",
    # Passkey approvals.
    "scripts/approvals/",
    "flake/modules/approvals.nix",
    # Required checks, CI trust and who may push.
    "config/ci/forgejo-protection.json",
    "config/ci/woodpecker-repository.json",
    "flake/modules/woodpecker.nix",
    # Secret recipients.
    ".sops.yaml",
    # The orchestrator controller's credential boundary: the admission policy
    # that keeps its Job permission from mounting the namespace's Secrets.
    "config/orchestrator/worker-admission.yaml",
    # Spending caps.
    "config/spend.json",
)

# Disk layouts and the NAS data module: a deployment can destroy stored data.
DATA_PATHS = (
    "flake/modules/disko",
    "flake/disks/",
    "flake/modules/nas-data.nix",
)


def enforcement(path: str) -> bool:
    return path.startswith(ENFORCEMENT_PATHS)
