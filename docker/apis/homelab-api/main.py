from fastapi import FastAPI, HTTPException, Depends, Header
from enum import Enum
import os 
import docker
from docker.errors import NotFound, APIError, DockerException
import arrow
import requests
from requests.exceptions import RequestException

API = os.getenv("API_KEY")

app = FastAPI()

client = docker.from_env()

class serviceName(str, Enum):
    grafana = "grafana"
    n8n = "n8n"
    pihole = "pihole"
    homeassistant = "homeassistant"

PROMETHEUS_URL = "http://prometheus:9090"

def check_api_key(x_api_key: str | None = Header(None)):
    if x_api_key != API:
        raise HTTPException(status_code=401, detail="Unauthorized")

def query_prometheus(promql: str) -> float | None:
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=3,
        )
        resp.raise_for_status()
    except RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error talking to Prometheus: {str(e)}",
        )

    data = resp.json()
    if data.get("status") != "success":
        raise HTTPException(
            status_code=502,
            detail=f"Prometheus returned error: {data.get('error', 'unknown')}",
        )

    result = data.get("data", {}).get("result", [])
    if not result:
        return None

    value_str = result[0]["value"][1]
    try:
        return float(value_str)
    except (ValueError, TypeError):
        return None

@app.get("/services", dependencies=[Depends(check_api_key)])
def list_services():
    try: 
        running_containers = client.containers.list()
        containers_info = []
        for container in running_containers:
            containers_info.append({
                "ID": f"{container.id[:12]}",
                "Name": f"{container.name}",
                "Image": f"{container.image.tags[0] if container.image.tags else 'N/A'}",
                "Status": f"{container.status} - up {arrow.get(container.attrs['State']['StartedAt']).humanize(only_distance=True)}",
            })
        return containers_info
    except DockerException:
        raise HTTPException(status_code=500, detail="Docker daemon is not running or not accessible")

@app.get("/status", dependencies=[Depends(check_api_key)])
def status():
    # CPU 
    load1 = query_prometheus("node_load1")
    cpu_usage_percent = query_prometheus('100 * (1 - avg by (instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])))')
    cpu_temp = query_prometheus('avg_over_time(node_hwmon_temp_celsius{chip="platform_coretemp_0"}[5m])')

    # Processes
    proc_running = query_prometheus("node_procs_running")
    # Memory
    mem_total = query_prometheus("node_memory_MemTotal_bytes{instance=\"host.docker.internal:9100\"}")
    mem_avail = query_prometheus("node_memory_MemAvailable_bytes{instance=\"host.docker.internal:9100\"}")

    # Disk
    disk_total = query_prometheus(
        'node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}'
    )
    disk_avail = query_prometheus(
        'node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}'
    )

    mem_used_percent = None
    if mem_total not in (None, 0) and mem_avail is not None:
        mem_used_percent = 100.0 * (1.0 - mem_avail / mem_total)

    disk_used_percent = None
    if disk_total not in (None, 0) and disk_avail is not None:
        disk_used_percent = 100.0 * (1.0 - disk_avail / disk_total)

    return {
        "cpu": {
            "load1": load1,
            "usage_percent": cpu_usage_percent,
            "temperature_celsius": cpu_temp,
        },
        "processes": {
            "running": proc_running,
        },
        "memory": {
            "total_bytes": mem_total,
            "available_bytes": mem_avail,
            "used_percent": mem_used_percent,
        },
        "disk": {
            "root_total_bytes": disk_total,
            "root_available_bytes": disk_avail,
            "root_used_percent": disk_used_percent,
        },
    }

@app.post("/restart/{service}", dependencies=[Depends(check_api_key)])
def restart_service(service: serviceName):
    try:
        container_name = service.value
        container = client.containers.get(container_name)
        container.restart()
        return {"message": f"{container_name} restarted successfully"}
    except NotFound:
        raise HTTPException(status_code=404, detail="Service not found")
    except APIError as e:
        raise HTTPException(status_code=500, detail=f"Error restarting service: {str(e)}")
    except DockerException:
        raise HTTPException(status_code=500, detail="Docker daemon is not running or not accessible")
