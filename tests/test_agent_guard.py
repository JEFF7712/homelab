import json
import subprocess
import unittest
from pathlib import Path

from scripts.agent.guard import AUTHORIZATION_MARKER, assess
from scripts.agent.hook_runtime import guard_response

ROOT = Path(__file__).resolve().parents[1]


class GuardClassificationTest(unittest.TestCase):
    def assert_kind(self, command: str, kind: str | None) -> None:
        verdict = assess(command)
        self.assertEqual(verdict.kind if verdict else None, kind, command)

    def test_forbidden_commands_cannot_be_authorized(self) -> None:
        for command in (
            "git commit --no-verify -m fix",
            "git commit -n -m fix",
            "git push git-agent:JEFF7712/homelab.git HEAD:main",
            "git push origin main",
            "git -C /tmp/repo push origin +main",
            "git push --force",
            'curl -X POST $API/pulls/3/merge -d \'{"Do":"merge","force_merge":true}\'',
            'python -c \'call("POST", url, {"force_merge": True})\'',
            f"{AUTHORIZATION_MARKER} git push origin refs/heads/main",
        ):
            with self.subTest(command=command):
                self.assert_kind(command, "forbidden")

    def test_live_mutations_require_authorization(self) -> None:
        for command in (
            "kubectl -n immich delete pod immich-cluster-2",
            "kubectl --context homelab apply -f x.yaml",
            "kubectl rollout restart deploy/jellyfin -n media",
            "kubectl drain homelab-05 --ignore-daemonsets",
            "flux suspend kustomization media",
            "helm upgrade foo chart",
            "tofu -chdir=tofu/opnsense apply desired.tfplan",
            "terraform state rm module.x",
            "nixos-rebuild switch --flake .#homelab-01 --target-host homelab-01",
            "ssh -i key homelab-01 'sudo /nix/store/abc/bin/switch-to-configuration switch'",
            "ssh homelab-02 sudo nixos-rebuild switch",
            "just deploy-fleet --target homelab-04",
            "python -m scripts.deploy_fleet --target nas-01",
            "just ha-apply -y",
            "nix develop ./flake -c kubectl scale deploy/x --replicas=0",
            "timeout 60 sudo kubectl label ns media a=b",
            "bash -c 'cd /srv && kubectl delete ns old'",
            "echo start && kubectl patch pv x --type=json -p '[]'",
        ):
            with self.subTest(command=command):
                self.assert_kind(command, "authorize")
                self.assert_kind(f"{AUTHORIZATION_MARKER} {command}", None)

    def test_reads_dry_runs_and_text_are_allowed(self) -> None:
        for command in (
            "kubectl get pods -A",
            "kubectl -n media logs deploy/bookshelf --tail=20",
            "kubectl apply --dry-run=server -f x.yaml",
            "kubectl rollout status deploy/x",
            "kubectl -n immich exec immich-cluster-1 -c postgres -- psql -c 'select 1'",
            "flux reconcile kustomization media --with-source",
            "tofu -chdir=tofu/cloudflare plan -out=desired.tfplan",
            "just deploy-fleet --dry-run",
            "python -m scripts.deploy_fleet --dry-run",
            "just ship",
            "just ship --deploy none",
            "just ship --deploy auto --no-merge",
            "just ship --deploy deploy-fleet-dry-run,deploy-fleet",
            "python -m scripts.agent.ship --deploy=opnsense-apply",
            "git push git-agent:JEFF7712/homelab.git HEAD",
            "git push --force-with-lease git-agent:JEFF7712/homelab.git feat/x",
            "git commit -m 'docs: explain why kubectl apply and git push origin main are blocked'",
            "rg -n 'kubectl delete' docs",
            "ssh homelab-01 'uptime; systemctl status k3s'",
            "ssh homelab-04 'pgrep -af \"switch-to-conf|nixos-rebuild\" | head'",
            "ssh homelab-04 'journalctl -u nixos-rebuild-switch-to-configuration -n 5'",
            "cat > notes.md <<'EOF'\nkubectl delete ns media\ntofu apply\nEOF\nwc -l notes.md",
            'python -m x <<<"$HOOK_INPUT"\nkubectl get nodes',
            "echo done # kubectl delete ns media",
        ):
            with self.subTest(command=command):
                self.assert_kind(command, None)


class GuardResponseTest(unittest.TestCase):
    def test_claude_and_codex_receive_a_pre_tool_use_deny(self) -> None:
        response = guard_response(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "kubectl delete ns media"},
            }
        )
        output = response["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(AUTHORIZATION_MARKER, output["permissionDecisionReason"])
        self.assertEqual(
            guard_response(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_input": {"command": "kubectl get ns"},
                }
            ),
            {},
        )

    def test_cursor_always_receives_an_explicit_permission(self) -> None:
        denied = guard_response(
            {
                "hook_event_name": "beforeShellExecution",
                "command": "git push origin main",
            }
        )
        self.assertEqual(denied["permission"], "deny")
        self.assertIn("main", denied["agent_message"])
        allowed = guard_response(
            {"hook_event_name": "beforeShellExecution", "command": "ls"}
        )
        self.assertEqual(allowed, {"permission": "allow"})

    def run_hook(self, payload: object) -> dict[str, object]:
        result = subprocess.run(
            ["bash", str(ROOT / "hooks/pre-tool-use")],
            cwd=ROOT,
            input=json.dumps(payload) if not isinstance(payload, str) else payload,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_hook_script_denies_and_fails_open_in_client_shapes(self) -> None:
        denied = self.run_hook(
            {
                "session_id": "fixture",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "tofu apply"},
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        cursor = self.run_hook(
            {"hook_event_name": "beforeShellExecution", "command": "kubectl get pods"}
        )
        self.assertEqual(cursor, {"permission": "allow"})
        self.assertEqual(self.run_hook("not json"), {})

    def test_every_blocking_client_wires_the_guard(self) -> None:
        claude = json.loads((ROOT / ".claude/settings.json").read_text())
        codex = json.loads((ROOT / ".codex/hooks.json").read_text())
        cursor = json.loads((ROOT / ".cursor/hooks.json").read_text())
        for config in (claude, codex):
            (entry,) = config["hooks"]["PreToolUse"]
            self.assertEqual(entry["matcher"], "Bash")
            self.assertIn("hooks/pre-tool-use", entry["hooks"][0]["command"])
        self.assertEqual(
            cursor["hooks"]["beforeShellExecution"][0]["command"], "hooks/pre-tool-use"
        )
        self.assertIn(
            '"pre-tool-use"', (ROOT / ".opencode/plugins/agent-harness.js").read_text()
        )


if __name__ == "__main__":
    unittest.main()
