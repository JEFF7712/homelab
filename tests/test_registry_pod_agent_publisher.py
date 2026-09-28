import base64
import unittest

from scripts.registry.core import render_access_control
from scripts.registry.provision_pod_agent import (
    IDENTITY,
    check_policy,
    publisher_password,
)


class PodAgentPublisherTest(unittest.TestCase):
    def test_only_project_policy_may_change(self):
        old = render_access_control({"images": []})
        new = render_access_control(
            {
                "images": [
                    {
                        "kind": "first-party",
                        "destination_repository": "apps/pod-agent",
                    }
                ]
            }
        )
        check_policy(old, new)
        new["repositories"]["upstream/**"]["defaultPolicy"] = ["read"]
        with self.assertRaises(ValueError):
            check_policy(old, new)

    def test_credential_rejects_other_identity(self):
        def auth(user):
            return {
                "auths": {
                    "registry.rupan.dev": {
                        "auth": base64.b64encode(
                            f"{user}:test-password".encode()
                        ).decode()
                    }
                }
            }

        self.assertEqual(publisher_password(auth(IDENTITY)), "test-password")
        with self.assertRaises(ValueError):
            publisher_password(auth("maintenance"))


if __name__ == "__main__":
    unittest.main()
