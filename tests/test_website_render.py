from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.registry.core import rewrite_consumer_digests
from scripts.websites.render import (
    FLUX_WEBSITES,
    HEADER,
    WEBSITES,
    SpecError,
    load_sites,
    parse_spec,
    plan,
    registration_errors,
    render,
)

DIGEST = "sha256:" + "a" * 64
IMAGE = f"registry.rupan.dev/apps/demo@{DIGEST}"


def spec(**fields: object) -> dict[str, object]:
    return {"profile": "static", "image": IMAGE, **fields}


def documents(files: dict[str, str]) -> dict[str, dict]:
    return {name: yaml.safe_load(text) for name, text in files.items()}


class SiteSpecValidation(unittest.TestCase):
    def assert_rejected(self, raw: object, message: str) -> None:
        with self.assertRaisesRegex(SpecError, message):
            parse_spec("demo", raw)

    def test_minimal_static_spec_takes_profile_defaults(self) -> None:
        site = parse_spec("demo", spec())
        self.assertEqual((site.port, site.replicas), (8080, 1))
        self.assertEqual((site.memory_request, site.memory_limit), ("128Mi", "256Mi"))
        self.assertEqual(site.service_name, "demo-svc")

    def test_image_must_be_a_digest_pinned_first_party_reference(self) -> None:
        for image in (
            "registry.rupan.dev/apps/demo:latest",
            "ghcr.io/example/demo@" + DIGEST,
            "registry.rupan.dev/upstream/demo@" + DIGEST,
            f"{IMAGE}0",
        ):
            self.assert_rejected(spec(image=image), "image must be")

    def test_unknown_keys_are_rejected(self) -> None:
        self.assert_rejected(spec(env={"A": "b"}), "unknown keys")

    def test_static_profile_fixes_the_port(self) -> None:
        self.assert_rejected(spec(port=3000), "always listens on 8080")

    def test_server_profile_requires_an_unprivileged_port(self) -> None:
        self.assert_rejected(spec(profile="server"), "unprivileged integer port")
        self.assert_rejected(spec(profile="server", port=80), "unprivileged")
        self.assert_rejected(spec(profile="server", port="3000"), "unprivileged")

    def test_resource_knobs_are_bounded(self) -> None:
        self.assert_rejected(spec(replicas=0), "replicas")
        self.assert_rejected(spec(replicas=True), "replicas")
        self.assert_rejected(spec(memory={"limit": "256Mi"}), "exactly request")
        self.assert_rejected(
            spec(memory={"request": "1Gi", "limit": "512Mi"}), "exceeds"
        )
        self.assert_rejected(
            spec(memory={"request": "128M", "limit": "256Mi"}), "256Mi or 1Gi"
        )

    def test_extra_manifests_must_be_plain_sibling_files(self) -> None:
        for extra in ("../secret.yaml", "deployment.yaml", "notes.txt"):
            self.assert_rejected(spec(extra_manifests=[extra]), "plain sibling")


class SiteRender(unittest.TestCase):
    def test_every_generated_file_carries_the_header(self) -> None:
        for text in render(parse_spec("demo", spec())).values():
            self.assertTrue(text.startswith(HEADER))

    def test_static_and_server_profiles_differ_only_in_probe_and_port(self) -> None:
        static = documents(render(parse_spec("demo", spec())))
        server = documents(
            render(parse_spec("demo", spec(profile="server", port=3000)))
        )
        web = static["deployment.yaml"]["spec"]["template"]["spec"]["containers"][0]
        app = server["deployment.yaml"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(
            web["readinessProbe"]["httpGet"], {"path": "/", "port": "http"}
        )
        self.assertEqual(app["readinessProbe"]["tcpSocket"], {"port": "http"})
        self.assertEqual(app["ports"], [{"name": "http", "containerPort": 3000}])
        for probe in ("readinessProbe", "livenessProbe"):
            web.pop(probe), app.pop(probe)
        web.pop("ports"), app.pop("ports")
        self.assertEqual(web, app)
        self.assertEqual(static["service.yaml"], server["service.yaml"])

    def test_service_selects_the_deployment_pods_through_the_named_port(self) -> None:
        files = documents(render(parse_spec("demo", spec())))
        pod = files["deployment.yaml"]["spec"]["template"]
        service = files["service.yaml"]["spec"]
        self.assertEqual(service["selector"], pod["metadata"]["labels"])
        self.assertEqual(
            files["deployment.yaml"]["spec"]["selector"]["matchLabels"],
            pod["metadata"]["labels"],
        )
        self.assertEqual(service["ports"][0]["targetPort"], "http")

    def test_multiple_replicas_add_a_disruption_budget(self) -> None:
        single = render(parse_spec("demo", spec()))
        double = render(parse_spec("demo", spec(replicas=2)))
        self.assertNotIn("pdb.yaml", single)
        self.assertEqual(yaml.safe_load(double["pdb.yaml"])["spec"]["minAvailable"], 1)
        self.assertIn(
            "pdb.yaml", yaml.safe_load(double["kustomization.yaml"])["resources"]
        )

    def test_protected_namespace_is_never_pruned(self) -> None:
        files = documents(render(parse_spec("demo", spec(protect_namespace=True))))
        self.assertEqual(
            files["namespace.yaml"]["metadata"]["annotations"],
            {"kustomize.toolkit.fluxcd.io/prune": "disabled"},
        )

    def test_extra_manifests_follow_generated_resources(self) -> None:
        files = documents(
            render(parse_spec("demo", spec(extra_manifests=["worker.yaml"])))
        )
        self.assertEqual(files["kustomization.yaml"]["resources"][-1:], ["worker.yaml"])


class SitePlan(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "gitops/websites/demo"
        self.directory.mkdir(parents=True)

    def write(self, site_spec: dict[str, object]) -> None:
        (self.directory / "site.yaml").write_text(yaml.safe_dump(site_spec))
        site = parse_spec("demo", site_spec)
        for name, text in render(site).items():
            (self.directory / name).write_text(text)

    def test_hand_edit_to_a_generated_file_is_stale(self) -> None:
        self.write(spec())
        path = self.directory / "deployment.yaml"
        path.write_text(path.read_text().replace("cpu: 500m", "cpu: 2"))
        writes, _, _ = plan(parse_spec("demo", spec()), self.directory)
        self.assertEqual(list(writes), ["deployment.yaml"])

    def test_dropping_replicas_deletes_the_disruption_budget(self) -> None:
        self.write(spec(replicas=2))
        _, deletes, _ = plan(parse_spec("demo", spec()), self.directory)
        self.assertEqual(deletes, ["pdb.yaml"])

    def test_unlisted_and_missing_files_are_reported(self) -> None:
        self.write(spec(extra_manifests=["worker.yaml"]))
        (self.directory / "stray.yaml").write_text("{}\n")
        _, _, unowned = plan(
            parse_spec("demo", spec(extra_manifests=["worker.yaml"])), self.directory
        )
        self.assertEqual(unowned, ["stray.yaml", "worker.yaml (missing)"])

    def test_registry_promotion_keeps_spec_and_render_in_step(self) -> None:
        self.write(spec())
        promoted = "sha256:" + "b" * 64
        updated = rewrite_consumer_digests(
            self.root,
            ["gitops/websites/demo/deployment.yaml:20"],
            destination_registry="registry.rupan.dev",
            destination_repository="apps/demo",
            previous_digest=DIGEST,
            digest=promoted,
        )
        self.assertEqual(
            sorted(updated),
            [
                "gitops/websites/demo/deployment.yaml",
                "gitops/websites/demo/site.yaml",
            ],
        )
        site = parse_spec(
            "demo", yaml.safe_load((self.directory / "site.yaml").read_text())
        )
        self.assertTrue(site.image.endswith(promoted))
        self.assertEqual(plan(site, self.directory), ({}, [], []))


class CommittedWebsites(unittest.TestCase):
    def test_committed_manifests_match_their_specs(self) -> None:
        sites = load_sites(WEBSITES)
        self.assertTrue(sites)
        for site in sites:
            with self.subTest(site=site.name):
                self.assertEqual(plan(site, WEBSITES / site.name), ({}, [], []))

    def test_every_site_is_registered_and_health_checked(self) -> None:
        self.assertEqual(
            registration_errors(load_sites(WEBSITES), WEBSITES, FLUX_WEBSITES), []
        )


if __name__ == "__main__":
    unittest.main()
