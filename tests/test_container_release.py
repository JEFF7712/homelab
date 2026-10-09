from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.ci import container_release as release
from scripts.ci import dotnet_service, firmware, nixos_config, python_service
from scripts.ci.applications import configuration, load_catalog
from scripts.ci.dockerfile_supply import parse_dockerfile

LOCAL_BASE = (
    "registry.rupan.dev/upstream/docker.io/library/python"
    ":3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
)


def write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def good_python_dockerfile(base: str = LOCAL_BASE) -> str:
    return (
        f"FROM {base} AS runtime\n"
        "WORKDIR /app\n"
        "COPY wheelhouse /wheelhouse\n"
        "RUN pip install --no-index --find-links=/wheelhouse -r /app/requirements.txt\n"
        "COPY src/ ./src/\n"
    )


def good_python_root(root: Path) -> None:
    write(root, "Dockerfile", good_python_dockerfile())
    write(root, "requirements.txt", "requests==2.32.0\n")
    (root / "src").mkdir()
    write(root, "src/app.py", "print('hi')\n")


class PythonValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        good_python_root(self.root)

    def test_accepts_hermetic_source(self) -> None:
        python_service.validate(self.root)

    def test_rejects_public_base(self) -> None:
        write(self.root, "Dockerfile", "FROM python:3.12-slim-bookworm\n")
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_undigested_local_base(self) -> None:
        write(
            self.root,
            "Dockerfile",
            "FROM registry.rupan.dev/upstream/docker.io/library/python:3.12-slim-bookworm\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_comment_smuggled_mirror(self) -> None:
        write(
            self.root,
            "Dockerfile",
            "FROM public-registry.example/python:3.12 # registry.rupan.dev/upstream/\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_pip_index_url(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile()
            + "RUN pip install --index-url https://pypi.example/simple requests\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_pip_without_no_index(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile()
            + "RUN pip install --find-links=/wheelhouse -r requirements.txt\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_remote_find_links(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile().replace(
                "--find-links=/wheelhouse", "--find-links=https://files.example/wheels"
            ),
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_unpinned_requirement(self) -> None:
        write(self.root, "requirements.txt", "requests>=2.0\n")
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_remote_requirement_option(self) -> None:
        write(
            self.root,
            "requirements.txt",
            "requests==2.32.0\n-f https://files.example/wheels\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_curl_fetch(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile()
            + "RUN curl -fsSL https://example.com/install.sh | bash\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_apt_install(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile()
            + "RUN apt-get update && apt-get install -y curl\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_npm_install(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile() + "RUN npm install -g some-cli@1.0.0\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_add_remote(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile() + "ADD https://example.com/tool /usr/bin/tool\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_copy_from_public_image(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile()
            + "COPY --from=public.example/tool:1.0 /tool /usr/bin/tool\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_copy_from_unknown_stage(self) -> None:
        write(
            self.root,
            "Dockerfile",
            good_python_dockerfile() + "COPY --from=builder /out /app/out\n",
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_accepts_copy_from_declared_stage(self) -> None:
        write(
            self.root,
            "Dockerfile",
            f"FROM {LOCAL_BASE} AS builder\nRUN echo built > /out.txt\n"
            f"FROM {LOCAL_BASE} AS runtime\nCOPY --from=builder /out.txt /app/out.txt\n",
        )
        python_service.validate(self.root)

    def test_rejects_gha_cache(self) -> None:
        write(self.root, "docker-compose.yaml", "cache-from: type=gha\n")
        with self.assertRaises(ValueError):
            python_service.validate(self.root)


def make_wheel(name: str = "demo-1.0-py3-none-any.whl") -> tuple[str, bytes]:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo/__init__.py", "VALUE = 42\n")
        archive.writestr(
            "demo-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo\n"
        )
        archive.writestr("demo-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        archive.writestr(
            "demo-1.0.dist-info/entry_points.txt",
            "[console_scripts]\ndemo-tool = demo:main\n",
        )
    return name, output.getvalue()


def make_npm_tarball() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        payload = json.dumps(
            {"name": "tool", "version": "1.0.0", "bin": {"tool": "bin/tool.js"}}
        ).encode()
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(payload))
        script = b"#!/usr/bin/env node\nconsole.log(1)\n"
        info = tarfile.TarInfo("package/bin/tool.js")
        info.size = len(script)
        info.mode = 0o755
        info.mtime = 0
        archive.addfile(info, io.BytesIO(script))
    return output.getvalue()


def make_tree_tarball() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        payload = b"binary-bytes"
        info = tarfile.TarInfo("bundle/tool")
        info.size = len(payload)
        info.mode = 0o755
        info.mtime = 0
        archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def base_fixture(root: Path) -> None:
    config = {
        "os": "linux",
        "architecture": "amd64",
        "config": {"User": "", "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"]},
        "rootfs": {"diff_ids": []},
    }
    manifest = {
        "schemaVersion": 2,
        "mediaType": release.MANIFEST,
        "config": release.descriptor(release.encode(config), release.CONFIG),
        "layers": [],
    }
    (root / "blobs" / "sha256").mkdir(parents=True)
    config_raw = release.encode(config)
    (root / "blobs" / "sha256" / release.digest(config_raw)[7:]).write_bytes(config_raw)
    manifest_raw = release.encode(manifest)
    digest_value = release.digest(manifest_raw)
    (root / "blobs" / "sha256" / digest_value[7:]).write_bytes(manifest_raw)
    base = f"registry.rupan.dev/upstream/example/base@{digest_value}"
    (root / "input.json").write_bytes(
        release.encode(
            {
                "base": base,
                "manifest": {
                    "mediaType": manifest["mediaType"],
                    "digest": digest_value,
                    "size": len(manifest_raw),
                },
                "original": {
                    "mediaType": manifest["mediaType"],
                    "digest": digest_value,
                    "size": len(manifest_raw),
                },
                "files": [],
                "supply": [],
            }
        )
    )
    (root / "base_ref.txt").write_bytes(base.encode())


class ContainerReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "src"
        self.source.mkdir()
        (self.source / "app.py").write_text("print('hi')\n")
        self.base = self.root / "base"
        self.base.mkdir()
        base_fixture(self.base)
        self.wheel_name, self.wheel = make_wheel()
        (self.base / "supply").mkdir()
        (self.base / "supply" / self.wheel_name).write_bytes(self.wheel)
        self.base_ref = (self.base / "base_ref.txt").read_text().strip()

    def supply(self, **overrides) -> list[dict]:
        entry = {
            "package": "demo-wheels",
            "version": "1",
            "filename": self.wheel_name,
            "sha256": release.digest(self.wheel),
            "kind": "wheel",
            "dest": "/usr/local",
        }
        entry.update(overrides)
        return [entry]

    def test_assemble_is_deterministic(self) -> None:
        first = release.assemble(
            self.source,
            self.base,
            self.root / "out1",
            self.base_ref,
            "a" * 40,
            ["app.py"],
            self.supply(),
            [],
            "3.12",
            1000,
            1000,
            "1000:1000",
            ["python", "/app/app.py"],
            {"PYTHONUNBUFFERED": "1"},
            "/app",
        )
        second = release.assemble(
            self.source,
            self.base,
            self.root / "out2",
            self.base_ref,
            "a" * 40,
            ["app.py"],
            self.supply(),
            [],
            "3.12",
            1000,
            1000,
            "1000:1000",
            ["python", "/app/app.py"],
            {"PYTHONUNBUFFERED": "1"},
            "/app",
        )
        self.assertEqual(first, second)

    def test_assemble_binds_commit_and_base(self) -> None:
        with self.assertRaises(ValueError):
            release.assemble(
                self.source,
                self.base,
                self.root / "out3",
                "registry.rupan.dev/upstream/example/other@sha256:" + "0" * 64,
                "a" * 40,
                ["app.py"],
                self.supply(),
                [],
                "3.12",
                1000,
                1000,
                "1000:1000",
                ["python", "/app/app.py"],
                {},
                "/app",
            )

    def test_wheel_lands_in_site_packages_with_launcher(self) -> None:
        release.assemble(
            self.source,
            self.base,
            self.root / "out4",
            self.base_ref,
            "a" * 40,
            ["app.py"],
            self.supply(),
            [],
            "3.12",
            1000,
            1000,
            "1000:1000",
            ["python", "/app/app.py"],
            {},
            "/app",
        )
        out = self.root / "out4"
        release_record = json.loads((out / "release.json").read_bytes())
        manifest = json.loads(release.read_blob(out, release_record["manifest"]))
        supply_layer = release.read_blob(out, manifest["layers"][-1])
        with tarfile.open(fileobj=io.BytesIO(supply_layer), mode="r") as archive:
            names = set(archive.getnames())
        self.assertIn("usr/local/lib/python3.12/site-packages/demo/__init__.py", names)
        self.assertIn("usr/local/bin/demo-tool", names)

    def test_supply_digest_mismatch_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            release.assemble(
                self.source,
                self.base,
                self.root / "out5",
                self.base_ref,
                "a" * 40,
                ["app.py"],
                self.supply(sha256="sha256:" + "f" * 64),
                [],
                "3.12",
                1000,
                1000,
                "1000:1000",
                ["python", "/app/app.py"],
                {},
                "/app",
            )

    def test_npm_bins_must_match_package_json(self) -> None:
        tarball = make_npm_tarball()
        (self.base / "supply" / "tool.tgz").write_bytes(tarball)
        entry = {
            "package": "tool",
            "version": "1",
            "filename": "tool.tgz",
            "sha256": release.digest(tarball),
            "kind": "npm",
            "dest": "/opt/tool",
            "bins": {"tool": "bin/other.js"},
        }
        with self.assertRaises(ValueError):
            release.assemble(
                self.source,
                self.base,
                self.root / "out6",
                self.base_ref,
                "a" * 40,
                ["app.py"],
                [entry],
                [],
                "3.12",
                1000,
                1000,
                "1000:1000",
                ["python", "/app/app.py"],
                {},
                "/app",
            )

    def test_tree_and_file_kinds(self) -> None:
        tree_blob = make_tree_tarball()
        (self.base / "supply" / "bundle.tgz").write_bytes(tree_blob)
        binary = b"\x7fELFfake"
        (self.base / "supply" / "age").write_bytes(binary)
        supply = [
            {
                "package": "p",
                "version": "1",
                "filename": "bundle.tgz",
                "sha256": release.digest(tree_blob),
                "kind": "tree",
                "dest": "/opt/cursor",
            },
            {
                "package": "p",
                "version": "1",
                "filename": "age",
                "sha256": release.digest(binary),
                "kind": "file",
                "dest": "/usr/bin/age",
            },
        ]
        digest_value = release.assemble(
            self.source,
            self.base,
            self.root / "out7",
            self.base_ref,
            "a" * 40,
            ["app.py"],
            supply,
            [],
            "3.12",
            1000,
            1000,
            "1000:1000",
            ["python", "/app/app.py"],
            {},
            "/app",
        )
        self.assertTrue(digest_value.startswith("sha256:"))

    def test_app_launcher_requires_released_module(self) -> None:
        with self.assertRaises(ValueError):
            release.assemble(
                self.source,
                self.base,
                self.root / "out8",
                self.base_ref,
                "a" * 40,
                ["app.py"],
                self.supply(),
                [
                    {
                        "name": "tool",
                        "module": "missing.cli",
                        "attr": "app",
                        "syspath": "/app/src",
                    }
                ],
                "3.12",
                1000,
                1000,
                "1000:1000",
                ["tool"],
                {},
                "/app",
            )

    def test_missing_release_file_fails(self) -> None:
        with self.assertRaises(ValueError):
            release.assemble(
                self.source,
                self.base,
                self.root / "out9",
                self.base_ref,
                "a" * 40,
                ["absent.py"],
                self.supply(),
                [],
                "3.12",
                1000,
                1000,
                "1000:1000",
                ["python", "/app/app.py"],
                {},
                "/app",
            )


class DotnetValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docker").mkdir()
        (self.root / "src").mkdir()
        (self.root / "frontend").mkdir()

    def write_dockerfile(self, content: str) -> None:
        (self.root / "docker" / "Dockerfile").write_text(content)

    def test_accepts_local_digest_base(self) -> None:
        self.write_dockerfile(
            "FROM registry.rupan.dev/upstream/ghcr.io/linuxserver/baseimage-alpine@sha256:"
            + "0" * 64
            + "\n"
        )
        dotnet_service.validate(self.root)

    def test_rejects_public_base(self) -> None:
        self.write_dockerfile("FROM ghcr.io/linuxserver/baseimage-alpine:3.21\n")
        with self.assertRaises(ValueError):
            dotnet_service.validate(self.root)

    def test_rejects_tagged_local_base(self) -> None:
        self.write_dockerfile(
            "FROM registry.rupan.dev/upstream/ghcr.io/linuxserver/baseimage-alpine:3.21\n"
        )
        with self.assertRaises(ValueError):
            dotnet_service.validate(self.root)

    def test_rejects_apk_add(self) -> None:
        self.write_dockerfile(
            "FROM registry.rupan.dev/upstream/ghcr.io/linuxserver/baseimage-alpine@sha256:"
            + "0" * 64
            + "\nRUN apk add --no-cache icu-libs\n"
        )
        with self.assertRaises(ValueError):
            dotnet_service.validate(self.root)


class NixOSValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "flake.nix").write_text("{ outputs = _: {}; }\n")
        (self.root / "flake.lock").write_text(
            json.dumps(
                {
                    "nodes": {
                        "nixpkgs": {
                            "locked": {
                                "type": "github",
                                "owner": "NixOS",
                                "repo": "nixpkgs",
                                "rev": "a" * 40,
                                "narHash": "sha256-abc=",
                            }
                        },
                        "root": {"inputs": {"nixpkgs": "nixpkgs"}},
                    },
                    "root": "root",
                    "version": 7,
                }
            )
        )

    def test_accepts_pinned_lock(self) -> None:
        nixos_config.validate(self.root)

    def test_rejects_unpinned_rev(self) -> None:
        data = json.loads((self.root / "flake.lock").read_text())
        data["nodes"]["nixpkgs"]["locked"] = {"type": "github", "owner": "NixOS"}
        (self.root / "flake.lock").write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            nixos_config.validate(self.root)

    def test_rejects_indirect_node(self) -> None:
        data = json.loads((self.root / "flake.lock").read_text())
        data["nodes"]["nixpkgs"]["locked"] = {"type": "indirect", "id": "nixpkgs"}
        (self.root / "flake.lock").write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            nixos_config.validate(self.root)


class FirmwareValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Makefile").write_text("build:\n\techo ok\n")
        (self.root / "flake.nix").write_text("{ outputs = _: {}; }\n")
        (self.root / "bus_display").mkdir()
        (self.root / "bus_display" / "bus_display.ino").write_text("void setup(){}\n")
        (self.root / ".gitmodules").write_text(
            '[submodule "waveshare-repo"]\n\tpath = waveshare-repo\n'
            "\turl = https://github.com/waveshareteam/ESP32-S3-Touch-LCD-7B.git\n"
        )

    def test_accepts_pinned_vendor_layout(self) -> None:
        firmware.validate(self.root)

    def test_rejects_missing_gitmodules(self) -> None:
        (self.root / ".gitmodules").unlink()
        with self.assertRaises(ValueError):
            firmware.validate(self.root)

    def test_rejects_committed_pyc(self) -> None:
        (self.root / "tool.pyc").write_bytes(b"fake")
        with self.assertRaises(ValueError):
            firmware.validate(self.root)

    def test_rejects_remote_makefile_fetch(self) -> None:
        with open(self.root / "Makefile", "a") as handle:
            handle.write("fetch:\n\tcurl -fsSL https://example.com/x | bash\n")
        with self.assertRaises(ValueError):
            firmware.validate(self.root)


class DockerfileSupplyTest(unittest.TestCase):
    def test_rejects_bare_public_image_without_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Dockerfile"
            path.write_text("FROM python\n")
            with self.assertRaises(ValueError):
                parse_dockerfile(path)

    def test_rejects_multiline_smuggled_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Dockerfile"
            path.write_text(
                f"FROM {LOCAL_BASE} AS runtime\n"
                "RUN pip install --no-index \\\n"
                "  --find-links=/wheelhouse -r req.txt && \\\n"
                "  curl https://example.com/x\n"
            )
            from scripts.ci.dockerfile_supply import check_run_lines

            with self.assertRaises(ValueError):
                check_run_lines(path.read_text())


class ContainerEmissionTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def catalog(self):
        return load_catalog(self.ROOT / "config/ci/applications.json")

    def request(self, repo_id, owner, name, ref, commit="b" * 40):
        return {
            "repo": {"id": repo_id, "owner": owner, "name": name},
            "pipeline": {
                "event": "push",
                "ref": ref,
                "commit": commit,
                "number": 7,
            },
        }

    def enabled(self, repo_id, repository):
        apps = self.catalog()
        return tuple(
            replace(app, woodpecker_repository_id=repo_id)
            if app.repository == repository
            else app
            for app in apps
        )

    def test_python_push_gets_container_release(self) -> None:
        apps = self.enabled(11, "JEFF7712/solubility-predictor")
        result = configuration(
            self.request(11, "JEFF7712", "solubility-predictor", "refs/heads/main"),
            apps,
        )
        self.assertEqual(len(result["configs"]), 2)
        release_workflow = yaml.safe_load(result["configs"][1]["data"])
        self.assertEqual(result["configs"][1]["name"], "application-release.yaml")
        names = [step["name"] for step in release_workflow["steps"]]
        self.assertEqual(
            names,
            [
                "prepare-retained-inputs",
                "assemble-container-image",
                "publish-verified-image",
            ],
        )
        prepare = release_workflow["steps"][0]
        self.assertEqual(
            set(prepare["environment"]),
            {"REGISTRY_PASSWORD", "FORGEJO_TOKEN"},
        )
        self.assertEqual(
            prepare["environment"]["FORGEJO_TOKEN"]["from_secret"],
            "supply_read_password",
        )
        publish = release_workflow["steps"][2]
        self.assertEqual(
            publish["environment"]["REGISTRY_PASSWORD"]["from_secret"],
            "solubility-gnn_registry_password",
        )
        self.assertIn("--scripts", release_workflow["steps"][1]["commands"][0])

    def test_python_pull_request_gets_validation_only(self) -> None:
        apps = self.enabled(11, "JEFF7712/solubility-predictor")
        request = self.request(
            11, "JEFF7712", "solubility-predictor", "refs/pull/3/head"
        )
        request["pipeline"]["event"] = "pull_request"
        result = configuration(request, apps)
        self.assertEqual(len(result["configs"]), 1)

    def test_dotnet_push_gets_no_release(self) -> None:
        apps = self.enabled(13, "JEFF7712/bookshelf")
        result = configuration(
            self.request(13, "JEFF7712", "bookshelf", "refs/heads/develop"),
            apps,
        )
        self.assertEqual(len(result["configs"]), 1)
        self.assertEqual(result["configs"][0]["name"], "application-validation.yaml")

    def test_unenrolled_python_repo_is_rejected_without_api(self) -> None:
        apps = self.catalog()
        with patch("scripts.ci.policy.api") as api:
            from scripts.ci.policy import resolve_configuration

            with self.assertRaisesRegex(ValueError, "not enrolled"):
                resolve_configuration(
                    self.request(
                        99, "JEFF7712", "solubility-predictor", "refs/heads/main"
                    ),
                    {},
                    apps,
                )
            api.assert_not_called()

    def test_container_catalog_entries_validate(self) -> None:
        apps = {app.id: app for app in self.catalog()}
        self.assertEqual(apps["solubility-gnn"].release_kind, "container")
        self.assertEqual(apps["pod-agent"].release_kind, "container")
        self.assertEqual(apps["pod-agent"].release_user, "101:101")
        self.assertEqual(apps["bookshelf"].release_kind, "none")
        self.assertEqual(apps["darkbit"].release_kind, "static")


class PythonWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        good_python_root(self.root)

    def write_workflow(self, content: str) -> None:
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "deploy.yml").write_text(content)

    def test_rejects_registry_publisher_workflow(self) -> None:
        self.write_workflow(
            "jobs:\n  push:\n    steps:\n      - run: docker push registry.rupan.dev/x\n"
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_rejects_gha_cache_workflow(self) -> None:
        self.write_workflow(
            "jobs:\n  x:\n    steps:\n      - uses: a\n        with:\n          c: type=gha\n"
        )
        with self.assertRaises(ValueError):
            python_service.validate(self.root)

    def test_accepts_unrelated_quality_workflow(self) -> None:
        self.write_workflow("jobs:\n  lint:\n    steps:\n      - run: ruff check .\n")
        python_service.validate(self.root)


class EnrollmentTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_all_assignments_enrolled(self) -> None:
        apps = {
            app.id: app
            for app in load_catalog(self.ROOT / "config/ci/applications.json")
        }
        self.assertEqual(apps["solubility-gnn"].woodpecker_repository_id, 18)
        self.assertEqual(apps["pod-agent"].woodpecker_repository_id, 19)
        self.assertEqual(apps["bookshelf"].woodpecker_repository_id, 20)
        self.assertEqual(apps["nixos-config"].woodpecker_repository_id, 21)
        self.assertEqual(apps["bus-route-display"].woodpecker_repository_id, 22)
        self.assertIsNotNone(apps["darkbit"].woodpecker_repository_id)

    def test_nonpublished_apps_claim_no_artifacts(self) -> None:
        apps = {
            app.id: app
            for app in load_catalog(self.ROOT / "config/ci/applications.json")
        }
        for app_id in ("nixos-config", "bus-route-display"):
            self.assertEqual(apps[app_id].artifact_repository, "-")
            self.assertEqual(apps[app_id].deployment_path, "-")
            self.assertEqual(apps[app_id].release_kind, "none")

    def test_nixos_and_firmware_push_get_no_release(self) -> None:
        import yaml

        apps = load_catalog(self.ROOT / "config/ci/applications.json")
        for repo_id, owner, name, ref in (
            (21, "JEFF7712", "nixos-config", "refs/heads/main"),
            (22, "JEFF7712", "bus-route-display", "refs/heads/main"),
        ):
            result = configuration(
                {
                    "repo": {"id": repo_id, "owner": owner, "name": name},
                    "pipeline": {
                        "event": "push",
                        "ref": ref,
                        "commit": "c" * 40,
                        "number": 3,
                    },
                },
                apps,
            )
            self.assertEqual(len(result["configs"]), 1)
            self.assertEqual(
                result["configs"][0]["name"], "application-validation.yaml"
            )
            workflow = yaml.safe_load(result["configs"][0]["data"])
            self.assertEqual(workflow["steps"][0]["name"], "application-validation")


class SupplyLinkTest(unittest.TestCase):
    def test_link_entries_round_trip_identity(self) -> None:
        from scripts.ci.container_release import check_supply_item, supply_identity

        entry = {
            "kind": "link",
            "path": "/usr/local/bin/agent",
            "target": "/opt/cursor-agent/bundle/cursor-agent",
        }
        self.assertEqual(check_supply_item(entry), entry)
        self.assertEqual(supply_identity([entry]), [entry])

    def test_link_entry_rejects_relative_paths(self) -> None:
        from scripts.ci.container_release import check_supply_item

        with self.assertRaises(ValueError):
            check_supply_item(
                {"kind": "link", "path": "usr/local/bin/agent", "target": "/x"}
            )
        with self.assertRaises(ValueError):
            check_supply_item(
                {"kind": "link", "path": "/usr/local/bin/agent", "target": "../x"}
            )

    def test_link_materializes_symlink_in_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "supply").mkdir()
            layer = release.collect_supply(
                root,
                [
                    {
                        "kind": "link",
                        "path": "/usr/local/bin/agent",
                        "target": "/opt/cursor-agent/bundle/cursor-agent",
                    }
                ],
                [],
                set(),
                "3.12",
                101,
                101,
            )
            with tarfile.open(fileobj=io.BytesIO(layer), mode="r") as archive:
                names = archive.getnames()
                self.assertIn("usr/local/bin/agent", names)
                member = archive.getmember("usr/local/bin/agent")
                self.assertTrue(member.issym())
                self.assertEqual(
                    member.linkname, "/opt/cursor-agent/bundle/cursor-agent"
                )


class RunLineParsingTest(unittest.TestCase):
    def check(self, body: str) -> None:
        from scripts.ci.dockerfile_supply import check_run_lines

        check_run_lines(f"FROM x\nRUN {body}\n")

    def test_allows_tool_paths_with_forbidden_names(self) -> None:
        self.check("mkdir -p /opt/npm/codex && tar xzf t.tgz -C /opt/npm/codex")
        self.check("ln -s /opt/npm/codex/bin/codex.js /usr/local/bin/codex")

    def test_rejects_smuggled_installer_after_pip(self) -> None:
        from scripts.ci.dockerfile_supply import check_run_lines

        with self.assertRaises(ValueError):
            check_run_lines(
                "FROM x\nRUN pip install --no-index --find-links=/w /app && apt-get install -y curl\n"
            )

    def test_rejects_bare_uv_sync(self) -> None:
        from scripts.ci.dockerfile_supply import check_run_lines

        with self.assertRaises(ValueError):
            check_run_lines("FROM x\nRUN uv sync --locked --no-dev\n")


class NixOSLaneTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_nixos_push_gets_hygiene_plus_flake_check(self) -> None:
        import yaml

        apps = load_catalog(self.ROOT / "config/ci/applications.json")
        result = configuration(
            {
                "repo": {"id": 21, "owner": "JEFF7712", "name": "nixos-config"},
                "pipeline": {
                    "event": "push",
                    "ref": "refs/heads/main",
                    "commit": "d" * 40,
                    "number": 5,
                },
            },
            apps,
        )
        self.assertEqual(len(result["configs"]), 1)
        workflow = yaml.safe_load(result["configs"][0]["data"])
        names = [step["name"] for step in workflow["steps"]]
        self.assertEqual(names, ["application-validation", "nix-flake-check"])
        check = workflow["steps"][1]
        self.assertIn("nixos/nix", check["image"])
        self.assertIn("nix flake check --no-write-lock-file", check["commands"])
        self.assertNotIn("environment", workflow["steps"][0])


class SplitBaseTest(unittest.TestCase):
    def test_strips_cosmetic_tag(self) -> None:
        from scripts.ci.container_release import split_base

        repo, ref = split_base(
            "registry.rupan.dev/upstream/docker.io/library/python"
            ":3.12-slim-bookworm@sha256:" + "c" * 64
        )
        self.assertEqual(repo, "upstream/docker.io/library/python")
        self.assertEqual(ref, "sha256:" + "c" * 64)

    def test_accepts_tagless_base(self) -> None:
        from scripts.ci.container_release import split_base

        repo, ref = split_base(
            "registry.rupan.dev/upstream/docker.io/nginxinc/nginx-unprivileged@sha256:"
            + "d" * 64
        )
        self.assertEqual(repo, "upstream/docker.io/nginxinc/nginx-unprivileged")

    def test_rejects_bad_digest_and_registry(self) -> None:
        from scripts.ci.container_release import split_base

        with self.assertRaises(ValueError):
            split_base("registry.rupan.dev/upstream/x@sha256:zzz")
        with self.assertRaises(ValueError):
            split_base("docker.io/library/python@sha256:" + "c" * 64)
