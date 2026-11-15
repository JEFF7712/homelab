"""Native management-host worker. Its configuration is an installed blueprint."""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.switches.model import (
    TARGETS,
    canonical,
    digest,
    encryption_configuration,
    review_plan,
    validate_inventory,
)

STATE_BASE = "https://s3.internal:3902/state/switches-"
BLUEPRINT_FILES = ("main.tf.json", "providers.tf", "backend.tf", ".terraform.lock.hcl")


def blueprint_digest(root: Path) -> str:
    return digest(
        b"".join(
            name.encode() + b"\0" + (root / name).read_bytes()
            for name in BLUEPRINT_FILES
        )
        + b"".join(
            name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes()
            for name in ("model.py", "runner.py")
        )
    )


class Engine:
    def __init__(
        self,
        blueprint: Path,
        work_root: Path,
        tofu: str,
        keys: dict[str, str],
        credentials: Path | None = None,
        *,
        local_state_dir: Path | None = None,
        cli_config: str | None = None,
    ) -> None:
        self.blueprint, self.work_root, self.tofu = blueprint, work_root, tofu
        self.keys, self.credentials = keys, credentials
        self.local_state_dir, self.cli_config = local_state_dir, cli_config
        self.work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.work_root.chmod(0o700)
        if local_state_dir:
            local_state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextmanager
    def locked(self) -> Iterator[None]:
        with (self.work_root / "execution.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another switch operation is running") from None
            yield

    def state_bytes(self, name: str, backend: dict[str, str]) -> bytes:
        if self.local_state_dir:
            path = self.local_state_dir / (name + ".tfstate")
            return path.read_bytes() if path.exists() else b""
        if (
            backend.get("username") not in {"plan", "deploy"}
            or not backend.get("password")
            or not backend.get("ca_pem")
        ):
            raise ValueError("Authenticated TLS state-backend credentials are required")
        request = urllib.request.Request(STATE_BASE + name)
        authorization = base64.b64encode(
            (backend["username"] + ":" + backend["password"]).encode()
        ).decode()
        request.add_header("Authorization", "Basic " + authorization)
        try:
            with urllib.request.urlopen(
                request,
                context=ssl.create_default_context(cadata=backend["ca_pem"]),
                timeout=20,
            ) as response:
                return response.read(4 << 20)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return b""
            raise ValueError(f"State backend returned HTTP {error.code}") from None

    def environment(
        self,
        name: str,
        directory: Path,
        backend: dict[str, str],
        *,
        adoption: bool = False,
        write: bool = False,
    ) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TF_", "TPLINK_EASYSMART_"))
        }
        env.update(
            TF_IN_AUTOMATION="1",
            TF_INPUT="false",
            TF_VAR_switch_name=name,
            TF_VAR_adopt_existing=json.dumps(adoption),
            TF_ENCRYPTION=encryption_configuration(self.keys[name]),
            TPLINK_EASYSMART_USERNAME="readonly",
            TPLINK_EASYSMART_PASSWORD="readonly",
            TPLINK_EASYSMART_ALLOW_UNVERIFIED_FIRMWARE="false",
        )
        if self.cli_config:
            env["TF_CLI_CONFIG_FILE"] = self.cli_config
        if not self.local_state_dir:
            ca = directory / "state-ca.pem"
            ca.write_text(backend["ca_pem"])
            env.update(
                TF_HTTP_ADDRESS=STATE_BASE + name,
                TF_HTTP_LOCK_ADDRESS=STATE_BASE + name + "/lock",
                TF_HTTP_UNLOCK_ADDRESS=STATE_BASE + name + "/lock",
                TF_HTTP_LOCK_METHOD="LOCK",
                TF_HTTP_UNLOCK_METHOD="UNLOCK",
                TF_HTTP_USERNAME=backend["username"],
                TF_HTTP_PASSWORD=backend["password"],
                SSL_CERT_FILE=str(ca),
            )
        if write:
            if not self.credentials:
                raise ValueError("Write credentials are unavailable")
            credential = json.loads(self.credentials.read_text())[name]
            env.update(
                TPLINK_EASYSMART_USERNAME=credential["username"],
                TPLINK_EASYSMART_PASSWORD=credential["password"],
            )
        return env

    def run(
        self,
        directory: Path,
        env: dict[str, str],
        arguments: list[str],
        allowed: tuple[int, ...] = (0,),
    ) -> str:
        process = subprocess.Popen(
            [self.tofu, *arguments],
            cwd=directory,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=300)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode not in allowed:
            message = stdout + stderr
            for value in [
                *self.keys.values(),
                env.get("TPLINK_EASYSMART_PASSWORD", ""),
                env.get("TF_HTTP_PASSWORD", ""),
            ]:
                if value:
                    message = message.replace(value, "[REDACTED]")
            raise ValueError(f"OpenTofu {arguments[0]} failed:\n" + message[-6000:])
        return stdout

    def request(
        self, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        name = payload.get("switch")
        if name not in TARGETS or not re.fullmatch(
            r"[0-9a-f]{40}", payload.get("source_revision", "")
        ):
            raise ValueError("A known switch and exact source revision are required")
        inventory = payload["inventory"]
        validate_inventory(inventory)
        if payload.get("blueprint_sha256") != blueprint_digest(self.blueprint):
            raise ValueError(
                "Installed OpenTofu blueprint differs; deploy the management host first"
            )
        return name, inventory, payload.get("backend", {})

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        name, inventory, backend = self.request(payload)
        with self.locked():
            state = self.state_bytes(name, backend)
            adoption = not state
            plan_id = uuid.uuid4().hex
            directory = self.work_root / plan_id
            directory.mkdir(mode=0o700)
            for filename in BLUEPRINT_FILES:
                shutil.copy2(self.blueprint / filename, directory / filename)
            (directory / "inventory.json").write_bytes(canonical(inventory))
            if self.local_state_dir:
                (directory / "backend.tf").write_text(
                    'terraform {\n backend "local" {}\n}\n'
                )
            env = self.environment(name, directory, backend, adoption=adoption)
            init = [
                "init",
                "-reconfigure",
                "-input=false",
                "-lockfile=readonly",
                "-no-color",
            ]
            if self.local_state_dir:
                init.append(
                    "-backend-config=path="
                    + str(self.local_state_dir / (name + ".tfstate"))
                )
            self.run(directory, env, init)
            self.run(
                directory,
                env,
                [
                    "plan",
                    "-input=false",
                    "-no-color",
                    "-parallelism=1",
                    "-lock-timeout=20s",
                    "-detailed-exitcode",
                    "-out=plan.tfplan",
                ],
                (0, 2),
            )
            rendered = self.run(directory, env, ["show", "-json", "plan.tfplan"])
            if self.keys[name] in rendered:
                raise ValueError("Encryption key appeared in rendered plan")
            review = review_plan(
                json.loads(rendered), inventory, name, adoption=adoption
            )
            if self.state_bytes(name, backend) != state:
                raise ValueError("State changed while planning; create a new plan")
            receipt = {
                "schema_version": 1,
                "plan_id": plan_id,
                "switch": name,
                "source_revision": payload["source_revision"],
                "blueprint_sha256": blueprint_digest(self.blueprint),
                "inventory_sha256": digest(canonical(inventory)),
                "state_sha256": digest(state),
                "plan_sha256": digest((directory / "plan.tfplan").read_bytes()),
                "created_at": int(time.time()),
                "adoption": adoption,
                "review": review,
            }
            (directory / "receipt.json").write_bytes(canonical(receipt))
            return receipt

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        name, inventory, backend = self.request(payload)
        submitted = payload.get("receipt", {})
        plan_id = submitted.get("plan_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
            raise ValueError("Invalid saved-plan identifier")
        with self.locked():
            directory = self.work_root / plan_id
            receipt = json.loads((directory / "receipt.json").read_text())
            if receipt != submitted:
                raise ValueError("CI receipt differs from the immutable saved plan")
            if (
                receipt["switch"] != name
                or receipt["source_revision"] != payload["source_revision"]
                or receipt["blueprint_sha256"] != blueprint_digest(self.blueprint)
                or receipt["inventory_sha256"] != digest(canonical(inventory))
            ):
                raise ValueError("Saved plan does not match this source and inventory")
            if not 0 <= time.time() - receipt["created_at"] <= 86400:
                raise ValueError("Saved plan expired")
            if receipt["state_sha256"] != digest(self.state_bytes(name, backend)):
                raise ValueError("State changed after planning; create a new plan")
            if receipt["plan_sha256"] != digest(
                (directory / "plan.tfplan").read_bytes()
            ):
                raise ValueError("Encrypted saved plan changed")
            if not self.local_state_dir and backend.get("username") != "deploy":
                raise ValueError("Apply requires the deploy state-backend identity")
            if (directory / "inventory.json").read_bytes() != canonical(inventory):
                raise ValueError("Saved inventory changed")
            env = self.environment(
                name, directory, backend, adoption=receipt["adoption"]
            )
            rendered = self.run(directory, env, ["show", "-json", "plan.tfplan"])
            review = review_plan(
                json.loads(rendered), inventory, name, adoption=receipt["adoption"]
            )
            if review != receipt["review"]:
                raise ValueError("Saved plan review changed")
            if review["changes"]:
                env = self.environment(name, directory, backend, write=True)
            self.run(
                directory,
                env,
                [
                    "apply",
                    "-input=false",
                    "-no-color",
                    "-lock-timeout=20s",
                    "plan.tfplan",
                ],
            )
            state = self.state_bytes(name, backend)
            envelope = json.loads(state)
            if not envelope.get("encrypted_data") or "resources" in envelope:
                raise ValueError("State encryption verification failed")
            return {
                "switch": name,
                "applied_plan_id": plan_id,
                "state_sha256": digest(state),
                "review": review,
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "apply"])
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--state-keys", type=Path, required=True)
    parser.add_argument("--write-credentials", type=Path, required=True)
    parser.add_argument("--tofu", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        payload = json.loads(sys.stdin.buffer.read(2 << 20))
        engine = Engine(
            args.blueprint,
            args.work_root,
            args.tofu,
            json.loads(args.state_keys.read_text()),
            args.write_credentials,
        )
        result = getattr(engine, args.action)(payload)
        print(json.dumps(result))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f"switch-tofu: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
