from __future__ import annotations

import json
import unittest

from scripts.agent.diag import diagnose, digest_mismatches, pod_problems, running_images

DIGEST = "sha256:" + "a" * 64


def pod(name: str, ready: bool, restarts: int, waiting: str = "") -> dict:
    return {
        "metadata": {"name": name, "namespace": "app"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "web",
                    "ready": ready,
                    "restartCount": restarts,
                    "state": {"waiting": {"reason": waiting}}
                    if waiting
                    else {"running": {}},
                    "image": "registry.rupan.dev/apps/app@" + DIGEST,
                    "imageID": "registry.rupan.dev/apps/app@" + DIGEST,
                }
            ],
        },
    }


class DiagTest(unittest.TestCase):
    def test_only_unready_or_restarting_containers_are_problems(self) -> None:
        problems = pod_problems(
            [pod("ok", True, 0), pod("crash", False, 4, "CrashLoopBackOff")]
        )
        self.assertEqual([p["pod"] for p in problems], ["crash"])
        self.assertEqual(problems[0]["reason"], "CrashLoopBackOff")

    def test_running_digests_are_compared_with_the_lock(self) -> None:
        images = running_images([pod("ok", True, 0)])
        self.assertEqual(images, {"apps/app": {DIGEST}})
        lock = {"images": [{"destination_repository": "apps/app", "digest": DIGEST}]}
        self.assertEqual(digest_mismatches(images, lock), [])
        other = {
            "images": [
                {"destination_repository": "apps/app", "digest": "sha256:" + "b" * 64}
            ]
        }
        self.assertEqual(digest_mismatches(images, other)[0]["running"], DIGEST)

    def test_diagnosis_follows_kustomization_namespaces_and_fetches_logs(self) -> None:
        calls = []
        kustomization = {
            "metadata": {"name": "app"},
            "spec": {"path": "./gitops/app"},
            "status": {
                "lastAppliedRevision": "main@sha1:x",
                "conditions": [{"type": "Ready", "status": "True"}],
                "inventory": {"entries": [{"id": "app_web_apps_Deployment"}]},
            },
        }
        responses = {
            "kustomization": kustomization,
            "deploy,sts,ds": {
                "items": [
                    {
                        "kind": "Deployment",
                        "metadata": {"name": "web", "namespace": "app"},
                        "spec": {"replicas": 1},
                        "status": {"readyReplicas": 0},
                    }
                ]
            },
            "pods": {"items": [pod("crash", False, 2, "CrashLoopBackOff")]},
            "events": {"items": []},
        }

        def run(arguments):
            calls.append(list(arguments))
            if "logs" in arguments:
                return 0, "boom\n"
            for key, value in responses.items():
                if key in arguments:
                    return 0, json.dumps(value)
            return 1, "unexpected"

        report = diagnose("app", run)
        self.assertFalse(report["healthy"])
        self.assertEqual(report["namespaces"], ["app"])
        self.assertEqual(report["problems"][0]["log_tail"], ["boom"])
        self.assertIn("--previous", next(c for c in calls if "logs" in c))
        self.assertNotIn("delete", json.dumps(calls))


if __name__ == "__main__":
    unittest.main()
