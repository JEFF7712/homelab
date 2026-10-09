from __future__ import annotations

import base64
import copy
import unittest

from scripts.registry.core import RegistryError, render_access_control
from scripts.registry.provision_darkbit import check_policy, publisher_password


class DarkbitPublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = {
            "images": [
                {
                    "kind": "first-party",
                    "destination_repository": "apps/darkbit",
                    "producer": {"local_publisher": "forgejo-darkbit"},
                },
                {
                    "kind": "first-party",
                    "destination_repository": "apps/other",
                    "producer": {},
                },
            ]
        }
        self.desired = render_access_control(self.lock)
        self.current = copy.deepcopy(self.desired)
        self.current["repositories"]["apps/darkbit"]["policies"][1]["users"] = [
            "publisher-darkbit"
        ]

    def test_policy_preserves_legacy_and_other_app_authority(self) -> None:
        check_policy(self.current, self.desired)
        other = self.desired["repositories"]["apps/other"]
        self.assertNotIn("forgejo-darkbit", str(other))
        self.assertNotIn("delete", str(self.desired["repositories"]["apps/darkbit"]))

    def test_unrelated_policy_drift_and_extra_authority_rejected(self) -> None:
        wrong = copy.deepcopy(self.desired)
        wrong["repositories"]["apps/other"]["defaultPolicy"] = ["read"]
        with self.assertRaisesRegex(ValueError, "drift"):
            check_policy(self.current, wrong)
        wrong = copy.deepcopy(self.desired)
        wrong["repositories"]["apps/darkbit"]["policies"][1]["actions"] = [
            "read",
            "create",
            "update",
            "delete",
        ]
        with self.assertRaisesRegex(ValueError, "scoped"):
            check_policy(self.current, wrong)

    def test_another_project_identity_cannot_be_assigned(self) -> None:
        self.lock["images"][0]["producer"]["local_publisher"] = "forgejo-other"
        with self.assertRaises(RegistryError):
            render_access_control(self.lock)

    def test_credential_requires_local_identity(self) -> None:
        for user, succeeds in [
            ("forgejo-darkbit", True),
            ("maintenance", False),
            ("publisher-darkbit", False),
        ]:
            value = {
                "auths": {
                    "registry.rupan.dev": {
                        "auth": base64.b64encode(
                            (user + ":test-password").encode()
                        ).decode()
                    }
                }
            }
            if succeeds:
                self.assertEqual(publisher_password(value), "test-password")
            else:
                with self.assertRaises(ValueError):
                    publisher_password(value)


if __name__ == "__main__":
    unittest.main()
