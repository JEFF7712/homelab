"""Offline US place search and directions: geo.py and its manifest wiring.

geo.py and the update.py it imports are written out from the rendered
kiwix-update ConfigMap, so the tests track the shipped code. Every test is
offline: downloads, the cluster and the servers are fakes.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from tests.gitops_render import config_map, rendered

DATA = config_map("offline", "kiwix-update")["data"]
_SOURCE = tempfile.TemporaryDirectory()
for _name in ("geo.py", "update.py"):
    (Path(_SOURCE.name) / _name).write_text(DATA[_name])
_SPEC = importlib.util.spec_from_file_location(
    "geo_under_test", Path(_SOURCE.name) / "geo.py"
)
assert _SPEC and _SPEC.loader
geo = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = geo
_SPEC.loader.exec_module(geo)
UpdateError = geo.update.UpdateError
CONFIG = json.loads(DATA["geo.json"])
TODAY = datetime.date(2026, 11, 23)


def md5_line(body: bytes, name: str) -> bytes:
    return f"{hashlib.md5(body).hexdigest()}  {name}\n".encode()


class Site:
    """Fake download server: HEAD answers, checksum files and bodies."""

    def __init__(self) -> None:
        self.heads: dict[str, tuple[str, int, str]] = {}
        self.files: dict[str, bytes] = {}
        self.bodies: dict[str, bytes] = {}
        self.transfers: list[str] = []

    def publish(
        self,
        url: str,
        final: str,
        body: bytes,
        modified: str = "",
        served: bytes | None = None,
    ) -> None:
        self.heads[url] = (final, len(body), modified)
        self.files[f"{final}.md5"] = md5_line(body, final.rsplit("/", 1)[-1])
        self.bodies[final] = body if served is None else served

    def head(self, url: str) -> tuple[str, int, str]:
        return self.heads[url]

    def fetch(self, url: str) -> bytes:
        return self.files[url]

    def transfer(self, url: str, part: Path, size: int) -> None:
        self.transfers.append(url)
        part.write_bytes(self.bodies[url][:size])


def photon_dump(*members: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        for name in members:
            if name.endswith("/"):
                info = tarfile.TarInfo(name.rstrip("/"))
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = 4
                archive.addfile(info, io.BytesIO(b"data"))
    return buffer.getvalue()


class GeoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for part in ("search", "routing", "staging"):
            (self.root / part).mkdir()
        self.site = Site()

    def current(self, part: str) -> str | None:
        link = self.root / part / "current"
        return geo.os.readlink(link) if link.is_symlink() else None

    def built(self, part: str, name: str, file: str) -> None:
        (self.root / part / name).mkdir()
        (self.root / part / name / file).mkdir() if part == "search" else (
            self.root / part / name / file
        ).write_bytes(b"tiles")


class SearchTest(GeoTestCase):
    URL = CONFIG["search"]["url"]

    def update(self, today: datetime.date = TODAY) -> tuple[str, str | None]:
        return geo.update_search(
            self.root,
            CONFIG["search"],
            head=self.site.head,
            fetch=self.site.fetch,
            transfer=self.site.transfer,
            unpack=geo.unpack_photon,
            reserve=0,
            max_age_days=90,
            today=today,
        )

    def test_new_index_is_unpacked_beside_the_served_one_but_not_switched(self) -> None:
        self.built("search", "photon-20260501", "photon_data")
        geo.point(self.root / "search", "photon-20260501")
        dump = photon_dump("photon_data/", "photon_data/node_1/data")
        self.site.publish(self.URL, self.URL, dump, "Tue, 17 Nov 2026 20:48:55 GMT")
        message, target = self.update()
        self.assertEqual(target, "photon-20261117")
        self.assertIn("unpacked", message)
        self.assertTrue(
            (self.root / "search/photon-20261117/photon_data/node_1/data").exists()
        )
        self.assertEqual(self.current("search"), "photon-20260501")
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_young_index_is_not_checked(self) -> None:
        self.built("search", "photon-20261101", "photon_data")
        geo.point(self.root / "search", "photon-20261101")
        self.assertEqual(self.update()[1], None)
        self.assertEqual(self.site.transfers, [])

    def test_corrupt_download_is_dropped_and_nothing_unpacked(self) -> None:
        dump = photon_dump("photon_data/")
        self.site.publish(
            self.URL,
            self.URL,
            dump,
            "Tue, 17 Nov 2026 20:48:55 GMT",
            served=b"x" * len(dump),
        )
        with self.assertRaisesRegex(UpdateError, "MD5"):
            self.update()
        self.assertFalse((self.root / "search/photon-20261117").exists())
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_unpack_refuses_files_outside_photon_data(self) -> None:
        archive = self.root / "dump.tar.bz2"
        archive.write_bytes(photon_dump("photon_data/", "etc/passwd"))
        with self.assertRaisesRegex(UpdateError, "unexpected"):
            geo.unpack_photon(archive, self.root / "search/photon-20261117")
        self.assertFalse((self.root / "search/photon-20261117").exists())


class RoutingTest(GeoTestCase):
    URL = CONFIG["routing"]["url"]
    DATED = "https://download.geofabrik.de/north-america/us/us-261122.osm.pbf"

    def prepare(self) -> tuple[str, str | None]:
        return geo.prepare_routing(
            self.root,
            CONFIG["routing"],
            head=self.site.head,
            fetch=self.site.fetch,
            transfer=self.site.transfer,
            reserve=0,
            max_age_days=90,
            today=TODAY,
        )

    def test_new_extract_is_verified_and_a_build_requested(self) -> None:
        self.site.publish(self.URL, self.DATED, b"pbf data")
        message, target = self.prepare()
        self.assertEqual(target, "us-261122")
        self.assertIn("build requested", message)
        request = json.loads((self.root / geo.BUILD_REQUEST).read_text())
        self.assertEqual(
            request, {"pbf": "staging/us-261122.osm.pbf", "out": "routing/us-261122"}
        )
        self.assertEqual(
            (self.root / "staging/us-261122.osm.pbf").read_bytes(), b"pbf data"
        )

    def test_an_existing_build_is_reused_without_downloading(self) -> None:
        self.built("routing", "us-261122", "valhalla_tiles.tar")
        self.site.publish(self.URL, self.DATED, b"pbf data")
        self.assertEqual(self.prepare()[1], "us-261122")
        self.assertEqual(self.site.transfers, [])
        self.assertFalse((self.root / geo.BUILD_REQUEST).exists())

    def test_undated_extract_is_an_error(self) -> None:
        self.site.publish(self.URL, self.URL, b"pbf data")
        with self.assertRaisesRegex(UpdateError, "dated"):
            self.prepare()


class FinishTest(GeoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.built("search", "photon-20260501", "photon_data")
        self.built("search", "photon-20261117", "photon_data")
        self.built("routing", "us-260501", "valhalla_tiles.tar")
        self.built("routing", "us-261122", "valhalla_tiles.tar")
        geo.point(self.root / "search", "photon-20260501")
        geo.point(self.root / "routing", "us-260501")
        (self.root / "staging/us-261122.osm.pbf").write_bytes(b"pbf")
        self.cluster = mock.Mock()
        self.cluster.ready.return_value = True
        self.geo_file = self.root / "geo.json"
        self.geo_file.write_text(json.dumps(CONFIG))

    def finish(
        self, pending: dict[str, str], problems: list[str], failed: tuple[str, ...] = ()
    ) -> int:
        (self.root / geo.STATE).write_text(json.dumps(pending))
        (self.root / geo.FAILED).write_text(json.dumps(list(failed)))
        args = SimpleNamespace(
            maps_root=self.root,
            geo=self.geo_file,
            maps_url="http://web",
            maps_host="maps.homelab",
            timeout=60,
        )
        with mock.patch.object(geo.time, "sleep"):
            return geo.finish_geo(
                args, cluster=self.cluster, verify=lambda *_: problems
            )

    def test_verified_switch_restarts_servers_and_deletes_old_data(self) -> None:
        pending = {"search": "photon-20261117", "routing": "us-261122"}
        self.assertEqual(self.finish(pending, []), 0)
        self.assertEqual(self.current("search"), "photon-20261117")
        self.assertEqual(self.current("routing"), "us-261122")
        restarted = sorted(call.args[0] for call in self.cluster.restart.call_args_list)
        self.assertEqual(restarted, ["maps-routing", "maps-search"])
        self.assertFalse((self.root / "search/photon-20260501").exists())
        self.assertFalse((self.root / "routing/us-260501").exists())
        self.assertFalse((self.root / "staging/us-261122.osm.pbf").exists())

    def test_failed_check_restores_the_previous_data(self) -> None:
        pending = {"search": "photon-20261117", "routing": "us-261122"}
        self.assertEqual(self.finish(pending, ["route has no legs"]), 1)
        self.assertEqual(self.current("search"), "photon-20260501")
        self.assertEqual(self.current("routing"), "us-260501")
        self.assertEqual(self.cluster.restart.call_count, 4)
        self.assertTrue((self.root / "routing/us-261122").exists())

    def test_leftover_build_request_means_the_build_failed(self) -> None:
        (self.root / geo.BUILD_REQUEST).write_text("{}")
        pending = {"search": "photon-20261117", "routing": "us-261122"}
        self.assertEqual(self.finish(pending, []), 1)
        self.assertEqual(self.current("routing"), "us-260501")
        self.assertEqual(self.current("search"), "photon-20261117")
        restarted = [call.args[0] for call in self.cluster.restart.call_args_list]
        self.assertEqual(restarted, ["maps-search"])

    def test_nothing_new_only_checks_the_servers(self) -> None:
        self.assertEqual(self.finish({}, []), 0)
        self.cluster.restart.assert_not_called()


class ChecksTest(GeoTestCase):
    def test_jar_must_match_its_pinned_hash(self) -> None:
        jar = {"url": "https://x/photon.jar", "path": "photon.jar", "sha256": "0" * 64}
        with self.assertRaisesRegex(UpdateError, "SHA-256"):
            geo.install_jar(self.root, jar, lambda url: b"jar")
        self.assertFalse((self.root / "search/photon.jar").exists())
        jar["sha256"] = hashlib.sha256(b"jar").hexdigest()
        self.assertIn("installed", geo.install_jar(self.root, jar, lambda url: b"jar"))
        self.assertIn("current", geo.install_jar(self.root, jar, lambda url: b"other"))

    def test_server_check_searches_and_routes_through_the_maps_host(self) -> None:
        seen: list[tuple[str, Any]] = []

        def get(url: str, timeout: float = 60, headers: Any = None) -> bytes:
            seen.append((url, headers))
            return json.dumps(
                {"features": [{"properties": {"name": "Chicago"}}]}
            ).encode()

        def post(url: str, body: Any, headers: Any) -> bytes:
            seen.append((url, headers))
            self.assertEqual(body["locations"], CONFIG["check"]["route"])
            return json.dumps(
                {"trip": {"legs": [{}], "summary": {"length": 90.1}}}
            ).encode()

        with (
            mock.patch.object(geo.update, "http_get", get),
            mock.patch.object(geo, "http_post", post),
        ):
            self.assertEqual(
                geo.verify_geo("http://web", "maps.homelab", CONFIG["check"]), []
            )
        self.assertEqual([h["Host"] for _, h in seen], ["maps.homelab", "maps.homelab"])
        self.assertTrue(seen[0][0].startswith("http://web/api/search?q=Chicago"))
        self.assertEqual(seen[1][0], "http://web/api/route")

    def test_md5_files_must_hold_a_checksum(self) -> None:
        self.assertEqual(geo.md5_of(md5_line(b"x", "f")), hashlib.md5(b"x").hexdigest())
        with self.assertRaises(UpdateError):
            geo.md5_of(b"<html>not found</html>")


class GeoManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.objects = {
            (d["kind"], d["metadata"]["name"]): d for d in rendered("offline")
        }

    def pod(self, kind: str, name: str) -> dict[str, Any]:
        spec = self.objects[(kind, name)]["spec"]
        if kind == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        return spec["template"]["spec"]

    def test_update_job_fetches_builds_then_finishes_on_a_large_node(self) -> None:
        pod = self.pod("CronJob", "geo-update")
        self.assertEqual([c["name"] for c in pod["initContainers"]], ["fetch", "build"])
        self.assertEqual([c["name"] for c in pod["containers"]], ["finish"])
        self.assertEqual(
            pod["initContainers"][1]["command"], ["/bin/sh", "/app/build-routing.sh"]
        )
        self.assertIn("build-routing.sh", DATA)
        terms = pod["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]
        hosts = terms["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
        self.assertEqual(sorted(hosts), ["homelab-04", "homelab-05"])
        self.assertEqual(pod["serviceAccountName"], "geo-update")
        # homelab-05's requests are nearly all allocated; a large request left
        # the first build Pending (2026-11-23), so the build borrows memory.
        build = pod["initContainers"][1]["resources"]
        self.assertEqual(build["requests"]["memory"], "1Gi")
        self.assertEqual(build["limits"]["memory"], "12Gi")

    def test_update_job_may_only_restart_the_two_servers(self) -> None:
        (rule,) = self.objects[("Role", "geo-update")]["rules"]
        self.assertEqual(sorted(rule["resourceNames"]), ["maps-routing", "maps-search"])
        self.assertEqual(sorted(rule["verbs"]), ["get", "patch"])
        self.assertEqual(
            sorted(geo.DEPLOYMENTS.values()), ["maps-routing", "maps-search"]
        )

    def test_servers_copy_their_data_to_local_disk_and_run_one_at_a_time(self) -> None:
        for name, path in (("maps-search", "/data"), ("maps-routing", "/data")):
            deployment = self.objects[("Deployment", name)]["spec"]
            self.assertEqual(deployment["strategy"]["type"], "Recreate")
            pod = deployment["template"]["spec"]
            data = next(v for v in pod["volumes"] if v["name"] == "data")
            self.assertIn("emptyDir", data)
            archive = next(
                m
                for m in pod["initContainers"][0]["volumeMounts"]
                if m["name"] == "archive"
            )
            self.assertTrue(archive["readOnly"])
            self.assertIn(
                path, [m["mountPath"] for m in pod["containers"][0]["volumeMounts"]]
            )

    def test_search_server_runs_the_pinned_jar(self) -> None:
        command = self.pod("Deployment", "maps-search")["containers"][0]["command"]
        self.assertIn(f"/maps/search/{CONFIG['search']['jar']['path']}", command)
        self.assertRegex(CONFIG["search"]["jar"]["sha256"], r"^[0-9a-f]{64}$")

    def test_viewer_reaches_search_and_directions_through_nginx(self) -> None:
        nginx = self.objects[("ConfigMap", "offline-web")]["data"]["nginx.conf"]
        self.assertIn(
            "proxy_pass http://maps-search.offline.svc.cluster.local:2322/api;", nginx
        )
        self.assertIn(
            "proxy_pass http://maps-routing.offline.svc.cluster.local:8002/route;",
            nginx,
        )
        page = self.objects[("ConfigMap", "offline-web")]["data"]["index.html"]
        self.assertIn("fetch(`/api/search?", page)
        self.assertIn('fetch("/api/route"', page)
        for name, port in (("maps-search", 2322), ("maps-routing", 8002)):
            self.assertEqual(
                self.objects[("Service", name)]["spec"]["ports"][0]["port"], port
            )


if __name__ == "__main__":
    unittest.main()
