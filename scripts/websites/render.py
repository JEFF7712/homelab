"""Render gitops/websites/<site>/site.yaml into the site's Kubernetes manifests.

site.yaml is the only file to edit for a converted site. It names a profile
and a digest-pinned image; the profile fixes everything else (probes, ports,
hardening, network policy). The renderer owns namespace.yaml,
network-policy.yaml, deployment.yaml, service.yaml, pdb.yaml and
kustomization.yaml in that directory, and `--check` fails when any of them
differs from a fresh render, so a hand edit to a generated file cannot merge.
A listed site without site.yaml must be named in HANDWRITTEN, so taking a site
out of generation is a change to this file and needs code-owner review.

The image reference is copied verbatim into deployment.yaml. Registry
promotion rewrites that exact string in every file with an `image:` line, so
a promotion moves site.yaml and deployment.yaml together and the pair stays
rendered.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WEBSITES = ROOT / "gitops/websites"
FLUX_WEBSITES = ROOT / "gitops/clusters/homelab-01/websites.yaml"
SPEC = "site.yaml"
HEADER = "# Generated from site.yaml by scripts/websites/render.py. Edit site.yaml.\n"
GENERATED = (
    "namespace.yaml",
    "network-policy.yaml",
    "deployment.yaml",
    "service.yaml",
    "pdb.yaml",
    "kustomization.yaml",
)
PROFILES = ("static", "server")
# Sites whose manifests are written by hand. Taking a site out of generation
# means adding it here, so it needs the same code-owner review as a renderer
# change (see CODEOWNERS).
HANDWRITTEN = ("bus-display", "uwagent")
STATIC_PORT = 8080
LINE_LIMIT = 120
IMAGE = re.compile(
    r"registry\.rupan\.dev/apps/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}"
)
QUANTITY = re.compile(r"[1-9][0-9]*(Mi|Gi)")
NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
KEYS = {
    "profile",
    "image",
    "port",
    "replicas",
    "memory",
    "service_name",
    "protect_namespace",
    "extra_manifests",
}


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    name: str
    profile: str
    image: str
    port: int
    replicas: int
    memory_request: str
    memory_limit: str
    service_name: str
    protect_namespace: bool
    extra_manifests: tuple[str, ...]


def parse_spec(name: str, raw: Any) -> Site:
    def fail(message: str) -> SpecError:
        return SpecError(f"{name}/{SPEC}: {message}")

    if not NAME.fullmatch(name):
        raise fail("directory name is not a DNS label")
    if not isinstance(raw, dict):
        raise fail("must be a mapping")
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise fail(f"unknown keys {unknown}")

    profile = raw.get("profile")
    if profile not in PROFILES:
        raise fail(f"profile must be one of {list(PROFILES)}")
    image = raw.get("image")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise fail("image must be registry.rupan.dev/apps/<repo>@sha256:<digest>")

    if profile == "static":
        if "port" in raw:
            raise fail("the static profile always listens on 8080; remove port")
        port = STATIC_PORT
    else:
        port = raw.get("port")
        if type(port) is not int or not 1024 <= port <= 65535:
            raise fail("server profile needs an unprivileged integer port")

    replicas = raw.get("replicas", 1)
    if type(replicas) is not int or not 1 <= replicas <= 5:
        raise fail("replicas must be an integer from 1 to 5")

    memory = raw.get("memory", {"request": "128Mi", "limit": "256Mi"})
    if not isinstance(memory, dict) or set(memory) != {"request", "limit"}:
        raise fail("memory must set exactly request and limit")
    for key in ("request", "limit"):
        if not isinstance(memory[key], str) or not QUANTITY.fullmatch(memory[key]):
            raise fail(f"memory.{key} must look like 256Mi or 1Gi")
    if _mebibytes(memory["request"]) > _mebibytes(memory["limit"]):
        raise fail("memory.request exceeds memory.limit")

    service_name = raw.get("service_name", f"{name}-svc")
    if not isinstance(service_name, str) or not NAME.fullmatch(service_name):
        raise fail("service_name must be a DNS label")

    protect_namespace = raw.get("protect_namespace", False)
    if type(protect_namespace) is not bool:
        raise fail("protect_namespace must be true or false")

    extras = raw.get("extra_manifests", [])
    if not isinstance(extras, list) or not all(isinstance(e, str) for e in extras):
        raise fail("extra_manifests must be a list of file names")
    for extra in extras:
        if "/" in extra or not extra.endswith(".yaml") or extra in GENERATED + (SPEC,):
            raise fail(f"extra_manifests entry {extra!r} is not a plain sibling file")
    if len(set(extras)) != len(extras):
        raise fail("extra_manifests has duplicates")

    return Site(
        name=name,
        profile=profile,
        image=image,
        port=port,
        replicas=replicas,
        memory_request=memory["request"],
        memory_limit=memory["limit"],
        service_name=service_name,
        protect_namespace=protect_namespace,
        extra_manifests=tuple(extras),
    )


def _mebibytes(quantity: str) -> int:
    value = int(quantity[:-2])
    return value * 1024 if quantity.endswith("Gi") else value


def _probe(site: Site, delay: int, period: int) -> str:
    if site.profile == "static":
        check = """            httpGet:
              path: /
              port: http
"""
    else:
        check = """            tcpSocket:
              port: http
"""
    return f"""{check}            initialDelaySeconds: {delay}
            periodSeconds: {period}
"""


def _image_line(image: str) -> str:
    line = f"          image: {image}"
    if len(line) > LINE_LIMIT:
        line += "  # yamllint disable-line rule:line-length"
    return line + "\n"


def render(site: Site) -> dict[str, str]:
    name = site.name
    annotations = ""
    if site.protect_namespace:
        annotations = """  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
"""
    files = {
        "namespace.yaml": f"""apiVersion: v1
kind: Namespace
metadata:
{annotations}  name: {name}
  labels:
    pod-security.kubernetes.io/enforce: restricted
""",
        "network-policy.yaml": f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: isolate-ingress
  namespace: {name}
spec:
  podSelector: {{}}
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector: {{}}
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: cloudflare
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: observability
""",
        "deployment.yaml": f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: website-deploy
  namespace: {name}
spec:
  replicas: {site.replicas}
  selector:
    matchLabels:
      app: {name}-web
  template:
    metadata:
      labels:
        app: {name}-web
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: web
{_image_line(site.image)}          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: {site.port}
          livenessProbe:
{_probe(site, 15, 30)}          readinessProbe:
{_probe(site, 5, 10)}          resources:
            limits:
              cpu: 500m
              memory: {site.memory_limit}
            requests:
              cpu: 100m
              memory: {site.memory_request}
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 101
      volumes:
        - name: tmp
          emptyDir: {{}}
""",
        "service.yaml": f"""apiVersion: v1
kind: Service
metadata:
  name: {site.service_name}
  namespace: {name}
spec:
  type: ClusterIP
  selector:
    app: {name}-web
  ports:
    - name: http
      protocol: TCP
      port: 80
      targetPort: http
""",
    }
    if site.replicas > 1:
        files["pdb.yaml"] = f"""apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: website-deploy
  namespace: {name}
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: {name}-web
"""
    resources = [file for file in GENERATED if file in files] + list(
        site.extra_manifests
    )
    files["kustomization.yaml"] = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n"
        + "".join(f"  - {resource}\n" for resource in resources)
    )
    return {file: HEADER + text for file, text in files.items()}


def load_sites(websites: Path) -> list[Site]:
    return [
        parse_spec(spec.parent.name, yaml.safe_load(spec.read_text()))
        for spec in sorted(websites.glob(f"*/{SPEC}"))
    ]


def registration_errors(sites: list[Site], websites: Path, flux: Path) -> list[str]:
    listed = set(
        yaml.safe_load((websites / "kustomization.yaml").read_text())["resources"]
    )
    checks = {
        check["namespace"]
        for check in yaml.safe_load(flux.read_text())["spec"].get("healthChecks", [])
        if check.get("kind") == "Deployment" and check.get("name") == "website-deploy"
    }
    errors = []
    for site in sites:
        if site.name not in listed:
            errors.append(
                f"{site.name}: missing from {websites.name}/kustomization.yaml"
            )
        if site.name not in checks:
            errors.append(f"{site.name}: no website-deploy healthCheck in {flux.name}")
    return errors


def management_errors(sites: list[Site], websites: Path) -> list[str]:
    listed = yaml.safe_load((websites / "kustomization.yaml").read_text())["resources"]
    generated = {site.name for site in sites}
    errors = []
    for name in listed:
        if name in generated and name in HANDWRITTEN:
            errors.append(f"{name}: has {SPEC} but is listed in HANDWRITTEN")
        elif name not in generated and name not in HANDWRITTEN:
            errors.append(
                f"{name}: no {SPEC}; a handwritten site must be added to HANDWRITTEN "
                "in scripts/websites/render.py, which needs code-owner review"
            )
    return errors


def plan(site: Site, directory: Path) -> tuple[dict[str, str], list[str], list[str]]:
    """Return files to write, generated files to delete, and unowned files."""
    wanted = render(site)
    writes = {
        file: text
        for file, text in wanted.items()
        if not (directory / file).is_file() or (directory / file).read_text() != text
    }
    deletes = [
        file
        for file in GENERATED
        if file not in wanted and (directory / file).is_file()
    ]
    owned = set(GENERATED) | set(site.extra_manifests) | {SPEC}
    unowned = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name not in owned
    )
    missing = [
        extra for extra in site.extra_manifests if not (directory / extra).is_file()
    ]
    return writes, deletes, unowned + [f"{extra} (missing)" for extra in missing]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing when stale"
    )
    args = parser.parse_args(argv)

    try:
        sites = load_sites(WEBSITES)
    except SpecError as error:
        print(error, file=sys.stderr)
        return 2
    problems = registration_errors(sites, WEBSITES, FLUX_WEBSITES)
    problems += management_errors(sites, WEBSITES)
    stale = []
    for site in sites:
        directory = WEBSITES / site.name
        writes, deletes, unowned = plan(site, directory)
        problems += [f"{site.name}: unowned file {file}" for file in unowned]
        for file, text in writes.items():
            stale.append(f"{site.name}/{file}")
            if not args.check:
                (directory / file).write_text(text)
        for file in deletes:
            stale.append(f"{site.name}/{file} (obsolete)")
            if not args.check:
                (directory / file).unlink()
    for problem in problems:
        print(problem, file=sys.stderr)
    if args.check and stale:
        for file in stale:
            print(f"stale: {file}", file=sys.stderr)
        print("run `just websites-render` and commit the result", file=sys.stderr)
        return 1
    for file in [] if args.check else stale:
        print(f"rendered {file}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
