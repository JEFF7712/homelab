"""Cold-copy the existing Prometheus TSDB; retain its NFS PVC for rollback."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

NAMESPACE = "observability"
PROMETHEUS = "kube-prometheus-stack-prometheus"
STATEFULSET = "prometheus-" + PROMETHEUS
OLD_CLAIM = f"prometheus-{PROMETHEUS}-db-{STATEFULSET}-0"
NEW_CLAIM = f"prometheus-local-db-{STATEFULSET}-0"
JOB = "prometheus-tsdb-cold-copy"
IMAGE = "registry.rupan.dev/upstream/docker.io/alpine/k8s@sha256:44ef4942e171939b9c665a4a84beb80e2dcdb9a24330d4651cfdfd2e9deecc47"


def kubectl(
    *args: str,
    document: dict[str, Any] | None = None,
    namespace: str = NAMESPACE,
) -> str:
    request_timeout = "1900s" if args[0] in ("wait", "rollout") else "30s"
    result = subprocess.run(
        ["kubectl", f"--request-timeout={request_timeout}", "-n", namespace, *args],
        input=json.dumps(document) if document is not None else None,
        text=True,
        capture_output=True,
        check=True,
        timeout=1900,
    )
    return result.stdout


def get(kind: str, name: str) -> dict[str, Any]:
    return json.loads(kubectl("get", kind, name, "-o", "json"))


def copy_job(source_uid: str = "", target_uid: str = "") -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": JOB,
            "namespace": NAMESPACE,
            "annotations": {
                "homelab.rupan.dev/source-pvc-uid": source_uid,
                "homelab.rupan.dev/target-pvc-uid": target_uid,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 1800,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "nodeSelector": {"kubernetes.io/hostname": "homelab-05"},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "runAsGroup": 2000,
                        "fsGroup": 2000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "copy",
                            "image": IMAGE,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"memory": "512Mi"},
                            },
                            "command": [
                                "/bin/sh",
                                "-ec",
                                """set -o pipefail
test -d /source/prometheus-db/wal
# Never overwrite a target that has already served, or a partial failed copy.
if test -d /target/prometheus-db; then
  test "$(ls -A /target)" = prometheus-db
  test -z "$(ls -A /target/prometheus-db)"
else
  test -z "$(ls -A /target)"
fi
tar -C /source -cf - . | tar -C /target -xf -
(cd /source && find . -type f -exec sha256sum {} + | sort) > /tmp/source.sha256
(cd /target && find . -type f -exec sha256sum {} + | sort) > /tmp/target.sha256
cmp /tmp/source.sha256 /tmp/target.sha256
test -s /tmp/source.sha256
cp /tmp/source.sha256 /target/.migration-files.sha256
date -u +%FT%TZ > /target/prometheus-db/.migration-complete
sync
echo 'Cold TSDB copy and full file checksum comparison passed; NFS source unchanged.'
""",
                            ],
                            "volumeMounts": [
                                {
                                    "name": "source",
                                    "mountPath": "/source",
                                    "readOnly": True,
                                },
                                {"name": "target", "mountPath": "/target"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "source",
                            "persistentVolumeClaim": {"claimName": OLD_CLAIM},
                        },
                        {
                            "name": "target",
                            "persistentVolumeClaim": {"claimName": NEW_CLAIM},
                        },
                        {"name": "tmp", "emptyDir": {}},
                    ],
                }
            },
        },
    }


def migrate() -> None:
    if os.getenv("HOMELAB_AUTHORIZED") != "1":
        raise ValueError("Run under the task authorization with HOMELAB_AUTHORIZED=1")
    prometheus = get("prometheus", PROMETHEUS)
    template = prometheus["spec"]["storage"]["volumeClaimTemplate"]
    if template.get("metadata", {}).get("name") != "prometheus-local-db":
        raise ValueError(
            "Deploy the declared local-storage Helm values before migrating"
        )
    old_pvc = get("pvc", OLD_CLAIM)
    if old_pvc["spec"].get("storageClassName") != "nfs-cluster":
        raise ValueError("Unexpected source PVC storage class")
    old_pv = get("pv", old_pvc["spec"]["volumeName"])
    if old_pv["spec"].get("nfs", {}).get("server") != "10.0.30.20":
        raise ValueError("Unexpected source NFS server")
    if not old_pv["spec"]["nfs"].get("path", "").startswith("/tank/cluster/"):
        raise ValueError("Unexpected source NFS export path")
    new_pv = get("pv", "prometheus-nvme-05")
    if (
        new_pv["spec"].get("local", {}).get("path") != "/persist/prometheus"
        or new_pv["spec"].get("storageClassName") != "local-nvme"
        or new_pv["spec"].get("persistentVolumeReclaimPolicy") != "Retain"
    ):
        raise ValueError("Unexpected destination local path")
    expressions = (
        new_pv["spec"]
        .get("nodeAffinity", {})
        .get("required", {})
        .get("nodeSelectorTerms", [])
    )
    if expressions != [
        {
            "matchExpressions": [
                {
                    "key": "kubernetes.io/hostname",
                    "operator": "In",
                    "values": ["homelab-05"],
                }
            ]
        }
    ]:
        raise ValueError("Unexpected destination node affinity")
    current_pods = json.loads(kubectl("get", "pods", "-o", "json"))["items"]
    current = next(
        (pod for pod in current_pods if pod["metadata"]["name"] == STATEFULSET + "-0"),
        None,
    )
    if current is not None and current.get("spec", {}).get("nodeName") == "homelab-05":
        claims = {
            volume.get("persistentVolumeClaim", {}).get("claimName")
            for volume in current["spec"].get("volumes", [])
        }
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in current.get("status", {}).get("conditions", [])
        )
        if NEW_CLAIM in claims and ready:
            retained = get("pvc", OLD_CLAIM)
            if retained["spec"].get("storageClassName") != "nfs-cluster":
                raise ValueError(
                    "Retained rollback PVC has an unexpected storage class"
                )
            print(
                "Prometheus is already Ready on local storage; NFS rollback claim retained."
            )
            return
    # Stop the owner as well as Helm: a long copy must not be interrupted by a
    # periodic Flux apply replacing the temporary pause settings.
    kubectl(
        "patch",
        "kustomization",
        "observability",
        "--type=merge",
        "-p",
        '{"spec":{"suspend":true}}',
        namespace="flux-system",
    )
    print("Pausing Helm and Prometheus reconciliation for an offline copy.", flush=True)
    kubectl(
        "patch",
        "helmrelease",
        "kube-prometheus-stack",
        "--type=merge",
        "-p",
        '{"spec":{"suspend":true}}',
    )
    kubectl(
        "patch",
        "prometheus",
        PROMETHEUS,
        "--type=merge",
        "-p",
        '{"spec":{"paused":true}}',
    )
    # Wait for the operator to observe the pause before scaling its child.
    kubectl(
        "wait",
        "prometheus",
        PROMETHEUS,
        "--for=jsonpath={.status.paused}=true",
        "--timeout=120s",
    )
    kubectl("scale", "statefulset", STATEFULSET, "--replicas=0")
    pods = json.loads(kubectl("get", "pods", "-o", "json"))["items"]
    if any(pod["metadata"]["name"] == f"{STATEFULSET}-0" for pod in pods):
        kubectl("wait", "pod", f"{STATEFULSET}-0", "--for=delete", "--timeout=300s")
    kubectl(
        "apply",
        "-f",
        "-",
        document={
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": NEW_CLAIM, "namespace": NAMESPACE},
            "spec": template["spec"],
        },
    )
    # A successful job is reusable for an interrupted migration. A failed or
    # partial target is retained for investigation, never erased by this tool.
    existing = json.loads(kubectl("get", "jobs", "-o", "json"))
    prior = next(
        (job for job in existing["items"] if job["metadata"]["name"] == JOB), None
    )
    target_pvc = get("pvc", NEW_CLAIM)
    job = copy_job(old_pvc["metadata"]["uid"], target_pvc["metadata"]["uid"])
    if prior is None:
        kubectl("create", "-f", "-", document=job)
    elif prior["metadata"].get("annotations", {}) != job["metadata"]["annotations"]:
        raise ValueError("Existing copy Job belongs to different PVC identities")
    kubectl("wait", "job", JOB, "--for=condition=complete", "--timeout=1850s")
    print(kubectl("logs", f"job/{JOB}"), flush=True)
    # StatefulSet claim templates are immutable. Both PVCs remain retained.
    kubectl("delete", "statefulset", STATEFULSET, "--cascade=orphan")
    kubectl(
        "patch",
        "prometheus",
        PROMETHEUS,
        "--type=merge",
        "-p",
        '{"spec":{"paused":false}}',
    )
    kubectl(
        "patch",
        "helmrelease",
        "kube-prometheus-stack",
        "--type=merge",
        "-p",
        '{"spec":{"suspend":false}}',
    )
    kubectl(
        "patch",
        "kustomization",
        "observability",
        "--type=merge",
        "-p",
        '{"spec":{"suspend":false}}',
        namespace="flux-system",
    )
    kubectl("wait", "--for=create", "statefulset", STATEFULSET, "--timeout=120s")
    kubectl("rollout", "status", f"statefulset/{STATEFULSET}", "--timeout=600s")
    pod = get("pod", STATEFULSET + "-0")
    if pod["spec"].get("nodeName") != "homelab-05":
        raise RuntimeError("Prometheus did not start on the retained local PV node")
    print(
        "Prometheus now runs on homelab-05; old NFS PVC remains available for rollback."
    )


if __name__ == "__main__":
    migrate()
