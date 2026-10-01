from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEBHOOK_DIR = ROOT / "gitops" / "flux-webhook"
CLUSTER_DIR = ROOT / "gitops" / "clusters" / "homelab-01"


def load_single(path: pathlib.Path) -> dict:
    docs = [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    assert len(docs) == 1, f"{path} must contain exactly one document"
    return docs[0]


class FluxWebhookContract(unittest.TestCase):
    def test_receiver_reconciles_root_sources(self) -> None:
        receiver = load_single(WEBHOOK_DIR / "receiver.yaml")
        self.assertEqual(receiver["apiVersion"], "notification.toolkit.fluxcd.io/v1")
        self.assertEqual(receiver["kind"], "Receiver")
        self.assertEqual(receiver["metadata"]["name"], "gitlab-push")
        self.assertEqual(receiver["metadata"]["namespace"], "flux-system")
        spec = receiver["spec"]
        self.assertEqual(spec["type"], "gitlab")
        # Flux matches GitLab's raw X-Gitlab-Event names, not generic kinds.
        self.assertEqual(sorted(spec["events"]), ["Push Hook", "Tag Push Hook"])
        self.assertEqual(spec["secretRef"], {"name": "receiver-token"})
        resources = {(r["kind"], r["name"], r["namespace"]) for r in spec["resources"]}
        self.assertEqual(
            resources,
            {
                ("GitRepository", "flux-system", "flux-system"),
                ("Kustomization", "flux-system", "flux-system"),
            },
        )

    def test_token_comes_from_gitlab_not_plaintext(self) -> None:
        token = load_single(WEBHOOK_DIR / "receiver-token.yaml")
        self.assertEqual(token["kind"], "ExternalSecret")
        self.assertEqual(token["metadata"]["namespace"], "flux-system")
        self.assertEqual(token["spec"]["target"]["name"], "receiver-token")
        store = token["spec"]["secretStoreRef"]
        self.assertEqual(
            (store["kind"], store["name"]), ("ClusterSecretStore", "gitlab-project")
        )
        keys = [entry["secretKey"] for entry in token["spec"]["data"]]
        self.assertIn("token", keys)
        for entry in token["spec"]["data"]:
            self.assertIn("remoteRef", entry)
        self.assertNotIn("stringData", token)

    def test_forgejo_receiver_reconciles_root_sources(self) -> None:
        receiver = load_single(WEBHOOK_DIR / "forgejo-receiver.yaml")
        self.assertEqual(receiver["apiVersion"], "notification.toolkit.fluxcd.io/v1")
        self.assertEqual(receiver["kind"], "Receiver")
        self.assertEqual(receiver["metadata"]["name"], "forgejo-push")
        self.assertEqual(receiver["metadata"]["namespace"], "flux-system")
        spec = receiver["spec"]
        self.assertEqual(spec["type"], "generic")
        self.assertEqual(spec["secretRef"], {"name": "receiver-token"})
        resources = {(r["kind"], r["name"], r["namespace"]) for r in spec["resources"]}
        self.assertEqual(
            resources,
            {
                ("GitRepository", "flux-system", "flux-system"),
                ("Kustomization", "flux-system", "flux-system"),
            },
        )

    def test_cluster_wiring_depends_on_eso(self) -> None:
        wiring = load_single(CLUSTER_DIR / "flux-webhook.yaml")
        self.assertEqual(wiring["kind"], "Kustomization")
        self.assertEqual(wiring["spec"]["path"], "./gitops/flux-webhook")
        self.assertEqual(
            wiring["spec"]["sourceRef"],
            {"kind": "GitRepository", "name": "flux-system"},
        )
        self.assertIn({"name": "eso"}, wiring["spec"]["dependsOn"])
        cluster = load_single(CLUSTER_DIR / "kustomization.yaml")
        self.assertIn("flux-webhook.yaml", cluster["resources"])
        bundle = load_single(WEBHOOK_DIR / "kustomization.yaml")
        self.assertEqual(
            sorted(bundle["resources"]),
            ["forgejo-receiver.yaml", "receiver-token.yaml", "receiver.yaml"],
        )


if __name__ == "__main__":
    unittest.main()
