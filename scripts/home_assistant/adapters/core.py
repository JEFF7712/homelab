from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..canonical import canonical_hash
from ..client import HomeAssistantClient
from ..models import OwnerMode, PlanAction, ResourceDocument
from .base import BaseAdapter

CUSTOM_SENTENCES_DIR = Path("home-assistant") / "custom_sentences"
CUSTOM_COMPONENTS_DIR = Path("home-assistant") / "custom_components"
WWW_DIR = Path("home-assistant") / "www"


def iter_custom_sentences(repo_root: Path) -> list[tuple[str, str]]:
    """Collect custom_sentences/<lang>/<name>.yaml as (configmap_key, content).

    ConfigMap keys cannot contain slashes, so <lang>/<name> becomes
    <lang>.<name> (split on the first dot on deploy). Language and file
    names must therefore avoid dots of their own beyond the .yaml suffix.
    """
    base = repo_root / CUSTOM_SENTENCES_DIR
    if not base.is_dir():
        return []
    collected: list[tuple[str, str]] = []
    for lang_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for sentence_file in sorted(lang_dir.glob("*.yaml")):
            if not sentence_file.is_file():
                continue
            key = f"{lang_dir.name}.{sentence_file.name}"
            collected.append((key, sentence_file.read_text(encoding="utf-8")))
    return collected


def iter_custom_components(repo_root: Path) -> list[tuple[str, str]]:
    base = repo_root / CUSTOM_COMPONENTS_DIR
    if not base.is_dir():
        return []
    collected: list[tuple[str, str]] = []
    for domain_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        for component_file in sorted(
            path for path in domain_dir.iterdir() if path.is_file()
        ):
            key = f"component.{domain_dir.name}.{component_file.name}"
            collected.append((key, component_file.read_text(encoding="utf-8")))
    return collected


def iter_www(repo_root: Path) -> list[tuple[str, str]]:
    """Collect www/<dir>/<file> as www.<dir>.<file> for /config/www/<dir>/.

    The init container copies them onto the PVC before HA starts, so every
    file of a bundle (for example the Jarvis face) lands together.
    """
    base = repo_root / WWW_DIR
    if not base.is_dir():
        return []
    collected: list[tuple[str, str]] = []
    for www_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        for www_file in sorted(path for path in www_dir.iterdir() if path.is_file()):
            key = f"www.{www_dir.name}.{www_file.name}"
            collected.append((key, www_file.read_text(encoding="utf-8")))
    return collected


def render_config_configmap(repo_root: Path) -> tuple[str, str]:
    """Render gitops/home-assistant/config.yaml content plus its checksum.

    The ConfigMap carries configuration.yaml plus every
    home-assistant/custom_sentences/<lang>/*.yaml file, custom component and
    www bundle file. The checksum covers
    all rendered content so HA restarts when any of it changes.
    """
    core_file = repo_root / "home-assistant" / "core" / "configuration.yaml"
    core_content = core_file.read_text(encoding="utf-8")

    def _block(content: str) -> str:
        indented = "\n".join(
            f"    {line}" if line else "" for line in content.splitlines()
        )
        return f"{indented}\n"

    parts = [
        "apiVersion: v1\n",
        "kind: ConfigMap\n",
        "metadata:\n",
        "  name: home-assistant-config\n",
        "  namespace: home-assistant\n",
        "data:\n",
        f"  configuration.yaml: |\n{_block(core_content)}",
    ]
    checksum_parts = [core_content]
    for key, content in iter_custom_sentences(repo_root):
        parts.append(f"  {key}: |\n{_block(content)}")
        checksum_parts.append(f"{key}\n{content}")
    for key, content in iter_custom_components(repo_root):
        parts.append(f"  {key}: |\n{_block(content)}")
        checksum_parts.append(f"{key}\n{content}")
    for key, content in iter_www(repo_root):
        parts.append(f"  {key}: |\n{_block(content)}")
        checksum_parts.append(f"{key}\n{content}")
    cm_text = "".join(parts)
    checksum = hashlib.sha256("\n".join(checksum_parts).encode("utf-8")).hexdigest()
    return cm_text, checksum


def sync_core_to_gitops(repo_root: Path) -> None:
    """Synchronize home-assistant/core/configuration.yaml (plus custom_sentences)
    into gitops/home-assistant/config.yaml and recalculate checksum/config
    in gitops/home-assistant/deployment.yaml."""
    core_file = repo_root / "home-assistant" / "core" / "configuration.yaml"
    if not core_file.is_file():
        return
    cm_text, checksum = render_config_configmap(repo_root)

    # Render into gitops ConfigMap
    config_cm_file = repo_root / "gitops" / "home-assistant" / "config.yaml"
    config_cm_file.write_text(cm_text, encoding="utf-8")

    # Update deployment checksum/config
    deployment_file = repo_root / "gitops" / "home-assistant" / "deployment.yaml"
    if deployment_file.is_file():
        dep_text = deployment_file.read_text(encoding="utf-8")
        dep_text = re.sub(
            r"checksum/config:\s*[a-f0-9]+",
            f"checksum/config: {checksum}",
            dep_text,
        )
        deployment_file.write_text(dep_text, encoding="utf-8")


class CoreConfigurationAdapter(BaseAdapter):
    kind = "core"
    owner_mode = OwnerMode.GIT_OWNED.value
    supports_mutation = False

    def export_from_live(self, client: HomeAssistantClient) -> list[ResourceDocument]:
        cfg = client.get_core_configuration()
        return [
            ResourceDocument(
                kind=self.kind,
                key="configuration",
                owner_mode=self.owner_mode,
                desired=cfg,
                metadata={"status": "live-probed"},
            )
        ]

    def canonicalize(self, doc: ResourceDocument) -> ResourceDocument:
        return doc

    def validate(self, doc: ResourceDocument) -> list[str]:
        errors: list[str] = []
        if not isinstance(doc.desired, dict):
            return ["Core configuration must be a mapping"]
        if "default_config" not in doc.desired and "homeassistant" not in doc.desired:
            errors.append(
                "Core configuration must include 'default_config' or 'homeassistant'"
            )
        return errors

    def apply(self, client: HomeAssistantClient, action: PlanAction) -> None:
        raise NotImplementedError(
            "Core configuration is git-owned and deployed via GitOps / Flux ConfigMap. "
            "Direct live API mutation is not supported for core configuration."
        )

    def verify(self, client: HomeAssistantClient, doc: ResourceDocument) -> bool:
        live_cfg = client.get_core_configuration()
        return canonical_hash(live_cfg) == canonical_hash(doc.desired)

    def delete(self, client: HomeAssistantClient, key: str) -> None:
        raise NotImplementedError("Core configuration cannot be deleted.")
