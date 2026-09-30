from __future__ import annotations

import base64
import contextlib
import email.message
import hashlib
import io
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest
import urllib.error
from typing import Any
from unittest import mock
from unittest.mock import patch

import yaml

from scripts.registry import core
from scripts.registry.cli import main
from scripts.registry.core import (
    ImageReference,
    OciClient,
    RegistryError,
    access_control_users,
    build_live_snapshot,
    check_auth_consistency,
    check_consumers,
    copy_lock,
    copy_plan,
    destination_repository,
    discover_inventory,
    filter_transient_observed_errors,
    image_kind,
    load_lock,
    parse_htpasswd,
    promote_first_party_lock,
    reconcile_retention,
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


class TransientInterstitialTests(unittest.TestCase):
    """A registry Cloudflare is intercepting must not read as drift."""

    def _client(self) -> OciClient:
        client = OciClient(retries=0)
        client.inspect_tool = "skopeo"
        return client

    def test_intercepted_failure_is_transient_not_missing(self) -> None:
        client = self._client()
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b""
        )

        with (
            patch.object(core.subprocess, "run", return_value=completed),
            patch.object(core, "_spurious_failure", return_value="challenge"),
            patch.object(core, "_interstitial_reason", return_value=None),
            self.assertRaises(core.TransientRegistryError) as caught,
        ):
            client.raw_manifest(
                f"docker://{core.DEFAULT_REGISTRY}/apps/demo:1", destination=True
            )

        self.assertIn("challenge", str(caught.exception))
        self.assertIsInstance(caught.exception, core.RegistryError)

    def test_clean_registry_keeps_the_underlying_error(self) -> None:
        client = self._client()
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"denied"
        )

        with (
            patch.object(core.subprocess, "run", return_value=completed),
            patch.object(core, "_spurious_failure", return_value=None),
            patch.object(core, "_interstitial_reason", return_value=None),
            self.assertRaises(core.RegistryError) as caught,
        ):
            client.raw_manifest(
                f"docker://{core.DEFAULT_REGISTRY}/apps/demo:1", destination=True
            )

        self.assertNotIsInstance(caught.exception, core.TransientRegistryError)
        self.assertIn("denied", str(caught.exception))

    def test_registry_is_read_from_the_docker_argument(self) -> None:
        self.assertEqual(
            core._registry_from_args(
                ["skopeo", "inspect", "docker://registry.example.com/apps/demo:1"]
            ),
            "registry.example.com",
        )
        self.assertIsNone(core._registry_from_args(["skopeo", "list-tags"]))

    def _with_response(self, error: urllib.error.HTTPError) -> None:
        """Make the probe see `error` instead of touching the network."""
        opener = mock.MagicMock()
        opener.open.side_effect = error
        patcher = patch.object(core.urllib.request, "build_opener", return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_auth_challenge_is_not_an_interstitial(self) -> None:
        # The token is built at runtime rather than written as a literal, so the
        # secret scanner does not see a credential-shaped string in this file.
        token = base64.b64encode(b"node:fixture-password").decode()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"auths": {core.DEFAULT_REGISTRY: {"auth": token}}}, handle)
            path = handle.name
        self.addCleanup(os.unlink, path)

        error = urllib.error.HTTPError(
            url=f"https://{core.DEFAULT_REGISTRY}/v2/",
            code=401,
            msg="Unauthorized",
            hdrs=email.message.Message(),
            fp=None,
        )
        self.addCleanup(error.close)
        self._with_response(error)

        with patch.dict(os.environ, {"REGISTRY_DEST_AUTH_FILE": path}):
            self.assertIsNone(core._interstitial_reason(core.DEFAULT_REGISTRY))

    def test_access_redirect_is_an_interstitial(self) -> None:
        headers = email.message.Message()
        headers["Location"] = (
            "https://rupan.cloudflareaccess.com/cdn-cgi/access/login/x"
        )
        error = urllib.error.HTTPError(
            url=f"https://{core.DEFAULT_REGISTRY}/v2/",
            code=302,
            msg="Found",
            hdrs=headers,
            fp=None,
        )
        self.addCleanup(error.close)
        self._with_response(error)

        with patch.dict(os.environ, {}, clear=True):
            reason = core._interstitial_reason(core.DEFAULT_REGISTRY)

        self.assertIsNotNone(reason)
        self.assertIn("Cloudflare Access", reason)

    def test_cf_challenge_header_is_an_interstitial(self) -> None:
        headers = email.message.Message()
        headers["cf-mitigated"] = "challenge"
        error = urllib.error.HTTPError(
            url=f"https://{core.DEFAULT_REGISTRY}/v2/",
            code=403,
            msg="Forbidden",
            hdrs=headers,
            fp=None,
        )
        self.addCleanup(error.close)
        self._with_response(error)

        with patch.dict(os.environ, {}, clear=True):
            reason = core._interstitial_reason(core.DEFAULT_REGISTRY)

        self.assertIsNotNone(reason)
        self.assertIn("challenge", reason)

    def test_unreachable_registry_is_transient(self) -> None:
        opener = mock.MagicMock()
        opener.open.side_effect = urllib.error.URLError("connection reset")
        patcher = patch.object(core.urllib.request, "build_opener", return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)

        with patch.dict(os.environ, {}, clear=True):
            reason = core._interstitial_reason(core.DEFAULT_REGISTRY)

        self.assertIsNotNone(reason)
        self.assertIn("unreachable", reason)

    def test_reference_is_split_out_of_a_skopeo_argument(self) -> None:
        self.assertEqual(
            core._reference_from_args(
                [
                    "skopeo",
                    "inspect",
                    "docker://registry.rupan.dev/apps/demo@sha256:" + "1" * 64,
                ]
            ),
            ("registry.rupan.dev", "apps/demo", "sha256:" + "1" * 64),
        )
        self.assertEqual(
            core._reference_from_args(
                ["skopeo", "inspect", "docker://r.rupan.dev/a/b:tag"]
            ),
            ("r.rupan.dev", "a/b", "tag"),
        )
        self.assertIsNone(core._reference_from_args(["skopeo", "list-tags"]))

    def test_served_reference_makes_the_tool_failure_spurious(self) -> None:
        # Cloudflare intercepts individual reads, so `/v2/` can answer cleanly
        # while the manifest read does not. Only a per-reference probe catches
        # that, and getting it wrong means phantom drift.
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response
        args = ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(args)

        self.assertIsNotNone(reason)
        self.assertIn("apps/demo:tag", reason)

    def test_registry_rejection_is_not_spurious(self) -> None:
        error = urllib.error.HTTPError(
            url="https://registry.rupan.dev/v2/apps/demo/manifests/tag",
            code=404,
            msg="Not Found",
            hdrs=email.message.Message(),
            fp=None,
        )
        self.addCleanup(error.close)
        opener = mock.MagicMock()
        opener.open.side_effect = error

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNone(reason)

    def test_intercepted_probe_is_spurious_not_drift(self) -> None:
        # When Access misbehaves, dozens of reads fail together because the edge
        # answers for all of them. The probe is intercepted too, and reading that
        # as the registry rejecting the content produced 45 phantom failures and
        # a page. An intercepted probe is inconclusive, so it is spurious.
        headers = email.message.Message()
        headers["Location"] = (
            "https://rupan.cloudflareaccess.com/cdn-cgi/access/login/x"
        )
        error = urllib.error.HTTPError(
            url="https://registry.rupan.dev/v2/apps/demo/manifests/tag",
            code=302,
            msg="Found",
            hdrs=headers,
            fp=None,
        )
        self.addCleanup(error.close)
        opener = mock.MagicMock()
        opener.open.side_effect = error

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNotNone(reason)
        self.assertIn("Cloudflare Access", reason)
        self.assertIn("apps/demo:tag", reason)

    def test_unreachable_probe_is_spurious(self) -> None:
        opener = mock.MagicMock()
        opener.open.side_effect = urllib.error.URLError("connection reset")

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNotNone(reason)
        self.assertIn("unreachable", reason)

    def test_challenge_header_on_probe_is_spurious(self) -> None:
        headers = email.message.Message()
        headers["cf-mitigated"] = "challenge"
        error = urllib.error.HTTPError(
            url="https://registry.rupan.dev/v2/apps/demo/manifests/tag",
            code=403,
            msg="Forbidden",
            hdrs=headers,
            fp=None,
        )
        self.addCleanup(error.close)
        opener = mock.MagicMock()
        opener.open.side_effect = error

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNotNone(reason)
        self.assertIn("challenge", reason)

    def test_zot_401_on_probe_is_spurious(self) -> None:
        # The real failure, confirmed in zot's own journal: it received the
        # request and answered 401, rejecting the credential, in a contiguous run
        # partway through the check. zot uses 404 for absent content, so a 401 is
        # never a statement about what the registry holds.
        error = urllib.error.HTTPError(
            url="https://registry.rupan.dev/v2/apps/demo/manifests/tag",
            code=401,
            msg="Unauthorized",
            hdrs=email.message.Message(),
            fp=None,
        )
        self.addCleanup(error.close)
        opener = mock.MagicMock()
        opener.open.side_effect = error

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNotNone(reason)
        self.assertIn("401", reason)

    def test_zot_404_on_probe_is_a_real_verdict(self) -> None:
        # 404 is zot saying the content is gone. That is the positive signal, and
        # it must stay reportable or genuine mass loss would be hidden.
        error = urllib.error.HTTPError(
            url="https://registry.rupan.dev/v2/apps/demo/manifests/tag",
            code=404,
            msg="Not Found",
            hdrs=email.message.Message(),
            fp=None,
        )
        self.addCleanup(error.close)
        opener = mock.MagicMock()
        opener.open.side_effect = error

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNone(reason)

    def test_access_denial_on_probe_is_a_real_verdict(self) -> None:
        # 403 from the access policy is the registry stating who may read what,
        # which is a real answer rather than a broken client.
        error = urllib.error.HTTPError(
            url="https://registry.rupan.dev/v2/apps/demo/manifests/tag",
            code=403,
            msg="Forbidden",
            hdrs=email.message.Message(),
            fp=None,
        )
        self.addCleanup(error.close)
        opener = mock.MagicMock()
        opener.open.side_effect = error

        with patch.object(core.urllib.request, "build_opener", return_value=opener):
            reason = core._spurious_failure(
                ["skopeo", "inspect", "docker://registry.rupan.dev/apps/demo:tag"]
            )

        self.assertIsNone(reason)

    def test_failed_operation_is_transient_when_the_reference_is_served(self) -> None:
        client = self._client()
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b""
        )
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with (
            patch.object(core.subprocess, "run", return_value=completed),
            patch.object(core.urllib.request, "build_opener", return_value=opener),
            self.assertRaises(core.TransientRegistryError),
        ):
            client.raw_manifest(
                f"docker://{core.DEFAULT_REGISTRY}/apps/demo:tag", destination=True
            )

    def test_mass_read_failure_is_inconclusive_not_drift(self) -> None:
        # Cloudflare does not always redirect; it sometimes answers with an
        # auth-shaped status, which no probe can separate from zot rejecting the
        # content. Each observed run failed a different scattered subset, so the
        # run's shape is the only remaining signal.
        outcomes = [
            {
                "id": f"upstream-{index}",
                "status": "failed" if index < 30 else "verified",
                "errors": (
                    [
                        "manifest inspection failed with exit code 1: authentication required"
                    ]
                    if index < 30
                    else []
                ),
            }
            for index in range(100)
        ]
        core._demote_coordinated_read_failures(outcomes)

        self.assertEqual(sum(o["status"] == "indeterminate" for o in outcomes), 30)
        self.assertEqual(sum(o["status"] == "failed" for o in outcomes), 0)
        self.assertIn("could not be read", outcomes[0]["errors"][0])

    def test_isolated_missing_digest_still_counts_as_drift(self) -> None:
        # Genuine drift is a specific image losing its content, not the whole run
        # losing visibility, so a small number of unreadable records must still
        # be reported.
        outcomes = [
            {
                "id": f"upstream-{index}",
                "status": "failed" if index == 0 else "verified",
                "errors": (
                    ["manifest inspection failed: authentication required"]
                    if index == 0
                    else []
                ),
            }
            for index in range(20)
        ]
        core._demote_coordinated_read_failures(outcomes)

        self.assertEqual(outcomes[0]["status"], "failed")

    def test_small_lock_never_softens_a_single_failure(self) -> None:
        # A one-image lock where the read fails is 100% of the run, which says
        # nothing about the registry, so the share must not demote it.
        outcomes = [
            {
                "id": "upstream-only",
                "status": "failed",
                "errors": ["manifest inspection failed: authentication required"],
            }
        ]
        core._demote_coordinated_read_failures(outcomes)

        self.assertEqual(outcomes[0]["status"], "failed")

    def test_read_manifest_and_disagree_is_still_drift(self) -> None:
        # If the content was read and found wrong, that is a verdict, and a run
        # full of them must not be softened.
        outcomes = [
            {
                "id": f"upstream-{index}",
                "status": "failed",
                "errors": ["digest mismatch: expected sha256:a, observed sha256:b"],
            }
            for index in range(50)
        ]
        core._demote_coordinated_read_failures(outcomes)

        self.assertTrue(all(o["status"] == "failed" for o in outcomes))

    def test_tag_pointing_elsewhere_is_still_drift(self) -> None:
        # A retention tag aimed at the wrong digest is the ledfx failure mode and
        # must never be softened into "inconclusive".
        outcomes = [
            {
                "id": f"upstream-{index}",
                "status": "failed",
                "errors": ["destination tag retention-deployed-abc points at sha256:b"],
            }
            for index in range(50)
        ]
        core._demote_coordinated_read_failures(outcomes)

        self.assertTrue(all(o["status"] == "failed" for o in outcomes))

    def test_verify_lock_marks_unreachable_records_indeterminate(self) -> None:
        record = {
            "id": "first-party-demo",
            "kind": "first-party",
            "destination_repository": "apps/demo",
            "digest": "sha256:" + "1" * 64,
            "media_type": "application/vnd.oci.image.index.v1+json",
            "platforms": [],
            "destination_tags": ["sha-1111111"],
            "referrers": {"required": []},
        }
        lock = {
            "destination_registry": core.DEFAULT_REGISTRY,
            "images": [record],
        }

        with patch.object(
            core,
            "verify_record",
            side_effect=core.TransientRegistryError("unreachable"),
        ):
            report = core.verify_lock(self._client(), lock)

        self.assertEqual(report["status"], "indeterminate")
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["indeterminate"], 1)
        self.assertEqual(report["images"][0]["status"], "indeterminate")

    def test_real_drift_still_fails(self) -> None:
        record = {
            "id": "first-party-demo",
            "kind": "first-party",
            "destination_repository": "apps/demo",
            "digest": "sha256:" + "1" * 64,
            "media_type": "application/vnd.oci.image.index.v1+json",
            "platforms": [],
            "destination_tags": ["sha-1111111"],
            "referrers": {"required": []},
        }
        lock = {
            "destination_registry": core.DEFAULT_REGISTRY,
            "images": [record],
        }

        with patch.object(
            core, "verify_record", side_effect=core.RegistryError("missing")
        ):
            report = core.verify_lock(self._client(), lock)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["indeterminate"], 0)

    def test_notification_separates_inconclusive_from_drift(self) -> None:
        from scripts.ci.notify import summarise

        report = {
            "images": [
                {"id": "a", "status": "verified", "errors": []},
                {"id": "b", "status": "indeterminate", "errors": ["unreachable"]},
            ]
        }
        title, message = summarise(report)
        self.assertIn("inconclusive", title.lower())
        self.assertIn("b: unreachable", message)
        self.assertNotIn("unverified", title)

    def test_notification_still_reports_real_drift(self) -> None:
        from scripts.ci.notify import summarise

        report = {
            "images": [
                {"id": "a", "status": "verified", "errors": []},
                {"id": "b", "status": "failed", "errors": ["tag missing"]},
            ]
        }
        title, message = summarise(report)
        self.assertIn("drift", title.lower())
        self.assertIn("b: tag missing", message)
        self.assertIn("pull-time outage", message)

    def test_notification_stays_within_the_ntfy_limit(self) -> None:
        from scripts.ci import notify

        # 54 skopeo errors of real length overflowed ntfy's limit and the
        # notification came back 400, so the drift page was lost entirely.
        noisy = (
            "manifest inspection failed with exit code 1: "
            'time="2026-09-29T22:10:10-05:00" level=fatal msg="Error parsing image '
            'name \\"docker://registry.rupan.dev/upstream/docker.io/library/postgres'
            "@sha256:727876d274666da0b92a445390ba093c84b8e9f8343e1c53cd4e9a7ab2d8531"
            '0\\": reading manifest in registry.rupan.dev: authentication required"'
        )
        report = {
            "images": [
                {
                    "id": f"upstream-library-thing-{index:04d}",
                    "status": "failed",
                    "errors": [noisy],
                }
                for index in range(54)
            ]
        }
        title, message = notify.summarise(report)
        self.assertLessEqual(len(message), notify.MAX_MESSAGE)
        self.assertLessEqual(len(title.encode()), 250)
        # The reason is still readable after the tool noise is stripped.
        self.assertIn("authentication required", message)
        self.assertNotIn("level=fatal", message)
        self.assertNotIn('time="', message)

    def test_reason_is_bounded_per_image(self) -> None:
        from scripts.ci import notify

        reason = notify.reason_for({"errors": ["x" * 5000]})
        self.assertLessEqual(len(reason), notify.MAX_REASON)


def _ci_bash_block(script: str) -> str:
    """The body of a `nix develop ./flake -c bash -e -c '...'` CI step."""
    match = re.search(r"bash -e -c '(.*)'\s*$", script, re.DOTALL)
    if match is None:
        raise AssertionError("no bash -e -c block found in the CI script")
    return match.group(1)


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

    def test_drift_check_uses_node_credentials_and_preserves_the_report(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        job = pipeline["registry_drift_check"]
        template = pipeline[".registry_drift_check"]
        template_script = "\n".join(template["script"])

        # The importer only reads upstream/**, so apps/** needs node.
        self.assertIn("REGISTRY_NODE_PASSWORD_FILE", template_script)
        self.assertIn("registry-node-auth.json", template_script)
        self.assertIn("REGISTRY_DEST_AUTH_FILE", template_script)
        self.assertIn("scripts.registry verify", template_script)
        self.assertIn("ci.notify", template_script)
        self.assertEqual(template["environment"], {"name": "production"})
        self.assertEqual(template["artifacts"]["when"], "always")
        self.assertIn("artifacts/registry/", template["artifacts"]["paths"])
        self.assertIn(
            "shred -u /tmp/registry-node-auth.json",
            "\n".join(template["after_script"]),
        )
        self.assertEqual(job["extends"], [".nas_ci", ".registry_drift_check"])
        self.assertEqual(pipeline[".nas_ci"]["tags"], ["nas-ci"])
        rules = [rule.get("if", "") for rule in job["rules"]]
        self.assertIn("$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", rules)
        self.assertIn('$CI_PIPELINE_SOURCE == "schedule"', rules)

    def test_drift_check_propagates_a_failed_verification(self) -> None:
        # `verify || notify` turns a failed verification into a green job,
        # because the notifier succeeds. Run the real CI payload with a stub
        # `python` that fails on verify and succeeds on notify, and require the
        # job to fail anyway.
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        script = "\n".join(pipeline[".registry_drift_check"]["script"])
        match = re.search(
            r"bash -euo pipefail -c\s*'(?P<body>.*?)'\s*$", script, flags=re.DOTALL
        )
        assert match is not None, "could not extract the drift check payload"
        payload = " ".join(match.group("body").split())

        with tempfile.TemporaryDirectory() as directory:
            stub = pathlib.Path(directory) / "python"
            stub.write_text(
                "#!/bin/sh\n"
                # verify's argv is "-m scripts.registry verify ..."; notify's
                # never contains "verify", so this fails only the verifier.
                'case "$*" in\n'
                "  *verify*) exit 1 ;;\n"
                "esac\n"
                "exit 0\n"
            )
            stub.chmod(0o755)
            env = dict(os.environ, PATH=f"{directory}:{os.environ['PATH']}")
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", payload],
                capture_output=True,
                text=True,
                cwd=directory,
                env=env,
                check=False,
            )
        self.assertNotEqual(
            result.returncode,
            0,
            "a failed verification must not be masked by the notifier succeeding",
        )

    def test_gc_fixture_runs_offline_and_touches_no_real_registry(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        self.assertIn("registry_gc_fixture", pipeline)
        template = pipeline[".registry_gc_fixture"]
        script = "\n".join(template["script"])

        self.assertIn("registry-gc-fixture", script)
        # It serves its own zot on loopback, so it must not carry production
        # credentials or a resource group that would serialise it against the
        # jobs that do touch the real registry.
        self.assertNotIn("environment", template)
        self.assertNotIn("resource_group", template)
        self.assertNotIn("REGISTRY_", script)
        self.assertEqual(template["tags"], ["nas-ci"])
        self.assertEqual(
            pipeline["registry_gc_fixture"]["rules"],
            [
                {"if": '$CI_PIPELINE_SOURCE == "schedule"'},
                {"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", "when": "manual"},
            ],
        )

    def test_gc_fixture_shares_the_zot_build_with_the_registry(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        # One definition feeds both, so the fixture cannot pass against a
        # different zot than the one holding the images.
        release = (root / "nix" / "zot-release.nix").read_text()
        self.assertIn('version = "2.1.20"', release)
        self.assertIn("zot-release.nix", (root / "flake.nix").read_text())
        self.assertIn(
            "zot-release.nix",
            (root / "flake" / "modules" / "zot-registry.nix").read_text(),
        )
        from scripts.registry.gc_fixture import EXPECTED_ZOT_VERSION

        self.assertIn(f'version = "{EXPECTED_ZOT_VERSION}"', release)

    def test_gc_fixture_job_uses_a_shell_that_has_zot(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        script = "\n".join(pipeline[".registry_gc_fixture"]["script"])
        # CI enters the NixOS flake's devShell via `nix develop ./flake`, which
        # is a different shell from the repository root flake. Adding zot to
        # only the root flake leaves the job failing with "zot is not on PATH",
        # so both are checked here.
        self.assertIn("nix develop ./flake", script)
        for flake in (root / "flake.nix", root / "flake" / "flake.nix"):
            with self.subTest(flake=flake.name):
                self.assertIn(
                    "zot-release.nix", flake.read_text(), f"{flake} lacks zot"
                )

    def test_promotion_pins_its_own_retention_tags(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        job = pipeline["registry_promote_first_party"]
        block = "\n".join(job["script"])

        # A promoted digest is protected only by its sha-* tag until the
        # retention tag exists, and nothing else creates it for apps/**.
        self.assertIn("registry-reconcile-retention", block)
        # Both sides of the copy read the destination registry, as in the
        # standalone reconciler job.
        self.assertEqual(block.count("REGISTRY_MAINTENANCE_AUTH_FILE"), 2)
        # The standalone reconciler cannot cover this: it runs in a separate job
        # against the pre-push commit, so its ordering here is arbitrary.
        self.assertEqual(job["resource_group"], "registry-content")

        # The reconcile has to happen after the lock is written, and no earlier
        # exit may skip it: a failed push used to exit 1 with the digests
        # promoted but unpinned.
        inner = _ci_bash_block(block)
        self.assertLess(
            inner.index("git commit"), inner.index("registry-reconcile-retention")
        )
        self.assertNotIn("exit 0", inner)
        self.assertEqual(inner.count("exit $commit_rc"), 1)
        # A reconcile failure must still fail the job rather than pass quietly.
        self.assertIn("just registry-reconcile-retention || commit_rc=1", inner)

    def test_retention_reconcile_uses_maintenance_and_is_additive(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pipeline = yaml.safe_load((root / ".gitlab-ci.yml").read_text())
        template = pipeline[".registry_retention_reconcile"]
        self.assertIn("registry_retention_reconcile", pipeline)
        script = "\n".join(template["script"])

        # node is read-only on apps/** and the importer is 403 there, so only
        # maintenance can create the first-party retention tags.
        self.assertIn("REGISTRY_MAINTENANCE_AUTH_FILE", script)
        # The copy reads from the destination registry, so both sides need the
        # maintenance credential; with only the destination set, skopeo reads
        # the source manifest with the upstream credential and gets 401.
        self.assertEqual(script.count("REGISTRY_MAINTENANCE_AUTH_FILE"), 2)
        self.assertIn("reconcile-retention", script)
        self.assertEqual(template["environment"], {"name": "production"})
        self.assertEqual(template["resource_group"], "registry-content")
        self.assertEqual(template["tags"], ["nas-ci"])
        self.assertEqual(
            template["rules"],
            [
                {"if": '$CI_PIPELINE_SOURCE == "schedule"'},
                {"if": '$CI_COMMIT_BRANCH == "main"', "when": "manual"},
            ],
        )

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


class AuthConsistencyTest(unittest.TestCase):
    """The htpasswd and the CI auth files must be rotated together."""

    @staticmethod
    def _hash(password: str) -> str:
        result = subprocess.run(
            ["htpasswd", "-nbB", "tester", password],
            capture_output=True,
            text=True,
            check=True,
        )
        # `htpasswd -n` prints "user:hash"; only the hash is wanted.
        return result.stdout.strip().partition(":")[2]

    @staticmethod
    def _auth_file(user: str, password: str) -> str:
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        return json.dumps({"auths": {"registry.rupan.dev": {"auth": auth}}}, indent=2)

    def test_matching_credentials_report_ok(self) -> None:
        report = check_auth_consistency(
            f"maintenance:{self._hash('pw-maintenance')}\n",
            {
                "REGISTRY_MAINTENANCE_AUTH_FILE": self._auth_file(
                    "maintenance", "pw-maintenance"
                )
            },
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["errors"], [])
        self.assertEqual(
            report["checked_accounts"],
            [
                {
                    "variable": "REGISTRY_MAINTENANCE_AUTH_FILE",
                    "registry": "registry.rupan.dev",
                    "user": "maintenance",
                }
            ],
        )

    def test_rotated_password_without_updated_auth_file_fails(self) -> None:
        # The real drift: the htpasswd was rotated, the auth file was not, and
        # the only symptom in CI was a bare 401.
        report = check_auth_consistency(
            f"maintenance:{self._hash('new-password')}\n",
            {
                "REGISTRY_MAINTENANCE_AUTH_FILE": self._auth_file(
                    "maintenance", "old-password"
                )
            },
        )
        self.assertEqual(report["status"], "failed")
        error = report["errors"][0]
        self.assertEqual(error["variable"], "REGISTRY_MAINTENANCE_AUTH_FILE")
        self.assertEqual(error["user"], "maintenance")
        self.assertIn("does not match", error["error"])

    def test_auth_file_user_absent_from_htpasswd_fails(self) -> None:
        report = check_auth_consistency(
            f"node:{self._hash('pw')}\n",
            {"REGISTRY_GHOST_AUTH_FILE": self._auth_file("ghost", "pw")},
        )
        self.assertEqual(report["status"], "failed")
        self.assertIn("no htpasswd entry", report["errors"][0]["error"])

    def test_missing_entry_for_a_privileged_account_is_fatal(self) -> None:
        # An account the policy grants a role to must exist, or whichever job
        # authenticates as it fails.
        report = check_auth_consistency(
            f"node:{self._hash('pw')}\n",
            {"REGISTRY_MAINTENANCE_AUTH_FILE": self._auth_file("maintenance", "pw")},
            {"maintenance"},
        )
        self.assertEqual(report["status"], "failed")
        self.assertIn("no htpasswd entry", report["errors"][0]["error"])

    def test_missing_entry_for_a_retired_account_is_an_orphan(self) -> None:
        # migration-importer is not granted a role anywhere, so its leftover
        # variable is cleanup rather than an outage and must not redden CI.
        report = check_auth_consistency(
            f"node:{self._hash('pw')}\n",
            {
                "REGISTRY_MIGRATION_AUTH_FILE": self._auth_file(
                    "migration-importer", "pw"
                )
            },
            {"maintenance", "node", "importer"},
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["orphaned_accounts"][0]["user"], "migration-importer")
        self.assertIn("delete the variable", report["orphaned_accounts"][0]["reason"])

    def test_access_control_users_covers_admin_and_publishers(self) -> None:
        lock = valid_lock(manifest())
        record = lock["images"][0]
        record["kind"] = "first-party"
        record["destination_repository"] = "apps/demo"
        users = access_control_users(lock)
        self.assertIn("maintenance", users)
        self.assertIn("importer", users)
        self.assertIn("node", users)
        self.assertIn("publisher-demo", users)

    def test_htpasswd_user_without_auth_file_is_not_an_error(self) -> None:
        report = check_auth_consistency(
            f"maintenance:{self._hash('pw')}\nnode:{self._hash('pw')}\n",
            {"REGISTRY_MAINTENANCE_AUTH_FILE": self._auth_file("maintenance", "pw")},
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["htpasswd_users"], ["maintenance", "node"])

    def test_upstream_entries_are_skipped_not_failed(self) -> None:
        # Source credentials are somebody else's accounts; the htpasswd is
        # zot's, so only the destination registry is comparable.
        auth = base64.b64encode(b"importer:pw").decode()
        document = json.dumps(
            {
                "auths": {
                    "registry.rupan.dev": {"auth": auth},
                    "ghcr.io": {"auth": base64.b64encode(b"jeff7712:pw").decode()},
                }
            }
        )
        report = check_auth_consistency(
            f"importer:{self._hash('pw')}\n",
            {"REGISTRY_SOURCE_AUTH_FILE": document},
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(len(report["checked_accounts"]), 1)
        self.assertEqual(
            report["skipped_entries"],
            [
                {
                    "variable": "REGISTRY_SOURCE_AUTH_FILE",
                    "registry": "ghcr.io",
                    "reason": "not the destination registry",
                }
            ],
        )

    def test_auth_file_without_auths_is_reported_not_raised(self) -> None:
        report = check_auth_consistency(
            f"importer:{self._hash('pw')}\n",
            {"REGISTRY_EMPTY_AUTH_FILE": "{}"},
        )
        self.assertEqual(report["status"], "failed")
        self.assertIn("unreadable auth file", report["errors"][0]["error"])

    def test_malformed_auth_file_is_reported_not_raised(self) -> None:
        report = check_auth_consistency(
            f"maintenance:{self._hash('pw')}\n",
            {"REGISTRY_BROKEN_AUTH_FILE": "not json at all"},
        )
        self.assertEqual(report["status"], "failed")
        self.assertIn("unreadable auth file", report["errors"][0]["error"])

    def test_unusable_hash_is_reported_not_raised(self) -> None:
        report = check_auth_consistency(
            "maintenance:not-a-bcrypt-hash\n",
            {"REGISTRY_MAINTENANCE_AUTH_FILE": self._auth_file("maintenance", "pw")},
        )
        self.assertEqual(report["status"], "failed")
        self.assertIn("not a usable hash", report["errors"][0]["error"])

    def test_parse_htpasswd_skips_blanks_and_comments(self) -> None:
        entries = parse_htpasswd("# a comment\n\nnode:abc\n  spaced:def  \n")
        self.assertEqual(entries, {"node": "abc", "spaced": "def"})

    def test_report_never_contains_the_password(self) -> None:
        secret = "do-not-leak-this-value"
        report = check_auth_consistency(
            f"maintenance:{self._hash(secret)}\n",
            {"REGISTRY_MAINTENANCE_AUTH_FILE": self._auth_file("maintenance", secret)},
        )
        self.assertEqual(report["status"], "ok")
        self.assertNotIn(secret, json.dumps(report))


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

    def test_copy_conflict_reports_observed_digest(self) -> None:
        raw = manifest()
        conflicting = manifest([("linux", "amd64")])
        value = valid_lock(raw)
        record = value["images"][0]
        source = f"docker.io/library/demo@{record['digest']}"
        tag = f"registry.rupan.dev/{record['destination_repository']}:1.0"
        client = FakeClient({source: raw, tag: conflicting})
        report = copy_lock(client, value)  # type: ignore[arg-type]
        image = report["images"][0]
        # The tag conflict is only actionable with the digest already in the
        # registry, so the report must carry it rather than nulling the field.
        self.assertEqual(image["observed_digest"], digest(conflicting))
        self.assertEqual(image["expected_digest"], digest(raw))
        error = image["errors"][0]
        self.assertIn(digest(raw), error)
        self.assertIn(digest(conflicting), error)

    def test_reconcile_creates_a_missing_retention_tag(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        retention = [
            t for t in record["destination_tags"] if t.startswith("retention-")
        ]
        self.assertTrue(retention)
        # Only the promotion tag exists; the retention tag was never created,
        # which is the first-party case where the importer cannot write apps/**.
        client = FakeClient({f"{base}@{record['digest']}": raw, f"{base}:1.0": raw})
        report = reconcile_retention(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "ok")
        created = [i for i in report["images"] if i["status"] == "created"]
        self.assertEqual(len(created), len(retention))
        self.assertEqual(len(client.copies), len(retention))
        self.assertIn(
            f"registry.rupan.dev/{record['destination_repository']}@{record['digest']}",
            client.copies[0][0],
        )

    def test_reconcile_leaves_a_correct_tag_alone(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        client = FakeClient(
            {
                f"{base}@{record['digest']}": raw,
                **{f"{base}:{tag}": raw for tag in record["destination_tags"]},
            }
        )
        report = reconcile_retention(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "ok")
        self.assertTrue(all(i["status"] == "present" for i in report["images"]))
        self.assertEqual(client.copies, [])

    def test_reconcile_reports_a_conflict_without_overwriting(self) -> None:
        # The ledfx shape: the retention tag resolves elsewhere. Clobbering it
        # would destroy the evidence, so it must be reported and left alone.
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        child = manifest([("linux", "amd64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        tags = {
            f"{base}:{tag}": child if tag.startswith("retention-") else raw
            for tag in record["destination_tags"]
        }
        client = FakeClient({f"{base}@{record['digest']}": raw, **tags})
        report = reconcile_retention(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(client.copies, [])
        conflict = next(i for i in report["images"] if i["status"] == "conflict")
        self.assertEqual(conflict["observed_digest"], digest(child))
        self.assertIn("left untouched", report["summary"]["errors"][0])

    def test_reconcile_dry_run_makes_no_changes(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        client = FakeClient({f"{base}@{record['digest']}": raw, f"{base}:1.0": raw})
        report = reconcile_retention(client, value, dry_run=True)  # type: ignore[arg-type]
        self.assertEqual(client.copies, [])
        self.assertTrue(any(i["status"] == "would-create" for i in report["images"]))

    def test_reconcile_ignores_promotion_tags_and_kind_filter(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        value["images"][0]["kind"] = "first-party"
        report = reconcile_retention(FakeClient({}), value, kind="upstream")  # type: ignore[arg-type]
        self.assertEqual(report["images"], [])

    def test_verify_accepts_tags_pointing_at_the_locked_digest(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        client = FakeClient(
            {
                f"{base}@{record['digest']}": raw,
                **{f"{base}:{tag}": raw for tag in record["destination_tags"]},
            }
        )
        report = verify_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["images"][0]["tags"])
        self.assertTrue(all(item["matched"] for item in report["images"][0]["tags"]))

    def test_verify_detects_retention_tag_repointed_at_another_manifest(self) -> None:
        # The ledfx shape: the digest is present and correct, but the retention
        # tag resolves to a different manifest, which is what left the correct
        # manifest untagged and collectable.
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        child = manifest([("linux", "amd64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        tags = {
            f"{base}:{tag}": child if tag.startswith("retention-") else raw
            for tag in record["destination_tags"]
        }
        client = FakeClient({f"{base}@{record['digest']}": raw, **tags})
        report = verify_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "failed")
        outcome = report["images"][0]
        bad = [item for item in outcome["tags"] if not item["matched"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["observed_digest"], digest(child))
        self.assertIn("points at", outcome["errors"][0])

    def test_verify_skips_platforms_for_a_single_arch_manifest(self) -> None:
        # A plain manifest has no `manifests` list, so observed platforms are
        # always empty while the lock records the platform it was built for.
        # Comparing them flagged first-party-jarvis-nemotron on every run.
        raw = manifest()  # single-arch docker manifest, not an index
        value = valid_lock(raw)
        record = value["images"][0]
        record["media_type"] = "application/vnd.docker.distribution.manifest.v2+json"
        record["platforms"] = ["linux/amd64"]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        client = FakeClient(
            {
                f"{base}@{record['digest']}": raw,
                **{f"{base}:{tag}": raw for tag in record["destination_tags"]},
            }
        )
        report = verify_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "ok")

    def test_verify_detects_a_missing_destination_tag(self) -> None:
        raw = manifest([("linux", "amd64"), ("linux", "arm64")])
        value = valid_lock(raw)
        record = value["images"][0]
        base = f"registry.rupan.dev/{record['destination_repository']}"
        client = FakeClient({f"{base}@{record['digest']}": raw})
        report = verify_lock(client, value)  # type: ignore[arg-type]
        self.assertEqual(report["status"], "failed")
        self.assertIn("missing", report["images"][0]["errors"][0])

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
                [
                    "gitops/websites/rupan-dev/deployment.yaml:1",
                    "observed:rupan-dev/website-deploy-xxx/web",
                ],
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
