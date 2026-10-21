from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from scripts.ci import container_release as release

INPUTS = {
    "/inputs/nuget-supply.tar.gz": "nuget",
    "/inputs/yarn-supply.tar.gz": "yarn-mirror",
    "/inputs/node.tar.gz": "node",
    "/inputs/yarn-cli.tgz": "yarn-cli",
}
PROJECTS = (
    "NzbDrone.Console/Readarr.Console.csproj",
    "NzbDrone.Update/Readarr.Update.csproj",
    "NzbDrone.Mono/Readarr.Mono.csproj",
)
FILES = [
    ["runtime/bin", "app/bin"],
    ["runtime/dotnet", "app/dotnet"],
    ["runtime/.sovereign-build.json", "app/.sovereign-build.json"],
]


def bundled_source(directory: Path) -> bytes:
    import base64

    library = base64.b64encode(
        (directory / "container_release.py").read_bytes()
    ).decode()
    compiler = base64.b64encode((directory / "dotnet_release.py").read_bytes()).decode()
    return (
        "import base64,sys,types\n"
        "scripts=types.ModuleType('scripts'); scripts.__path__=[]\n"
        "ci=types.ModuleType('scripts.ci'); ci.__path__=[]\n"
        "scripts.ci=ci; sys.modules['scripts']=scripts; sys.modules['scripts.ci']=ci\n"
        "module=types.ModuleType('scripts.ci.container_release')\n"
        "sys.modules[module.__name__]=module; ci.container_release=module\n"
        f"exec(compile(base64.b64decode({library!r}),'<server-owned-oci-library>','exec'),module.__dict__)\n"
        f"exec(compile(base64.b64decode({compiler!r}),'<server-owned-dotnet-release>','exec'))\n"
    ).encode()


def check_contract(supply: list[dict[str, Any]], image: str) -> None:
    if not re.fullmatch(
        r"registry\.rupan\.dev/apps/dotnet-sdk-6@sha256:[0-9a-f]{64}", image
    ):
        raise ValueError("Dotnet compiler must be a pinned retained SDK image")
    destinations: set[str] = set()
    filenames: set[str] = set()
    for item in supply:
        release.check_supply_item(item)
        if item["kind"] != "file":
            raise ValueError("Dotnet supply must use pinned files")
        if item["dest"] in destinations or item["filename"] in filenames:
            raise ValueError("Dotnet supply has duplicate destinations or filenames")
        destinations.add(item["dest"])
        filenames.add(item["filename"])
    if not set(INPUTS).issubset(destinations):
        raise ValueError("Dotnet supply must retain NuGet, Yarn, Node and Yarn CLI")
    if destinations - set(INPUTS) != {"/usr/lib/x86_64-linux-gnu/libsqlite3.so.0"}:
        raise ValueError("Dotnet runtime must retain its SQLite library")


def unpack(data: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > 100000 or sum(m.size for m in members) > 4 * 1024**3:
            raise ValueError("Dotnet retained archive exceeds extraction bounds")
        archive.extractall(destination, members=members, filter="data")


def runtime_supply(supply: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in supply if item["dest"] not in INPUTS]


def copy_build_source(source: Path, destination: Path) -> None:
    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns(".git", ".sovereign")
    )


def check_runtime_host(runtime: Path) -> None:
    host = runtime / "dotnet/dotnet"
    if not host.is_file() or not host.stat().st_mode & 0o111:
        raise ValueError("Dotnet runtime host must be an executable file")


def build(
    source: Path,
    base: Path,
    output: Path,
    supply: list[dict[str, Any]],
    image: str,
    commit: str,
) -> None:
    check_contract(supply, image)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Dotnet build requires an exact source commit")
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    for item in supply:
        data = (base / "supply" / item["filename"]).read_bytes()
        if release.digest(data) != item["sha256"]:
            raise ValueError(f"Retained input mismatch: {item['filename']}")
        if item["dest"] in INPUTS:
            unpack(data, inputs / INPUTS[item["dest"]])
    work = output / "source"
    copy_build_source(source, work)
    for lock in work.rglob("packages.lock.json"):
        lock.unlink()
    nodes = list((inputs / "node").glob("*/bin/node"))
    yarns = list((inputs / "yarn-cli").glob("*/bin/yarn.js"))
    if len(nodes) != 1 or len(yarns) != 1:
        raise ValueError("Retained Node and Yarn must each contain one CLI")
    node, yarn = nodes[0], yarns[0]
    home = output / "home"
    home.mkdir()
    environment = {
        **os.environ,
        "HOME": str(home.resolve()),
        "PATH": str(node.parent.resolve()) + os.pathsep + os.environ["PATH"],
        "DOTNET_CLI_HOME": str(home.resolve()),
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1",
        "NUGET_PACKAGES": str((home / "nuget").resolve()),
        "SOURCE_DATE_EPOCH": "0",
    }
    for key in ("FORGEJO_TOKEN", "REGISTRY_PASSWORD", "NODE_ENV"):
        environment.pop(key, None)
    sdks = subprocess.check_output(
        ["dotnet", "--list-sdks"], env=environment, text=True
    )
    if not any(line.startswith("6.0.428 ") for line in sdks.splitlines()):
        raise ValueError("Retained SDK must provide .NET 6.0.428")
    (work / ".yarnrc").write_text(
        'yarn-offline-mirror "'
        + str((inputs / "yarn-mirror").resolve())
        + '"\n'
        + "yarn-offline-mirror-pruning false\n"
    )
    command = [str(node.resolve()), str(yarn.resolve())]
    subprocess.run(
        command + ["install", "--offline", "--frozen-lockfile", "--non-interactive"],
        cwd=work,
        env=environment,
        check=True,
    )
    subprocess.run(
        command + ["run", "build", "--env", "production"],
        cwd=work,
        env=environment,
        check=True,
    )
    config = output / "NuGet.Config"
    from xml.sax.saxutils import escape

    config.write_text(
        '<configuration><packageSources><clear/><add key="retained" value="'
        + escape(str((inputs / "nuget").resolve()), {'"': "&quot;"})
        + '"/></packageSources></configuration>\n'
    )
    runtime = output / "runtime"
    app = runtime / "bin"
    app.mkdir(parents=True)
    for project in PROJECTS:
        subprocess.run(
            [
                "dotnet",
                "publish",
                str((work / "src" / project).resolve()),
                "-c",
                "Release",
                "-r",
                "linux-x64",
                "--self-contained",
                "false",
                "-f",
                "net6.0",
                "-o",
                str(app.resolve()),
                "-p:Platform=Posix",
                "-p:RuntimeIdentifiers=linux-x64",
                "-p:RestoreConfigFile=" + str(config.resolve()),
                "-p:RestorePackagesWithLockFile=false",
                "-p:RestoreLockedMode=false",
                "-p:Deterministic=true",
                "-p:AssemblyVersion=10.0.0.0",
                "-p:EnableSourceControlManagerQueries=false",
                "-p:EnableSourceLink=false",
                "-p:SourceRevisionId=" + commit,
                "-m:2",
            ],
            cwd=work,
            env=environment,
            check=True,
        )
    shutil.copytree(work / "_output/UI", app / "UI")
    dotnet = Path(shutil.which("dotnet") or "").resolve()
    toolkit = runtime / "dotnet"
    toolkit.mkdir()
    shutil.copy2(dotnet, toolkit / "dotnet")
    for name in ("host", "shared"):
        shutil.copytree(dotnet.parent / name, toolkit / name)
    for name in (
        "Readarr.dll",
        "Readarr.Mono.dll",
        "Readarr.Update.dll",
        "UI/index.html",
    ):
        if not (app / name).is_file():
            raise ValueError(f"Dotnet release output missing: {name}")
    check_runtime_host(runtime)
    receipt = {
        "source": commit,
        "sdk_image": image,
        "supply": release.supply_identity(supply),
    }
    (runtime / ".sovereign-build.json").write_bytes(release.encode(receipt))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "assemble", "publish"))
    for name in (
        "base",
        "commit",
        "repository",
        "files",
        "supply",
        "scripts",
        "python",
        "user",
        "entrypoint",
        "env",
        "workdir",
        "forgejo-owner",
        "build-image",
    ):
        parser.add_argument("--" + name, required=True)
    for name in ("number", "uid", "gid"):
        parser.add_argument("--" + name, required=True, type=int)
    args = parser.parse_args()
    supply = [
        release.check_supply_item(x) for x in release.parse_manifests(args.supply)
    ]
    check_contract(supply, args.build_image)
    files = release.check_files_manifest(args.files)
    if files != FILES or json.loads(args.scripts):
        raise ValueError("Dotnet release must use its compiler-owned runtime tree")
    source = Path.cwd()
    base = source / ".sovereign/base"
    output = source / ".sovereign/build"
    artifact = source / ".sovereign/release"
    if args.operation == "prepare":
        if (source / ".sovereign").exists():
            raise ValueError("Reserved build workspace must not exist in source")
        release.prepare(
            base,
            args.base,
            files,
            supply,
            release.Registry("node", os.environ["REGISTRY_PASSWORD"]),
            args.forgejo_owner,
            os.environ["FORGEJO_TOKEN"],
        )
        return
    if args.operation == "assemble":
        build(source, base, output, supply, args.build_image, args.commit)
        print(
            "Assembled "
            + release.assemble(
                output,
                base,
                artifact,
                args.base,
                args.commit,
                files,
                runtime_supply(supply),
                [],
                args.python,
                args.uid,
                args.gid,
                args.user,
                json.loads(args.entrypoint),
                json.loads(args.env),
                args.workdir,
            )
        )
        return
    receipt = json.loads((output / "runtime/.sovereign-build.json").read_bytes())
    expected = {
        "source": args.commit,
        "sdk_image": args.build_image,
        "supply": release.supply_identity(supply),
    }
    if receipt != expected:
        raise ValueError("Dotnet build input provenance mismatch")
    print(
        "Published "
        + release.publish(
            artifact,
            base,
            args.base,
            args.commit,
            args.repository,
            args.number,
            files,
            runtime_supply(supply),
            [],
            args.python,
            args.uid,
            args.gid,
            args.user,
            json.loads(args.entrypoint),
            json.loads(args.env),
            args.workdir,
            release.Registry(
                release.publish_user(args.repository), os.environ["REGISTRY_PASSWORD"]
            ),
            output,
        )
    )


if __name__ == "__main__":
    main()
