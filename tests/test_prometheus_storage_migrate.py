from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import prometheus_storage_migrate as migration


class PrometheusStorageMigrationTest(unittest.TestCase):
    def identities(self) -> dict[tuple[str, str], dict]:
        return {
            ("prometheus", migration.PROMETHEUS): {
                "spec": {
                    "storage": {
                        "volumeClaimTemplate": {
                            "metadata": {"name": "prometheus-local-db"},
                            "spec": {"storageClassName": "local-nvme"},
                        },
                    }
                }
            },
            ("pvc", migration.OLD_CLAIM): {
                "metadata": {"uid": "source-uid"},
                "spec": {"storageClassName": "nfs-cluster", "volumeName": "old-pv"},
            },
            ("pvc", migration.NEW_CLAIM): {"metadata": {"uid": "target-uid"}},
            ("pv", "old-pv"): {
                "spec": {
                    "nfs": {"server": "10.0.30.20", "path": "/tank/cluster/prometheus"}
                }
            },
            ("pv", "prometheus-nvme-05"): {
                "spec": {
                    "local": {"path": "/persist/prometheus"},
                    "storageClassName": "local-nvme",
                    "persistentVolumeReclaimPolicy": "Retain",
                    "nodeAffinity": {
                        "required": {
                            "nodeSelectorTerms": [
                                {
                                    "matchExpressions": [
                                        {
                                            "key": "kubernetes.io/hostname",
                                            "operator": "In",
                                            "values": ["homelab-05"],
                                        }
                                    ]
                                }
                            ]
                        }
                    },
                }
            },
            ("pod", migration.STATEFULSET + "-0"): {"spec": {"nodeName": "homelab-05"}},
        }

    def test_cold_copy_preserves_source_and_checks_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, scratch = (
                root / name for name in ("source", "target", "tmp")
            )
            (source / "prometheus-db/wal").mkdir(parents=True)
            (source / "prometheus-db/wal/00000001").write_bytes(b"sample-wal")
            (target / "prometheus-db").mkdir(parents=True)
            scratch.mkdir()
            command = migration.copy_job()["spec"]["template"]["spec"]["containers"][0][
                "command"
            ][-1]
            mounts = {"source": source, "target": target, "tmp": scratch}
            command = re.sub(
                r"/(source|target|tmp)(?=[^A-Za-z0-9_.-]|$)",
                lambda match: str(mounts[match[1]]),
                command,
            )
            subprocess.run(["bash", "-ec", command], check=True, capture_output=True)
            self.assertEqual(
                (target / "prometheus-db/wal/00000001").read_bytes(), b"sample-wal"
            )
            self.assertTrue((target / "prometheus-db/.migration-complete").exists())
            self.assertFalse((source / "prometheus-db/.migration-complete").exists())
            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(
                    ["bash", "-ec", command], check=True, capture_output=True
                )

    def test_wrong_source_configuration_cannot_pause_or_scale(self) -> None:
        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                migration,
                "get",
                return_value={
                    "spec": {
                        "storage": {
                            "volumeClaimTemplate": {"metadata": {"name": "old"}}
                        }
                    }
                },
            ),
            patch.object(migration, "kubectl") as mutate,
        ):
            with self.assertRaisesRegex(ValueError, "Deploy the declared"):
                migration.migrate()
            mutate.assert_not_called()

    def test_copy_mounts_source_read_only_and_has_no_api_token(self) -> None:
        spec = migration.copy_job()["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        mounts = spec["containers"][0]["volumeMounts"]
        self.assertTrue(
            next(mount for mount in mounts if mount["name"] == "source")["readOnly"]
        )
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])

    def test_completed_migration_can_be_verified_again_without_downtime(self) -> None:
        identities = self.identities()
        pod = {
            "metadata": {"name": migration.STATEFULSET + "-0"},
            "spec": {
                "nodeName": "homelab-05",
                "volumes": [
                    {"persistentVolumeClaim": {"claimName": migration.NEW_CLAIM}},
                ],
            },
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                migration, "get", side_effect=lambda kind, name: identities[kind, name]
            ),
            patch.object(
                migration, "kubectl", return_value=json.dumps({"items": [pod]})
            ) as commands,
        ):
            migration.migrate()
        self.assertEqual(commands.call_count, 1)
        self.assertEqual(commands.call_args.args[:2], ("get", "pods"))

    def test_valid_copy_pauses_all_owners_and_never_deletes_pvcs(self) -> None:
        identities = self.identities()
        calls = []

        def invoke(*args, **kwargs):
            calls.append((args, kwargs))
            return json.dumps({"items": []}) if args[0] == "get" else ""

        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                migration, "get", side_effect=lambda kind, name: identities[kind, name]
            ),
            patch.object(migration, "kubectl", side_effect=invoke),
        ):
            migration.migrate()
        mutations = [(args, kwargs) for args, kwargs in calls if args[0] != "get"]
        self.assertEqual(
            mutations[0][0][:3], ("patch", "kustomization", "observability")
        )
        self.assertEqual(mutations[0][1]["namespace"], "flux-system")
        self.assertEqual(mutations[1][0][:2], ("patch", "helmrelease"))
        self.assertFalse(any(args[:2] == ("delete", "pvc") for args, _ in calls))
        copied = next(
            kwargs["document"] for args, kwargs in calls if args[0] == "create"
        )
        self.assertEqual(
            copied["metadata"]["annotations"],
            {
                "homelab.rupan.dev/source-pvc-uid": "source-uid",
                "homelab.rupan.dev/target-pvc-uid": "target-uid",
            },
        )

    def test_wrong_destination_node_fails_before_any_mutation(self) -> None:
        identities = self.identities()
        identities["pv", "prometheus-nvme-05"]["spec"]["nodeAffinity"] = {}
        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                migration, "get", side_effect=lambda kind, name: identities[kind, name]
            ),
            patch.object(migration, "kubectl") as mutate,
        ):
            with self.assertRaisesRegex(ValueError, "node affinity"):
                migration.migrate()
            self.assertFalse(
                any(call.args[0] != "get" for call in mutate.call_args_list)
            )

    def test_failed_copy_leaves_reconciliation_paused(self) -> None:
        identities = self.identities()
        calls = []

        def fail_copy(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("wait", "job", migration.JOB):
                raise subprocess.CalledProcessError(1, ["kubectl", *args])
            return json.dumps({"items": []}) if args[0] == "get" else ""

        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                migration, "get", side_effect=lambda kind, name: identities[kind, name]
            ),
            patch.object(migration, "kubectl", side_effect=fail_copy),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                migration.migrate()
        self.assertFalse(any(args[0] == "delete" for args in calls))
        self.assertFalse(any("false" in part for args in calls for part in args))
