"""Example FastAPI app for Mini PaaS."""

import os
import platform
import time

from fastapi import FastAPI

app = FastAPI(title="Mini PaaS Python example")
STARTED = time.time()


@app.get("/")
def index() -> dict[str, object]:
    return {
        "message": "Hello from Mini PaaS",
        "runtime": f"Python {platform.python_version()}",
        "deployment": os.environ.get("MINIPAAS_DEPLOYMENT"),
        "greeting": os.environ.get("GREETING", "set GREETING in the Environment tab"),
    }


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "uptime_seconds": round(time.time() - STARTED, 1)}
