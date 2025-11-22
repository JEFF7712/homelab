from fastapi import FastAPI, HTTPException, Depends, Header
from enum import Enum
import os 
import docker
from docker.errors import NotFound, APIError, DockerException
import uvicorn

API = os.getenv("API_KEY")

app = FastAPI()

client = docker.from_env()

class serviceName(str, Enum):
    grafana = "grafana"
    n8n = "n8n"
    pihole = "pihole"
    homeassistant = "homeassistant"


def check_api_key(x_api_key: str | None = Header(None)):
    return {"x_api_key seen by server:", repr(x_api_key)}
    if x_api_key != API:
        raise HTTPException(status_code=401, detail="Unauthorized")

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
                "Status": f"{container.status}"
            })
        return containers_info
    except DockerException:
        raise HTTPException(status_code=500, detail="Docker daemon is not running or not accessible")

# @app.get("/status", dependencies=[Depends(check_api_key)])
# def status():
#     # get system status and metrics
#     ...

@app.post("/restart/{service}", dependencies=[Depends(check_api_key)])
def restart_service(service: serviceName):
    # Add try and except block to handle errors
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
