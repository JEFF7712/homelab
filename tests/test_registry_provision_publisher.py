from __future__ import annotations

import base64
import json
import unittest

from scripts.registry.provision_publisher import (
    auth_file,
    htpasswd_secret,
    publisher_identity,
    replace_entry,
    secret_name,
)


def policy(users: list[str]) -> dict:
    return {
        "repositories": {
            "apps/bus-display": {
                "defaultPolicy": [],
                "policies": [
                    {"users": ["node"], "actions": ["read"]},
                    {"users": users, "actions": ["read", "create", "update"]},
                ],
            }
        }
    }


class ProvisionPublisherTest(unittest.TestCase):
    def test_only_an_existing_single_publisher_grant_is_provisioned(self) -> None:
        self.assertEqual(
            publisher_identity(policy(["publisher-bus-display"]), "apps/bus-display"),
            "publisher-bus-display",
        )
        for users in ([], ["publisher-a", "publisher-b"]):
            with self.subTest(users=users), self.assertRaises(ValueError):
                publisher_identity(policy(users), "apps/bus-display")
        with self.assertRaisesRegex(ValueError, "no access policy"):
            publisher_identity(policy(["publisher-x"]), "apps/unknown")

    def test_rotation_replaces_only_the_identity_entry(self) -> None:
        entries = ["node:$2y$old", "publisher-bus-display:$2y$old", "maintenance:$2y$m"]
        updated = replace_entry(
            entries, "publisher-bus-display", "publisher-bus-display:$2y$new"
        )
        self.assertEqual(
            updated,
            ["node:$2y$old", "maintenance:$2y$m", "publisher-bus-display:$2y$new"],
        )
        self.assertEqual(
            replace_entry(["node:x"], "publisher-new", "publisher-new:y"),
            ["node:x", "publisher-new:y"],
        )

    def test_secret_and_auth_file_match_release_contract(self) -> None:
        self.assertEqual(
            secret_name("apps/lidarr-extended"),
            "registry_lidarr_extended_publisher_auth_file",
        )
        document = json.loads(auth_file("publisher-bus-display", "pw"))
        token = document["auths"]["registry.rupan.dev"]["auth"]
        self.assertEqual(base64.b64decode(token).decode(), "publisher-bus-display:pw")

    def test_htpasswd_secret_carries_new_content_and_keeps_scope(self) -> None:
        existing = {
            "id": 7,
            "name": "registry_htpasswd_file",
            "value": "",
            "events": ["deployment", "manual"],
            "images": None,
        }
        self.assertEqual(
            htpasswd_secret(existing, "node:x\npublisher-new:y\n"),
            {
                "name": "registry_htpasswd_file",
                "value": "node:x\npublisher-new:y\n",
                "events": ["deployment", "manual"],
                "images": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
