import os
import subprocess
import unittest
from pathlib import Path

GUARD = Path(
    os.environ.get(
        "NIXOS_CI_JOB_GUARD",
        Path(__file__).resolve().parents[1]
        / "flake/modules/github-runner-nixos/job-guard.sh",
    )
)


class GitHubRunnerGuardTests(unittest.TestCase):
    def run_guard(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": os.environ["PATH"],
            "GITHUB_REPOSITORY": "JEFF7712/nixos-config",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_WORKFLOW_REF": "JEFF7712/nixos-config/.github/workflows/build-toplevel.yml@refs/heads/main",
        }
        environment.update(overrides)
        return subprocess.run(
            ["bash", str(GUARD)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_trusted_artifact_events(self) -> None:
        for workflow, events in (
            ("build-toplevel", ("push", "workflow_dispatch")),
            ("build-iso", ("push", "workflow_dispatch", "schedule")),
        ):
            for event in events:
                with self.subTest(workflow=workflow, event=event):
                    result = self.run_guard(
                        GITHUB_EVENT_NAME=event,
                        GITHUB_WORKFLOW_REF=f"JEFF7712/nixos-config/.github/workflows/{workflow}.yml@refs/heads/main",
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_untrusted_jobs_are_rejected(self) -> None:
        for override in (
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "pull_request_target"},
            {"GITHUB_REF": "refs/pull/42/merge"},
            {"GITHUB_REF": "refs/heads/topic"},
            {"GITHUB_REPOSITORY": "attacker/nixos-config"},
            {
                "GITHUB_WORKFLOW_REF": "JEFF7712/nixos-config/.github/workflows/check.yml@refs/heads/main"
            },
            {
                "GITHUB_WORKFLOW_REF": "JEFF7712/nixos-config/.github/workflows/build-toplevel.yml@refs/heads/topic"
            },
            {"GITHUB_EVENT_NAME": ""},
            {"GITHUB_WORKFLOW_REF": ""},
        ):
            with self.subTest(override=override):
                self.assertNotEqual(self.run_guard(**override).returncode, 0)
