"""Install Pulldown's native Hermes plugin without changing host configuration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any


OWNER = "pulldown"
MARKER = ".pulldown-install.json"
MANAGED_FILES = ("plugin.yaml", "__init__.py", "runtime.json")
ASSET_ROOT = Path(__file__).parent / "hermes_plugin"


class InstallError(ValueError):
    """An installation could not be changed without replacing unowned files."""


def resolve_home(home: str | Path | None = None) -> Path:
    """Keep lexical paths so ancestor links can be checked before following them."""
    value = home if home is not None else os.environ.get("HERMES_HOME", "").strip()
    return Path(os.path.abspath(os.path.expanduser(str(value or Path.home() / ".hermes"))))


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _assert_safe_path(path: Path) -> None:
    """Reject symlinks and Windows junctions anywhere above an install target."""
    current = Path(os.path.abspath(path))
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        else:
            is_reparse = bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            if stat.S_ISLNK(info.st_mode) or is_reparse:
                raise InstallError(f"Refusing a symlink or junction in the Hermes install path: {current}")
        if current == current.parent:
            return
        current = current.parent


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _assert_cache_directory(path: Path) -> None:
    _assert_safe_path(path)
    if _exists(path) and not path.is_dir():
        raise InstallError(f"Pulldown's Hermes cache path must be a directory: {path}")


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _read_marker(plugin_dir: Path) -> dict[str, Any]:
    marker_path = plugin_dir / MARKER
    _assert_safe_path(marker_path)
    try:
        if not _is_regular_file(marker_path):
            raise ValueError("Missing regular ownership marker")
        marker = json.loads(marker_path.read_bytes())
        if (
            not isinstance(marker, dict)
            or marker.get("schema_version") != 1
            or marker.get("owner") != OWNER
            or not isinstance(marker.get("files"), dict)
            or set(marker["files"]) != set(MANAGED_FILES)
            or any(
                not isinstance(marker["files"][name], str)
                or re.fullmatch(r"[a-f0-9]{64}", marker["files"][name]) is None
                for name in MANAGED_FILES
            )
        ):
            raise ValueError("Invalid ownership marker")
        return marker
    except (OSError, ValueError, TypeError) as error:
        raise InstallError(f"Refusing to modify an unowned Hermes plugin directory: {plugin_dir}") from error


def _assert_unchanged(plugin_dir: Path, marker: dict[str, Any]) -> None:
    for name in MANAGED_FILES:
        target = plugin_dir / name
        _assert_safe_path(target)
        if not _exists(target):
            continue  # Reinstalling can repair missing managed files.
        if not _is_regular_file(target) or _hash(target.read_bytes()) != marker["files"][name]:
            raise InstallError(
                f"Preserving a modified Hermes plugin file: {target}. "
                "Move or restore it before reinstalling or uninstalling."
            )


def _write_atomic(target: Path, content: bytes) -> None:
    _assert_safe_path(target)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pulldown-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        _assert_safe_path(target)
        # Replacing the directory entry leaves hard-linked copies untouched.
        os.replace(temporary, target)
    finally:
        if _exists(temporary):
            temporary.unlink()


def _runtime_issues(runtime_path: Path, home: Path) -> list[str]:
    try:
        runtime = json.loads(runtime_path.read_bytes())
        if not isinstance(runtime, dict):
            return ["Hermes runtime configuration must be a JSON object."]
        issues = []
        executable = runtime.get("python")
        if not isinstance(executable, str) or not Path(executable).is_absolute() or not Path(executable).is_file():
            issues.append("Hermes runtime python must point to an existing absolute file path.")
        elif not os.access(executable, os.X_OK):
            issues.append("The Python executable recorded in the Hermes runtime is not executable.")
        expected_cache = home / "cache" / "pulldown"
        if runtime.get("cache_dir") != str(expected_cache):
            issues.append("Hermes runtime cache_dir must stay in this profile's cache/pulldown directory.")
        _assert_cache_directory(expected_cache)
        return issues
    except (OSError, ValueError, TypeError) as error:
        return [f"Hermes runtime configuration is unavailable or invalid: {error}"]


def install(home: str | Path | None = None) -> dict[str, Any]:
    """Copy owned plugin files and record this environment's Python executable."""
    target_home = resolve_home(home)
    plugin_dir = target_home / "plugins" / "pulldown"
    cache_dir = target_home / "cache" / "pulldown"
    _assert_safe_path(plugin_dir)
    _assert_cache_directory(cache_dir)
    if _exists(plugin_dir):
        _assert_unchanged(plugin_dir, _read_marker(plugin_dir))

    # Do not resolve symlinks: a virtualenv's bin/python may link to system Python,
    # but invoking that system path would lose the virtualenv's dependencies.
    executable = os.path.abspath(sys.executable)
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise InstallError(f"The current Python executable is unavailable: {executable}")
    contents: dict[str, bytes] = {}
    for name in ("plugin.yaml", "__init__.py"):
        source = ASSET_ROOT / name
        if not source.is_file():
            raise InstallError(f"Missing packaged Hermes asset: {source}. Reinstall Pulldown before installing its plugin.")
        contents[name] = source.read_bytes()
    contents["runtime.json"] = _json_bytes({"python": executable, "cache_dir": str(cache_dir)})
    contents[MARKER] = _json_bytes({
        "schema_version": 1,
        "owner": OWNER,
        "files": {name: _hash(contents[name]) for name in MANAGED_FILES},
    })

    # Read and validate every input before changing the host profile.
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        _write_atomic(plugin_dir / name, content)
    return {
        "home": str(target_home),
        "plugin_dir": str(plugin_dir),
        "installed_paths": [str(plugin_dir / name) for name in contents],
    }


def doctor(home: str | Path | None = None) -> dict[str, Any]:
    """Check copied files and runtime paths without claiming Hermes loaded them."""
    target_home = resolve_home(home)
    plugin_dir = target_home / "plugins" / "pulldown"
    assets = [
        {"name": name, "path": str(plugin_dir / name), "exists": _is_regular_file(plugin_dir / name)}
        for name in (*MANAGED_FILES, MARKER)
    ]
    issues = []
    try:
        _assert_safe_path(plugin_dir)
        if not _exists(plugin_dir):
            issues.append("The Pulldown Hermes plugin is not installed in this profile.")
        else:
            _assert_unchanged(plugin_dir, _read_marker(plugin_dir))
            issues.extend(f"Missing Hermes plugin file: {asset['path']}" for asset in assets if not asset["exists"])
            issues.extend(_runtime_issues(plugin_dir / "runtime.json", target_home))
    except (OSError, ValueError, TypeError) as error:
        issues.append(str(error))
    return {
        "home": str(target_home),
        "plugin_dir": str(plugin_dir),
        "installed": all(asset["exists"] for asset in assets) and not issues,
        "assets": assets,
        "issues": issues,
    }


def uninstall(home: str | Path | None = None) -> dict[str, Any]:
    """Remove unchanged managed files, preserving user additions and host settings."""
    target_home = resolve_home(home)
    plugin_dir = target_home / "plugins" / "pulldown"
    result: dict[str, Any] = {"home": str(target_home), "plugin_dir": str(plugin_dir), "removed_paths": []}
    _assert_safe_path(plugin_dir)
    if not _exists(plugin_dir):
        return result
    _assert_unchanged(plugin_dir, _read_marker(plugin_dir))
    for name in MANAGED_FILES:
        target = plugin_dir / name
        _assert_safe_path(target)
        if _exists(target):
            target.unlink()
            result["removed_paths"].append(str(target))
    # Keep the marker when user files or generated bytecode remain, allowing a
    # later reinstall to reuse the directory without treating it as unowned.
    if all(path.name == MARKER for path in plugin_dir.iterdir()):
        (plugin_dir / MARKER).unlink()
        result["removed_paths"].append(str(plugin_dir / MARKER))
        plugin_dir.rmdir()
    return result
