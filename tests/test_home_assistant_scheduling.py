import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def accepts_node(spec: dict, hostname: str) -> bool:
    labels = {"kubernetes.io/hostname": hostname}
    if any(
        labels.get(key) != value for key, value in spec.get("nodeSelector", {}).items()
    ):
        return False
    required = (
        spec.get("affinity", {})
        .get("nodeAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution")
    )
    if required is None:
        return True
    for term in required["nodeSelectorTerms"]:
        matches = []
        for expression in term.get("matchExpressions", []):
            value = labels.get(expression["key"])
            operator = expression["operator"]
            if operator == "In":
                matches.append(value in expression["values"])
            elif operator == "NotIn":
                matches.append(value not in expression["values"])
            else:
                raise AssertionError(f"Unsupported scheduling operator: {operator}")
        if matches and all(matches):
            return True
    return False


class HomeAssistantSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        deployment = yaml.safe_load(
            (ROOT / "gitops/home-assistant/deployment.yaml").read_text()
        )
        self.spec = deployment["spec"]["template"]["spec"]

    def test_multiple_failover_nodes_remain_eligible(self) -> None:
        for hostname in ("homelab-01", "homelab-02", "homelab-03", "homelab-04"):
            with self.subTest(hostname=hostname):
                self.assertTrue(accepts_node(self.spec, hostname))

    def test_ledfx_host_is_excluded_even_when_ledfx_is_stopped(self) -> None:
        deployments = yaml.safe_load_all(
            (ROOT / "gitops/music-assistant/ledfx.yaml").read_text()
        )
        ledfx = next(doc for doc in deployments if doc["kind"] == "Deployment")
        hostname = ledfx["spec"]["template"]["spec"]["nodeSelector"][
            "kubernetes.io/hostname"
        ]
        self.assertFalse(accepts_node(self.spec, hostname))


if __name__ == "__main__":
    unittest.main()
