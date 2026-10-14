import base64
import hashlib
import io
import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.native_release import (
    call_builder,
    check_request,
    coordinate,
    finalize,
    kubernetes_probe,
    proof_job,
    published_receipt,
)


def request() -> dict:
    return {
        "sha": "a" * 40,
        "number": 42,
        "parent": 41,
        "source_token": "test-source-token",
        "supply_token": "test-package-token",
        "registry_read_password": "test-read-password",
        "publisher_auth": {
            "auths": {
                "registry.rupan.dev": {
                    "auth": base64.b64encode(
                        b"publisher-jarvis-nemotron:test-publisher-password"
                    ).decode()
                }
            }
        },
    }


class Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {
            "Docker-Content-Digest": "sha256:" + hashlib.sha256(data).hexdigest()
        }


class NativeReleaseTests(unittest.TestCase):
    def test_builder_uses_pinned_nas_relay_and_keeps_secrets_on_stdin(self) -> None:
        value = request()
        with (
            patch.dict(
                os.environ,
                {
                    "NIX_SSHOPTS": "-i '/protected/fleet key' -o StrictHostKeyChecking=yes"
                },
            ),
            patch("scripts.ci.native_release.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(
                {"source_revision": value["sha"], "pipeline": value["number"]}
            )
            call_builder(value, "remote")
            command = run.call_args.args[0]
            proxy = next(
                x.removeprefix("ProxyCommand=")
                for x in command
                if x.startswith("ProxyCommand=")
            )
            relay = shlex.split(proxy)
            self.assertIn("/protected/fleet key", relay)
            self.assertIn("StrictHostKeyChecking=yes", relay)
            self.assertIn("ConnectTimeout=10", command)
            self.assertEqual(relay[-3:], ["-W", "%h:%p", "rupan@10.0.30.20"])
            self.assertIn("rupan@10.0.30.14", command)
            self.assertNotIn(value["source_token"], " ".join(command))
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), value)

    def test_kubernetes_probe_reaches_authoritative_control_plane(self) -> None:
        with (
            patch.dict(os.environ, {"NIX_SSHOPTS": "-o BatchMode=yes"}),
            patch("scripts.ci.native_release.subprocess.run") as run,
            tempfile.TemporaryDirectory() as directory,
            patch(
                "scripts.ci.native_release.Path",
                side_effect=lambda p: Path(directory) / p,
            ),
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = b"proof passed"
            kubernetes_probe(request(), "sha256:" + "b" * 64)
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertIn("rupan@10.0.30.11", call.args[0])

    def test_finalization_rejects_corrupted_manifest_before_tag_write(self) -> None:
        value = {**request(), "digest": "sha256:" + "b" * 64}
        with (
            patch(
                "scripts.ci.native_release.published_receipt",
                return_value={"digest": value["digest"]},
            ),
            patch(
                "urllib.request.urlopen", return_value=Response(b"corrupted")
            ) as opened,
        ):
            with self.assertRaisesRegex(ValueError, "accepted manifest digest"):
                finalize(value)
            self.assertEqual(opened.call_count, 1)

    def test_invalid_source_and_pipeline_cannot_select_remote_paths(self) -> None:
        for field, bad in (("sha", "../escape"), ("number", True), ("parent", -1)):
            value = request()
            value[field] = bad
            with self.assertRaises(ValueError):
                check_request(value)

    def test_other_publisher_identity_is_rejected(self) -> None:
        value = request()
        value["publisher_auth"]["auths"]["registry.rupan.dev"]["auth"] = (
            base64.b64encode(b"maintenance:test-password").decode()
        )
        with self.assertRaisesRegex(ValueError, "scoped publisher"):
            check_request(value)

    def test_kubernetes_proof_uses_same_runtime_without_api_credentials(self) -> None:
        job = proof_job(request(), "sha256:" + "b" * 64)
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(spec["runtimeClassName"], "nvidia")
        self.assertFalse(spec["automountServiceAccountToken"])
        driver = next(v for v in spec["volumes"] if v["name"] == "host-driver")
        self.assertEqual(
            driver["hostPath"], {"path": "/run/opengl-driver/lib", "type": "Directory"}
        )
        mount = next(
            v
            for v in spec["containers"][0]["volumeMounts"]
            if v["name"] == "host-driver"
        )
        self.assertEqual(mount["mountPath"], "/host-driver")
        self.assertTrue(mount["readOnly"])
        self.assertEqual(
            spec["containers"][0]["image"],
            "registry.rupan.dev/apps/jarvis-nemotron@sha256:" + "b" * 64,
        )
        self.assertTrue(
            all(
                m.get("readOnly")
                for m in spec["containers"][0]["volumeMounts"]
                if m["name"] != "temporary"
            )
        )

    def test_bad_digest_cannot_enter_kubernetes_image_reference(self) -> None:
        with self.assertRaises(ValueError):
            proof_job(request(), "latest")

    def fixture(self, revision: str) -> tuple[bytes, bytes]:
        config = json.dumps(
            {
                "config": {
                    "Labels": {"org.opencontainers.image.revision": revision},
                    "User": "1000:1000",
                    "Entrypoint": ["/opt/venv/bin/python", "/app/bridge.py"],
                }
            }
        ).encode()
        manifest = json.dumps(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": "sha256:" + hashlib.sha256(config).hexdigest()},
            }
        ).encode()
        return manifest, config

    def test_manifest_and_configuration_have_distinct_verified_digests(self) -> None:
        manifest, config = self.fixture("a" * 40)
        with patch(
            "urllib.request.urlopen", side_effect=[Response(manifest), Response(config)]
        ):
            receipt = published_receipt(request(), "candidate-ci-42", retain=False)
        self.assertNotEqual(receipt["digest"], receipt["config_digest"])
        self.assertEqual(
            receipt["digest"], "sha256:" + hashlib.sha256(manifest).hexdigest()
        )
        self.assertIsNone(receipt["retention_tag"])

    def test_source_mismatch_rejects_candidate_before_release(self) -> None:
        manifest, config = self.fixture("c" * 40)
        with patch(
            "urllib.request.urlopen", side_effect=[Response(manifest), Response(config)]
        ):
            with self.assertRaisesRegex(ValueError, "runtime contract"):
                published_receipt(request(), "candidate-ci-42", retain=False)

    def test_failed_kubernetes_probe_cannot_finalize_release(self) -> None:
        value = request()
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "publisher.json"
            auth.write_text(json.dumps(value["publisher_auth"]))
            environment = {
                "CI_COMMIT_SHA": value["sha"],
                "CI_PIPELINE_NUMBER": "42",
                "CI_OPERATION_PARENT": "41",
                "FORGEJO_SOURCE_READ_TOKEN": value["source_token"],
                "FORGEJO_PACKAGE_TOKEN": value["supply_token"],
                "REGISTRY_READ_PASSWORD": value["registry_read_password"],
                "REGISTRY_JARVIS_PUBLISHER_AUTH_FILE": str(auth),
            }
            with (
                patch.dict(os.environ, environment),
                patch(
                    "scripts.ci.native_release.call_builder",
                    return_value={"digest": "sha256:" + "b" * 64},
                ) as builder,
                patch(
                    "scripts.ci.native_release.kubernetes_probe",
                    side_effect=RuntimeError("Driver stub rejected"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Driver stub"):
                    coordinate()
            self.assertEqual(builder.call_count, 1)
            self.assertEqual(builder.call_args.args[1], "remote")


if __name__ == "__main__":
    unittest.main()
