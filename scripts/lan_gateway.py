"""Render the LAN gateway nginx config from the Cloudflare Tunnel ingress rules.

config/lan-gateway.json selects which tunnel hostnames also resolve on the LAN.
The router reproduces each selected hostname's tunnel rules in order, path
rules included, so a LAN request reaches the same origin, or the same status
response, that the tunnel would give it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path("config/lan-gateway.json")
INGRESS = Path("gitops/cloudflare/ingress-config.yaml")
OUTPUT = Path("gitops/lan-gateway/nginx.conf")
CLUSTER_DOMAIN = "svc.cluster.local"
HTTP_PORT = 8080
HTTPS_PORT = 8443


@dataclass(frozen=True)
class Rule:
    path: str | None
    service: str


@dataclass(frozen=True)
class Host:
    name: str
    forward_client_address: bool
    rules: tuple[Rule, ...]


def load_config(root: Path) -> dict[str, Any]:
    config = json.loads((root / CONFIG).read_text())
    if not isinstance(config, dict) or not isinstance(config.get("hosts"), list):
        raise ValueError(f"{CONFIG}: expected an object with a hosts list")
    return config


def tunnel_rules(root: Path) -> list[dict[str, Any]]:
    config_map = yaml.safe_load((root / INGRESS).read_text())
    tunnel = yaml.safe_load(config_map["data"]["config.yaml"])
    rules = tunnel["ingress"]
    if not isinstance(rules, list):
        raise ValueError(f"{INGRESS}: ingress must be a list")
    return rules


def select_hosts(config: dict[str, Any], rules: list[dict[str, Any]]) -> list[Host]:
    names = [host["name"] for host in config["hosts"]]
    if len(names) != len(set(names)):
        raise ValueError(f"{CONFIG}: host names must be unique")
    hosts = []
    for entry in config["hosts"]:
        if not entry.get("enabled", True):
            continue
        name = entry["name"]
        matching = [rule for rule in rules if rule.get("hostname") == name]
        if not matching:
            raise ValueError(f"{name}: no Cloudflare Tunnel ingress rule serves it")
        selected: list[Rule] = []
        for rule in matching:
            if selected and selected[-1].path is None:
                raise ValueError(
                    f"{name}: a tunnel rule follows its path-less rule and is unreachable"
                )
            selected.append(Rule(path=rule.get("path"), service=rule["service"]))
        hosts.append(
            Host(
                name=name,
                forward_client_address=entry.get("forwardClientAddress", True),
                rules=tuple(selected),
            )
        )
    return hosts


def upstream(service: str) -> str:
    """Return a fully qualified origin URL; nginx's resolver applies no search path."""
    parts = urlsplit(service)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"unsupported tunnel service {service!r}")
    host = parts.hostname
    labels = host.split(".")
    if host.endswith(f".{CLUSTER_DOMAIN}"):
        pass
    elif len(labels) == 2:
        host = f"{host}.{CLUSTER_DOMAIN}"
    else:
        raise ValueError(f"tunnel service {service!r} is not a cluster Service")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.path not in {"", "/"}:
        raise ValueError(f"tunnel service {service!r} carries a path")
    return f"{parts.scheme}://{host}:{port}"


def action(service: str) -> list[str]:
    if service.startswith("http_status:"):
        status = int(service.removeprefix("http_status:"))
        return [f"return {status};"]
    return [f"set $upstream {upstream(service)};", "proxy_pass $upstream;"]


def nginx_regex(path: str) -> str:
    if any(character in path for character in '"{};\n'):
        raise ValueError(f"path rule {path!r} cannot be quoted for nginx")
    return f'"{path}"'


def server_block(host: Host) -> list[str]:
    lines = [
        "  server {",
        f"    listen {HTTPS_PORT} ssl;",
        f"    server_name {host.name};",
        "    proxy_set_header Host $host;",
        "    proxy_set_header Upgrade $http_upgrade;",
        "    proxy_set_header Connection $connection_upgrade;",
        "    proxy_set_header X-Forwarded-Proto https;",
        "    proxy_set_header X-Forwarded-Host $host;",
    ]
    if host.forward_client_address:
        lines += [
            "    proxy_set_header X-Forwarded-For $remote_addr;",
            "    proxy_set_header X-Real-IP $remote_addr;",
        ]
    for rule in host.rules:
        location = (
            "location /"
            if rule.path is None
            else f"location ~ {nginx_regex(rule.path)}"
        )
        lines.append(f"    {location} {{")
        lines += [f"      {line}" for line in action(rule.service)]
        lines.append("    }")
    if host.rules[-1].path is not None:
        # The tunnel's catch-all answers 404 when no path rule matches.
        lines += ["    location / {", "      return 404;", "    }"]
    lines.append("  }")
    return lines


def render(root: Path) -> str:
    hosts = select_hosts(load_config(root), tunnel_rules(root))
    lines = [
        "# Generated by `python -m scripts.lan_gateway` from config/lan-gateway.json",
        "# and gitops/cloudflare/ingress-config.yaml. Do not edit.",
        "worker_processes auto;",
        "pid /tmp/nginx.pid;",
        "error_log /dev/stderr warn;",
        "events {",
        "  worker_connections 4096;",
        "}",
        "http {",
        "  access_log /dev/stdout;",
        "  server_tokens off;",
        "  client_body_temp_path /tmp/client_body;",
        "  proxy_temp_path /tmp/proxy;",
        "  fastcgi_temp_path /tmp/fastcgi;",
        "  uwsgi_temp_path /tmp/uwsgi;",
        "  scgi_temp_path /tmp/scgi;",
        f"  resolver kube-dns.kube-system.{CLUSTER_DOMAIN} valid=30s ipv6=off;",
        "  resolver_timeout 5s;",
        "  map $http_upgrade $connection_upgrade {",
        "    default upgrade;",
        "    '' close;",
        "  }",
        "  client_max_body_size 0;",
        "  proxy_request_buffering off;",
        "  proxy_buffering off;",
        "  proxy_http_version 1.1;",
        "  proxy_read_timeout 1h;",
        "  proxy_send_timeout 1h;",
        "  http2 on;",
        "  ssl_certificate /etc/nginx/tls/tls.crt;",
        "  ssl_certificate_key /etc/nginx/tls/tls.key;",
        "  ssl_protocols TLSv1.2 TLSv1.3;",
        "  ssl_session_cache shared:tls:10m;",
        "  server {",
        f"    listen {HTTP_PORT} default_server;",
        "    location = /healthz {",
        "      access_log off;",
        '      return 200 "ok\\n";',
        "    }",
        "    location / {",
        "      return 308 https://$host$request_uri;",
        "    }",
        "  }",
        "  server {",
        f"    listen {HTTPS_PORT} ssl default_server;",
        "    ssl_reject_handshake on;",
        "  }",
    ]
    for host in hosts:
        lines += server_block(host)
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the LAN gateway nginx config from the tunnel rules."
    )
    parser.add_argument(
        "--check", action="store_true", help="fail when the committed output is stale"
    )
    args = parser.parse_args(argv)
    content = render(ROOT)
    target = ROOT / OUTPUT
    if args.check:
        if not target.exists() or target.read_text() != content:
            print(
                f"{OUTPUT} is stale; run python -m scripts.lan_gateway", file=sys.stderr
            )
            return 1
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
