from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Any

import yaml

from scripts.verifier.websites import (
    Unreadable,
    key,
    load_tree,
    verify,
)

ROOT = Path(__file__).resolve().parent.parent


def reader(files: dict[str, str]):
    return files.get


def repo_files() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text()
        for path in (ROOT / "gitops/websites").rglob("*.yaml")
    }


def specs(files: dict[str, str]) -> set[str]:
    return {
        path.split("/")[2]
        for path in files
        if path.startswith("gitops/websites/") and path.endswith("/site.yaml")
    }


def dump(*docs: dict[str, Any]) -> str:
    return yaml.safe_dump_all(docs, sort_keys=False)


class Change:
    """A head tree derived from the committed websites, plus its changed paths."""

    def __init__(self) -> None:
        self.base = repo_files()
        self.head = dict(self.base)
        self.changed: set[str] = set()

    def write(self, path: str, text: str) -> None:
        self.head[path] = text
        self.changed.add(path)

    def delete(self, path: str) -> None:
        self.head.pop(path, None)
        self.changed.add(path)

    def edit(self, path: str, change) -> None:
        docs = list(yaml.safe_load_all(self.head[path]))
        for doc in docs:
            change(doc)
        self.write(path, dump(*docs))

    def findings(self):
        return verify(
            load_tree(reader(self.base)),
            load_tree(reader(self.head)),
            self.changed,
            specs(self.base),
            specs(self.head),
        )


def web(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["spec"]["template"]["spec"]["containers"][0]


class VerifierAcceptsDelegatedChanges(unittest.TestCase):
    def test_committed_tree_against_itself_is_clean(self) -> None:
        self.assertEqual(Change().findings(), [])

    def test_site_yaml_driven_memory_change_is_approved(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"]
            + "memory:\n  request: 192Mi\n  limit: 384Mi\n",
        )
        change.edit(
            "gitops/websites/darkbit/deployment.yaml",
            lambda d: web(d)["resources"].update(
                limits={"cpu": "500m", "memory": "384Mi"},
                requests={"cpu": "100m", "memory": "192Mi"},
            ),
        )
        self.assertEqual(change.findings(), [])

    def test_renderer_change_that_leaves_existing_renders_identical_is_approved(
        self,
    ) -> None:
        change = Change()
        # Reformatting a generated file without changing its objects.
        path = "gitops/websites/ism/service.yaml"
        change.write(path, dump(*yaml.safe_load_all(change.base[path])))
        self.assertEqual(change.findings(), [])

    def test_new_site_with_hardened_objects_is_approved(self) -> None:
        change = Change()
        for name in (
            "namespace",
            "network-policy",
            "deployment",
            "service",
            "kustomization",
            "site",
        ):
            source = change.base[f"gitops/websites/darkbit/{name}.yaml"]
            change.write(
                f"gitops/websites/zephyr/{name}.yaml",
                source.replace("darkbit", "zephyr"),
            )
        root = "gitops/websites/kustomization.yaml"
        change.write(root, change.base[root] + "  - zephyr\n")
        self.assertEqual(change.findings(), [])

    def test_storage_added_through_site_change_is_approved(self) -> None:
        change = Change()
        site = "gitops/websites/photography"
        change.write(
            f"{site}/site.yaml", change.base[f"{site}/site.yaml"] + "storage: 1Gi\n"
        )
        change.write(
            f"{site}/volume.yaml",
            dump(
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": "photography-data",
                        "namespace": "photography",
                        "annotations": {
                            "kustomize.toolkit.fluxcd.io/prune": "disabled"
                        },
                    },
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "storageClassName": "nfs-cluster",
                        "resources": {"requests": {"storage": "1Gi"}},
                    },
                }
            ),
        )
        change.edit(
            f"{site}/namespace.yaml",
            lambda d: (
                d["metadata"]
                .setdefault("annotations", {})
                .update({"kustomize.toolkit.fluxcd.io/prune": "disabled"})
            ),
        )
        change.write(
            f"{site}/kustomization.yaml",
            change.base[f"{site}/kustomization.yaml"] + "  - volume.yaml\n",
        )
        self.assertEqual(change.findings(), [])


class VerifierRejectsChangesOutsideAuthority(unittest.TestCase):
    def assert_finding(self, change: Change, severity: str, fragment: str) -> None:
        findings = change.findings()
        self.assertTrue(
            any(f.severity == severity and fragment in f.message for f in findings),
            [str(f) for f in findings],
        )

    def test_renderer_change_that_alters_an_untouched_site_is_rejected(self) -> None:
        change = Change()
        change.edit(
            "gitops/websites/ism/deployment.yaml",
            lambda d: web(d)["resources"]["limits"].update(cpu="1"),
        )
        self.assert_finding(change, "repair", "without a site.yaml change")

    def test_site_yaml_change_cannot_buy_a_privileged_container(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "privileged: true\n",
        )
        change.edit(
            "gitops/websites/darkbit/deployment.yaml",
            lambda d: web(d)["securityContext"].update(
                privileged=True, allowPrivilegeEscalation=True
            ),
        )
        self.assert_finding(change, "authority", "may not be privileged")
        self.assert_finding(change, "repair", "allowPrivilegeEscalation")

    def test_public_service_is_recorded_but_host_network_needs_authority(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        change.edit(
            "gitops/websites/darkbit/service.yaml",
            lambda d: d["spec"].update(type="LoadBalancer"),
        )
        change.edit(
            "gitops/websites/darkbit/deployment.yaml",
            lambda d: d["spec"]["template"]["spec"].update(hostNetwork=True),
        )
        self.assert_finding(change, "notice", "ClusterIP")
        self.assert_finding(change, "authority", "hostNetwork")

    def test_widened_ingress_is_allowed_and_recorded(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        change.edit(
            "gitops/websites/darkbit/network-policy.yaml",
            lambda d: d["spec"]["ingress"].append(
                {"from": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}
            ),
        )
        self.assert_finding(change, "notice", "ingress may only come from")
        self.assertFalse([f for f in change.findings() if f.severity != "notice"])

    def test_unpinned_image_is_rejected(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        change.edit(
            "gitops/websites/darkbit/deployment.yaml",
            lambda d: web(d).update(image="ghcr.io/x/y:latest"),
        )
        self.assert_finding(change, "repair", "digest-pinned")

    def test_object_in_another_namespace_is_rejected(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        change.edit(
            "gitops/websites/darkbit/service.yaml",
            lambda d: d["metadata"].update(namespace="kube-system"),
        )
        self.assert_finding(change, "repair", "must live in namespace darkbit")

    def test_unvetted_kind_needs_authority(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        change.write(
            "gitops/websites/darkbit/rbac.yaml",
            dump(
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "RoleBinding",
                    "metadata": {"name": "x", "namespace": "darkbit"},
                }
            ),
        )
        change.write(
            "gitops/websites/darkbit/kustomization.yaml",
            change.base["gitops/websites/darkbit/kustomization.yaml"]
            + "  - rbac.yaml\n",
        )
        self.assert_finding(change, "authority", "not a kind the verifier vets")

    def test_stateless_removal_is_recorded_but_stored_data_needs_authority(
        self,
    ) -> None:
        change = Change()
        root = "gitops/websites/kustomization.yaml"
        change.write(root, change.base[root].replace("  - darkbit\n", ""))
        self.assert_finding(change, "notice", "removing a site")
        change = Change()
        change.write(root, change.base[root].replace("  - uwagent\n", ""))
        self.assert_finding(change, "authority", "and its stored data")
        change = Change()
        site = "gitops/websites/uwagent"
        change.write(
            f"{site}/kustomization.yaml",
            change.base[f"{site}/kustomization.yaml"].replace("  - volume.yaml\n", ""),
        )
        self.assert_finding(change, "authority", "discards stored data")

    def test_claim_without_prune_protection_is_rejected(self) -> None:
        change = Change()
        site = "gitops/websites/photography"
        change.write(
            f"{site}/site.yaml", change.base[f"{site}/site.yaml"] + "storage: 1Gi\n"
        )
        change.write(
            f"{site}/volume.yaml",
            dump(
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": "d", "namespace": "photography"},
                    "spec": {
                        "storageClassName": "nfs-cluster",
                        "resources": {"requests": {"storage": "1Gi"}},
                    },
                }
            ),
        )
        change.write(
            f"{site}/kustomization.yaml",
            change.base[f"{site}/kustomization.yaml"] + "  - volume.yaml\n",
        )
        self.assert_finding(change, "repair", "prune: disabled")
        self.assert_finding(change, "repair", "prune-protected namespace")

    def test_existing_debt_does_not_block_a_routine_change_but_new_debt_does(
        self,
    ) -> None:
        change = Change()
        path = "gitops/websites/darkbit/deployment.yaml"
        change.base[path] = dump(
            *[
                {
                    **d,
                    "spec": {
                        **d["spec"],
                        "template": {
                            **d["spec"]["template"],
                            "spec": {
                                **d["spec"]["template"]["spec"],
                                "automountServiceAccountToken": True,
                            },
                        },
                    },
                }
                for d in yaml.safe_load_all(change.base[path])
            ]
        )
        change.head[path] = change.base[path]
        change.edit(path, lambda d: web(d).update(image=web(d)["image"][:-1] + "0"))
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        self.assertEqual(change.findings(), [])
        change.edit(path, lambda d: d["spec"]["template"]["spec"].update(hostPID=True))
        self.assert_finding(change, "authority", "hostPID")

    def test_other_workload_kinds_get_the_same_pod_checks(self) -> None:
        change = Change()
        change.write(
            "gitops/websites/darkbit/site.yaml",
            change.base["gitops/websites/darkbit/site.yaml"] + "\n",
        )
        job = {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": {"name": "sweep", "namespace": "darkbit"},
            "spec": {
                "jobTemplate": {
                    "spec": {
                        "template": {
                            "spec": {
                                "hostNetwork": True,
                                "containers": [{"name": "x", "image": "busybox"}],
                            }
                        }
                    }
                }
            },
        }
        change.write("gitops/websites/darkbit/cron.yaml", dump(job))
        change.write(
            "gitops/websites/darkbit/kustomization.yaml",
            change.base["gitops/websites/darkbit/kustomization.yaml"]
            + "  - cron.yaml\n",
        )
        self.assert_finding(change, "authority", "hostNetwork")
        self.assert_finding(change, "repair", "digest-pinned")

    def test_helper_kinds_are_recorded_but_identity_kinds_need_authority(self) -> None:
        for kind, severity in (
            ("CiliumNetworkPolicy", "notice"),
            ("ServiceAccount", "authority"),
            ("HTTPRoute", "authority"),
        ):
            change = Change()
            change.write(
                "gitops/websites/darkbit/site.yaml",
                change.base["gitops/websites/darkbit/site.yaml"] + "\n",
            )
            change.write(
                "gitops/websites/darkbit/extra.yaml",
                dump(
                    {
                        "apiVersion": "v1",
                        "kind": kind,
                        "metadata": {"name": "x", "namespace": "darkbit"},
                    }
                ),
            )
            change.write(
                "gitops/websites/darkbit/kustomization.yaml",
                change.base["gitops/websites/darkbit/kustomization.yaml"]
                + "  - extra.yaml\n",
            )
            with self.subTest(kind=kind):
                self.assert_finding(change, severity, "not a kind the verifier vets")

    def test_unchanged_unvetted_objects_in_a_touched_site_are_left_alone(self) -> None:
        change = Change()
        path = "gitops/websites/sovereign/site.yaml"
        change.write(path, change.base[path] + "\n")
        self.assertEqual(change.findings(), [])

    def test_kustomize_features_and_paths_outside_the_site_are_unreadable(self) -> None:
        for text in (
            "resources:\n  - deployment.yaml\npatches:\n  - path: x.yaml\n",
            "resources:\n  - ../darkbit/deployment.yaml\n",
            "resources:\n  - https://example.com/x.yaml\n",
        ):
            files = repo_files()
            files["gitops/websites/ism/kustomization.yaml"] = text
            with self.assertRaises(Unreadable):
                load_tree(reader(files))

    def test_oversized_file_is_unreadable(self) -> None:
        files = repo_files()
        files["gitops/websites/ism/service.yaml"] = "# " + "x" * 300_000
        with self.assertRaises(Unreadable):
            load_tree(reader(files))

    def test_duplicate_object_is_unreadable(self) -> None:
        files = repo_files()
        files["gitops/websites/ism/service.yaml"] += (
            "---\n" + files["gitops/websites/ism/service.yaml"]
        )
        with self.assertRaises(Unreadable):
            load_tree(reader(files))

    def test_keys_match_the_canonical_render_keys(self) -> None:
        site = load_tree(reader(repo_files()))["ism"]
        self.assertIn("Deployment/ism/website-deploy", site)
        self.assertIn("Namespace//ism", site)
        self.assertEqual(
            key(copy.deepcopy(site["Service/ism/ism-svc"])), "Service/ism/ism-svc"
        )


if __name__ == "__main__":
    unittest.main()
