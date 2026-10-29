from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from click.testing import CliRunner
import pytest

from pulldown import hermes_install as installer
from pulldown.cli import main


@pytest.fixture
def assets(tmp_path, monkeypatch):
    root = tmp_path / "package" / "hermes_plugin"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_bytes(b"name: pulldown\nversion: 0.1.0\n")
    (root / "__init__.py").write_bytes(b"def register(ctx):\n    pass\n")
    monkeypatch.setattr(installer, "ASSET_ROOT", root)
    return root


def _plugin(home):
    return home / "plugins" / "pulldown"


def _symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"Creating symlinks is unavailable: {error}")


def _replace_runtime(home, **updates):
    plugin = _plugin(home)
    runtime = json.loads((plugin / "runtime.json").read_bytes())
    runtime.update(updates)
    content = json.dumps(runtime).encode("utf-8")
    (plugin / "runtime.json").write_bytes(content)
    marker = json.loads((plugin / installer.MARKER).read_bytes())
    marker["files"]["runtime.json"] = hashlib.sha256(content).hexdigest()
    (plugin / installer.MARKER).write_text(json.dumps(marker), encoding="utf-8")


def test_install_keeps_config_and_records_current_environment(tmp_path, assets):
    home = tmp_path / "profile"
    home.mkdir()
    config = home / "config.yaml"
    original = b"plugins:\r\n  enabled: [cartograph]\r\n# Keep this comment.\r\n"
    config.write_bytes(original)

    result = installer.install(home)

    assert result["plugin_dir"] == str(_plugin(home))
    assert config.read_bytes() == original
    for name in ("__init__.py", "plugin.yaml"):
        assert (_plugin(home) / name).read_bytes() == (assets / name).read_bytes()
    runtime = json.loads((_plugin(home) / "runtime.json").read_bytes())
    assert runtime == {"python": os.path.abspath(sys.executable), "cache_dir": str(home / "cache" / "pulldown")}
    assert installer.doctor(home)["installed"]
    assert installer.doctor(home)["issues"] == []


def test_home_override_precedes_environment_and_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "environment"))
    assert installer.resolve_home() == tmp_path / "environment"
    assert installer.resolve_home(tmp_path / "explicit") == tmp_path / "explicit"
    monkeypatch.delenv("HERMES_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    assert installer.resolve_home() == tmp_path / "user" / ".hermes"


def test_install_preserves_virtualenv_python_symlink_path(tmp_path, assets, monkeypatch):
    executable = tmp_path / "virtualenv" / ("python.exe" if os.name == "nt" else "python")
    executable.parent.mkdir()
    _symlink(executable, Path(sys.executable).resolve())
    monkeypatch.setattr(sys, "executable", str(executable))
    home = tmp_path / "profile"

    installer.install(home)

    runtime = json.loads((_plugin(home) / "runtime.json").read_bytes())
    assert runtime["python"] == str(executable)
    assert runtime["python"] != str(executable.resolve())
    assert installer.doctor(home)["installed"]


def test_reinstall_repairs_missing_files_and_leaves_user_additions(tmp_path, assets):
    home = tmp_path / "profile"
    installer.install(home)
    plugin = _plugin(home)
    (plugin / "__init__.py").unlink()
    (plugin / "notes.txt").write_text("Keep these notes", encoding="utf-8")
    assert not installer.doctor(home)["installed"]

    installer.install(home)

    assert (plugin / "__init__.py").read_bytes() == (assets / "__init__.py").read_bytes()
    assert (plugin / "notes.txt").read_text() == "Keep these notes"
    assert installer.doctor(home)["installed"]


@pytest.mark.parametrize("action", [installer.install, installer.uninstall])
@pytest.mark.parametrize("filename", installer.MANAGED_FILES)
def test_modified_managed_files_block_all_mutation(tmp_path, assets, action, filename):
    home = tmp_path / "profile"
    installer.install(home)
    plugin = _plugin(home)
    (plugin / filename).write_bytes(b"user modification\n")
    before = {path.name: path.read_bytes() for path in plugin.iterdir()}

    with pytest.raises(installer.InstallError, match="Preserving a modified"):
        action(home)

    assert {path.name: path.read_bytes() for path in plugin.iterdir()} == before
    assert not installer.doctor(home)["installed"]


@pytest.mark.parametrize("marker", [None, {}, {"schema_version": 1, "owner": "another-plugin", "files": {}}])
def test_unowned_directory_is_preserved(tmp_path, assets, marker):
    home = tmp_path / "profile"
    plugin = _plugin(home)
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_bytes(b"existing plugin")
    if marker is not None:
        (plugin / installer.MARKER).write_text(json.dumps(marker))

    for action in (installer.install, installer.uninstall):
        with pytest.raises(installer.InstallError, match="unowned"):
            action(home)

    assert (plugin / "__init__.py").read_bytes() == b"existing plugin"
    assert not installer.doctor(home)["installed"]


@pytest.mark.parametrize("location", ["home", "plugins", "cache", "managed", "marker"])
def test_install_and_doctor_reject_symlinks(tmp_path, assets, location):
    home = tmp_path / "profile"
    real = tmp_path / "redirected"
    if location in ("managed", "marker"):
        installer.install(home)
        name = "__init__.py" if location == "managed" else installer.MARKER
        link = _plugin(home) / name
        real.write_bytes(link.read_bytes())
        link.unlink()
        _symlink(link, real)
    else:
        real.mkdir()
        link = home if location == "home" else home / location
        link.parent.mkdir(parents=True, exist_ok=True)
        _symlink(link, real, directory=True)

    with pytest.raises(installer.InstallError, match="symlink or junction"):
        installer.install(home)

    assert not installer.doctor(home)["installed"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_install_refuses_windows_junction_ancestor(tmp_path, assets):
    home = tmp_path / "profile"
    real = tmp_path / "redirected"
    real.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(home), str(real)], check=True, capture_output=True)
    try:
        with pytest.raises(installer.InstallError, match="symlink or junction"):
            installer.install(home)
        assert list(real.iterdir()) == []
    finally:
        # rmdir removes the junction itself, never its target directory.
        os.rmdir(home)


def test_reinstall_uses_atomic_replacement_for_hardlinks(tmp_path, assets):
    home = tmp_path / "profile"
    installer.install(home)
    plugin = _plugin(home)
    previous = tmp_path / "hard-linked-plugin.py"
    os.link(plugin / "__init__.py", previous)
    original = previous.read_bytes()
    (assets / "__init__.py").write_bytes(b"def register(ctx):\n    return None\n")

    installer.install(home)

    assert previous.read_bytes() == original
    assert (plugin / "__init__.py").read_bytes() == (assets / "__init__.py").read_bytes()
    assert installer.doctor(home)["installed"]
    assert not list(plugin.glob("*.tmp"))


@pytest.mark.parametrize("field,value", [("python", "python"), ("python", "/missing/python"), ("cache_dir", "/other/cache")])
def test_doctor_checks_runtime_paths(tmp_path, assets, field, value):
    home = tmp_path / "profile"
    installer.install(home)
    _replace_runtime(home, **{field: value})

    report = installer.doctor(home)

    assert not report["installed"]
    assert any(field in issue for issue in report["issues"])


def test_install_refuses_cache_file_before_creating_plugin(tmp_path, assets):
    home = tmp_path / "profile"
    cache = home / "cache" / "pulldown"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"user data")

    with pytest.raises(installer.InstallError, match="must be a directory"):
        installer.install(home)

    assert cache.read_bytes() == b"user data"
    assert not _plugin(home).exists()


def test_missing_packaged_asset_does_not_create_host_files(tmp_path, assets):
    (assets / "plugin.yaml").unlink()
    home = tmp_path / "profile"

    with pytest.raises(installer.InstallError, match="Missing packaged Hermes asset"):
        installer.install(home)

    assert not home.exists()


def test_uninstall_preserves_config_cache_and_additions_then_allows_reinstall(tmp_path, assets):
    home = tmp_path / "profile"
    installer.install(home)
    plugin = _plugin(home)
    (home / "config.yaml").write_bytes(b"plugins: {enabled: [pulldown, cartograph]}\n")
    cache = home / "cache" / "pulldown"
    cache.mkdir(parents=True)
    (cache / "page.json").write_bytes(b"cached page")
    (plugin / "notes.txt").write_bytes(b"user notes")

    installer.uninstall(home)

    assert (home / "config.yaml").read_bytes() == b"plugins: {enabled: [pulldown, cartograph]}\n"
    assert (cache / "page.json").read_bytes() == b"cached page"
    assert (plugin / "notes.txt").read_bytes() == b"user notes"
    assert (plugin / installer.MARKER).exists()
    assert all(not (plugin / name).exists() for name in installer.MANAGED_FILES)
    installer.install(home)
    assert installer.doctor(home)["installed"]


def test_uninstall_removes_only_managed_directory_and_is_repeatable(tmp_path, assets):
    home = tmp_path / "profile"
    installer.install(home)
    result = installer.uninstall(home)
    assert len(result["removed_paths"]) == 4
    assert not _plugin(home).exists()
    assert home.exists()
    assert installer.uninstall(home)["removed_paths"] == []


def test_cli_json_reports_missing_valid_and_removed_installations(tmp_path, assets):
    home = tmp_path / "profile"
    runner = CliRunner()
    missing = runner.invoke(main, ["hermes", "doctor", "--home", str(home), "--json"])
    assert missing.exit_code == 1
    assert json.loads(missing.output)["installed"] is False
    installed = runner.invoke(main, ["hermes", "install", "--home", str(home), "--json-output"])
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.output)["plugin_dir"] == str(_plugin(home))
    valid = runner.invoke(main, ["hermes", "doctor", "--home", str(home), "-j"])
    assert valid.exit_code == 0, valid.output
    assert json.loads(valid.output)["installed"] is True
    removed = runner.invoke(main, ["hermes", "uninstall", "--home", str(home), "--json"])
    assert removed.exit_code == 0, removed.output
    assert len(json.loads(removed.output)["removed_paths"]) == 4
