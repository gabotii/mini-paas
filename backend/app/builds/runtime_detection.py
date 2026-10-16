"""Detect the runtime of a source tree from marker files.

Precedence: Dockerfile > .NET project (*.csproj) > package.json > Python markers > index.html. An explicit runtime
chosen by the user overrides detection but must still be buildable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.builds.dotnet import find_csproj_files, has_dotnet_project
from app.models import Runtime

PYTHON_MARKERS = ("requirements.txt", "pyproject.toml")


class RuntimeDetectionError(ValueError):
    pass


@dataclass(frozen=True)
class Detection:
    runtime: Runtime
    reason: str


def detect_runtime(root: Path) -> Detection | None:
    if (root / "Dockerfile").is_file():
        return Detection(Runtime.DOCKERFILE, "Dockerfile found")
    csproj = find_csproj_files(root)
    if csproj:
        return Detection(Runtime.DOTNET, f"{csproj[0].relative_to(root).as_posix()} found")
    if (root / "package.json").is_file():
        return Detection(Runtime.NODE, "package.json found")
    for marker in PYTHON_MARKERS:
        if (root / marker).is_file():
            return Detection(Runtime.PYTHON, f"{marker} found")
    if (root / "index.html").is_file():
        return Detection(Runtime.STATIC, "index.html found")
    return None


def _has_python_entry(root: Path) -> bool:
    return any((root / m).is_file() for m in PYTHON_MARKERS) or any(root.glob("*.py"))


def _has(name: str) -> Callable[[Path], bool]:
    def check(root: Path) -> bool:
        return (root / name).is_file()

    return check


_REQUIREMENTS: dict[Runtime, tuple[Callable[[Path], bool], str]] = {
    Runtime.DOCKERFILE: (_has("Dockerfile"), "a Dockerfile"),
    Runtime.NODE: (_has("package.json"), "a package.json"),
    Runtime.PYTHON: (_has_python_entry, "requirements.txt, pyproject.toml or a .py file"),
    Runtime.STATIC: (_has("index.html"), "an index.html"),
    Runtime.DOTNET: (has_dotnet_project, "a .csproj project"),
}


def resolve_runtime(requested: Runtime, root: Path) -> Detection:
    if requested == Runtime.AUTO:
        detected = detect_runtime(root)
        if detected is None:
            raise RuntimeDetectionError(
                "Could not detect the runtime: add a Dockerfile, a .NET project (.csproj), "
                "package.json, requirements.txt/pyproject.toml or index.html at the project root"
            )
        return detected
    check, needs = _REQUIREMENTS[requested]
    if not check(root):
        raise RuntimeDetectionError(f"Runtime '{requested}' was selected but the source has no {needs}")
    return Detection(requested, "selected by user")
