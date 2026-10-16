""".NET (ASP.NET Core) support: locate the web project and generate a multi-stage Dockerfile.

The SDK image builds and publishes; the runtime image (`mcr.microsoft.com/dotnet/aspnet`) runs
the app as its built-in non-root `app` user. The app listens on $PORT through ASPNETCORE_URLS.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SDK_IMAGE = "mcr.microsoft.com/dotnet/sdk"
RUNTIME_IMAGE = "mcr.microsoft.com/dotnet/aspnet"
DEFAULT_DOTNET_VERSION = "10.0"
MAX_DEPTH = 3
SKIP_DIRS = {"bin", "obj", "node_modules", ".git", ".vs", "tests", "test"}

_TFM_RE = re.compile(r"<TargetFrameworks?>\s*net(\d+\.\d+)", re.IGNORECASE)
_ASSEMBLY_RE = re.compile(r"<AssemblyName>\s*([^<\s]+)\s*</AssemblyName>", re.IGNORECASE)
_SDK_WEB_RE = re.compile(r'Sdk\s*=\s*"Microsoft\.NET\.Sdk\.(Web|Razor|BlazorWebAssembly)"', re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# Build outputs and IDE state from a developer machine: never part of the build context.
DOTNET_DOCKERIGNORE_EXTRA = """\
**/bin/
**/obj/
.vs/
*.user
*.suo
"""


class DotnetProjectError(ValueError):
    pass


@dataclass(frozen=True)
class DotnetProject:
    path: PurePosixPath  # relative to the source root
    version: str  # e.g. "10.0"
    assembly: str  # main DLL name without extension
    version_detected: bool


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")[: 256 * 1024]
    except OSError:
        return ""


def find_csproj_files(root: Path, max_depth: int = MAX_DEPTH) -> list[Path]:
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_file() and entry.suffix.lower() == ".csproj":
                found.append(entry)
            elif entry.is_dir() and depth < max_depth and entry.name.lower() not in SKIP_DIRS:
                walk(entry, depth + 1)

    walk(root, 0)
    return found


def has_dotnet_project(root: Path) -> bool:
    return bool(find_csproj_files(root))


def find_web_project(root: Path) -> DotnetProject:
    candidates = find_csproj_files(root)
    if not candidates:
        raise DotnetProjectError("No .csproj file found")
    web = [p for p in candidates if _SDK_WEB_RE.search(_read(p))]
    pool = web or candidates
    if len(pool) > 1:
        # Prefer a project at the root; otherwise the choice is ambiguous.
        at_root = [p for p in pool if p.parent == root]
        if len(at_root) != 1:
            names = ", ".join(str(p.relative_to(root).as_posix()) for p in pool[:5])
            raise DotnetProjectError(
                f"Several .NET projects found ({names}); add a Dockerfile to choose how to build"
            )
        pool = at_root
    project = pool[0]
    text = _read(project)
    tfm = _TFM_RE.search(text)
    assembly_match = _ASSEMBLY_RE.search(text)
    assembly = assembly_match.group(1) if assembly_match else project.stem
    relative = PurePosixPath(project.relative_to(root).as_posix())
    for value in (assembly, *relative.parts):
        if not _SAFE_NAME_RE.match(value):
            raise DotnetProjectError(f"Unsupported characters in .NET project path or name: {value!r}")
    return DotnetProject(
        path=relative,
        version=tfm.group(1) if tfm else DEFAULT_DOTNET_VERSION,
        assembly=assembly,
        version_detected=tfm is not None,
    )


def dotnet_dockerfile(project: DotnetProject, start_command: str | None) -> tuple[str, str]:
    """Return (Dockerfile text, start command)."""
    command = start_command or f"env ASPNETCORE_URLS=http://0.0.0.0:${{PORT}} dotnet {project.assembly}.dll"
    lines = [
        f"FROM {SDK_IMAGE}:{project.version} AS build",
        "ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1",
        "WORKDIR /src",
        "COPY . .",
        f'RUN dotnet restore "{project.path}"',
        f'RUN dotnet publish "{project.path}" -c Release -o /app/publish --no-restore /p:UseAppHost=false',
        "",
        f"FROM {RUNTIME_IMAGE}:{project.version}",
        "ENV ASPNETCORE_ENVIRONMENT=Production DOTNET_CLI_TELEMETRY_OPTOUT=1 PORT=8080",
        "WORKDIR /app",
        # Owned by the runtime user: ASP.NET apps commonly keep SQLite files and uploads
        # (App_Data) next to the binaries. The container filesystem is ephemeral.
        "COPY --from=build --chown=app:app /app/publish .",
        "RUN mkdir -p /app/App_Data && chown app:app /app/App_Data",
        "USER app",
        "EXPOSE 8080",
        "CMD " + json.dumps(["sh", "-c", f"exec {command}"]),
    ]
    return "\n".join(lines) + "\n", command
