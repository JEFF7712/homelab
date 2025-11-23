from enum import Enum
from typing import Optional, List
import hmac
import os
import subprocess
import arrow
import docker
import requests
from docker.errors import NotFound, APIError, DockerException
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pydantic import BaseModel
from requests.exceptions import RequestException

# Load API_KEY

API = os.getenv("API_KEY") 

def ensure_api_key() -> None:
    if not API:
        raise RuntimeError("API_KEY environment variable is not set")

def check_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not API:
        # Should never happen if startup ran correctly, but guard anyway.
        raise HTTPException(status_code=500, detail="Server API key misconfigured")

    if x_api_key is None or not hmac.compare_digest(x_api_key, API):
        raise HTTPException(status_code=401, detail="Unauthorized")

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_api_key()
    yield

# Configuration

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
PROMETHEUS_INSTANCE = os.getenv("PROM_INSTANCE", "host.docker.internal:9100")

REPO_ROOT = os.getenv("REPO_ROOT", "/repo")
DEPLOY_SCRIPT = os.getenv("DEPLOY_SCRIPT", "/repo/scripts/deploy.sh")

# FastAPI and Docker client

app = FastAPI(title="Homelab Control API", lifespan=lifespan)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

client = docker.from_env()

class serviceName(str, Enum):
    grafana = "grafana"
    n8n = "n8n"
    pihole = "pihole"
    homeassistant = "homeassistant"


# Models

class ContainerInfo(BaseModel):
    id: str
    name: str
    image: str
    status: str
    uptime: Optional[str] = None


class CPUStatus(BaseModel):
    load1: Optional[float]
    usage_percent: Optional[float]
    temperature_celsius: Optional[float]


class ProcessStatus(BaseModel):
    running: Optional[float]


class MemoryStatus(BaseModel):
    total_bytes: Optional[float]
    available_bytes: Optional[float]
    used_percent: Optional[float]


class DiskStatus(BaseModel):
    root_total_bytes: Optional[float]
    root_available_bytes: Optional[float]
    root_used_percent: Optional[float]


class NodeStatus(BaseModel):
    cpu: CPUStatus
    processes: ProcessStatus
    memory: MemoryStatus
    disk: DiskStatus

# Helpers

def query_prometheus(promql: str) -> Optional[float]:
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


# Endpoints

@app.get("/", response_class=HTMLResponse)
def index():
    with open("frontend/index.html", "r") as f:
        return f.read()

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/services", response_model=List[ContainerInfo], dependencies=[Depends(check_api_key)])
def list_services():
    try:
        running_containers = client.containers.list()
        containers_info: List[ContainerInfo] = []

        for container in running_containers:
            started_at = container.attrs["State"].get("StartedAt")
            try:
                human_uptime = arrow.get(started_at).humanize(only_distance=True)
            except Exception:
                human_uptime = None

            containers_info.append(
                ContainerInfo(
                    id=(container.id or "")[:12],
                    name=container.name,
                    image=container.image.tags[0] if container.image.tags else "N/A",
                    status=container.status,
                    uptime=human_uptime,
                )
            )
        return containers_info
    except DockerException:
        raise HTTPException(
            status_code=500,
            detail="Docker daemon is not running or not accessible",
        )


@app.get("/status", response_model=NodeStatus, dependencies=[Depends(check_api_key)])
def status():
    # CPU
    load1 = query_prometheus("node_load1")
    cpu_usage_percent = query_prometheus(
        '100 * (1 - avg by (instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])))'
    )
    cpu_temp = query_prometheus(
        'avg_over_time(node_hwmon_temp_celsius{chip="platform_coretemp_0"}[5m])'
    )

    # Processes
    proc_running = query_prometheus("node_procs_running")

    # Memory
    mem_total = query_prometheus(
        f'node_memory_MemTotal_bytes{{instance="{PROMETHEUS_INSTANCE}"}}'
    )
    mem_avail = query_prometheus(
        f'node_memory_MemAvailable_bytes{{instance="{PROMETHEUS_INSTANCE}"}}'
    )

    # Disk
    disk_total = query_prometheus(
        'node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}'
    )
    disk_avail = query_prometheus(
        'node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}'
    )

    mem_used_percent: Optional[float] = None
    if mem_total not in (None, 0) and mem_avail is not None:
        mem_used_percent = 100.0 * (1.0 - mem_avail / mem_total)

    disk_used_percent: Optional[float] = None
    if disk_total not in (None, 0) and disk_avail is not None:
        disk_used_percent = 100.0 * (1.0 - disk_avail / disk_total)

    return NodeStatus(
        cpu=CPUStatus(
            load1=load1,
            usage_percent=cpu_usage_percent,
            temperature_celsius=cpu_temp,
        ),
        processes=ProcessStatus(
            running=proc_running,
        ),
        memory=MemoryStatus(
            total_bytes=mem_total,
            available_bytes=mem_avail,
            used_percent=mem_used_percent,
        ),
        disk=DiskStatus(
            root_total_bytes=disk_total,
            root_available_bytes=disk_avail,
            root_used_percent=disk_used_percent,
        ),
    )


@app.post("/restart/{service}", dependencies=[Depends(check_api_key)])
def restart_service(service: serviceName):
    container_name = service.value
    try:
        container = client.containers.get(container_name)
        prev_status = container.status
        container.restart()
        container.reload()
        return {
            "service": container_name,
            "previous_status": prev_status,
            "current_status": container.status,
        }
    except NotFound:
        raise HTTPException(status_code=404, detail="Service not found")
    except APIError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error restarting service: {str(e)}",
        )
    except DockerException:
        raise HTTPException(
            status_code=500,
            detail="Docker daemon is not running or not accessible",
        )


@app.post("/deploy", dependencies=[Depends(check_api_key)])
def deploy():
    if not os.path.exists(DEPLOY_SCRIPT):
        raise HTTPException(
            status_code=500,
            detail=f"Deploy script not found at {DEPLOY_SCRIPT}",
        )
    try:
        subprocess.Popen(
            ["/bin/bash", DEPLOY_SCRIPT],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"message": "Deploy triggered"}
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start deploy: {e}",
        )
