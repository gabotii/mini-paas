from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.builds.dockerfiles import (
    GENERATED_DOCKERFILE,
    BuildPlanError,
    exposed_port,
    node_start_command,
    prepare_build,
    python_start_command,
)
from app.builds.runtime_detection import RuntimeDetectionError, detect_runtime, resolve_runtime
from app.models import Runtime


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return root


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"Dockerfile": "FROM x", "package.json": "{}"}, Runtime.DOCKERFILE),
        ({"package.json": "{}", "requirements.txt": ""}, Runtime.NODE),
        ({"requirements.txt": "flask"}, Runtime.PYTHON),
        ({"pyproject.toml": "[project]"}, Runtime.PYTHON),
        ({"index.html": "<h1>"}, Runtime.STATIC),
    ],
)
def test_detection_precedence(tmp_path: Path, files: dict[str, str], expected: Runtime) -> None:
    detection = detect_runtime(tree(tmp_path, files))
    assert detection is not None
    assert detection.runtime == expected


def test_detection_fails_without_markers(tmp_path: Path) -> None:
    tree(tmp_path, {"README.md": "hi"})
    assert detect_runtime(tmp_path) is None
    with pytest.raises(RuntimeDetectionError):
        resolve_runtime(Runtime.AUTO, tmp_path)


def test_user_override_must_be_buildable(tmp_path: Path) -> None:
    tree(tmp_path, {"index.html": "x", "app.py": "print(1)"})
    assert resolve_runtime(Runtime.PYTHON, tmp_path).runtime == Runtime.PYTHON
    with pytest.raises(RuntimeDetectionError, match=r"package\.json"):
        resolve_runtime(Runtime.NODE, tmp_path)


def test_python_fastapi_detection(tmp_path: Path) -> None:
    tree(tmp_path, {"main.py": "from fastapi import FastAPI\napi = FastAPI()\n"})
    command, extra = python_start_command(tmp_path, "fastapi\n")
    assert command == "uvicorn main:api --host 0.0.0.0 --port ${PORT}"
    assert extra == ["uvicorn"]


def test_python_procfile_wins(tmp_path: Path) -> None:
    tree(tmp_path, {"Procfile": "web: gunicorn app:app\n", "main.py": "app = FastAPI()"})
    assert python_start_command(tmp_path, "")[0] == "gunicorn app:app"


def test_python_without_entrypoint_fails(tmp_path: Path) -> None:
    tree(tmp_path, {"requirements.txt": "requests"})
    with pytest.raises(BuildPlanError):
        python_start_command(tmp_path, "requests")


def test_python_dockerfile_is_non_root_and_exec(tmp_path: Path) -> None:
    tree(tmp_path, {"requirements.txt": "fastapi\nuvicorn\n", "main.py": "app = FastAPI()\n"})
    plan = prepare_build(tmp_path, Runtime.PYTHON, None)
    text = (tmp_path / GENERATED_DOCKERFILE).read_text()
    assert plan.default_port == 8000
    assert plan.read_only_rootfs
    assert "USER 10001" in text
    assert 'CMD ["sh", "-c", "exec uvicorn main:app' in text
    assert (tmp_path / ".dockerignore").is_file()


def test_start_command_is_json_escaped(tmp_path: Path) -> None:
    tree(tmp_path, {"requirements.txt": "", "main.py": ""})
    prepare_build(tmp_path, Runtime.PYTHON, 'python -c "print(1)"; echo $PORT')
    cmd_line = next(
        line for line in (tmp_path / GENERATED_DOCKERFILE).read_text().splitlines() if line.startswith("CMD")
    )
    assert json.loads(cmd_line[4:]) == ["sh", "-c", 'exec python -c "print(1)"; echo $PORT']


def test_node_start_command() -> None:
    assert node_start_command({"scripts": {"start": "node server.js"}}) == "node server.js"
    assert node_start_command({"scripts": {"start": "next start"}}) == "npm start"
    assert node_start_command({"main": "app.js"}) == "node app.js"
    assert node_start_command({}) == "node index.js"


def test_node_dockerfile_builds_and_prunes(tmp_path: Path) -> None:
    pkg = {"scripts": {"build": "tsc", "start": "node dist/index.js"}}
    tree(tmp_path, {"package.json": json.dumps(pkg), "package-lock.json": "{}"})
    plan = prepare_build(tmp_path, Runtime.NODE, None)
    text = (tmp_path / GENERATED_DOCKERFILE).read_text()
    assert plan.default_port == 3000
    assert "RUN npm ci" in text
    assert "RUN npm run build" in text
    assert "npm prune --omit=dev" in text
    assert "USER node" in text


def test_node_invalid_package_json(tmp_path: Path) -> None:
    tree(tmp_path, {"package.json": "{not json"})
    with pytest.raises(BuildPlanError, match="not valid JSON"):
        prepare_build(tmp_path, Runtime.NODE, None)


def test_static_plan(tmp_path: Path) -> None:
    tree(tmp_path, {"index.html": "x"})
    plan = prepare_build(tmp_path, Runtime.STATIC, None)
    assert plan.default_port == 8080
    assert "nginx-unprivileged" in (tmp_path / GENERATED_DOCKERFILE).read_text()


def test_user_dockerfile_uses_expose(tmp_path: Path) -> None:
    tree(tmp_path, {"Dockerfile": "FROM python\nEXPOSE 5000\n"})
    plan = prepare_build(tmp_path, Runtime.DOCKERFILE, None)
    assert plan.dockerfile == "Dockerfile"
    assert plan.default_port == 5000
    assert not plan.read_only_rootfs
    assert not (tmp_path / GENERATED_DOCKERFILE).exists()


def test_exposed_port_parsing() -> None:
    assert exposed_port("FROM x\n  expose 3000/tcp\n") == 3000
    assert exposed_port("FROM x\n") is None
    assert exposed_port("EXPOSE 99999") is None


# ------------------------------------------------------------------ .NET
WEB_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>
</Project>"""
LIB_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>
</Project>"""


def test_dotnet_detected_before_node(tmp_path: Path) -> None:
    tree(tmp_path, {"Shop.csproj": WEB_CSPROJ, "Program.cs": "", "package.json": "{}"})
    detection = detect_runtime(tmp_path)
    assert detection is not None
    assert detection.runtime == Runtime.DOTNET
    assert detection.reason == "Shop.csproj found"


def test_dotnet_plan_for_web_project(tmp_path: Path) -> None:
    tree(
        tmp_path,
        {"Shop.csproj": WEB_CSPROJ, "Program.cs": "", "bin/Debug/x.dll": "", "obj/project.assets.json": "{}"},
    )
    plan = prepare_build(tmp_path, Runtime.DOTNET, None)
    text = (tmp_path / GENERATED_DOCKERFILE).read_text()
    assert plan.default_port == 8080
    assert plan.read_only_rootfs is False  # SQLite/uploads next to the binaries
    assert "FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build" in text
    assert "FROM mcr.microsoft.com/dotnet/aspnet:10.0" in text
    assert 'RUN dotnet publish "Shop.csproj"' in text
    assert "USER app" in text
    assert "dotnet Shop.dll" in text and "ASPNETCORE_URLS=http://0.0.0.0:${PORT}" in text
    ignore = (tmp_path / ".dockerignore").read_text()
    assert "**/bin/" in ignore and "**/obj/" in ignore


def test_dotnet_nested_web_project_and_assembly_name(tmp_path: Path) -> None:
    csproj = WEB_CSPROJ.replace("</PropertyGroup>", "<AssemblyName>StoreApp</AssemblyName></PropertyGroup>")
    tree(
        tmp_path,
        {
            "Store.sln": "",
            "src/Store.Web/Store.Web.csproj": csproj,
            "src/Store.Core/Store.Core.csproj": LIB_CSPROJ,
        },
    )
    prepare_build(tmp_path, Runtime.DOTNET, None)
    text = (tmp_path / GENERATED_DOCKERFILE).read_text()
    assert 'dotnet restore "src/Store.Web/Store.Web.csproj"' in text
    assert "dotnet StoreApp.dll" in text


def test_dotnet_ambiguous_projects_need_a_dockerfile(tmp_path: Path) -> None:
    tree(tmp_path, {"a/A.csproj": WEB_CSPROJ, "b/B.csproj": WEB_CSPROJ})
    with pytest.raises(BuildPlanError, match=r"Several .NET projects"):
        prepare_build(tmp_path, Runtime.DOTNET, None)


def test_dotnet_version_defaults_when_missing(tmp_path: Path) -> None:
    tree(tmp_path, {"App.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>'})
    plan = prepare_build(tmp_path, Runtime.DOTNET, None)
    assert "assumed" in plan.notes[0]
    assert "sdk:10.0" in (tmp_path / GENERATED_DOCKERFILE).read_text()


def test_dotnet_build_outputs_are_not_scanned(tmp_path: Path) -> None:
    tree(tmp_path, {"bin/Debug/Stale.csproj": WEB_CSPROJ, "index.html": "x"})
    detection = detect_runtime(tmp_path)
    assert detection is not None
    assert detection.runtime == Runtime.STATIC
