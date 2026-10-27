from __future__ import annotations

import base64
import gzip
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Application:
    id: str
    repository: str
    woodpecker_repository_id: int | None
    default_branch: str
    validator: str
    validation_image: str
    release_base_image: str
    artifact_repository: str
    deployment_path: str
    state: str
    release_kind: str
    release_files: list[Any]
    release_supply: list[Any]
    release_scripts: list[Any]
    release_python: str
    release_uid: int
    release_gid: int
    release_user: str
    release_entrypoint: list[Any]
    release_env: dict[str, Any]
    release_workdir: str
    node_manager: str
    node_dir: str
    node_build: list[Any]
    node_outputs: list[Any]
    node_seeds: list[Any]
    node_runtime: str


def load_catalog(path: Path) -> tuple[Application, ...]:
    data = json.loads(path.read_text())
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "applications"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Unsupported application catalog schema")
    if not isinstance(data["applications"], list):
        raise ValueError("Application catalog must contain a list")
    applications: list[Application] = []
    identifiers: set[str] = set()
    repositories: set[str] = set()
    enrollments: set[int] = set()
    for item in data["applications"]:
        if not isinstance(item, dict) or set(item) != set(
            Application.__dataclass_fields__
        ):
            raise ValueError("Invalid application catalog fields")
        if any(
            not isinstance(v, str) or not v
            for k, v in item.items()
            if k
            not in {
                "woodpecker_repository_id",
                "release_files",
                "release_supply",
                "release_scripts",
                "release_python",
                "release_uid",
                "release_gid",
                "release_user",
                "release_entrypoint",
                "release_env",
                "release_workdir",
                "node_manager",
                "node_dir",
                "node_build",
                "node_outputs",
                "node_seeds",
                "node_runtime",
            }
        ):
            raise ValueError("Application catalog values must be nonempty strings")
        app = Application(**item)
        enrollment = app.woodpecker_repository_id
        if enrollment is not None and (type(enrollment) is not int or enrollment <= 1):
            raise ValueError(
                "Application enrollment must be a non-homelab repository ID"
            )
        if (
            not re.fullmatch(r"[a-z][a-z0-9-]*", app.id)
            or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", app.repository)
            or app.repository == "JEFF7712/homelab"
        ):
            raise ValueError("Invalid application identity")
        if (
            app.default_branch not in {"main", "develop"}
            or app.validator
            not in {
                "static-site-v1",
                "python-service-v1",
                "dotnet-service-v1",
                "node-service-v1",
                "nixos-config-v1",
                "firmware-v1",
            }
            or app.state not in {"stateless", "stateful"}
        ):
            raise ValueError("Unsupported application contract")
        expected_kinds = {
            "static-site-v1": {"static"},
            "python-service-v1": {"container", "none"},
            "dotnet-service-v1": {"none", "dotnet"},
            "node-service-v1": {"none", "node"},
            "nixos-config-v1": {"none"},
            "firmware-v1": {"none"},
        }[app.validator]
        if app.release_kind not in expected_kinds:
            raise ValueError("Release kind must match the validator contract")
        if app.release_kind in {"container", "dotnet"}:
            from scripts.ci.container_release import (
                check_files_manifest,
                check_scripts_manifest,
                check_supply_item,
                parse_manifests,
            )

            check_files_manifest(json.dumps(app.release_files))
            supply_entries = list(parse_manifests(json.dumps(app.release_supply)))
            if app.validator == "python-service-v1" and not supply_entries:
                raise ValueError("Python container releases must pin retained supply")
            for entry in supply_entries:
                check_supply_item(entry)
            check_scripts_manifest(json.dumps(app.release_scripts))
            if not re.fullmatch(r"3\.(1[0-9])", app.release_python):
                raise ValueError("Release Python must be pinned like 3.12")
            for number, name in ((app.release_uid, "uid"), (app.release_gid, "gid")):
                if type(number) is not int or not 0 < number < 60000:
                    raise ValueError(f"Release {name} must be a nonzero id")
            if app.release_user != f"{app.release_uid}:{app.release_gid}":
                raise ValueError("Release user must match uid:gid")
            if (
                not isinstance(app.release_entrypoint, list)
                or not app.release_entrypoint
                or not all(isinstance(p, str) and p for p in app.release_entrypoint)
            ):
                raise ValueError("Release entrypoint must be a nonempty string list")
            if not isinstance(app.release_env, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in app.release_env.items()
            ):
                raise ValueError("Release env must be a string mapping")
            if not app.release_workdir.startswith("/"):
                raise ValueError("Release workdir must be absolute")
            if app.release_kind == "dotnet":
                from scripts.ci.dotnet_release import FILES, check_contract

                check_contract(supply_entries, app.validation_image)
                if app.release_files != FILES or app.release_scripts:
                    raise ValueError("Dotnet releases require compiler-owned outputs")
        elif app.release_kind == "node":
            from scripts.ci.node_release import (
                check_build_manifest,
                check_outputs_manifest,
                check_seeds_manifest,
            )
            from scripts.ci.node_release import (
                check_files_manifest as check_node_files,
            )
            from scripts.ci.node_release import (
                check_supply_item as check_node_supply,
            )
            from scripts.ci.node_release import (
                parse_manifests as parse_node_manifests,
            )

            if app.validator != "node-service-v1":
                raise ValueError("Node releases require the node-service validator")
            if app.node_manager != "npm":
                raise ValueError("Node releases must pin the npm package manager")
            if app.node_dir and (
                app.node_dir.startswith("/")
                or re.search(r"(^|/)\.\.(/|$)", app.node_dir)
            ):
                raise ValueError("Node build directory must be contained relative")
            check_build_manifest(json.dumps(app.node_build))
            check_outputs_manifest(json.dumps(app.node_outputs))
            seed_pairs = check_seeds_manifest(json.dumps(app.node_seeds))
            check_node_files(json.dumps(app.release_files))
            supply_entries = list(parse_node_manifests(json.dumps(app.release_supply)))
            if not supply_entries:
                raise ValueError("Node releases must pin retained supply")
            dests = set()
            filenames = set()
            for entry in supply_entries:
                check_node_supply(entry)
                dests.add(entry["dest"])
                filenames.add(entry["filename"])
            if (
                "/inputs/node.tar.gz" not in dests
                or "/inputs/node-musl.tar.gz" not in dests
                or "/inputs/node-libs.tar.gz" not in dests
                or "/inputs/npm-cache.tar.gz" not in dests
            ):
                raise ValueError(
                    "Node releases must retain both toolchains, system libraries,"
                    " and the npm cache"
                )
            for filename, _dest in seed_pairs:
                if filename not in filenames:
                    raise ValueError(
                        f"Seed references unretained supply file: {filename}"
                    )
            if app.release_python or app.release_scripts:
                raise ValueError("Node releases must not carry Python fields")
            for number, name in ((app.release_uid, "uid"), (app.release_gid, "gid")):
                if type(number) is not int or not 0 < number < 60000:
                    raise ValueError(f"Release {name} must be a nonzero id")
            if app.release_user != f"{app.release_uid}:{app.release_gid}":
                raise ValueError("Release user must match uid:gid")
            if app.node_runtime not in {"node", "static"}:
                raise ValueError("Node runtime must be node or static")
            if app.node_runtime == "static" and (app.release_uid, app.release_gid) != (
                101,
                101,
            ):
                raise ValueError("Static releases must run as 101:101")
            if app.node_runtime == "node":
                if (
                    not isinstance(app.release_entrypoint, list)
                    or not app.release_entrypoint
                    or not all(isinstance(p, str) and p for p in app.release_entrypoint)
                ):
                    raise ValueError("Node server releases need a nonempty entrypoint")
                if not app.release_workdir.startswith("/"):
                    raise ValueError("Node server releases need an absolute workdir")
            else:
                if app.release_entrypoint:
                    raise ValueError("Static releases keep the base entrypoint")
                if app.release_workdir:
                    raise ValueError("Static releases keep the base workdir")
            if not isinstance(app.release_env, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in app.release_env.items()
            ):
                raise ValueError("Release env must be a string mapping")
        elif (
            any(
                [
                    app.release_files,
                    app.release_supply,
                    app.release_scripts,
                    app.release_python,
                    app.release_user,
                    app.release_entrypoint,
                    app.release_env,
                    app.release_workdir,
                    app.node_manager,
                    app.node_dir,
                    app.node_build,
                    app.node_outputs,
                    app.node_runtime,
                    app.node_seeds,
                ]
            )
            or app.release_uid != 0
            or app.release_gid != 0
        ):
            raise ValueError("Non-container releases must not carry container fields")
        retained_dotnet_sdk = app.release_kind == "dotnet" and re.fullmatch(
            r"registry\.rupan\.dev/apps/dotnet-sdk-6@sha256:[a-f0-9]{64}",
            app.validation_image,
        )
        if not retained_dotnet_sdk and not re.fullmatch(
            r"registry\.rupan\.dev/upstream/(docker\.io/(library/python|nginxinc/nginx-unprivileged|oven/bun|library/node|nixos/nix)|ghcr\.io/astral-sh/uv)[A-Za-z0-9_./-]*@sha256:[a-f0-9]{64}",
            app.validation_image,
        ):
            raise ValueError("Validation image must be an immutable local image")
        if not re.fullmatch(
            r"registry\.rupan\.dev/upstream/(docker\.io/(library/python|library/nginx|library/node|nginxinc/nginx-unprivileged)|ghcr\.io/linuxserver/baseimage-alpine)[A-Za-z0-9_./-]*@sha256:[a-f0-9]{64}",
            app.release_base_image,
        ):
            raise ValueError("Release base must be an immutable local image")
        if app.release_kind == "node":
            if (
                app.node_runtime == "node"
                and "/library/node" not in app.release_base_image
            ):
                raise ValueError("Node server releases require a node base image")
            if app.node_runtime == "static" and (
                "/library/nginx" not in app.release_base_image
                and "/nginxinc/nginx-unprivileged" not in app.release_base_image
            ):
                raise ValueError("Static node releases require an nginx base image")
        if app.release_kind == "container":
            if app.artifact_repository != f"apps/{app.id}":
                raise ValueError("Application artifact must match its identity")
        elif app.release_kind in {"static", "node"}:
            if app.artifact_repository != f"apps/{app.id}":
                raise ValueError("Application artifact must match its identity")
        elif app.validator in {"nixos-config-v1", "firmware-v1"}:
            if app.artifact_repository != "-":
                raise ValueError("Non-published applications must not claim artifacts")
        else:
            if app.artifact_repository != f"apps/{app.id}":
                raise ValueError("Application artifact must match its identity")
        if app.deployment_path == "-":
            if not (
                app.release_kind == "none"
                and app.validator in {"nixos-config-v1", "firmware-v1"}
            ):
                raise ValueError("Empty deployment path requires a non-published app")
        elif not re.fullmatch(
            r"gitops/(websites/[A-Za-z0-9][A-Za-z0-9_.-]*|media|pod-agent|voice|obsidian)",
            app.deployment_path,
        ):
            raise ValueError("Application deployment path must be an owned GitOps path")
        if (
            app.id in identifiers
            or app.repository in repositories
            or enrollment is not None
            and enrollment in enrollments
        ):
            raise ValueError("Duplicate application identity or enrollment")
        identifiers.add(app.id)
        repositories.add(app.repository)
        if enrollment is not None:
            enrollments.add(enrollment)
        applications.append(app)
    return tuple(applications)


def configuration(
    request: dict[str, Any], applications: tuple[Application, ...]
) -> dict[str, Any]:
    import yaml

    repo, pipeline = request["repo"], request["pipeline"]
    identity = f"{repo.get('owner', repo.get('namespace'))}/{repo.get('name')}"
    app = next(
        (
            a
            for a in applications
            if a.woodpecker_repository_id is not None
            and type(repo.get("id")) is int
            and a.woodpecker_repository_id == repo["id"]
            and a.repository == identity
        ),
        None,
    )
    if app is None:
        raise ValueError("Application repository is not enrolled")
    if pipeline.get("variables") or pipeline.get("deploy_to") or pipeline.get("deploy"):
        raise ValueError(
            "Application pipeline cannot request operations or variable overrides"
        )
    event = pipeline.get("event")
    if event not in {
        "push",
        "pull_request",
        "manual",
        "pull_request_closed",
        "pull_request_metadata",
        "tag",
        "release",
    }:
        raise ValueError("Unsupported application event")
    lifecycle = event in {
        "pull_request_closed",
        "pull_request_metadata",
        "tag",
        "release",
    }
    validator_files = {
        "static-site-v1": "static_site.py",
        "python-service-v1": "python_service.py",
        "dotnet-service-v1": "dotnet_service.py",
        "node-service-v1": "node_service.py",
        "nixos-config-v1": "nixos_config.py",
        "firmware-v1": "firmware.py",
    }
    bundled_supply = {"python-service-v1", "dotnet-service-v1", "node-service-v1"}

    def validator_source(validator: str) -> bytes:
        """Self-contained validator source for secret-free sandbox execution.

        Supply validators share dockerfile_supply, which cannot be imported
        from a repository checkout (the sandbox must never execute checkout
        modules). The server therefore bundles the shared module with the
        validator and strips the sibling import.
        """
        text = Path(__file__).with_name(validator_files[validator]).read_text()
        if validator not in bundled_supply:
            return text.encode()
        supply = Path(__file__).with_name("dockerfile_supply.py").read_text()
        kept: list[str] = []
        skipping = False
        for line in text.splitlines():
            if line.startswith("from scripts.ci.dockerfile_supply import"):
                skipping = not line.rstrip().endswith(")")
                continue
            if skipping:
                skipping = line.strip() != ")"
                continue
            if line.startswith("from __future__ import"):
                continue
            kept.append(line)
        bundled = "\n".join(
            line
            for line in supply.splitlines()
            if not line.startswith("from __future__ import")
        )
        return (bundled + "\n" + "\n".join(kept) + "\n").encode()

    encoded_validators = {
        key: base64.b64encode(validator_source(key)).decode() for key in validator_files
    }
    encoded = encoded_validators[app.validator]
    if app.validator == "nixos-config-v1" and not lifecycle:
        workflow = {
            "when": [{"event": event}],
            "labels": {"tier": "sandbox", "type": "docker"},
            "steps": [
                {
                    "name": "application-validation",
                    "image": "registry.rupan.dev/upstream/docker.io/library/python@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc",
                    "commands": [
                        f"python -I -c \"import base64; exec(compile(base64.b64decode('{encoded}'), '<server-owned-static-validator>', 'exec'))\""
                    ],
                },
                {
                    "name": "nix-flake-check",
                    "image": app.validation_image,
                    "environment": {
                        "NIX_CONFIG": (
                            "experimental-features = nix-command flakes\n"
                            "accept-flake-config = true\n"
                            "sandbox = false\n"
                            # Host daemons kill builders sharing nixbld UIDs.
                            "build-users-group =\n"
                            "max-jobs = 2\n"
                            "cores = 2\n"
                            "connect-timeout = 5\n"
                            "download-attempts = 3\n"
                            "extra-substituters = http://10.0.30.20:8080/homelab?priority=30\n"
                            "extra-trusted-public-keys = homelab:J+OVQOCG2sNT2KoVbWGPikoWcIbBanHnY2NOcMF3vwk=\n"
                        )
                    },
                    "commands": [
                        "nix fmt -- --fail-on-change --no-cache",
                        "nix flake check --no-write-lock-file",
                    ],
                },
            ],
        }
        name = "application-validation"
        configs = [
            {"name": name + ".yaml", "data": yaml.safe_dump(workflow, sort_keys=False)}
        ]
        return {"configs": configs}
    workflow = {
        "when": [{"event": event}],
        "labels": {"tier": "sandbox", "type": "docker"},
        "steps": [
            {
                "name": "application-lifecycle"
                if lifecycle
                else "application-validation",
                "image": app.validation_image,
                "commands": [
                    "true"
                    if lifecycle
                    else f"python -I -c \"import base64; exec(compile(base64.b64decode('{encoded}'), '<server-owned-static-validator>', 'exec'))\""
                ],
            }
        ],
    }
    name = "application-lifecycle" if lifecycle else "application-validation"
    configs = [
        {"name": name + ".yaml", "data": yaml.safe_dump(workflow, sort_keys=False)}
    ]
    releasable = app.release_kind in {"static", "container", "node", "dotnet"}
    if (
        event == "push"
        and pipeline.get("ref") == f"refs/heads/{app.default_branch}"
        and not pipeline.get("from_fork")
        and releasable
    ):
        commit, number = pipeline.get("commit"), pipeline.get("number")
        if (
            not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or type(number) is not int
            or not 0 < number < 1000000
        ):
            raise ValueError(
                "Main release requires exact source commit and pipeline number"
            )
        if app.release_kind == "static":
            release_source = Path(__file__).with_name("static_release.py").read_bytes()
            release_tag = "<server-owned-static-release>"
            release_stub = (
                "python -I -c \"import base64; exec(compile(base64.b64decode('{encoded}'), "
                f"'{release_tag}', 'exec'))\""
            )
            release_payload = base64.b64encode(release_source).decode()
            release_args = [
                "--base",
                app.release_base_image,
                "--commit",
                commit,
                "--repository",
                app.artifact_repository,
                "--number",
                str(number),
            ]
        elif app.release_kind == "node":
            release_source = Path(__file__).with_name("node_release.py").read_bytes()
            release_tag = "<server-owned-node-release>"
            release_stub = (
                'python -I -c "import base64,gzip; exec(compile(gzip.decompress('
                f"base64.b64decode('{{encoded}}')), '{release_tag}', 'exec'))\""
            )
            release_payload = base64.b64encode(
                gzip.compress(release_source, mtime=0)
            ).decode()
            release_args = [
                "--base",
                app.release_base_image,
                "--commit",
                commit,
                "--repository",
                app.artifact_repository,
                "--number",
                str(number),
                "--files",
                json.dumps(app.release_files),
                "--supply",
                json.dumps(app.release_supply),
                "--manager",
                app.node_manager,
                "--dir",
                app.node_dir,
                "--build",
                json.dumps(app.node_build),
                "--outputs",
                json.dumps(app.node_outputs),
                "--seeds",
                json.dumps(app.node_seeds),
                "--runtime",
                app.node_runtime,
                "--uid",
                str(app.release_uid),
                "--gid",
                str(app.release_gid),
                "--user",
                app.release_user,
                "--entrypoint",
                json.dumps(app.release_entrypoint),
                "--env",
                json.dumps(app.release_env),
                "--workdir",
                app.release_workdir,
                "--forgejo-owner",
                app.repository.split("/")[0],
            ]
        else:
            if app.release_kind == "dotnet":
                from scripts.ci.dotnet_release import bundled_source

                release_source = bundled_source(Path(__file__).parent)
            else:
                release_source = (
                    Path(__file__).with_name("container_release.py").read_bytes()
                )
            release_tag = "<server-owned-container-release>"
            release_stub = (
                'python -I -c "import base64,gzip; exec(compile(gzip.decompress('
                f"base64.b64decode('{{encoded}}')), '{release_tag}', 'exec'))\""
            )
            release_payload = base64.b64encode(
                gzip.compress(release_source, mtime=0)
            ).decode()
            release_args = [
                "--base",
                app.release_base_image,
                "--commit",
                commit,
                "--repository",
                app.artifact_repository,
                "--number",
                str(number),
                "--files",
                json.dumps(app.release_files),
                "--supply",
                json.dumps(app.release_supply),
                "--scripts",
                json.dumps(app.release_scripts),
                "--python",
                app.release_python,
                "--uid",
                str(app.release_uid),
                "--gid",
                str(app.release_gid),
                "--user",
                app.release_user,
                "--entrypoint",
                json.dumps(app.release_entrypoint),
                "--env",
                json.dumps(app.release_env),
                "--workdir",
                app.release_workdir,
                "--forgejo-owner",
                app.repository.split("/")[0],
            ]
            if app.release_kind == "dotnet":
                release_args.extend(["--build-image", app.validation_image])
        command = release_stub.format(encoded=release_payload)
        arguments = " ".join(shlex.quote(v) for v in release_args)
        if app.release_kind == "static":
            assemble_name = "assemble-static-image"
        elif app.release_kind == "node":
            assemble_name = "assemble-node-image"
        else:
            assemble_name = "assemble-container-image"
        prepare_env: dict[str, Any] = {
            "REGISTRY_PASSWORD": {"from_secret": "registry_read_password"}
        }
        if app.release_kind in {"container", "node", "dotnet"}:
            prepare_env["FORGEJO_TOKEN"] = {"from_secret": "supply_read_password"}
        release = {
            "when": [{"event": "push", "branch": app.default_branch}],
            "depends_on": ["application-validation"],
            "labels": {"tier": "sandbox", "type": "docker"},
            "steps": [
                {
                    "name": "prepare-retained-inputs",
                    "image": app.validation_image,
                    "environment": prepare_env,
                    "commands": [command + " prepare " + arguments],
                },
                {
                    "name": assemble_name,
                    "image": app.validation_image,
                    "commands": [command + " assemble " + arguments],
                },
                {
                    "name": "publish-verified-image",
                    "image": app.validation_image,
                    "environment": {
                        "REGISTRY_PASSWORD": {
                            "from_secret": app.id + "_registry_password"
                        }
                    },
                    "commands": [command + " publish " + arguments],
                },
            ],
        }
        configs.append(
            {
                "name": "application-release.yaml",
                "data": yaml.safe_dump(release, sort_keys=False),
            }
        )
    return {"configs": configs}
