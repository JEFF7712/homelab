#!/usr/bin/env python3
"""Render the non-secret Jarvis voice topology from Git and optional live state.

Live Home Assistant pipeline data comes from the supported Assist pipeline
websocket API (``assist_pipeline/pipeline/list``) plus entity existence from
``get_states``. Storage files under ``/config/.storage`` are never scraped:
they are UI-managed state, and the old ``core.config_entries`` parse could
not substantiate the active pipeline engines. Without HA credentials the
live pipeline section reports ``unknown`` with an explicit reason. Stale
integrations are flagged for explicit cleanup, never mutated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VOICE_DIR = ROOT / "gitops" / "voice"
GATEWAYS = {
    "stt": "wyoming-whisper",
    "tts": "wyoming-chatterbox",
}
SATELLITE_ENTITY = "assist_satellite.homelab_05_satellite_assist_satellite"
DEFAULT_HA_URL = "http://10.0.40.13:8123"


def manifest_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in sorted(VOICE_DIR.glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text()):
            if isinstance(document, dict) and document.get("kind"):
                document["_source"] = str(path.relative_to(ROOT))
                documents.append(document)
    return documents


def backend_preference(document: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for container in (
        document.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    ):
        for env in container.get("env", []):
            if env.get("name") != "WYOMING_BACKENDS":
                continue
            for item in str(env.get("value", "")).split(","):
                if "=" not in item:
                    continue
                name, address = item.split("=", 1)
                host, port = address.rsplit(":", 1)
                result.append({"name": name, "host": host, "port": int(port)})
    return result


def backend_findings(gateways: dict[str, Any], services: set[str]) -> list[str]:
    """Flag gateway backends with no matching voice Service (stale candidates)."""
    findings: list[str] = []
    for mode, gateway in sorted(gateways.items()):
        for backend in gateway.get("backends", []):
            host = str(backend.get("host", ""))
            first_label = host.split(".", 1)[0]
            if first_label not in services:
                findings.append(
                    f"{mode} backend {backend.get('name')!r} points at host "
                    f"{host!r} with no matching voice Service; verify the "
                    "endpoint still exists instead of assuming fallback covers it"
                )
    return findings


def desired_report(documents: list[dict[str, Any]]) -> dict[str, Any]:
    deployments = []
    services = []
    gateways = {}
    for document in documents:
        kind = document.get("kind")
        metadata = document.get("metadata", {})
        name = metadata.get("name")
        if kind == "Deployment":
            deployments.append(
                {
                    "name": name,
                    "namespace": metadata.get("namespace", "default"),
                    "replicas": document.get("spec", {}).get("replicas", 1),
                    "source": document.get("_source"),
                }
            )
            backends = backend_preference(document)
            if backends:
                mode = (
                    "stt"
                    if name == "wyoming-stt-gateway"
                    else "tts"
                    if name == "wyoming-tts-gateway"
                    else None
                )
                if mode:
                    gateways[mode] = {"deployment": name, "backends": backends}
        elif kind == "Service":
            spec = document.get("spec", {})
            services.append(
                {
                    "name": name,
                    "namespace": metadata.get("namespace", "default"),
                    "selector": spec.get("selector", {}),
                    "ports": [
                        {
                            "name": p.get("name"),
                            "port": p.get("port"),
                            "targetPort": p.get("targetPort"),
                        }
                        for p in spec.get("ports", [])
                    ],
                    "source": document.get("_source"),
                }
            )
    service_names = {service["name"] for service in services}
    return {
        "deployments": sorted(deployments, key=lambda item: item["name"]),
        "gateways": gateways,
        "services": sorted(services, key=lambda item: item["name"]),
        "findings": backend_findings(gateways, service_names),
        "ha_pipeline": {
            "stt_gateway_service": "wyoming-whisper.voice:10300",
            "tts_gateway_service": "wyoming-chatterbox.voice:10201",
            "conversation_agent": "Jarvis Jev Router",
        },
    }


def run_json(command: list[str]) -> Any:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=15
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def resolve_token(token_arg: str | None, token_file_arg: str | None) -> str:
    if token_arg:
        return token_arg
    if token_file_arg and os.path.isfile(token_file_arg):
        return Path(token_file_arg).read_text(encoding="utf-8").strip()
    env_token = os.environ.get("HASS_TOKEN")
    if env_token:
        return env_token
    env_file = os.environ.get("HASS_TOKEN_FILE")
    if env_file and os.path.isfile(env_file):
        return Path(env_file).read_text(encoding="utf-8").strip()
    local_token = ROOT / ".agent-state" / "home-assistant" / "token"
    if local_token.is_file():
        return local_token.read_text(encoding="utf-8").strip()
    return ""


def summarize_pipelines(payload: Any, states: Any) -> dict[str, Any]:
    """Summarize assist pipelines with engine-entity existence evidence."""
    if not isinstance(payload, dict) or not isinstance(payload.get("pipelines"), list):
        return {
            "status": "unknown",
            "reason": "assist_pipeline/pipeline/list returned no pipeline list",
        }
    pipelines = payload["pipelines"]
    entity_ids: set[str] = set()
    satellite: dict[str, Any] | None = None
    if isinstance(states, list):
        for state in states:
            if not isinstance(state, dict):
                continue
            entity_id = state.get("entity_id")
            if isinstance(entity_id, str):
                entity_ids.add(entity_id)
            if entity_id == SATELLITE_ENTITY:
                satellite = {
                    "state": state.get("state"),
                    "attributes": {
                        key: state.get("attributes", {}).get(key)
                        for key in ("pipeline", "selected_pipeline")
                        if key in (state.get("attributes", {}) or {})
                    },
                }
    summarized = []
    missing: list[str] = []
    for pipeline in pipelines:
        if not isinstance(pipeline, dict):
            continue
        engines = {
            "stt_engine": pipeline.get("stt_engine"),
            "tts_engine": pipeline.get("tts_engine"),
            "conversation_engine": pipeline.get("conversation_engine"),
            "wake_word_entity": pipeline.get("wake_word_entity"),
        }
        summarized.append(
            {
                "id": pipeline.get("id"),
                "name": pipeline.get("name"),
                "language": pipeline.get("language"),
                "preferred": pipeline.get("id") == payload.get("preferred_pipeline"),
                **engines,
                "tts_voice": pipeline.get("tts_voice"),
            }
        )
        for key in ("stt_engine", "tts_engine", "conversation_engine"):
            engine = engines[key]
            if (
                isinstance(engine, str)
                and "." in engine
                and entity_ids
                and engine not in entity_ids
            ):
                missing.append(
                    f"pipeline {pipeline.get('id')!r} {key} {engine!r} has no "
                    "matching HA entity; verify in the UI before cleanup"
                )
    preferred = payload.get("preferred_pipeline")
    selected = None
    selected_evidence = "unknown"
    if satellite and satellite["attributes"].get("selected_pipeline"):
        selected = satellite["attributes"]["selected_pipeline"]
        selected_evidence = "satellite entity selected_pipeline attribute"
    elif satellite and satellite["attributes"].get("pipeline"):
        selected = satellite["attributes"]["pipeline"]
        selected_evidence = "satellite entity pipeline attribute"
    elif preferred is not None:
        selected_evidence = (
            "unknown: no satellite-selected pipeline attribute observed; "
            f"the preferred pipeline is {preferred!r}, which the satellite "
            "may or may not be using"
        )
    return {
        "status": "known",
        "preferred_pipeline": preferred,
        "selected_pipeline": selected,
        "selected_evidence": selected_evidence,
        "satellite": satellite,
        "pipelines": summarized,
        "missing_engine_entities": sorted(set(missing)),
    }


def fetch_live_pipelines(base_url: str, token: str, timeout: float) -> dict[str, Any]:
    if not token:
        return {
            "status": "unknown",
            "reason": (
                "no HA credentials; set HASS_TOKEN, HASS_TOKEN_FILE, "
                "--token, or --token-file for pipeline inspection"
            ),
        }
    try:
        from scripts.home_assistant.client import HomeAssistantClient

        client = HomeAssistantClient(base_url=base_url, token=token, timeout=timeout)
        try:
            ws = client._get_ws()
            payload = ws.call("assist_pipeline/pipeline/list")
            try:
                states = ws.call("get_states")
            except Exception:
                states = None
            return summarize_pipelines(payload, states)
        finally:
            if client._ws is not None:
                client._ws.close()
    except Exception as exc:
        return {"status": "unknown", "reason": f"pipeline lookup failed: {exc}"}


def live_report(
    ha_url: str = DEFAULT_HA_URL,
    token: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    deployments = run_json(["kubectl", "-n", "voice", "get", "deploy", "-o", "json"])
    endpoints = run_json(["kubectl", "-n", "voice", "get", "endpoints", "-o", "json"])
    return {
        "deployments": (
            [
                {
                    "name": item.get("metadata", {}).get("name"),
                    "available_replicas": item.get("status", {}).get(
                        "availableReplicas", 0
                    ),
                    "ready_replicas": item.get("status", {}).get("readyReplicas", 0),
                }
                for item in deployments.get("items", [])
            ]
            if isinstance(deployments, dict)
            else None
        ),
        "endpoints": (
            {
                item.get("metadata", {}).get("name"): [
                    address.get("ip")
                    for subset in item.get("subsets", [])
                    for address in subset.get("addresses", [])
                ]
                for item in endpoints.get("items", [])
            }
            if isinstance(endpoints, dict)
            else None
        ),
        "ha_pipeline": fetch_live_pipelines(ha_url, token, timeout),
    }


def build_report(
    include_live: bool = False,
    ha_url: str = DEFAULT_HA_URL,
    token: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "desired": desired_report(manifest_documents()),
        "live": live_report(ha_url, token, timeout)
        if include_live
        else {"status": "unknown", "reason": "live inspection not requested"},
    }


def check(report: dict[str, Any]) -> list[str]:
    desired = report["desired"]
    errors = []
    services = {service["name"] for service in desired["services"]}
    for name in ("wyoming-whisper", "wyoming-chatterbox"):
        if name not in services:
            errors.append(f"missing required voice Service: {name}")
    for mode, deployment in (
        ("stt", "wyoming-stt-gateway"),
        ("tts", "wyoming-tts-gateway"),
    ):
        gateway = desired["gateways"].get(mode)
        if (
            not gateway
            or gateway["deployment"] != deployment
            or not gateway["backends"]
        ):
            errors.append(f"missing {mode} gateway preference order")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="add read-only kubectl and HA inspection"
    )
    parser.add_argument(
        "--check", action="store_true", help="fail if desired-state invariants drift"
    )
    parser.add_argument("--ha-url", default=os.environ.get("HASS_URL", DEFAULT_HA_URL))
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    token = resolve_token(args.token, args.token_file)
    report = build_report(args.live, args.ha_url, token, args.timeout)
    print(json.dumps(report, indent=2, sort_keys=True))
    errors = check(report) if args.check else []
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
