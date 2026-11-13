from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "renovate.json").read_text())
K3S_SERVER = "flake/modules/k3s-server.nix"


def python_pattern(renovate_regex: str) -> re.Pattern[str]:
    """Translate Renovate's JavaScript named groups to Python syntax."""
    return re.compile(renovate_regex.replace("(?<", "(?P<"))


def manager_for(path: str) -> dict:
    found = [
        manager
        for manager in CONFIG.get("customManagers", [])
        if any(
            re.search(pattern.strip("/"), path)
            for pattern in manager["managerFilePatterns"]
        )
    ]
    if len(found) != 1:
        raise AssertionError(
            f"expected one custom manager for {path}, found {len(found)}"
        )
    return found[0]


def extract(manager: dict, text: str) -> list[dict[str, str]]:
    deps = []
    for regex in manager["matchStrings"]:
        for match in python_pattern(regex).finditer(text):
            deps.append(
                {
                    "depName": match.groupdict().get("depName")
                    or manager["depNameTemplate"],
                    "datasource": match.groupdict().get("datasource")
                    or manager["datasourceTemplate"],
                    "registryUrl": match.group("registryUrl"),
                    "currentValue": match.group("currentValue"),
                }
            )
    return deps


def effective_automerge(dep: dict[str, str], update_type: str) -> bool | None:
    """Apply packageRules in order, as Renovate does, for this custom dep."""
    automerge = None
    for rule in CONFIG["packageRules"]:
        if "matchUpdateTypes" in rule and update_type not in rule["matchUpdateTypes"]:
            continue
        if "matchManagers" in rule and "custom.regex" not in rule["matchManagers"]:
            continue
        if (
            "matchDatasources" in rule
            and dep["datasource"] not in rule["matchDatasources"]
        ):
            continue
        if "matchDepNames" in rule and dep["depName"] not in rule["matchDepNames"]:
            continue
        if (
            "matchPackageNames" in rule
            and dep["depName"] not in rule["matchPackageNames"]
        ):
            continue
        if "matchFileNames" in rule:
            continue
        if "automerge" in rule:
            automerge = rule["automerge"]
    return automerge


class CiliumChartManagerTest(unittest.TestCase):
    def test_extracts_the_pinned_chart_version_once(self) -> None:
        text = (ROOT / K3S_SERVER).read_text()
        version = re.search(
            r'repo = "https://helm\.cilium\.io";[^{}]*?version = "([^"]+)";', text
        )
        assert version is not None
        deps = extract(manager_for(K3S_SERVER), text)
        self.assertEqual(
            deps,
            [
                {
                    "depName": "cilium",
                    "datasource": "helm",
                    "registryUrl": "https://helm.cilium.io",
                    "currentValue": version.group(1),
                }
            ],
        )

    def test_match_stays_inside_the_chart_spec(self) -> None:
        manager = manager_for(K3S_SERVER)
        text = """
          repo = "https://helm.cilium.io";
          valuesContent = builtins.toJSON {
            version = "9.9.9";
          };
        """
        self.assertEqual(extract(manager, text), [])

    def test_no_update_type_automerges(self) -> None:
        dep = extract(manager_for(K3S_SERVER), (ROOT / K3S_SERVER).read_text())[0]
        for update_type in ("major", "minor", "patch", "digest", "pin"):
            with self.subTest(update_type=update_type):
                self.assertIs(effective_automerge(dep, update_type), False)

    def test_pull_request_explains_the_digest_follow_up(self) -> None:
        notes = [
            note
            for rule in CONFIG["packageRules"]
            if rule.get("matchDepNames") == ["cilium"]
            for note in rule.get("prBodyNotes", [])
        ]
        self.assertTrue(any("digest" in note and "registry" in note for note in notes))


if __name__ == "__main__":
    unittest.main()
