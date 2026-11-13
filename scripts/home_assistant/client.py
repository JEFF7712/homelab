from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .canonical import SecretTag
from .models import LockError

# Home Assistant restarts whenever its configuration rolls out, often while a
# deploy is starting; health checks wait this long for it to come back.
READY_TIMEOUT_SECONDS = float(os.environ.get("HA_READY_TIMEOUT", "600"))
READY_POLL_SECONDS = 10.0


class HomeAssistantClientError(Exception):
    """Base exception for Home Assistant client communication failures."""


class HomeAssistantAuthError(HomeAssistantClientError):
    """Authentication or token validation failure."""


class HomeAssistantNotFoundError(HomeAssistantClientError):
    """Requested resource was not found."""


class HomeAssistantWebSocketClient:
    """Standard-library WebSocket client for Home Assistant's WebSocket API."""

    def __init__(
        self, host: str, port: int, use_ssl: bool, token: str, timeout: float = 10.0
    ) -> None:
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.token = token
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._msg_id = 1
        self.ha_version: str | None = None

    def connect(self) -> None:
        raw_sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        if self.use_ssl:
            ctx = ssl.create_default_context()
            self._sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
        else:
            self._sock = raw_sock

        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = "/api/websocket"
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {ws_key}",
            "Sec-WebSocket-Version: 13",
            "",
            "",
        ]
        self._sock.sendall("\r\n".join(headers).encode("ascii"))

        # Read handshake response until \r\n\r\n
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise HomeAssistantClientError(
                    "WebSocket connection closed during handshake"
                )
            buf += chunk

        header_bytes, self._buf = buf.split(b"\r\n\r\n", 1)
        status_line = header_bytes.split(b"\r\n")[0].decode("ascii", errors="ignore")
        if "101" not in status_line:
            raise HomeAssistantClientError(
                f"WebSocket handshake rejected: {status_line}"
            )

        # Initial message from Home Assistant must be auth_required
        init_opcode, init_data = self._read_frame()
        init_msg = json.loads(init_data.decode("utf-8"))
        if init_msg.get("type") != "auth_required":
            raise HomeAssistantClientError(
                f"Unexpected initial WebSocket message: {init_msg}"
            )
        self.ha_version = init_msg.get("ha_version")

        # Send authentication message
        self._send_frame(json.dumps({"type": "auth", "access_token": self.token}))

        auth_opcode, auth_data = self._read_frame()
        auth_msg = json.loads(auth_data.decode("utf-8"))
        if auth_msg.get("type") == "auth_invalid":
            raise HomeAssistantAuthError(
                f"WebSocket authentication failed: {auth_msg.get('message', 'Invalid token')}"
            )
        if auth_msg.get("type") != "auth_ok":
            raise HomeAssistantAuthError(
                f"Unexpected authentication response: {auth_msg}"
            )

    def _recv_exact(self, n: int) -> bytes:
        if self._sock is None:
            raise HomeAssistantClientError("Socket is not connected")
        while len(self._buf) < n:
            chunk = self._sock.recv(n - len(self._buf))
            if not chunk:
                raise HomeAssistantClientError(
                    "WebSocket connection closed prematurely"
                )
            self._buf += chunk
        res = self._buf[:n]
        self._buf = self._buf[n:]
        return res

    def _read_frame(self) -> tuple[int, bytes]:
        hdr = self._recv_exact(2)
        b1, b2 = hdr[0], hdr[1]
        opcode = b1 & 0x0F
        has_mask = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]

        mask = self._recv_exact(4) if has_mask else None
        data = self._recv_exact(length)
        if has_mask and mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data

    def _send_frame(self, data_str: str) -> None:
        if self._sock is None:
            raise HomeAssistantClientError("Socket is not connected")
        payload = data_str.encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        hdr = bytearray([0x81])  # FIN + Text opcode
        length = len(payload)
        if length < 126:
            hdr.append(0x80 | length)
        elif length < 65536:
            hdr.append(0x80 | 126)
            hdr.extend(struct.pack("!H", length))
        else:
            hdr.append(0x80 | 127)
            hdr.extend(struct.pack("!Q", length))
        self._sock.sendall(bytes(hdr) + mask + masked)

    def call(self, cmd_type: str, **kwargs: Any) -> Any:
        if self._sock is None:
            self.connect()
        msg_id = self._msg_id
        self._msg_id += 1

        msg = {"id": msg_id, "type": cmd_type, **kwargs}
        self._send_frame(json.dumps(msg))

        # Wait for matching response
        while True:
            opcode, data = self._read_frame()
            if opcode == 0x08:  # Close frame
                self.close()
                raise HomeAssistantClientError("WebSocket connection closed by server")
            if opcode != 0x01:  # Non-text frame
                continue
            resp = json.loads(data.decode("utf-8"))
            if resp.get("id") == msg_id:
                if not resp.get("success", True):
                    err = resp.get("error", {})
                    code = err.get("code", "unknown")
                    msg = err.get("message", "Error executing command")
                    if code == "config_not_found" or code == "not_found":
                        raise HomeAssistantNotFoundError(f"Resource not found: {msg}")
                    raise HomeAssistantClientError(
                        f"WebSocket command '{cmd_type}' failed ({code}): {msg}"
                    )
                return resp.get("result")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class HomeAssistantClient:
    """Unified client for Home Assistant HTTP REST and WebSocket APIs."""

    def __init__(
        self,
        base_url: str = "http://10.0.40.13:8123",
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token or os.environ.get("HASS_TOKEN", "")
        self.timeout = timeout
        parsed = urllib.parse.urlparse(self.base_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._use_ssl = parsed.scheme == "https"
        self._ws: HomeAssistantWebSocketClient | None = None

    def _get_ws(self) -> HomeAssistantWebSocketClient:
        if self._ws is None:
            self._ws = HomeAssistantWebSocketClient(
                host=self._host,
                port=self._port,
                use_ssl=self._use_ssl,
                token=self.token,
                timeout=self.timeout,
            )
            self._ws.connect()
        return self._ws

    def http_request(self, method: str, path: str, payload: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        data_bytes = None
        if payload is not None:
            data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url, data=data_bytes, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_bytes = resp.read()
                if resp_bytes:
                    return json.loads(resp_bytes.decode("utf-8"))
                return None
        except urllib.error.HTTPError as exc:
            if exc.code == 401 or exc.code == 403:
                raise HomeAssistantAuthError(
                    f"HTTP authentication failure accessing {path}: {exc.code} {exc.reason}"
                ) from exc
            if exc.code == 404:
                raise HomeAssistantNotFoundError(
                    f"Resource not found at {path}: {exc.code} {exc.reason}"
                ) from exc
            body = exc.read().decode("utf-8", errors="ignore")
            raise HomeAssistantClientError(
                f"HTTP request to {path} failed ({exc.code}): {body or exc.reason}"
            ) from exc
        except Exception as exc:
            raise HomeAssistantClientError(
                f"Network error accessing {path}: {exc}"
            ) from exc

    def check_health(self, wait: float | None = None) -> dict[str, Any]:
        """Verify connectivity and read basic configuration. Connection
        failures and a core that is still starting are retried for up to
        `wait` seconds (READY_TIMEOUT_SECONDS by default), so a deploy that
        starts during a restart waits instead of failing. Authentication
        failures are not retried."""
        deadline = time.monotonic() + (READY_TIMEOUT_SECONDS if wait is None else wait)
        while True:
            try:
                data = self.http_request("GET", "/api/config")
                state = data.get("state", "RUNNING")
                if state == "RUNNING":
                    return {
                        "version": data.get("version"),
                        "location_name": data.get("location_name"),
                        "status": "ok",
                    }
                reason = f"core is {state}"
            except HomeAssistantAuthError:
                raise
            except HomeAssistantClientError as exc:
                reason = str(exc)
            if time.monotonic() >= deadline:
                raise HomeAssistantClientError(f"Home Assistant is not ready: {reason}")
            print(f"Waiting for Home Assistant: {reason}", file=sys.stderr, flush=True)
            time.sleep(READY_POLL_SECONDS)

    def get_core_configuration(self) -> dict[str, Any]:
        """Read live core configuration.yaml from cluster pod."""
        try:
            res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "exec",
                    "deployment/home-assistant",
                    "-c",
                    "home-assistant",
                    "--",
                    "cat",
                    "/config/configuration.yaml",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                from .canonical import parse_yaml

                parsed = parse_yaml(res.stdout)
                if isinstance(parsed, dict):
                    return parsed
            err_msg = res.stderr.strip() or f"Process returned code {res.returncode}"
            raise RuntimeError(
                f"Failed to read live /config/configuration.yaml: {err_msg}"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read live /config/configuration.yaml from pod: {exc}"
            ) from exc

    # Automations
    def list_automations(self) -> list[dict[str, Any]]:
        file_err: Exception | None = None
        try:
            res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "exec",
                    "deployment/home-assistant",
                    "-c",
                    "home-assistant",
                    "--",
                    "cat",
                    "/config/automations.yaml",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0:
                from .canonical import parse_yaml

                content = res.stdout.strip()
                if not content or content == "[]":
                    return []
                items = parse_yaml(content)
                if isinstance(items, list):
                    return [it for it in items if isinstance(it, dict)]
                return []
            else:
                file_err = RuntimeError(
                    f"kubectl cat /config/automations.yaml failed (rc={res.returncode}): {res.stderr.strip()}"
                )
        except Exception as exc:
            file_err = exc

        # Fallback to entity registry / REST API
        try:
            results: list[dict[str, Any]] = []
            entities = self.list_entities()
            for ent in entities:
                ent_id = ent.get("entity_id", "")
                if ent_id.startswith("automation."):
                    auto_id = ent.get("unique_id") or ent_id.removeprefix("automation.")
                    try:
                        cfg = self.get_automation(auto_id)
                        results.append(cfg)
                    except HomeAssistantNotFoundError:
                        pass

            return results
        except Exception as api_err:
            raise RuntimeError(
                f"Failed to list automations from filesystem ({file_err}) and API ({api_err})"
            ) from api_err

    def get_automation(self, automation_id: str) -> dict[str, Any]:
        return self.http_request(
            "GET", f"/api/config/automation/config/{automation_id}"
        )

    def save_automation(self, automation_id: str, config: dict[str, Any]) -> None:
        self.http_request(
            "POST", f"/api/config/automation/config/{automation_id}", config
        )

    def delete_automation(self, automation_id: str) -> None:
        self.http_request("DELETE", f"/api/config/automation/config/{automation_id}")

    # Scripts
    def list_scripts(self) -> list[dict[str, Any]]:
        file_err: Exception | None = None
        try:
            res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "exec",
                    "deployment/home-assistant",
                    "-c",
                    "home-assistant",
                    "--",
                    "cat",
                    "/config/scripts.yaml",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0:
                from .canonical import parse_yaml

                content = res.stdout.strip()
                if not content or content == "{}":
                    return []
                data = parse_yaml(content)
                if isinstance(data, dict):
                    return [
                        {"id": k, **v} for k, v in data.items() if isinstance(v, dict)
                    ]
                return []
            else:
                file_err = RuntimeError(
                    f"kubectl cat /config/scripts.yaml failed (rc={res.returncode}): {res.stderr.strip()}"
                )
        except Exception as exc:
            file_err = exc

        # Fallback to entity registry / REST API
        try:
            results: list[dict[str, Any]] = []
            entities = self.list_entities()
            for ent in entities:
                ent_id = ent.get("entity_id", "")
                if ent_id.startswith("script."):
                    s_id = ent.get("unique_id") or ent_id.removeprefix("script.")
                    try:
                        cfg = self.get_script(s_id)
                        results.append({"id": s_id, **cfg})
                    except Exception:
                        pass
            return results
        except Exception as api_err:
            raise RuntimeError(
                f"Failed to list scripts from filesystem ({file_err}) and API ({api_err})"
            ) from api_err

    def get_script(self, script_key: str) -> dict[str, Any]:
        return self.http_request("GET", f"/api/config/script/config/{script_key}")

    def save_script(self, script_key: str, config: dict[str, Any]) -> None:
        self.http_request("POST", f"/api/config/script/config/{script_key}", config)

    def delete_script(self, script_key: str) -> None:
        self.http_request("DELETE", f"/api/config/script/config/{script_key}")

    # Scenes
    def list_scenes(self) -> list[dict[str, Any]]:
        file_err: Exception | None = None
        try:
            res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "exec",
                    "deployment/home-assistant",
                    "-c",
                    "home-assistant",
                    "--",
                    "cat",
                    "/config/scenes.yaml",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0:
                from .canonical import parse_yaml

                content = res.stdout.strip()
                if not content or content == "[]":
                    return []
                items = parse_yaml(content)
                if isinstance(items, list):
                    return [it for it in items if isinstance(it, dict)]
                return []
            else:
                file_err = RuntimeError(
                    f"kubectl cat /config/scenes.yaml failed (rc={res.returncode}): {res.stderr.strip()}"
                )
        except Exception as exc:
            file_err = exc

        # Fallback to entity registry / REST API
        try:
            results: list[dict[str, Any]] = []
            entities = self.list_entities()
            for ent in entities:
                ent_id = ent.get("entity_id", "")
                if ent_id.startswith("scene."):
                    s_id = ent.get("unique_id") or ent_id.removeprefix("scene.")
                    try:
                        cfg = self.get_scene(s_id)
                        results.append(cfg)
                    except Exception:
                        pass
            return results
        except Exception as api_err:
            raise RuntimeError(
                f"Failed to list scenes from filesystem ({file_err}) and API ({api_err})"
            ) from api_err

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        return self.http_request("GET", f"/api/config/scene/config/{scene_id}")

    def save_scene(self, scene_id: str, config: dict[str, Any]) -> None:
        self.http_request("POST", f"/api/config/scene/config/{scene_id}", config)

    def delete_scene(self, scene_id: str) -> None:
        self.http_request("DELETE", f"/api/config/scene/config/{scene_id}")

    # Lovelace Dashboards
    def list_dashboards(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("lovelace/dashboards/list")
        return res if isinstance(res, list) else []

    def get_dashboard_config(self, url_path: str | None = None) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("lovelace/config", url_path=url_path)
        return res if isinstance(res, dict) else {}

    def save_dashboard_config(
        self, url_path: str | None, config: dict[str, Any]
    ) -> None:
        ws = self._get_ws()
        ws.call("lovelace/config/save", url_path=url_path, config=config)

    def create_dashboard(
        self,
        url_path: str,
        title: str,
        icon: str | None = None,
        require_admin: bool = False,
        show_in_sidebar: bool = True,
    ) -> dict[str, Any]:
        ws = self._get_ws()
        kwargs: dict[str, Any] = {
            "url_path": url_path,
            "title": title,
            "require_admin": require_admin,
            "show_in_sidebar": show_in_sidebar,
        }
        if icon:
            kwargs["icon"] = icon
        res = ws.call("lovelace/dashboards/create", **kwargs)
        return res if isinstance(res, dict) else {}

    def delete_dashboard(self, dashboard_id: str) -> None:
        ws = self._get_ws()
        ws.call("lovelace/dashboards/delete", dashboard_id=dashboard_id)

    # Registries
    def list_areas(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("config/area_registry/list")
        return res if isinstance(res, list) else []

    def create_area(self, name: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/area_registry/create", name=name, **kwargs)
        return res if isinstance(res, dict) else {}

    def update_area(self, area_id: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/area_registry/update", area_id=area_id, **kwargs)
        return res if isinstance(res, dict) else {}

    def delete_area(self, area_id: str) -> None:
        ws = self._get_ws()
        ws.call("config/area_registry/delete", area_id=area_id)

    def list_floors(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("config/floor_registry/list")
        return res if isinstance(res, list) else []

    def create_floor(self, name: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/floor_registry/create", name=name, **kwargs)
        return res if isinstance(res, dict) else {}

    def update_floor(self, floor_id: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/floor_registry/update", floor_id=floor_id, **kwargs)
        return res if isinstance(res, dict) else {}

    def delete_floor(self, floor_id: str) -> None:
        ws = self._get_ws()
        ws.call("config/floor_registry/delete", floor_id=floor_id)

    def list_labels(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("config/label_registry/list")
        return res if isinstance(res, list) else []

    def create_label(self, name: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/label_registry/create", name=name, **kwargs)
        return res if isinstance(res, dict) else {}

    def update_label(self, label_id: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/label_registry/update", label_id=label_id, **kwargs)
        return res if isinstance(res, dict) else {}

    def delete_label(self, label_id: str) -> None:
        ws = self._get_ws()
        ws.call("config/label_registry/delete", label_id=label_id)

    def list_devices(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("config/device_registry/list")
        return res if isinstance(res, list) else []

    def update_device(self, device_id: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/device_registry/update", device_id=device_id, **kwargs)
        return res if isinstance(res, dict) else {}

    def list_entities(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("config/entity_registry/list")
        return res if isinstance(res, list) else []

    def update_entity(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("config/entity_registry/update", entity_id=entity_id, **kwargs)
        return res if isinstance(res, dict) else {}

    def list_config_entries(self) -> list[dict[str, Any]]:
        ws = self._get_ws()
        res = ws.call("config_entries/get")
        return res if isinstance(res, list) else []

    def update_dashboard(self, dashboard_id: str, **kwargs: Any) -> dict[str, Any]:
        ws = self._get_ws()
        res = ws.call("lovelace/dashboards/update", dashboard_id=dashboard_id, **kwargs)
        return res if isinstance(res, dict) else {}

    def get_cluster_baseline(self, instance: str) -> dict[str, Any] | None:
        try:
            res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "get",
                    "configmap",
                    f"home-assistant-baseline-{instance}",
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to execute kubectl to get cluster baseline for {instance}: {exc}"
            ) from exc

        if res.returncode == 0:
            try:
                cm = json.loads(res.stdout)
                baseline_str = cm.get("data", {}).get("baseline.json")
                if baseline_str:
                    return json.loads(baseline_str)
                return None
            except Exception as exc:
                raise RuntimeError(
                    f"Corrupt cluster baseline ConfigMap for {instance}: {exc}"
                ) from exc

        stderr = res.stderr or ""
        if "NotFound" in stderr or "not found" in stderr:
            return None

        raise RuntimeError(
            f"Kubectl error fetching cluster baseline for {instance} (code {res.returncode}): "
            f"{stderr.strip() or res.stdout.strip()}"
        )

    def save_cluster_baseline(
        self, instance: str, baseline_data: dict[str, Any]
    ) -> None:
        payload = json.dumps(baseline_data, indent=2)
        cm_name = f"home-assistant-baseline-{instance}"
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": cm_name,
                "namespace": "home-assistant",
                "annotations": {
                    "home-assistant.homelab/content-hash": baseline_data.get(
                        "content_hash", ""
                    ),
                    "home-assistant.homelab/source-commit": baseline_data.get(
                        "source_commit", ""
                    ),
                    "home-assistant.homelab/updated-at": str(time.time()),
                },
            },
            "data": {"baseline.json": payload},
        }
        manifest = json.dumps(cm)
        try:
            res = subprocess.run(
                ["kubectl", "replace", "-f", "-"],
                input=manifest,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            if res.returncode != 0 and "not found" in (res.stderr or "").lower():
                res = subprocess.run(
                    ["kubectl", "create", "-f", "-"],
                    input=manifest,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to execute kubectl to save cluster baseline: {exc}"
            ) from exc

        if res.returncode != 0:
            raise RuntimeError(
                f"Kubectl failed to save cluster baseline for {instance} (code {res.returncode}): "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )

        # Readback verification
        readback = self.get_cluster_baseline(instance)
        if not readback:
            raise RuntimeError(
                f"Verification readback failed: ConfigMap {cm_name} not found after write"
            )
        if readback.get("content_hash") != baseline_data.get("content_hash"):
            raise RuntimeError(
                f"Verification readback content_hash mismatch: expected {baseline_data.get('content_hash')}, "
                f"got {readback.get('content_hash')}"
            )

    def acquire_cluster_lock(
        self, instance: str, owner: str, plan_id: str, timeout_seconds: int = 300
    ) -> None:
        cm_name = f"home-assistant-lock-{instance}"
        now = time.time()
        expires_at = now + timeout_seconds
        lock_data = {
            "owner": owner,
            "plan_id": plan_id,
            "acquired_at": now,
            "expires_at": expires_at,
        }
        payload = json.dumps(lock_data, indent=2)

        try:
            res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "create",
                    "configmap",
                    cm_name,
                    f"--from-literal=lock.json={payload}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Kubectl command error while acquiring cluster lock: {exc}"
            ) from exc

        if res.returncode == 0:
            return

        stderr = res.stderr or ""
        if "AlreadyExists" in stderr or "already exists" in stderr:
            try:
                get_res = subprocess.run(
                    [
                        "kubectl",
                        "-n",
                        "home-assistant",
                        "get",
                        "configmap",
                        cm_name,
                        "-o",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            except Exception as exc:
                raise LockError(
                    f"Cluster lock exists but failed to inspect: {exc}"
                ) from exc

            if get_res.returncode == 0:
                try:
                    cm = json.loads(get_res.stdout)
                    raw_lock = cm.get("data", {}).get("lock.json")
                    if raw_lock:
                        data = json.loads(raw_lock)
                        held_expires = data.get("expires_at", 0)
                        held_owner = data.get("owner", "unknown")
                        held_plan = data.get("plan_id", "unknown")
                        if now < held_expires:
                            raise LockError(
                                f"Cluster apply lock already held by '{held_owner}' for plan '{held_plan}' until {held_expires}"
                            )
                        # Lock expired: take over by deleting stale ConfigMap
                        subprocess.run(
                            [
                                "kubectl",
                                "-n",
                                "home-assistant",
                                "delete",
                                "configmap",
                                cm_name,
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=10,
                        )
                        return self.acquire_cluster_lock(
                            instance, owner, plan_id, timeout_seconds
                        )
                except LockError:
                    raise
                except Exception:
                    raise LockError(
                        f"Cluster lock ConfigMap {cm_name} exists and cannot be safely verified"
                    )

        raise LockError(
            f"Failed to acquire cluster lock: {stderr.strip() or res.stdout.strip()}"
        )

    def renew_cluster_lock(
        self, instance: str, owner: str, plan_id: str, timeout_seconds: int = 300
    ) -> None:
        cm_name = f"home-assistant-lock-{instance}"
        now = time.time()
        expires_at = now + timeout_seconds
        lock_data = {
            "owner": owner,
            "plan_id": plan_id,
            "acquired_at": now,
            "expires_at": expires_at,
        }
        payload = json.dumps(lock_data, indent=2)
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": cm_name, "namespace": "home-assistant"},
            "data": {"lock.json": payload},
        }
        try:
            subprocess.run(
                ["kubectl", "apply", "-f", "-"],
                input=json.dumps(cm),
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except Exception:
            pass

    def release_cluster_lock(self, instance: str, owner: str, plan_id: str) -> None:
        cm_name = f"home-assistant-lock-{instance}"
        try:
            get_res = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "home-assistant",
                    "get",
                    "configmap",
                    cm_name,
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if get_res.returncode == 0:
                cm = json.loads(get_res.stdout)
                raw_lock = cm.get("data", {}).get("lock.json")
                if raw_lock:
                    data = json.loads(raw_lock)
                    if data.get("plan_id") == plan_id and data.get("owner") == owner:
                        subprocess.run(
                            [
                                "kubectl",
                                "-n",
                                "home-assistant",
                                "delete",
                                "configmap",
                                cm_name,
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=10,
                        )
        except Exception:
            pass

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None


class MockHomeAssistantClient(HomeAssistantClient):
    """Offline credential-free mock client for deterministic testing."""

    def __init__(self) -> None:
        super().__init__(base_url="http://mock.local:8123", token="mock-token")
        self.automations: dict[str, dict[str, Any]] = {}
        self.scripts: dict[str, dict[str, Any]] = {}
        self.scenes: dict[str, dict[str, Any]] = {}
        self.dashboards: dict[str, dict[str, Any]] = {
            "map": {"views": [{"title": "Map View", "cards": [{"type": "map"}]}]}
        }
        self.dashboard_list: list[dict[str, Any]] = [
            {
                "id": "map",
                "title": "Map",
                "url_path": "map",
                "mode": "storage",
                "show_in_sidebar": True,
            }
        ]
        self.cluster_baselines: dict[str, dict[str, Any]] = {}
        self.cluster_locks: dict[str, dict[str, Any]] = {}
        self.areas: list[dict[str, Any]] = [
            {
                "area_id": "living_room",
                "name": "Living Room",
                "icon": "mdi:sofa",
                "floor_id": None,
                "aliases": [],
            }
        ]
        self.floors: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self.entities: list[dict[str, Any]] = []
        self.config_entries: list[dict[str, Any]] = []
        self.core_config: dict[str, Any] = {
            "default_config": None,
            "recorder": {"db_url": SecretTag("recorder_db_url")},
        }

    def check_health(self, wait: float | None = None) -> dict[str, Any]:
        return {"version": "2026.9.1", "location_name": "Mock Home", "status": "ok"}

    def get_core_configuration(self) -> dict[str, Any]:
        return dict(self.core_config)

    def list_automations(self) -> list[dict[str, Any]]:
        return list(self.automations.values())

    def list_scripts(self) -> list[dict[str, Any]]:
        return [{"id": k, **v} for k, v in self.scripts.items()]

    def list_scenes(self) -> list[dict[str, Any]]:
        return list(self.scenes.values())

    def get_automation(self, automation_id: str) -> dict[str, Any]:
        if automation_id not in self.automations:
            raise HomeAssistantNotFoundError(f"Automation {automation_id} not found")
        return self.automations[automation_id]

    def save_automation(self, automation_id: str, config: dict[str, Any]) -> None:
        self.automations[automation_id] = config
        ent_id = f"automation.{automation_id}"
        if not any(e.get("entity_id") == ent_id for e in self.entities):
            self.entities.append({"entity_id": ent_id, "unique_id": automation_id})

    def delete_automation(self, automation_id: str) -> None:
        if automation_id in self.automations:
            del self.automations[automation_id]
        ent_id = f"automation.{automation_id}"
        self.entities = [e for e in self.entities if e.get("entity_id") != ent_id]

    def get_script(self, script_key: str) -> dict[str, Any]:
        if script_key not in self.scripts:
            raise HomeAssistantNotFoundError(f"Script {script_key} not found")
        return self.scripts[script_key]

    def save_script(self, script_key: str, config: dict[str, Any]) -> None:
        self.scripts[script_key] = config
        ent_id = f"script.{script_key}"
        if not any(e.get("entity_id") == ent_id for e in self.entities):
            self.entities.append({"entity_id": ent_id, "unique_id": script_key})

    def delete_script(self, script_key: str) -> None:
        if script_key in self.scripts:
            del self.scripts[script_key]
        ent_id = f"script.{script_key}"
        self.entities = [e for e in self.entities if e.get("entity_id") != ent_id]

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        if scene_id not in self.scenes:
            raise HomeAssistantNotFoundError(f"Scene {scene_id} not found")
        return self.scenes[scene_id]

    def save_scene(self, scene_id: str, config: dict[str, Any]) -> None:
        self.scenes[scene_id] = config
        ent_id = f"scene.{scene_id}"
        if not any(e.get("entity_id") == ent_id for e in self.entities):
            self.entities.append({"entity_id": ent_id, "unique_id": scene_id})

    def delete_scene(self, scene_id: str) -> None:
        if scene_id in self.scenes:
            del self.scenes[scene_id]
        ent_id = f"scene.{scene_id}"
        self.entities = [e for e in self.entities if e.get("entity_id") != ent_id]

    def list_dashboards(self) -> list[dict[str, Any]]:
        return list(self.dashboard_list)

    def get_dashboard_config(self, url_path: str | None = None) -> dict[str, Any]:
        key = url_path or "default"
        if key not in self.dashboards:
            raise HomeAssistantNotFoundError(f"Dashboard {key} not found")
        return self.dashboards[key]

    def save_dashboard_config(
        self, url_path: str | None, config: dict[str, Any]
    ) -> None:
        key = url_path or "default"
        self.dashboards[key] = config

    def create_dashboard(
        self,
        url_path: str,
        title: str,
        icon: str | None = None,
        require_admin: bool = False,
        show_in_sidebar: bool = True,
    ) -> dict[str, Any]:
        item = {
            "id": url_path,
            "title": title,
            "url_path": url_path,
            "icon": icon,
            "require_admin": require_admin,
            "show_in_sidebar": show_in_sidebar,
            "mode": "storage",
        }
        self.dashboard_list.append(item)
        self.dashboards[url_path] = {"views": []}
        return item

    def delete_dashboard(self, dashboard_id: str) -> None:
        self.dashboard_list = [
            d for d in self.dashboard_list if d.get("id") != dashboard_id
        ]
        if dashboard_id in self.dashboards:
            del self.dashboards[dashboard_id]

    def update_dashboard(self, dashboard_id: str, **kwargs: Any) -> dict[str, Any]:
        for d in self.dashboard_list:
            if d.get("id") == dashboard_id or d.get("url_path") == dashboard_id:
                d.update(kwargs)
                return d
        raise HomeAssistantNotFoundError(f"Dashboard {dashboard_id} not found")

    def list_areas(self) -> list[dict[str, Any]]:
        return list(self.areas)

    def create_area(self, name: str, **kwargs: Any) -> dict[str, Any]:
        area_id = name.lower().replace(" ", "_")
        area = {
            "area_id": area_id,
            "name": name,
            "aliases": kwargs.get("aliases", []),
            "icon": kwargs.get("icon"),
            "floor_id": kwargs.get("floor_id"),
        }
        self.areas.append(area)
        return area

    def update_area(self, area_id: str, **kwargs: Any) -> dict[str, Any]:
        for a in self.areas:
            if a["area_id"] == area_id:
                a.update(kwargs)
                return a
        raise HomeAssistantNotFoundError(f"Area {area_id} not found")

    def delete_area(self, area_id: str) -> None:
        self.areas = [a for a in self.areas if a["area_id"] != area_id]

    def list_floors(self) -> list[dict[str, Any]]:
        return list(self.floors)

    def list_labels(self) -> list[dict[str, Any]]:
        return list(self.labels)

    def create_label(self, name: str, **kwargs: Any) -> dict[str, Any]:
        label_id = name.lower().replace(" ", "_")
        label = {"label_id": label_id, "name": name, **kwargs}
        self.labels.append(label)
        return label

    def update_label(self, label_id: str, **kwargs: Any) -> dict[str, Any]:
        for lbl in self.labels:
            if lbl.get("label_id") == label_id:
                lbl.update(kwargs)
                return lbl
        raise HomeAssistantNotFoundError(f"Label {label_id} not found")

    def delete_label(self, label_id: str) -> None:
        self.labels = [lbl for lbl in self.labels if lbl.get("label_id") != label_id]

    def list_devices(self) -> list[dict[str, Any]]:
        return list(self.devices)

    def list_entities(self) -> list[dict[str, Any]]:
        return list(self.entities)

    def update_entity(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        for ent in self.entities:
            if ent.get("entity_id") == entity_id:
                ent.update(kwargs)
                return dict(ent)
        raise HomeAssistantNotFoundError(f"Entity {entity_id} not found")

    def list_config_entries(self) -> list[dict[str, Any]]:
        return list(self.config_entries)

    def get_cluster_baseline(self, instance: str) -> dict[str, Any] | None:
        return self.cluster_baselines.get(instance)

    def save_cluster_baseline(
        self, instance: str, baseline_data: dict[str, Any]
    ) -> None:
        self.cluster_baselines[instance] = dict(baseline_data)

    def acquire_cluster_lock(
        self, instance: str, owner: str, plan_id: str, timeout_seconds: int = 300
    ) -> None:
        now = time.time()
        existing = self.cluster_locks.get(instance)
        if existing:
            if now < existing.get("expires_at", 0):
                raise LockError(
                    f"Cluster apply lock already held by '{existing.get('owner')}' "
                    f"for plan '{existing.get('plan_id')}' until {existing.get('expires_at')}"
                )
        self.cluster_locks[instance] = {
            "owner": owner,
            "plan_id": plan_id,
            "acquired_at": now,
            "expires_at": now + timeout_seconds,
        }

    def renew_cluster_lock(
        self, instance: str, owner: str, plan_id: str, timeout_seconds: int = 300
    ) -> None:
        existing = self.cluster_locks.get(instance)
        if (
            existing
            and existing.get("owner") == owner
            and existing.get("plan_id") == plan_id
        ):
            existing["expires_at"] = time.time() + timeout_seconds

    def release_cluster_lock(self, instance: str, owner: str, plan_id: str) -> None:
        existing = self.cluster_locks.get(instance)
        if (
            existing
            and existing.get("owner") == owner
            and existing.get("plan_id") == plan_id
        ):
            del self.cluster_locks[instance]
