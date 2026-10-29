from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agent.ship import extract_patch, load_routes, plan_operations
from scripts.ci import node_supply

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40
DIGEST = "sha256:" + "b" * 64


def catalog(**overrides: object) -> dict:
    entry = {
        "id": "site",
        "repository": "JEFF7712/site",
        "node_manager": "npm",
        "node_dir": "",
        "node_seeds": [],
        "release_supply": [
            {
                "package": "node-toolchain",
                "version": "1",
                "filename": "node.tar.gz",
                "sha256": "sha256:" + "1" * 64,
                "kind": "file",
                "dest": "/inputs/node.tar.gz",
            },
            {
                "package": "site-supply",
                "version": "3",
                "filename": "npm-cache.tar.gz",
                "sha256": "sha256:" + "3" * 64,
                "kind": "file",
                "dest": "/inputs/npm-cache.tar.gz",
            },
        ],
    }
    entry.update(overrides)
    return {"schema_version": 1, "applications": [entry]}


def requests(*items: dict) -> str:
    return json.dumps({"schema_version": 1, "requests": list(items)}, indent=2) + "\n"


REQUEST = {"application": "site", "version": "4", "source": SOURCE}


class RequestTests(unittest.TestCase):
    def test_committed_request_file_is_valid(self) -> None:
        node_supply.load_requests((ROOT / node_supply.REQUESTS).read_text())

    def test_rejects_malformed_requests(self) -> None:
        cases = [
            {**REQUEST, "version": "0"},
            {**REQUEST, "version": "v4"},
            {**REQUEST, "source": "main"},
            {**REQUEST, "extra": "x"},
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                node_supply.load_requests(requests(item))

    def test_rejects_duplicate_application(self) -> None:
        with self.assertRaisesRegex(ValueError, "one supply request"):
            node_supply.load_requests(requests(REQUEST, {**REQUEST, "version": "5"}))


class PlanTests(unittest.TestCase):
    def request(self, **fields: str) -> node_supply.Request:
        return node_supply.Request(**{**REQUEST, **fields})

    def test_plans_known_npm_application(self) -> None:
        target = node_supply.plan(self.request(), catalog())
        self.assertEqual(target.package, "site-supply")
        self.assertEqual(target.repository, "JEFF7712/site")
        self.assertEqual(target.toolchain["filename"], "node.tar.gz")

    def test_plans_digest_pinned_seeds(self) -> None:
        entries = catalog(node_seeds=[["plugins.tar.gz", "quartz/.quartz"]])
        seed = {
            **entries["applications"][0]["release_supply"][0],
            "filename": "plugins.tar.gz",
        }
        entries["applications"][0]["release_supply"].append(seed)
        target = node_supply.plan(self.request(), entries)
        self.assertEqual(target.seeds, ((seed, "quartz/.quartz"),))

    def test_version_must_advance(self) -> None:
        for version in ("2", "3"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                node_supply.plan(self.request(version=version), catalog())

    def test_rejects_unsupported_applications(self) -> None:
        cases = [
            (self.request(application="other"), catalog()),
            (self.request(), catalog(node_manager="")),
            (self.request(), catalog(node_seeds=[["a.tar.gz", "x/.quartz"]])),
            (self.request(), catalog(release_supply=[])),
        ]
        for request, entries in cases:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                node_supply.plan(request, entries)


class PatchTests(unittest.TestCase):
    def test_pin_changes_only_the_cache_entry(self) -> None:
        before = json.dumps(catalog(), indent=2) + "\n"
        after = json.loads(node_supply.pin(before, {"site": ("4", DIGEST)}))
        supply = after["applications"][0]["release_supply"]
        self.assertEqual(supply[0], catalog()["applications"][0]["release_supply"][0])
        self.assertEqual((supply[1]["version"], supply[1]["sha256"]), ("4", DIGEST))

    def test_pin_rejects_invalid_digest(self) -> None:
        with self.assertRaises(ValueError):
            node_supply.pin(json.dumps(catalog()), {"site": ("4", "sha256:x")})

    def test_retire_keeps_unrelated_requests(self) -> None:
        other = {**REQUEST, "application": "other"}
        remaining = json.loads(node_supply.retire(requests(REQUEST, other), {"site"}))
        self.assertEqual(remaining["requests"], [other])


class ToolchainTests(unittest.TestCase):
    def archive(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(directory)]))
        path = directory / "node.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        return path

    def test_reads_version_from_top_directory(self) -> None:
        path = self.archive("node-v24.21.0-linux-x64")
        self.assertEqual(node_supply.toolchain_version(path), "v24.21.0")

    def test_rejects_unversioned_archive(self) -> None:
        with self.assertRaises(ValueError):
            node_supply.toolchain_version(self.archive("toolchain"))


class RetainTests(unittest.TestCase):
    def target(self) -> node_supply.Target:
        return node_supply.plan(node_supply.Request(**REQUEST), catalog())

    def test_retained_version_is_pinned_without_repacking(self) -> None:
        def download(package, version, filename, dest, host, owner):
            dest.write_bytes(b"retained")
            return True

        with (
            tempfile.TemporaryDirectory() as work,
            mock.patch.object(node_supply, "download", side_effect=download),
            mock.patch.object(node_supply, "pack") as pack,
            mock.patch.object(node_supply, "upload") as upload,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = node_supply.retain(self.target(), Path(work))
        pack.assert_not_called()
        upload.assert_not_called()
        self.assertEqual(result, node_supply.digest_bytes(b"retained"))

    def test_rejects_toolchain_that_differs_from_its_pin(self) -> None:
        def download(package, version, filename, dest, host, owner):
            if package == "site-supply":
                return False
            dest.write_bytes(b"tampered")
            return True

        with (
            tempfile.TemporaryDirectory() as work,
            mock.patch.object(node_supply, "download", side_effect=download),
            mock.patch.object(node_supply, "pack") as pack,
            self.assertRaisesRegex(ValueError, "toolchain does not match"),
        ):
            node_supply.retain(self.target(), Path(work))
        pack.assert_not_called()

    def test_rejects_upload_that_reads_back_differently(self) -> None:
        calls = []

        def download(package, version, filename, dest, host, owner):
            calls.append(package)
            if package == "node-toolchain":
                dest.write_bytes(b"toolchain")
                return True
            if len(calls) == 1:
                return False
            dest.write_bytes(b"corrupted")
            return True

        target = self.target()
        toolchain = dict(
            target.toolchain, sha256=node_supply.digest_bytes(b"toolchain")
        )
        target = node_supply.Target(**{**vars(target), "toolchain": toolchain})
        with (
            tempfile.TemporaryDirectory() as work,
            mock.patch.object(node_supply, "download", side_effect=download),
            mock.patch.object(node_supply, "toolchain_version", return_value="v24"),
            mock.patch.object(node_supply, "nix_toolchain"),
            mock.patch.object(node_supply, "fetch_source"),
            mock.patch.object(
                node_supply, "pack", return_value={"snapshot_sha256": DIGEST}
            ) as pack,
            mock.patch.object(node_supply, "upload") as upload,
            self.assertRaisesRegex(RuntimeError, "differs from the uploaded"),
        ):
            node_supply.retain(target, Path(work))
        self.assertEqual(pack.call_args.kwargs["extra_libcs"], ("musl",))
        upload.assert_called_once()


class SeedTests(unittest.TestCase):
    def archive(self, root: Path, name: str = "plugins.tar.gz") -> Path:
        path = root / name
        with tarfile.open(path, "w:gz") as archive:
            data = b"retained plugin"
            member = tarfile.TarInfo("plugins/example/index.js")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        return path

    def test_restores_seed_at_release_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            archive = self.archive(root)
            node_supply.unpack_seeds(workspace, ((archive, "quartz/.quartz"),))
            self.assertEqual(
                (workspace / "quartz/.quartz/plugins/example/index.js").read_bytes(),
                b"retained plugin",
            )

    def test_rejects_escaping_destination_and_existing_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            archive = self.archive(root)
            for dest in ("../outside", "/outside"):
                with self.subTest(dest=dest), self.assertRaises(ValueError):
                    node_supply.unpack_seeds(workspace, ((archive, dest),))
            (workspace / "existing").mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                node_supply.unpack_seeds(workspace, ((archive, "existing"),))

    def test_rejects_tampered_seed_before_packing(self) -> None:
        entries = catalog(node_seeds=[["plugins.tar.gz", "quartz/.quartz"]])
        seed = {
            **entries["applications"][0]["release_supply"][0],
            "filename": "plugins.tar.gz",
        }
        entries["applications"][0]["release_supply"].append(seed)
        target = node_supply.plan(node_supply.Request(**REQUEST), entries)

        def download(package, version, filename, dest, host, owner):
            if package == "site-supply":
                return False
            dest.write_bytes(b"tampered")
            return True

        target.toolchain["sha256"] = node_supply.digest_bytes(b"tampered")
        with (
            tempfile.TemporaryDirectory() as work,
            mock.patch.object(node_supply, "download", side_effect=download),
            mock.patch.object(node_supply, "toolchain_version", return_value="v24"),
            mock.patch.object(node_supply, "nix_toolchain"),
            mock.patch.object(node_supply, "fetch_source"),
            mock.patch.object(node_supply, "pack") as pack,
            self.assertRaisesRegex(ValueError, "seed does not match its pin"),
        ):
            node_supply.retain(target, Path(work))
        pack.assert_not_called()


class PublishTests(unittest.TestCase):
    def repository(self, pending: str) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(root)]))
        (root / "config/ci").mkdir(parents=True)
        (root / node_supply.APPLICATIONS).write_text(
            json.dumps(catalog(), indent=2) + "\n"
        )
        (root / node_supply.REQUESTS).write_text(pending)
        for command in (
            ["git", "init", "--quiet"],
            ["git", "add", "."],
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
        ):
            subprocess.run(command, cwd=root, check=True)
        return root

    def test_emits_promotion_patch_that_pins_and_retires(self) -> None:
        root = self.repository(requests(REQUEST))
        output = io.StringIO()
        with (
            mock.patch.object(node_supply, "retain", return_value=DIGEST),
            contextlib.redirect_stdout(output),
        ):
            node_supply.publish(root, root / "out")
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(status, "?? out/\n")
        patch = extract_patch(output.getvalue())
        self.assertIsNotNone(patch)
        self.assertEqual(patch, (root / "out/promotion.patch").read_text())
        subprocess.run(
            ["git", "apply", "-"], input=patch, text=True, cwd=root, check=True
        )
        pinned = json.loads((root / node_supply.APPLICATIONS).read_text())
        cache = pinned["applications"][0]["release_supply"][1]
        self.assertEqual((cache["version"], cache["sha256"]), ("4", DIGEST))
        self.assertEqual(
            node_supply.load_requests((root / node_supply.REQUESTS).read_text()), []
        )
        receipt = json.loads((root / "out/receipt.json").read_text())
        self.assertEqual(receipt["pins"]["site"], {"version": "4", "sha256": DIGEST})

    def test_invalid_request_fails_before_any_retention(self) -> None:
        root = self.repository(requests({**REQUEST, "version": "3"}))
        with (
            mock.patch.object(node_supply, "retain") as retain,
            self.assertRaises(ValueError),
        ):
            node_supply.publish(root, root / "out")
        retain.assert_not_called()

    def test_no_requests_emits_no_patch(self) -> None:
        root = self.repository(requests())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            node_supply.publish(root, root / "out")
        self.assertIsNone(extract_patch(output.getvalue()))


class OperationTests(unittest.TestCase):
    def test_request_file_routes_to_the_publish_operation(self) -> None:
        operations = json.loads((ROOT / "config/ci/operations.json").read_text())
        plan = plan_operations(
            [str(node_supply.REQUESTS)], load_routes(), "auto", operations
        )
        self.assertEqual(plan.run, ["node-supply-publish"])
        self.assertEqual(plan.held, {})

    def test_operation_holds_only_the_supply_write_secret(self) -> None:
        operations = json.loads((ROOT / "config/ci/operations.json").read_text())
        operation = operations["node-supply-publish"]
        self.assertTrue(operation["agent"])
        self.assertTrue(operation["mutation"])
        self.assertEqual(
            operation["environment"]["FORGEJO_PACKAGE_TOKEN"],
            {"from_secret": "supply_write_token"},
        )
        secrets = {
            value["from_secret"]
            for value in operation["environment"].values()
            if isinstance(value, dict)
        }
        self.assertEqual(
            secrets,
            {
                "supply_write_token",
                "forgejo_source_read_token",
                "state_deploy_password",
                "state_ca_file",
            },
        )

    def test_every_operation_carries_the_runner_state_credentials(self) -> None:
        # scripts.ci.operation authenticates its permit and artifact transfer
        # with these before any operation command runs.
        operations = json.loads((ROOT / "config/ci/operations.json").read_text())
        for name, operation in operations.items():
            role = "deploy" if operation["mutation"] else "plan"
            with self.subTest(operation=name):
                environment = operation["environment"]
                self.assertEqual(environment.get("TF_HTTP_USERNAME"), role)
                self.assertEqual(
                    environment.get("TF_HTTP_PASSWORD"),
                    {"from_secret": f"state_{role}_password"},
                )
                self.assertEqual(
                    environment.get("STATE_CA_FILE"), {"from_secret": "state_ca_file"}
                )
                self.assertIn("STATE_CA_FILE", operation["file_secrets"])


if __name__ == "__main__":
    unittest.main()
