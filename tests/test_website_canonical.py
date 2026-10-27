from __future__ import annotations

import unittest

from scripts.websites.canonical import canonical_site, diff_sites, diff_values


def deployment(**container: object) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "website-deploy", "namespace": "demo"},
        "spec": {
            "template": {
                "spec": {"containers": [{"name": "web", **container}]},
            }
        },
    }


class CanonicalWebsiteRender(unittest.TestCase):
    def test_identical_resources_in_any_order_have_no_diff(self) -> None:
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "demo-svc", "namespace": "demo"},
        }
        base = {"demo": canonical_site([deployment(image="a"), service])}
        head = {"demo": canonical_site([service, None, deployment(image="a")])}
        self.assertEqual(diff_sites(base, head), [])

    def test_named_entries_are_matched_by_name_and_reported_once(self) -> None:
        before = {
            "volumes": [
                {"name": "cache", "emptyDir": {}},
                {"name": "tmp", "emptyDir": {}},
            ]
        }
        after = {"volumes": [{"name": "tmp", "emptyDir": {}}]}
        changes = diff_values(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][:2], ("volumes[cache]", before["volumes"][0]))

    def test_unnamed_lists_compare_positionally(self) -> None:
        self.assertEqual(
            diff_values({"command": ["sh", "-c"]}, {"command": ["sh", "-x"]}),
            [("command[1]", "-c", "-x")],
        )

    def test_unnamed_lists_of_different_length_report_the_whole_list(self) -> None:
        before = {"ports": [{"containerPort": 8080}]}
        after = {"ports": [{"containerPort": 8080}, {"containerPort": 9090}]}
        self.assertEqual(
            diff_values(before, after), [("ports", before["ports"], after["ports"])]
        )

    def test_field_change_reports_resource_path_and_values(self) -> None:
        base = {"demo": canonical_site([deployment(image="a")])}
        head = {"demo": canonical_site([deployment(image="b")])}
        self.assertEqual(
            diff_sites(base, head),
            [
                "demo: Deployment/demo/website-deploy "
                'spec.template.spec.containers[web].image: "a" -> "b"'
            ],
        )

    def test_added_removed_resources_and_sites_are_reported(self) -> None:
        pdb = {
            "kind": "PodDisruptionBudget",
            "metadata": {"name": "website-deploy", "namespace": "demo"},
        }
        base = {"demo": canonical_site([deployment()]), "gone": {}}
        head = {"demo": canonical_site([deployment(), pdb]), "new": {}}
        self.assertEqual(
            diff_sites(base, head),
            [
                "demo: PodDisruptionBudget/demo/website-deploy added",
                "gone: site removed",
                "new: site added",
            ],
        )

    def test_absent_field_is_distinct_from_null(self) -> None:
        base = {"demo": canonical_site([deployment(ports=None)])}
        head = {"demo": canonical_site([deployment()])}
        self.assertEqual(
            diff_sites(base, head),
            [
                "demo: Deployment/demo/website-deploy "
                "spec.template.spec.containers[web].ports: null -> <absent>"
            ],
        )

    def test_duplicate_resource_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate resource"):
            canonical_site([deployment(), deployment()])


if __name__ == "__main__":
    unittest.main()
