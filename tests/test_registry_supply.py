from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import stat
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

import yaml

from scripts.registry.cli import main
from scripts.registry.core import (
    ImageReference,
    OciClient,
    RegistryError,
    build_live_snapshot,
    check_consumers,
    copy_lock,
    copy_plan,
    destination_repository,
    discover_inventory,
    filter_transient_observed_errors,
    image_kind,
    load_lock,
    promote_first_party_lock,
    render_access_control,
    render_node_config,
    resolve_inventory,
    rewrite_consumer_digests,
    rewrite_observed_digests,
    select_promotion_candidate,
    validate_lock,
    verify_lock,
)


def manifest(platforms: list[tuple[str, str]] | None = None) -> bytes:
    if platforms is None:
        value = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"digest": "sha256:" + "1" * 64},
            "layers": [],
        }
    else:
        value = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
            "manifests": [
                {
                    "digest": "sha256:" + str(index + 1) * 64,
                    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                    "platform": {"os": operating_system, "architecture": architecture},
                }
                for index, (operating_system, architecture) in enumerate(platforms)
            ],
        }
    return json.dumps(value, separators=(",", ":")).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class OciClientTests(unittest.TestCase):
    def test_copy_uses_explicit_signature_policy_and_preserves_digests(self) -> None:
        client = OciClient()
        client.copy_tool = "skopeo"

        with patch.object(client, "_run", return_value=b"") as run:
            client.copy("docker.io/library/demo@sha256:" + "1" * 64, "local/demo:1")

        command = run.call_args.args[0]
        self.assertIn("--insecure-policy", command)
        self.assertIn("--preserve-digests", command)
        self.assertNotIn("--src-tls-verify=false", command)
        self.assertNotIn("--dest-tls-verify=false", command)


class RegistryCiContractTests(unittest.TestCase):
    def test_node_rollout_verifies_lock_without_retired_migration_identity(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        template_script = "\n".join(pipeline[".deploy_registry_node"]["script"])
        node_one = pipeline["deploy_registry_node_01"]

        self.assertEqual(node_one["needs"], ["registry_lock"])
        self.assertIn("REGISTRY_NODE_PASSWORD_FILE", template_script)
        self.assertIn("registry-node-auth.json", template_script)
        self.assertLess(
            template_script.index("registry-verify"),
            template_script.index("nixos-rebuild"),
        )
        self.assertNotIn("REGISTRY_MIGRATION_AUTH_FILE", template_script)

    def test_first_party_promotion_runs_on_schedule_and_commits_atomically(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        job = pipeline["registry_promote_first_party"]
        script = "\n".join(job["script"])

        rules = [rule.get("if", "") for rule in job["rules"]]
        self.assertIn('$CI_PIPELINE_SOURCE == "schedule"', rules)
        self.assertEqual(job["tags"], ["nas-ci"])
        self.assertEqual(job["resource_group"], "registry-content")
        self.assertEqual(job["environment"], {"name": "production"})
        self.assertIn("scripts.registry promote", script)
        self.assertIn("REGISTRY_NODE_PASSWORD_FILE", script)
        self.assertIn("registry-node-auth.json", script)
        self.assertNotIn("REGISTRY_IMPORTER_AUTH_FILE", script)
        self.assertIn("just check-registry", script)
        self.assertIn("GITLAB_PUSH_TOKEN", script)
        self.assertIn("git fetch", script)
        self.assertIn("git rebase FETCH_HEAD", script)
        self.assertLess(
            script.index("scripts.registry promote"), script.index("git commit")
        )
        self.assertLess(script.index("just check-registry"), script.index("git commit"))

    def test_resolve_reads_local_sources_with_node_credentials(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        job = pipeline["registry_resolve"]
        script = "\n".join(job["script"])

        self.assertEqual(job["environment"], {"name": "production"})
        self.assertIn("REGISTRY_NODE_PASSWORD_FILE", script)
        self.assertIn("registry-node-auth.json", script)
        self.assertNotIn("REGISTRY_IMPORTER_AUTH_FILE", script)


def lock_record(
    raw: bytes, *, tag: str = "1.0", repository: str = "library/demo"
) -> dict[str, Any]:
    expected = digest(raw)
    source = ImageReference("docker.io", repository, tag, expected)
    return {
        "id": "upstream-library-demo-123456789abc",
        "kind": "upstream",
        "source": {
            "registry": source.registry,
            "repository": source.repository,
            "tag": source.tag,
            "digest": expected,
            "reference": source.canonical,
        },
        "destination_repository": destination_repository(source),
        "consumers": ["gitops/demo.yaml:10"],
        "producer": None,
        "retention_class": "deployed",
        "digest": expected,
        "media_type": json.loads(raw)["mediaType"],
        "platforms": ["linux/amd64", "linux/arm64"] if b"manifests" in raw else [],
        "destination_tags": [tag, f"retention-deployed-{expected[7:23]}"],
        "referrers": {"required": [], "source_status": "not-enumerated"},
        "authenticity": {"status": "unsupported", "reason": "no policy"},
    }


def valid_lock(raw: bytes) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "registry-image-lock",
        "generated_at": "2026-09-06T00:00:00+00:00",
        "source_revision": "a" * 40,
        "destination_registry": "registry.rupan.dev",
        "images": [lock_record(raw)],
        "mirror_exceptions": [],
        "unresolved_inputs": [],
    }


class FakeClient:
    def __init__(self, manifests: dict[str, bytes]) -> None:
        self.manifests = manifests
        self.copies: list[tuple[str, str]] = []

    def raw_manifest(self, reference: str, *, destination: bool = False) -> bytes:
        del destination
        try:
            return self.manifests[reference]
        except KeyError as error:
            raise RegistryError(
                "manifest inspection failed with exit code 1"
            ) from error

    def copy(self, source: str, destination: str) -> None:
        self.copies.append((source, destination))
        raw = self.manifests[source]
        self.manifests[destination] = raw
        base = destination.rsplit(":", 1)[0]
        self.manifests[f"{base}@{digest(raw)}"] = raw


class ImageReferenceTest(unittest.TestCase):
    def test_normalizes_docker_hub_shorthand_and_maps_namespaces(self) -> None:
        nginx = ImageReference.parse("nginx:1.27")
        self.assertEqual(nginx.canonical, "docker.io/library/nginx:1.27")
        self.assertEqual(
            destination_repository(nginx), "upstream/docker.io/library/nginx"
        )

    def test_registry_port_mapping_is_valid_and_explicit(self) -> None:
        reference = ImageReference.parse("registry.example:5443/team/app:v1")
        self.assertEqual(
            destination_repository(reference),
            "upstream/registry.example/port-5443/team/app",
        )

    def test_local_first_party_images_are_not_upstream_imports(self) -> None:
        reference = ImageReference.parse(
            "registry.rupan.dev/apps/apolline@sha256:" + "a" * 64
        )
        self.assertEqual(image_kind(reference), "first-party")
        self.assertEqual(destination_repository(reference), "apps/apolline")

    def test_rejects_invalid_digest_port_and_repository(self) -> None:
        for value in (
            "busybox@sha256:1234",
            "registry.example:99999/team/app:v1",
            "docker.io/UPPER/Case:v1",
        ):
            with self.subTest(value=value), self.assertRaises(RegistryError):
                ImageReference.parse(value)


class LiveSnapshotTests(unittest.TestCase):
    def test_snapshot_uses_running_image_ids_and_job_templates(self) -> None:
        digest = "sha256:" + "a" * 64
        payload = {
            "kind": "List",
            "items": [
                {
                    "kind": "Pod",
                    "metadata": {"namespace": "voice", "name": "satellite"},
                    "spec": {
                        "containers": [
                            {"name": "satellite", "image": "ghcr.io/example/app:latest"}
                        ]
                    },
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "satellite",
                                "imageID": f"docker-pullable://ghcr.io/example/app@{digest}",
                            }
                        ],
                    },
                },
                {
                    "kind": "Pod",
                    "metadata": {"namespace": "voice", "name": "old-job"},
                    "spec": {
                        "containers": [{"name": "task", "image": "busybox:latest"}]
                    },
                    "status": {"phase": "Succeeded"},
                },
                {
                    "kind": "Pod",
                    "metadata": {"namespace": "web", "name": "local-site"},
                    "spec": {
                        "containers": [
                            {
                                "name": "web",
                                "image": f"registry.rupan.dev/apps/demo@{digest}",
                            }
                        ]
                    },
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "web",
                                "imageID": f"docker-pullable://ghcr.io/example/app@{digest}",
                            }
                        ],
                    },
                },
                {
                    "kind": "CronJob",
                    "metadata": {"namespace": "default", "name": "backup"},
                    "spec": {
                        "jobTemplate": {
                            "spec": {
                                "template": {
                                    "spec": {
                                        "containers": [
                                            {"name": "backup", "image": "busybox:1.36"}
                                        ]
                                    }
                                }
                            }
                        }
                    },
                },
                {
                    "kind": "Job",
                    "metadata": {"namespace": "kube-system", "name": "helm-install"},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {"name": "helm", "image": "rancher/klipper-helm:v1"}
                                ]
                            }
                        }
                    },
                    "status": {"completionTime": "2026-09-22T00:00:00Z"},
                },
            ],
        }

        snapshot = build_live_snapshot(payload, observed_at="2026-09-22T00:00:00Z")

        self.assertEqual(snapshot["observed_at"], "2026-09-22T00:00:00Z")
        self.assertEqual(
            snapshot["images"],
            [
                {
                    "reference": "docker.io/library/busybox:1.36",
                    "consumers": ["default/cronjob-backup/backup"],
                },
                {
                    "reference": f"ghcr.io/example/app:latest@{digest}",
                    "consumers": ["voice/satellite/satellite"],
                },
                {
                    "reference": f"registry.rupan.dev/apps/demo@{digest}",
                    "consumers": ["web/local-site/web"],
                },
            ],
        )
        app = ImageReference.parse("ghcr.io/jeff7712/site:1")
        self.assertEqual(destination_repository(app), "apps/site")


class InventoryTest(unittest.TestCase):
    def test_discovers_direct_flux_and_reports_generated_runtime_and_producer_gaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops/flux-system").mkdir(parents=True)
            (root / "flake/modules").mkdir(parents=True)
            (root / "gitops/app.yaml").write_text(
                "kind: Deployment\nspec:\n  image: nginx:1.27\n", encoding="utf-8"
            )
            (root / "gitops/flux-system/controller.yaml").write_text(
                "image: ghcr.io/fluxcd/source-controller:v1.9.1\n", encoding="utf-8"
            )
            (root / "gitops/release.yaml").write_text(
                "kind: HelmRelease\nspec:\n  chart:\n    spec:\n      chart: loki\n",
                encoding="utf-8",
            )
            (root / "gitops/first.yaml").write_text(
                "image: ghcr.io/jeff7712/app:1\n", encoding="utf-8"
            )
            (root / "gitops/local.yaml").write_text(
                "image: registry.rupan.dev/apps/apolline@sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            (root / "flake/modules/k3s-server.nix").write_text("{}", encoding="utf-8")
            inventory = discover_inventory(root)
        references = {item["source"]["reference"] for item in inventory["images"]}
        self.assertEqual(
            references,
            {
                "docker.io/library/nginx:1.27",
                "ghcr.io/fluxcd/source-controller:v1.9.1",
                "ghcr.io/jeff7712/app:1",
                "registry.rupan.dev/apps/apolline@sha256:" + "a" * 64,
            },
        )
        classes = {item["class"] for item in inventory["unresolved_inputs"]}
        self.assertEqual(
            classes,
            {"helm-generated", "k3s-bootstrap", "live-workloads", "producer-pipeline"},
        )

    def test_keeps_observed_digest_separate_from_desired_digest_only_reference(
        self,
    ) -> None:
        desired_digest = "sha256:" + "a" * 64
        observed_digest = "sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops").mkdir()
            (root / "gitops/app.yaml").write_text(
                f"image: registry.rupan.dev/apps/demo@{desired_digest}\n",
                encoding="utf-8",
            )
            (root / "registry").mkdir()
            (root / "registry/observed-images.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "registry-observed-images",
                        "coverage": ["live-workloads"],
                        "images": [
                            {
                                "reference": f"registry.rupan.dev/apps/demo@{observed_digest}",
                                "consumers": ["demo/pod/web"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            inventory = discover_inventory(root)

        references = {
            item["source"]["reference"]: item["consumers"]
            for item in inventory["images"]
        }
        self.assertEqual(
            references,
            {
                f"registry.rupan.dev/apps/demo@{desired_digest}": ["gitops/app.yaml:1"],
                f"registry.rupan.dev/apps/demo@{observed_digest}": [
                    "observed:demo/pod/web"
                ],
            },
        )

    def test_cli_inventory_is_offline_and_nonzero_when_gaps_remain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops").mkdir()
            (root / "gitops/app.yaml").write_text(
                "image: busybox:1.36\n", encoding="utf-8"
            )
            output = root / "inventory.json"
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = main(
                    ["--root", str(root), "inventory", "--output", str(output)]
                )
            self.assertEqual(result, 2)
            self.assertTrue(output.exists())
            self.assertEqual(
                json.loads(stream.getvalue())["kind"], "registry-inventory"
            )


class LockTest(unittest.TestCase):
    def test_validation_rejects_unknown_schema_missing_digest_and_conflicting_tag(
        self,
    ) -> None:
        raw = manifest()
        value = valid_lock(raw)
        value["schema_version"] = 2
        duplicate = dict(value["images"][0])
        duplicate["id"] = "other-id"
        duplicate["digest"] = "sha256:" + "f" * 64
        value["images"].append(duplicate)
        missing = dict(value["images"][0])
        missing["id"] = "missing-digest"
        missing["destination_repository"] = "upstream/docker.io/library/missing"
        missing["destination_tags"] = ["missing"]
        missing["digest"] = None
        value["images"].append(missing)
        errors = validate_lock(value)
        self.assertTrue(any("schema_version" in error for error in errors))
        self.assertTrue(any("digest must" in error for error in errors))
        self.assertTrue(any("conflicting digests" in error for error in errors))

    def test_incomplete_lock_is_rejected_for_operations(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        value["unresolved_inputs"] = [{"id": "gap"}]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "lock.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "unresolved inputs"):
                load_lock(path)

    def test_nonblocking_producer_handoff_does_not_reject_lock(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        value["unresolved_inputs"] = [
            {"id": "producer:app", "class": "producer-pipeline", "blocking": False}
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "lock.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(
                load_lock(path)["images"][0]["id"], value["images"][0]["id"]
            )

    def test_resolve_preserves_raw_digest_media_type_platforms_and_gaps(self) -> None:
        raw = manifest([("linux", "arm64"), ("linux", "amd64")])
        inventory = {
            "schema_version": 1,
            "kind": "registry-inventory",
            "source_revision": "b" * 40,
            "images": [
                {
                    "id": "upstream-library-demo-123456789abc",
                    "kind": "upstream",
                    "source": {
                        "registry": "docker.io",
                        "repository": "library/demo",
                        "tag": "1.0",
                        "digest": None,
                        "reference": "docker.io/library/demo:1.0",
                    },
                    "destination_repository": "upstream/docker.io/library/demo",
                    "consumers": ["gitops/demo.yaml:1"],
                    "producer": None,
                    "retention_class": "deployed",
                }
            ],
            "unresolved_inputs": [
                {
                    "id": "live-workloads:cluster",
                    "class": "live-workloads",
                    "consumer": "cluster",
                    "input": "pods",
                    "reason": "not inspected",
                }
            ],
        }
        client = FakeClient({"docker.io/library/demo:1.0": raw})
        lock, unresolved = resolve_inventory(inventory, client)  # type: ignore[arg-type]
        record = lock["images"][0]
        self.assertEqual(record["digest"], digest(raw))
        self.assertEqual(record["platforms"], ["linux/amd64", "linux/arm64"])
        self.assertEqual(
            record["media_type"],
            "application/vnd.docker.distribution.manifest.list.v2+json",
        )
        self.assertEqual(len(unresolved), 1)

    def test_resolve_decodes_local_upstream_mirror_paths(self) -> None:
        raw = manifest()
        expected_digest = digest(raw)
        local_reference = (
            "registry.rupan.dev/upstream/quay.io/prometheus-operator/"
            f"prometheus-config-reloader:v0.94.0@{expected_digest}"
        )
        inventory = {
            "schema_version": 1,
            "kind": "registry-inventory",
            "source_revision": "b" * 40,
            "images": [
                {
                    "id": "upstream-local-mirror",
                    "kind": "upstream",
                    "source": {
                        "registry": "registry.rupan.dev",
                        "repository": (
                            "upstream/quay.io/prometheus-operator/"
                            "prometheus-config-reloader"
                        ),
                        "tag": "v0.94.0",
                        "digest": expected_digest,
                        "reference": local_reference,
                    },
                    "destination_repository": (
                        "upstream/registry.rupan.dev/upstream/quay.io/"
                        "prometheus-operator/prometheus-config-reloader"
                    ),
                    "consumers": ["observed:observability/prometheus/config-reloader"],
                    "producer": None,
                    "retention_class": "deployed",
                    "observed_manifest": {
                        "media_type": "application/vnd.docker.distribution.manifest.v2+json",
                        "platforms": ["linux/amd64"],
                    },
                }
            ],
            "unresolved_inputs": [],
        }
        client = FakeClient(
            {
                f"quay.io/prometheus-operator/prometheus-config-reloader@{expected_digest}": raw
            }
        )

        lock, unresolved = resolve_inventory(inventory, client)  # type: ignore[arg-type]

        record = lock["images"][0]
        self.assertEqual(unresolved, [])
        self.assertEqual(
            record["source"]["reference"],
            "quay.io/prometheus-operator/prometheus-config-reloader:"
            f"v0.94.0@{expected_digest}",
        )
        self.assertEqual(
            record["destination_repository"],
            "upstream/quay.io/prometheus-operator/prometheus-config-reloader",
        )

    def test_plan_is_ordered_and_uses_digest_sources(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        second = lock_record(raw, tag="2.0", repository="library/zed")
        second["id"] = "z-image"
        value["images"].append(second)
        plan = copy_plan(value)
        self.assertEqual(
            [step["id"] for step in plan["steps"]],
            sorted(step["id"] for step in plan["steps"]),
        )
        self.assertTrue(all("@sha256:" in step["source"] for step in plan["steps"]))


class CopyVerifyTest(unittest.TestCase):
    def test_kind_filter_limits_remote_operations(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        first_party = json.loads(json.dumps(value["images"][0]))
        first_party["id"] = "first-party-demo"
        first_party["kind"] = "first-party"
        first_party["destination_repository"] = "apps/demo"
        value["images"].append(first_party)
        source_record = value["images"][0]["source"]
        source = (
            f"{source_record['registry']}/{source_record['repository']}"
            f"@{value['images'][0]['digest']}"
        )
        client = FakeClient({source: raw})
        report = copy_lock(client, value, kind="upstream")
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(report["images"][0]["id"], value["images"][0]["id"])

    def test_copy_is_digest_preserving_and_second_run_reuses_content(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        record = value["images"][0]
        source = f"docker.io/library/demo@{record['digest']}"
        client = FakeClient({source: raw})
        first = copy_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(first["status"], "ok")
        self.assertEqual(len(client.copies), 2)
        second = copy_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(second["images"][0]["status"], "reused")
        self.assertEqual(len(client.copies), 2)

    def test_copy_reuses_required_referrers_on_resume(self) -> None:
        raw = manifest()
        referrer = manifest([("linux", "amd64")])
        value = valid_lock(raw)
        referrer_digest = digest(referrer)
        value["images"][0]["referrers"] = {
            "required": [{"digest": referrer_digest}],
            "source_status": "enumerated",
        }
        source = f"docker.io/library/demo@{value['images'][0]['digest']}"
        referrer_source = f"docker.io/library/demo@{referrer_digest}"
        client = FakeClient({source: raw, referrer_source: referrer})

        first = copy_lock(client, value)  # type: ignore[arg-type]
        second = copy_lock(client, value)  # type: ignore[arg-type]

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["images"][0]["status"], "reused")
        self.assertEqual(len(client.copies), 3)

    def test_copy_refuses_conflicting_release_tag(self) -> None:
        raw = manifest()
        conflicting = manifest([("linux", "amd64")])
        value = valid_lock(raw)
        record = value["images"][0]
        source = f"docker.io/library/demo@{record['digest']}"
        tag = f"registry.rupan.dev/{record['destination_repository']}:1.0"
        client = FakeClient({source: raw, tag: conflicting})
        report = copy_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "failed")
        self.assertIn("refusing to overwrite", report["images"][0]["errors"][0])
        self.assertEqual(client.copies, [])

    def test_verify_detects_platform_mismatch(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        value["images"][0]["platforms"] = ["linux/amd64"]
        record = value["images"][0]
        destination = (
            f"registry.rupan.dev/{record['destination_repository']}@{record['digest']}"
        )
        report = verify_lock(FakeClient({destination: raw}), value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "failed")
        self.assertIn("platform mismatch", report["images"][0]["errors"][0])


class PolicyAndNodeConfigTest(unittest.TestCase):
    def test_access_control_is_lock_derived_and_least_privilege(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        value["images"][0]["kind"] = "first-party"
        value["images"][0]["destination_repository"] = "apps/demo"
        policy = render_access_control(value)
        repositories = policy["repositories"]
        self.assertEqual(repositories["**"]["defaultPolicy"], [])
        self.assertNotIn("anonymousPolicy", json.dumps(policy))
        self.assertEqual(
            repositories["apps/demo"]["policies"],
            [
                {"users": ["node"], "actions": ["read"]},
                {
                    "users": ["publisher-demo"],
                    "actions": ["read", "create", "update"],
                },
            ],
        )
        self.assertEqual(
            repositories["upstream/**"]["policies"][1],
            {
                "users": ["importer"],
                "actions": ["read", "create", "update"],
            },
        )
        self.assertEqual(policy["adminPolicy"]["users"], ["maintenance"])

    def test_policy_requires_exact_local_digest_or_tested_exception(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops").mkdir()
            path = root / "gitops/app.yaml"
            path.write_text("image: nginx:1.27\n", encoding="utf-8")
            report = check_consumers(root, value)
            self.assertEqual(report["status"], "failed")
            value["mirror_exceptions"] = [
                {
                    "source_reference": "docker.io/library/nginx:1.27",
                    "consumer": "gitops/app.yaml:*",
                    "tested": True,
                    "evidence": "docs/evidence.md",
                    "reason": "generated upstream reference",
                }
            ]
            report = check_consumers(root, value)
            self.assertEqual(report["status"], "ok")

    def test_check_consumers_uses_inventory_based_validation(self) -> None:
        raw = manifest()
        value = valid_lock(raw)
        destination = (
            f"registry.rupan.dev/{value['images'][0]['destination_repository']}"
            f"@{value['images'][0]['digest']}"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops").mkdir()
            workload = root / "gitops/app.yaml"
            workload.write_text(
                f"image: {destination}\n",
                encoding="utf-8",
            )
            report = check_consumers(root, value)
            self.assertEqual(report["status"], "ok")

            workload.write_text(
                "image: docker.io/library/demo:1.0\n",
                encoding="utf-8",
            )
            report = check_consumers(root, value)
            self.assertEqual(report["status"], "failed")
            self.assertIn("no tested mirror exception", report["errors"][0]["error"])

    def test_check_consumers_accepts_locked_source_and_mirror_digest_with_tag(
        self,
    ) -> None:
        raw = manifest()
        value = valid_lock(raw)
        source = value["images"][0]["source"]["reference"]
        mirror = (
            f"registry.rupan.dev/{value['images'][0]['destination_repository']}:1.0"
            f"@{value['images'][0]['digest']}"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops").mkdir()
            (root / "gitops/app.yaml").write_text(
                f"image: {source}\nimage: {mirror}\n", encoding="utf-8"
            )

            report = check_consumers(root, value)

        self.assertEqual(report["status"], "ok")

    def test_node_config_uses_exact_rewrites_tls_and_private_atomic_output(
        self,
    ) -> None:
        raw = manifest()
        value = valid_lock(raw)
        payload = render_node_config(value, "node-reader", 'secret:with"quotes')
        self.assertIn('"^library/demo$": "upstream/docker.io/library/demo"', payload)
        self.assertIn('endpoint:\n      - "https://registry.rupan.dev"', payload)
        self.assertIn("insecure_skip_verify: false", payload)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            lock_path = root / "lock.json"
            password_path = root / "password"
            output_path = root / "registries.yaml"
            lock_path.write_text(json.dumps(value), encoding="utf-8")
            password_path.write_text("top-secret\n", encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = main(
                    [
                        "node-config",
                        "--lock",
                        str(lock_path),
                        "--username",
                        "node-reader",
                        "--password-file",
                        str(password_path),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertNotIn("top-secret", stream.getvalue())
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertIn(
                'password: "top-secret"', output_path.read_text(encoding="utf-8")
            )

    def test_node_config_rejects_multiline_password(self) -> None:
        with self.assertRaisesRegex(RegistryError, "one nonempty line"):
            render_node_config(valid_lock(manifest()), "node", "one\ntwo")

    def test_node_config_does_not_rewrite_local_first_party_references(self) -> None:
        value = valid_lock(manifest())
        record = value["images"][0]
        record["kind"] = "first-party"
        record["source"].update(
            {
                "registry": "registry.rupan.dev",
                "repository": "apps/apolline",
                "reference": "registry.rupan.dev/apps/apolline@" + record["digest"],
            }
        )
        record["destination_repository"] = "apps/apolline"
        payload = render_node_config(value, "node-reader", "secret")
        self.assertNotIn('"registry.rupan.dev":\n    endpoint:', payload)


def first_party_record(
    raw: bytes, *, tag: str | None, repository: str = "jeff7712/rupan-dev"
) -> dict[str, Any]:
    expected = digest(raw)
    reference = f"ghcr.io/{repository}:{tag}@{expected}" if tag else None
    return {
        "id": "first-party-jeff7712-rupan-dev-157d71f1966e",
        "kind": "first-party",
        "source": {
            "registry": "ghcr.io",
            "repository": repository,
            "tag": tag,
            "digest": expected,
            "reference": reference or f"ghcr.io/{repository}@{expected}",
        },
        "destination_repository": "apps/rupan-dev",
        "consumers": ["gitops/websites/rupan-dev/deployment.yaml:22"],
        "producer": {
            "location": f"external-image-repository:ghcr.io/{repository}",
            "owner": "jeff7712",
            "pipeline_status": "unverified",
        },
        "retention_class": "deployed",
        "digest": expected,
        "media_type": "application/vnd.oci.image.index.v1+json",
        "platforms": ["linux/amd64", "unknown/unknown"],
        "destination_tags": (
            [tag, f"retention-deployed-{expected[7:23]}"]
            if tag
            else [f"retention-deployed-{expected[7:23]}"]
        ),
        "resolution": {"status": "source-registry-verified"},
        "referrers": {"required": [], "source_status": "not-enumerated"},
        "authenticity": {"status": "unsupported", "reason": "no policy"},
    }


def first_party_lock(raw: bytes, *, tag: str | None = "0.0.11") -> dict[str, Any]:
    value = valid_lock(raw)
    value["images"] = [first_party_record(raw, tag=tag)]
    return value


class PromoteFakeClient(FakeClient):
    def __init__(self, manifests: dict[str, bytes], tags: dict[str, list[str]]) -> None:
        super().__init__(manifests)
        self.tags = tags
        self.listed: list[str] = []

    def list_tags(self, repository: str, *, destination: bool = False) -> list[str]:
        del destination
        self.listed.append(repository)
        return sorted(self.tags.get(repository, []))


class PromoteFirstPartyTests(unittest.TestCase):
    def test_selects_newest_numeric_tag(self) -> None:
        self.assertEqual(
            select_promotion_candidate(["0.0.9", "0.0.12", "0.0.3"], "0.0.9"),
            "0.0.12",
        )
        self.assertIsNone(select_promotion_candidate(["latest", "edge"], "0.0.9"))
        self.assertIsNone(select_promotion_candidate([], "0.0.9"))

    def test_tracks_mutable_latest_tag(self) -> None:
        self.assertEqual(
            select_promotion_candidate(["latest", "0.0.99"], "latest"), "latest"
        )

    def test_skips_up_to_date_record(self) -> None:
        raw = manifest()
        lock = first_party_lock(raw)
        client = PromoteFakeClient(
            {"ghcr.io/jeff7712/rupan-dev:0.0.11": raw},
            {"ghcr.io/jeff7712/rupan-dev": ["0.0.10", "0.0.11"]},
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(summary["promoted"], [])
        self.assertEqual(len(summary["skipped"]), 1)
        self.assertEqual(client.copies, [])
        self.assertEqual(lock["images"][0]["source"]["tag"], "0.0.11")

    def test_promotes_newest_tag_already_published(self) -> None:
        old = manifest()
        new = manifest([("linux", "amd64"), ("linux", "arm64")])
        new_digest = digest(new)
        lock = first_party_lock(old)
        client = PromoteFakeClient(
            {
                "ghcr.io/jeff7712/rupan-dev:0.0.12": new,
                "registry.rupan.dev/apps/rupan-dev:0.0.12": new,
            },
            {"ghcr.io/jeff7712/rupan-dev": ["0.0.11", "0.0.12"]},
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(len(summary["promoted"]), 1)
        item = summary["promoted"][0]
        self.assertEqual(item["tag"], "0.0.12")
        self.assertEqual(item["digest"], new_digest)
        self.assertEqual(client.copies, [])
        self.assertEqual(summary["migrated"], [])
        record = lock["images"][0]
        self.assertEqual(record["digest"], new_digest)
        self.assertEqual(
            record["source"]["reference"],
            f"registry.rupan.dev/apps/rupan-dev:0.0.12@{new_digest}",
        )
        self.assertEqual(
            record["destination_tags"],
            sorted(["0.0.12", f"retention-deployed-{new_digest[7:23]}"]),
        )
        self.assertEqual(record["resolution"], {"status": "source-registry-verified"})
        self.assertEqual(validate_lock(lock), [])

    def test_migrates_up_to_date_record_to_local_source(self) -> None:
        raw = manifest()
        lock = first_party_lock(raw)
        client = PromoteFakeClient(
            {
                "ghcr.io/jeff7712/rupan-dev:0.0.11": raw,
                "registry.rupan.dev/apps/rupan-dev:0.0.11": raw,
            },
            {
                "ghcr.io/jeff7712/rupan-dev": ["0.0.11"],
                "registry.rupan.dev/apps/rupan-dev": ["0.0.11"],
            },
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(summary["promoted"], [])
        self.assertEqual(summary["migrated"], ["apps/rupan-dev"])
        record = lock["images"][0]
        self.assertEqual(record["source"]["registry"], "registry.rupan.dev")
        self.assertEqual(record["source"]["repository"], "apps/rupan-dev")
        self.assertEqual(
            record["source"]["reference"],
            f"registry.rupan.dev/apps/rupan-dev:0.0.11@{digest(raw)}",
        )
        self.assertEqual(client.copies, [])
        self.assertEqual(validate_lock(lock), [])

    def test_migrates_untagged_record_and_promotes_local_candidate(self) -> None:
        old = manifest()
        new = manifest([("linux", "amd64")])
        new_digest = digest(new)
        lock = first_party_lock(old, tag=None)
        client = PromoteFakeClient(
            {"registry.rupan.dev/apps/rupan-dev:0.0.3": new},
            {"registry.rupan.dev/apps/rupan-dev": ["0.0.3", "latest"]},
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(summary["migrated"], ["apps/rupan-dev"])
        self.assertEqual(len(summary["promoted"]), 1)
        self.assertEqual(summary["promoted"][0]["tag"], "0.0.3")
        self.assertEqual(summary["promoted"][0]["digest"], new_digest)
        record = lock["images"][0]
        self.assertEqual(record["source"]["registry"], "registry.rupan.dev")
        self.assertEqual(record["source"]["tag"], "0.0.3")
        self.assertEqual(record["digest"], new_digest)
        self.assertEqual(client.copies, [])
        self.assertEqual(validate_lock(lock), [])

    def test_mismatched_destination_tag_is_not_overwritten(self) -> None:
        old = manifest()
        new = manifest([("linux", "amd64")])
        other = manifest([("linux", "arm64")])
        lock = first_party_lock(old)
        client = PromoteFakeClient(
            {
                "ghcr.io/jeff7712/rupan-dev:0.0.12": new,
                "registry.rupan.dev/apps/rupan-dev:0.0.12": other,
            },
            {"ghcr.io/jeff7712/rupan-dev": ["0.0.11", "0.0.12"]},
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(summary["promoted"], [])
        self.assertEqual(len(summary["skipped"]), 1)
        self.assertIn("refusing to overwrite", summary["skipped"][0]["reason"])
        self.assertEqual(client.copies, [])
        self.assertEqual(lock["images"][0]["digest"], digest(old))

    def test_resolve_inspects_local_sources_with_destination_creds(self) -> None:
        raw = manifest()
        expected = digest(raw)
        inventory = {
            "schema_version": 1,
            "kind": "registry-inventory",
            "source_revision": "a" * 40,
            "unresolved_inputs": [],
            "images": [
                {
                    "id": "first-party-apps-demo-abc123",
                    "kind": "first-party",
                    "retention_class": "deployed",
                    "consumers": ["gitops/demo.yaml:10"],
                    "destination_repository": "apps/demo",
                    "producer": {
                        "location": "external-image-repository:registry.rupan.dev/apps/demo",
                        "owner": "jeff7712",
                        "pipeline_status": "unverified",
                    },
                    "source": {
                        "registry": "registry.rupan.dev",
                        "repository": "apps/demo",
                        "tag": "0.0.7",
                        "digest": None,
                        "reference": "registry.rupan.dev/apps/demo:0.0.7",
                    },
                }
            ],
        }
        client = FakeClient({"registry.rupan.dev/apps/demo:0.0.7": raw})
        calls: list[tuple[str, bool]] = []
        original = client.raw_manifest

        def recording(reference: str, *, destination: bool = False) -> bytes:
            calls.append((reference, destination))
            return original(reference, destination=destination)

        with patch.object(client, "raw_manifest", side_effect=recording):
            lock, unresolved = resolve_inventory(
                inventory, client, destination_registry="registry.rupan.dev"
            )
        self.assertIn(("registry.rupan.dev/apps/demo:0.0.7", True), calls)
        self.assertEqual(unresolved, [])
        self.assertEqual(lock["images"][0]["digest"], expected)
        self.assertEqual(
            lock["images"][0]["resolution"], {"status": "source-registry-verified"}
        )

    def test_skips_candidate_the_producer_has_not_published(self) -> None:
        old = manifest()
        new = manifest([("linux", "amd64")])
        lock = first_party_lock(old)
        client = PromoteFakeClient(
            {"ghcr.io/jeff7712/rupan-dev:0.0.12": new},
            {"ghcr.io/jeff7712/rupan-dev": ["0.0.11", "0.0.12"]},
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(summary["promoted"], [])
        self.assertEqual(len(summary["skipped"]), 1)
        self.assertIn("has not published", summary["skipped"][0]["reason"])
        self.assertEqual(client.copies, [])
        self.assertEqual(lock["images"][0]["source"]["tag"], "0.0.11")

    def test_promotes_latest_on_digest_change(self) -> None:
        old = manifest()
        new = manifest([("linux", "amd64")])
        new_digest = digest(new)
        lock = first_party_lock(old, tag="latest")
        client = PromoteFakeClient(
            {
                "ghcr.io/jeff7712/rupan-dev:latest": new,
                "registry.rupan.dev/apps/rupan-dev:latest": new,
            },
            {"ghcr.io/jeff7712/rupan-dev": ["latest", "0.0.99"]},
        )
        summary = promote_first_party_lock(client, lock)
        self.assertEqual(len(summary["promoted"]), 1)
        self.assertEqual(summary["promoted"][0]["tag"], "latest")
        self.assertEqual(lock["images"][0]["digest"], new_digest)
        self.assertEqual(client.copies, [])

    def test_cli_dry_run_writes_no_files(self) -> None:
        old = manifest()
        new = manifest([("linux", "amd64")])
        lock = first_party_lock(old)
        client = PromoteFakeClient(
            {
                "ghcr.io/jeff7712/rupan-dev:0.0.12": new,
                "registry.rupan.dev/apps/rupan-dev:0.0.12": new,
            },
            {"ghcr.io/jeff7712/rupan-dev": ["0.0.11", "0.0.12"]},
        )
        with tempfile.TemporaryDirectory() as directory:
            lock_path = pathlib.Path(directory) / "lock.json"
            inventory_path = pathlib.Path(directory) / "inventory.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            inventory_path.write_text("{}", encoding="utf-8")
            with patch("scripts.registry.cli.OciClient", return_value=client):
                result = main(
                    [
                        "--root",
                        directory,
                        "promote",
                        "--lock",
                        str(lock_path),
                        "--inventory",
                        str(inventory_path),
                        "--dry-run",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8"))["images"][0][
                    "source"
                ]["tag"],
                "0.0.11",
            )
            self.assertEqual(inventory_path.read_text(encoding="utf-8"), "{}")

    def test_transient_observed_errors_are_tolerated(self) -> None:
        previous = "sha256:" + "a" * 64
        errors = [
            {
                "consumer": "observed:rupan-dev/website-deploy-xxx/web",
                "reference": f"registry.rupan.dev/apps/rupan-dev@{previous}",
                "error": "stale",
            },
            {
                "consumer": "observed:other/app/web",
                "reference": "registry.rupan.dev/apps/other@sha256:" + "b" * 64,
                "error": "stale",
            },
            {
                "consumer": "gitops/websites/rupan-dev/deployment.yaml:22",
                "reference": f"registry.rupan.dev/apps/rupan-dev@{previous}",
                "error": "stale",
            },
        ]
        remaining = filter_transient_observed_errors(errors, {previous})
        self.assertEqual(len(remaining), 2)
        self.assertTrue(
            all(
                item["consumer"] != "observed:rupan-dev/website-deploy-xxx/web"
                for item in remaining
            )
        )

    def test_rewrites_explained_observations(self) -> None:
        old_digest = "sha256:" + "a" * 64
        new_digest = "sha256:" + "b" * 64
        other = "registry.rupan.dev/apps/other@sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            observed = root / "registry/observed-images.json"
            observed.parent.mkdir(parents=True)
            observed.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "consumers": ["rupan-dev/website-deploy-xxx/web"],
                                "reference": f"registry.rupan.dev/apps/rupan-dev@{old_digest}",
                            },
                            {"consumers": ["other/app/web"], "reference": other},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            count = rewrite_observed_digests(
                root,
                destination_registry="registry.rupan.dev",
                destination_repository="apps/rupan-dev",
                previous_digest=old_digest,
                digest=new_digest,
            )
            self.assertEqual(count, 1)
            text = observed.read_text(encoding="utf-8")
            self.assertIn(f"registry.rupan.dev/apps/rupan-dev@{new_digest}", text)
            self.assertNotIn(old_digest, text)
            self.assertIn(other, text)

    def test_observed_rewrite_tolerates_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            count = rewrite_observed_digests(
                pathlib.Path(directory),
                destination_registry="registry.rupan.dev",
                destination_repository="apps/rupan-dev",
                previous_digest="sha256:" + "a" * 64,
                digest="sha256:" + "b" * 64,
            )
            self.assertEqual(count, 0)

    def test_only_filter_limits_records(self) -> None:
        raw = manifest()
        lock = first_party_lock(raw)
        client = PromoteFakeClient({}, {"ghcr.io/jeff7712/rupan-dev": ["0.0.11"]})
        summary = promote_first_party_lock(client, lock, only={"apps/other"})
        self.assertEqual(summary["promoted"], [])
        self.assertEqual(summary["skipped"], [])
        self.assertEqual(client.listed, [])

    def test_list_tags_uses_source_authfile(self) -> None:
        client = OciClient()
        client.inspect_tool = "skopeo"
        with (
            patch.dict(
                os.environ, {"REGISTRY_SOURCE_AUTH_FILE": "/tmp/auth.json"}, clear=False
            ),
            patch.object(
                client,
                "_run",
                return_value=json.dumps({"Tags": ["0.0.2", "0.0.1"]}).encode(),
            ) as run,
        ):
            self.assertEqual(
                client.list_tags("ghcr.io/jeff7712/rupan-dev"), ["0.0.1", "0.0.2"]
            )
        command = run.call_args.args[0]
        self.assertEqual(
            command[:4], ["skopeo", "--registries-conf", "/dev/null", "list-tags"]
        )
        self.assertIn("/tmp/auth.json", command)

    def test_discovers_gitops_consumers_and_skips_observed(self) -> None:
        old_digest = "sha256:" + "a" * 64
        new_digest = "sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "gitops/websites/rupan-dev/deployment.yaml"
            target.parent.mkdir(parents=True)
            target.write_text(
                f"image: registry.rupan.dev/apps/rupan-dev@{old_digest}\n",
                encoding="utf-8",
            )
            updated = rewrite_consumer_digests(
                root,
                ["observed:rupan-dev/website-deploy-xxx/web"],
                destination_registry="registry.rupan.dev",
                destination_repository="apps/rupan-dev",
                previous_digest=old_digest,
                digest=new_digest,
            )
            self.assertEqual(updated, ["gitops/websites/rupan-dev/deployment.yaml"])
            self.assertIn(new_digest, target.read_text(encoding="utf-8"))

    def test_rewrite_rejects_stale_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "gitops/websites/rupan-dev/deployment.yaml"
            target.parent.mkdir(parents=True)
            target.write_text(
                "image: registry.rupan.dev/apps/rupan-dev@sha256:other\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RegistryError, "no longer pins"):
                rewrite_consumer_digests(
                    root,
                    ["gitops/websites/rupan-dev/deployment.yaml:22"],
                    destination_registry="registry.rupan.dev",
                    destination_repository="apps/rupan-dev",
                    previous_digest="sha256:" + "a" * 64,
                    digest="sha256:" + "b" * 64,
                )


if __name__ == "__main__":
    unittest.main()
