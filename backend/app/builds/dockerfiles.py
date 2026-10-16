"""Generate Dockerfiles for the built-in runtimes.

Generated images:
* run as a dedicated non-root user,
* keep application files owned by root (the app cannot modify its own code),
* are compatible with a read-only root filesystem (writes go to the /tmp tmpfs),
* start with `exec` so the app is PID 1's direct child and receives SIGTERM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.builds.dotnet import (
    DOTNET_DOCKERIGNORE_EXTRA,
    DotnetProjectError,
    dotnet_dockerfile,
    find_web_project,
)
from app.models import Runtime

GENERATED_DOCKERFILE = "Dockerfile.minipaas"
DEFAULT_PORTS = {Runtime.PYTHON: 8000, Runtime.NODE: 3000, Runtime.STATIC: 8080, Runtime.DOTNET: 8080}
# Runtimes whose generated images work with a read-only root filesystem. .NET apps commonly
# write SQLite files and uploads next to the binaries; Dockerfile images are unknown.
READ_ONLY_RUNTIMES = frozenset({Runtime.PYTHON, Runtime.NODE, Runtime.STATIC})
DEFAULT_DOCKERFILE_PORT = 8000

PYTHON_IMAGE = "python:3.12-slim"
NODE_IMAGE = "node:22-alpine"
STATIC_IMAGE = "nginxinc/nginx-unprivileged:1.27-alpine"

DOCKERIGNORE = """\
.git
**/__pycache__
**/*.pyc
.venv
venv
node_modules
.env
.env.*
Dockerfile.minipaas
"""


class BuildPlanError(ValueError):
    pass


@dataclass
class BuildPlan:
    runtime: Runtime
    dockerfile: str  # file name relative to the context
    default_port: int
    start_command: str | None
    read_only_rootfs: bool
    notes: list[str] = field(default_factory=list)


def _cmd(command: str) -> str:
    return "CMD " + json.dumps(["sh", "-c", f"exec {command}"])


def _read(path: Path, limit: int = 256 * 1024) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def procfile_web(root: Path) -> str | None:
    for line in _read(root / "Procfile").splitlines():
        if line.startswith("web:"):
            command = line.split(":", 1)[1].strip()
            return command or None
    return None


_FASTAPI_RE = re.compile(r"^(\w+)\s*=\s*FastAPI\(", re.MULTILINE)
_FLASK_RE = re.compile(r"^(\w+)\s*=\s*Flask\(", re.MULTILINE)


def python_start_command(root: Path, requirements: str) -> tuple[str, list[str]]:
    """Return (command, extra pip packages) to start a Python app."""
    if command := procfile_web(root):
        return command, []
    for module in ("main", "app", "server", "api"):
        source = _read(root / f"{module}.py")
        if not source:
            continue
        if match := _FASTAPI_RE.search(source):
            extra = [] if "uvicorn" in requirements.lower() else ["uvicorn"]
            return f"uvicorn {module}:{match.group(1)} --host 0.0.0.0 --port ${{PORT}}", extra
        if match := _FLASK_RE.search(source):
            if "gunicorn" in requirements.lower():
                return f"gunicorn --bind 0.0.0.0:${{PORT}} {module}:{match.group(1)}", []
            return f"flask --app {module}:{match.group(1)} run --host 0.0.0.0 --port ${{PORT}}", []
    if (root / "manage.py").is_file():
        return "python manage.py runserver 0.0.0.0:${PORT}", []
    for module in ("main", "app", "server"):
        if (root / f"{module}.py").is_file():
            return f"python {module}.py", []
    raise BuildPlanError(
        "Could not determine how to start the Python app: add a Procfile with a 'web:' line, "
        "a main.py/app.py, or set a start command in the project settings"
    )


def python_dockerfile(root: Path, start_command: str | None) -> tuple[str, str, list[str]]:
    requirements = _read(root / "requirements.txt")
    command, extra = (start_command, []) if start_command else python_start_command(root, requirements)
    lines = [
        f"FROM {PYTHON_IMAGE}",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \\",
        "    PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp PORT=8000",
        "WORKDIR /app",
        "RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app app",
    ]
    if (root / "requirements.txt").is_file():
        lines += ["COPY requirements.txt ./", "RUN pip install -r requirements.txt"]
    lines.append("COPY . .")
    if not (root / "requirements.txt").is_file() and (root / "pyproject.toml").is_file():
        lines.append("RUN pip install .")
    if extra:
        lines.append("RUN pip install " + " ".join(extra))
    lines += ["USER 10001", "EXPOSE 8000", _cmd(command)]
    notes = [f"Start command: {command}"]
    if extra:
        notes.append(f"Added missing server package(s): {', '.join(extra)}")
    return "\n".join(lines) + "\n", command, notes


def node_start_command(package: dict[str, object]) -> str:
    scripts = package.get("scripts")
    start = scripts.get("start") if isinstance(scripts, dict) else None
    if isinstance(start, str) and start.strip():
        # Run plain `node ...` scripts directly so signals reach the process and npm never
        # needs to write its log directory on the read-only filesystem.
        return start.strip() if start.strip().startswith("node ") else "npm start"
    main = package.get("main")
    if isinstance(main, str) and main.strip():
        return f"node {main.strip()}"
    return "node index.js"


def node_dockerfile(root: Path, start_command: str | None) -> tuple[str, str, list[str]]:
    try:
        package = json.loads(_read(root / "package.json"))
    except json.JSONDecodeError as exc:
        raise BuildPlanError(f"package.json is not valid JSON: {exc.msg}") from exc
    if not isinstance(package, dict):
        raise BuildPlanError("package.json must contain a JSON object")
    command = start_command or node_start_command(package)
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    has_lock = (root / "package-lock.json").is_file()
    install = "npm ci" if has_lock else "npm install --no-audit --no-fund"
    lines = [
        f"FROM {NODE_IMAGE}",
        "ENV NPM_CONFIG_CACHE=/tmp/.npm NPM_CONFIG_UPDATE_NOTIFIER=false PORT=3000",
        "WORKDIR /app",
        "COPY package*.json ./",
        f"RUN {install}",
        "COPY . .",
    ]
    if isinstance(scripts, dict) and "build" in scripts:
        lines.append("RUN npm run build")
    lines += [
        "RUN npm prune --omit=dev && rm -rf /tmp/.npm",
        "ENV NODE_ENV=production",
        "USER node",
        "EXPOSE 3000",
        _cmd(command),
    ]
    return "\n".join(lines) + "\n", command, [f"Start command: {command}"]


def static_dockerfile() -> str:
    return (
        "\n".join(
            [
                f"FROM {STATIC_IMAGE}",
                "COPY . /usr/share/nginx/html",
                "EXPOSE 8080",
            ]
        )
        + "\n"
    )


_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(\d{1,5})", re.MULTILINE | re.IGNORECASE)


def exposed_port(dockerfile_text: str) -> int | None:
    match = _EXPOSE_RE.search(dockerfile_text)
    if match:
        port = int(match.group(1))
        if 1 <= port <= 65535:
            return port
    return None


def prepare_build(root: Path, runtime: Runtime, start_command: str | None) -> BuildPlan:
    """Write the Dockerfile (unless the user supplied one) and return the build plan."""
    if not (root / ".dockerignore").exists():
        extra = DOTNET_DOCKERIGNORE_EXTRA if runtime == Runtime.DOTNET else ""
        (root / ".dockerignore").write_text(DOCKERIGNORE + extra, encoding="utf-8")

    if runtime == Runtime.DOCKERFILE:
        text = _read(root / "Dockerfile")
        return BuildPlan(
            runtime=runtime,
            dockerfile="Dockerfile",
            default_port=exposed_port(text) or DEFAULT_DOCKERFILE_PORT,
            start_command=start_command,
            read_only_rootfs=False,  # arbitrary images often write to their filesystem
            notes=["Using the Dockerfile from the source"],
        )
    if runtime == Runtime.PYTHON:
        text, command, notes = python_dockerfile(root, start_command)
    elif runtime == Runtime.NODE:
        text, command, notes = node_dockerfile(root, start_command)
    elif runtime == Runtime.STATIC:
        text, command, notes = static_dockerfile(), None, ["Serving files with nginx"]
    elif runtime == Runtime.DOTNET:
        try:
            project = find_web_project(root)
        except DotnetProjectError as exc:
            raise BuildPlanError(str(exc)) from exc
        text, command = dotnet_dockerfile(project, start_command)
        notes = [
            f".NET project: {project.path} (.NET {project.version}"
            + ("" if project.version_detected else ", TargetFramework not found: assumed")
            + ")",
            f"Start command: {command}",
        ]
    else:
        raise BuildPlanError(f"Unsupported runtime {runtime}")
    (root / GENERATED_DOCKERFILE).write_text(text, encoding="utf-8")
    return BuildPlan(
        runtime=runtime,
        dockerfile=GENERATED_DOCKERFILE,
        default_port=DEFAULT_PORTS[runtime],
        start_command=command,
        read_only_rootfs=runtime in READ_ONLY_RUNTIMES,
        notes=notes,
    )
