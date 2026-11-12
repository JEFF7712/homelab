"""Check effective ESO provider permissions and workload credential exposure."""

from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def render(directory: str) -> list[dict]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / directory)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)
    ]


class ClusterSecretIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.eso = render("gitops/eso")
        cls.pod = render("gitops/pod-agent")

    def test_master_store_is_only_usable_in_secret_source(self) -> None:
        master = next(
            d
            for d in self.eso
            if d["kind"] == "ClusterSecretStore"
            and d["metadata"]["name"] == "homelab-secrets"
        )
        self.assertEqual(
            master["spec"]["conditions"], [{"namespaces": ["secret-source"]}]
        )
        for doc in self.eso:
            if (
                doc["kind"] == "ExternalSecret"
                and doc["spec"]["secretStoreRef"]["name"] == "homelab-secrets"
            ):
                self.assertEqual(doc["metadata"]["namespace"], "secret-source")

    def test_each_domain_provider_can_only_read_its_own_projection(self) -> None:
        projections = {
            d["metadata"]["name"]: d
            for d in self.eso
            if d["kind"] == "ExternalSecret"
            and d["metadata"]["namespace"] == "secret-source"
        }
        roles = {d["metadata"]["name"]: d for d in self.eso if d["kind"] == "Role"}
        bindings = [d for d in self.eso if d["kind"] == "RoleBinding"]
        stores = [
            d
            for d in self.eso
            if d["kind"] == "ClusterSecretStore"
            and d["metadata"]["name"] != "homelab-secrets"
        ]
        self.assertGreaterEqual(len(stores), 14)
        readers = set()
        for store in stores:
            with self.subTest(store=store["metadata"]["name"]):
                (namespace,) = store["spec"]["conditions"][0]["namespaces"]
                provider = store["spec"]["provider"]["kubernetes"]
                reader = provider["auth"]["serviceAccount"]["name"]
                self.assertNotIn(reader, readers)
                readers.add(reader)
                grants = [
                    roles[b["roleRef"]["name"]]["rules"]
                    for b in bindings
                    if any(s["name"] == reader for s in b["subjects"])
                ]
                self.assertEqual(
                    grants,
                    [
                        [
                            {
                                "apiGroups": [""],
                                "resources": ["secrets"],
                                "resourceNames": ["homelab-" + namespace + "-values"],
                                "verbs": ["get"],
                            }
                        ]
                    ],
                )
                self.assertIn("homelab-" + namespace + "-values", projections)
                self.assertEqual(provider["remoteNamespace"], "secret-source")

    def test_all_declared_consumers_have_their_requested_source_properties(
        self,
    ) -> None:
        stores = {
            d["metadata"]["name"]: d
            for d in self.eso
            if d["kind"] == "ClusterSecretStore"
        }
        projections = {
            d["metadata"]["name"]: {i["secretKey"] for i in d["spec"]["data"]}
            for d in self.eso
            if d["kind"] == "ExternalSecret"
            and d["metadata"]["namespace"] == "secret-source"
        }
        for path in (ROOT / "gitops").rglob("*.yaml"):
            if (
                path.name.endswith(".sops.yaml")
                or "crds" in path.parts
                or path.name == "gotk-components.yaml"
            ):
                continue
            for doc in yaml.safe_load_all(path.read_text()):
                if not isinstance(doc, dict) or doc.get("kind") != "ExternalSecret":
                    continue
                ref = doc.get("spec", {}).get("secretStoreRef", {})
                if not ref.get("name", "").startswith("homelab-"):
                    continue
                store = stores[ref["name"]]
                namespace = doc["metadata"]["namespace"]
                self.assertIn(
                    namespace, store["spec"]["conditions"][0]["namespaces"], str(path)
                )
                if ref["name"] == "homelab-secrets":
                    continue
                for field in doc["spec"]["data"]:
                    remote = field["remoteRef"]
                    self.assertIn(
                        remote["property"], projections[remote["key"]], str(path)
                    )

    def test_sensitive_pod_agent_credentials_only_reach_their_callers(self) -> None:
        secrets = {
            d["spec"]["target"]["name"]: {i["secretKey"] for i in d["spec"]["data"]}
            for d in self.pod
            if d["kind"] == "ExternalSecret"
        }
        exposure: dict[str, set[str]] = {}
        for doc in self.pod:
            if doc["kind"] not in {"Deployment", "CronJob"}:
                continue
            name = doc["metadata"]["name"].removeprefix("pod-agent-")
            spec = (
                doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                if doc["kind"] == "CronJob"
                else doc["spec"]["template"]["spec"]
            )
            self.assertFalse(spec["automountServiceAccountToken"], name)
            for container in spec["containers"] + spec.get("initContainers", []):
                for source in container.get("envFrom", []):
                    if "secretRef" not in source:
                        continue
                    secret = source["secretRef"]["name"]
                    self.assertNotEqual(secret, "pod-agent-env")
                    for key in secrets[secret]:
                        exposure.setdefault(key, set()).add(name)
        self.assertEqual(exposure["MERCURY_API_TOKEN"], {"observe", "cycle"})
        self.assertEqual(
            exposure["DISTROJEFF_ZOHO_IMAP_PASSWORD"], {"email-watch", "cycle"}
        )
        self.assertEqual(exposure["DASHBOARD_TOKEN"], {"dashboard"})
        self.assertEqual(exposure["POD_AGENT_BACKUP_RECIPIENTS"], {"backup"})
        self.assertNotIn("TELEGRAM_BOT_TOKEN", exposure)
        self.assertNotIn("DARKBIT_PRINTIFY_API_TOKEN", exposure)
        self.assertEqual(exposure["DISCORD_OWNER_USER_IDS"], {"discord-bot"})

    def test_public_product_feed_has_no_credentials_or_database_mount(self) -> None:
        deployment = next(
            d
            for d in self.pod
            if d["kind"] == "Deployment" and d["metadata"]["name"] == "pod-agent-feed"
        )
        spec = deployment["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertNotIn("initContainers", spec)
        self.assertEqual(len(spec["containers"]), 1)
        container = spec["containers"][0]
        self.assertFalse(container.get("envFrom"))
        self.assertFalse(container.get("env"))
        self.assertEqual(
            container["volumeMounts"],
            [
                {
                    "name": "data",
                    "mountPath": "/feeds",
                    "subPath": "chatgpt-feeds",
                    "readOnly": True,
                }
            ],
        )
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        feed_secret = next(
            d
            for d in self.pod
            if d["kind"] == "ExternalSecret"
            and d["metadata"]["name"] == "pod-agent-feed-env"
        )
        self.assertEqual(
            {item["secretKey"] for item in feed_secret["spec"]["data"]},
            {"DISTROJEFF_ETSY_KEYSTRING", "DISTROJEFF_ETSY_SHARED_SECRET"},
        )

    def test_cnpg_local_promotion_removes_only_after_desired_refs_are_local(
        self,
    ) -> None:
        for domain in ("home-assistant", "analytics", "immich"):
            with self.subTest(domain=domain):
                cluster = next(
                    d for d in render("gitops/" + domain) if d["kind"] == "Cluster"
                )
                self.assertTrue(
                    cluster["spec"]["imageName"].startswith("registry.rupan.dev/")
                )
                self.assertIn("@sha256:", cluster["spec"]["imageName"])
        lock = json.loads((ROOT / "registry/images.lock.json").read_text())
        self.assertFalse(
            any(
                e.get("source_reference", "").startswith(
                    (
                        "ghcr.io/cloudnative-pg/",
                        "ghcr.io/tensorchord/cloudnative-vectorchord:",
                    )
                )
                for e in lock["mirror_exceptions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
