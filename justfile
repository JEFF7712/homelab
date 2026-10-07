agent-context *args:
    python -m scripts.agent context {{args}}

[positional-arguments]
agent-run id client *args:
    python -m scripts.agent agent-run "$@"

[positional-arguments]
agent-verify *args:
    python -m scripts.agent verify "$@"

[positional-arguments]
agent-smoke id client *args:
    python -m scripts.agent.client_smoke "$@"

doctor *args:
    python -m scripts.agent doctor {{args}}

check-changed *args:
    python -m scripts.agent check-changed {{args}}

check:
    bash scripts/checks/all.sh

fmt:
    ruff format scripts opnsense_reconciler tests
    nixfmt $(git ls-files -co --exclude-standard '*.nix')
    for stack in tofu/*/; do tofu -chdir="$stack" fmt; done

fmt-check:
    ruff format --check scripts opnsense_reconciler tests
    nixfmt --check $(git ls-files -co --exclude-standard '*.nix')
    for stack in tofu/*/; do tofu -chdir="$stack" fmt -check; done

check-python:
    bash scripts/checks/python.sh

check-nix target="all":
    bash scripts/checks/nix.sh {{target}}

cache-populate:
    nix build --no-link --print-out-paths \
      ./flake#nixosConfigurations.nas-01.config.system.build.toplevel \
      ./flake#nixosConfigurations.adguard-netbird-01.config.system.build.toplevel \
      ./flake#nixosConfigurations.homelab-01.config.system.build.toplevel \
      ./flake#nixosConfigurations.homelab-02.config.system.build.toplevel \
      ./flake#nixosConfigurations.homelab-03.config.system.build.toplevel \
      ./flake#nixosConfigurations.homelab-04.config.system.build.toplevel \
      ./flake#nixosConfigurations.homelab-05.config.system.build.toplevel \
      ./flake#checks.x86_64-linux.repository-contract \
      ./flake#checks.x86_64-linux.agent-workspace-network \
      ./flake#checks.x86_64-linux.agent-workspace-packet-flow \
      ./flake#checks.x86_64-linux.agent-workspace-host-reservations \
      ./flake#checks.x86_64-linux.agent-workspace-libvirt-normalization \
      ./flake#checks.x86_64-linux.zot-registry \
      | attic push local:homelab --stdin

deploy-fleet *args:
    python -m scripts.deploy_fleet {{args}}

check-gitops:
    bash scripts/checks/gitops.sh

voice-stt backend="local":
    python3 scripts/voice_stt_switch.py {{backend}}

jarvis-acoustic-eval *args:
    python3 scripts/jarvis_acoustic_eval.py {{args}}

voice-topology *args:
    python3 scripts/voice_topology.py --live {{args}}

voice-topology-check:
    python3 scripts/voice_topology.py --check

check-registry:
    bash scripts/checks/registry.sh

registry-inventory:
    python -m scripts.registry inventory --output registry/images.inventory.json

registry-snapshot-live:
    python -m scripts.registry snapshot-live

registry-resolve:
    python -m scripts.registry resolve --inventory registry/images.inventory.json --output registry/images.lock.json

registry-plan:
    python -m scripts.registry plan --lock registry/images.lock.json

registry-promote *args="":
    python -m scripts.registry promote --lock registry/images.lock.json --inventory registry/images.inventory.json {{args}}

registry-check:
    bash scripts/checks/registry-refresh.sh
    python -m scripts.registry check --lock registry/images.lock.json

registry-check-auth report="artifacts/registry/auth-report.json":
    python -m scripts.registry check-auth --lock registry/images.lock.json --report {{report}}

# Serves a throwaway zot on loopback and asserts what its collector keeps, so
# the retention guarantee is tested rather than assumed. Touches nothing real.
registry-gc-fixture report="artifacts/registry/gc-fixture-report.json":
    python -m scripts.registry.gc_fixture --report {{report}}

registry-access-control output="artifacts/registry/access-control.json":
    python -m scripts.registry access-control --lock registry/images.lock.json --output {{output}}

registry-reconcile-retention report="artifacts/registry/retention-report.json":
    python -m scripts.registry reconcile-retention --lock registry/images.lock.json --report {{report}}

registry-copy report="artifacts/registry/import-report.json":
    python -m scripts.registry copy --lock registry/images.lock.json --report {{report}}

registry-verify report="artifacts/registry/verify-report.json":
    python -m scripts.registry verify --lock registry/images.lock.json --report {{report}}

check-tofu:
    bash scripts/checks/tofu.sh

check-docs:
    python -m scripts.checks.docs

provision-check-deps:
    for stack in tofu/*/; do tofu -chdir="$stack" init -backend=false; done

refresh-crd-schemas:
    python scripts/checks/provision_schemas.py

task-new id *args:
    python -m scripts.agent task-new {{id}} {{args}}

task-resume id *args:
    python -m scripts.agent task-resume {{id}} {{args}}

task-checkpoint id *args:
    python -m scripts.agent task-checkpoint {{id}} {{args}}

task-export id *args:
    python -m scripts.agent task-export {{id}} {{args}}

status target *args:
    python -m scripts.agent status {{target}} {{args}}

check-ha:
    bash scripts/checks/home-assistant.sh

workspace-validate *args:
    python -m scripts.agent_workspaces --manifest config/agent-workspaces/workspaces.json validate {{args}}

workspace-plan *args:
    python -m scripts.agent_workspaces --manifest config/agent-workspaces/workspaces.json plan {{args}}

workspace-status id *args:
    python -m scripts.agent_workspaces --manifest config/agent-workspaces/workspaces.json status {{id}} {{args}}

workspace-provision id *args:
    python -m scripts.agent_workspaces --manifest config/agent-workspaces/workspaces.json provision {{id}} {{args}}

workspace-deprovision id *args:
    python -m scripts.agent_workspaces --manifest config/agent-workspaces/workspaces.json deprovision {{id}} {{args}}

ha-inventory *args:
    python -m scripts.home_assistant inventory {{args}}

ha-capture *args:
    python -m scripts.home_assistant capture {{args}}

ha-diff *args:
    python -m scripts.home_assistant diff {{args}}

ha-adopt *args:
    python -m scripts.home_assistant adopt {{args}}

ha-validate *args:
    python -m scripts.home_assistant validate {{args}}

ha-plan *args:
    python -m scripts.home_assistant plan {{args}}

ha-apply *args:
    python -m scripts.home_assistant apply {{args}}

ha-verify *args:
    python -m scripts.home_assistant verify {{args}}

ha-revert *args:
    python -m scripts.home_assistant revert {{args}}

# Live Jarvis routing eval (executes local-path device actions; run when
# someone is home to observe). Offline corpus checks run in the unit suite.
jarvis-eval-live *args:
    python -m scripts.jarvis_eval {{args}}

# Forgejo PR flow for agents. Requires one-time `tea login add` (see
# docs/runbooks/ci-pipelines.md "Agent Forgejo access"). Merge only after
# validation-v2 is green; prefer fast-forward so the Forgejo to GitLab
# mirror stays clean.
forgejo-pr title target="main":
    tea pr create --repo JEFF7712/homelab --base '{{target}}' --head $(git branch --show-current) --title '{{title}}'

forgejo-merge index:
    tea pr merge --repo JEFF7712/homelab {{index}}
