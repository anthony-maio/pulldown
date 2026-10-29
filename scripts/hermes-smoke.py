#!/usr/bin/env python3
"""Verify an installed Pulldown wheel through the real Hermes tool dispatcher.

Run in the same operating system as Hermes and the Pulldown environment. The
check uses a disposable profile, public pages, and an installed browser. It
tests native tool calls; it does not test a model's choice of tools.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ARTICLE = "https://pulldown.making-minds.ai/examples/field-notes.html"
BOARD = "https://pulldown.making-minds.ai/examples/live-board.html"
DOCUMENTATION = "https://docs.python.org/3/library/asyncio.html"
TOOL = "pulldown_fetch"


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(command: list[str], *, env: dict[str, str], cwd: Path,
        timeout: int = 120) -> str:
    completed = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Command exited {completed.returncode}: {command[0]}\n"
            f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}"
        )
    return completed.stdout


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes", default="hermes", help="Installed Hermes launcher")
    parser.add_argument("--hermes-source", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path,
                        help="Python executable in the environment with the installed wheel")
    parser.add_argument("--profile-parent", required=True, type=Path,
                        help="Existing Hermes profiles directory, to reuse its managed runtime")
    parser.add_argument("--wheel", type=Path, help="Record this wheel's SHA256 in the receipt")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_home", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def child_check(args: argparse.Namespace) -> dict:
    # Imports come from the installed host and its managed-runtime bootstrap.
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from model_tools import get_tool_definitions, handle_function_call
    from tools.registry import registry

    discover_plugins()
    manager = get_plugin_manager()
    plugins = manager.list_plugins()
    pulldown = next((item for item in plugins if item["name"] == "pulldown"), None)
    require(pulldown is not None, "Hermes did not discover Pulldown")
    require(pulldown["enabled"] and not pulldown["error"], str(pulldown))
    require(pulldown["source"] == "user", "Expected directory-plugin discovery")
    require(not any(
        item["enabled"] and item["source"] in {"user", "entrypoint", "project"}
        and item["name"] != "pulldown" for item in plugins
    ), "An unrelated external plugin was enabled")
    definitions = get_tool_definitions(
        enabled_toolsets=["pulldown"], quiet_mode=True, skip_tool_search_assembly=True,
    )
    exposed = {item["function"]["name"] for item in definitions}
    require(TOOL in exposed, "Pulldown is absent from the model's tool definitions")
    responses = {}

    def dispatch(label: str, arguments: dict) -> dict:
        raw = handle_function_call(
            TOOL, arguments, task_id="pulldown-smoke", session_id="pulldown-smoke",
            enabled_toolsets=["pulldown"], enabled_tools=[TOOL],
        )
        require(isinstance(raw, str), "Tool output must be a JSON string")
        result = json.loads(raw)
        require(isinstance(result, dict), "Expected structured Pulldown output")
        responses[label] = result
        args.output.with_suffix(".responses.json").write_text(
            json.dumps(responses, indent=2) + "\n", encoding="utf-8",
        )
        return result

    def successful(result: dict, source: str) -> None:
        require(result.get("ok") is True and not result.get("error"),
                f"Fetch failed for {source}: {result.get('error', result)}")
        require(result.get("status_code") == 200, f"Unexpected status for {source}")
        require(isinstance(result.get("content"), str) and result["content"].strip(),
                f"Empty content for {source}")
        require(result.get("url") == source, f"Source URL missing or changed for {source}")

    article = dispatch("article", {"url": ARTICLE, "detail": "structured"})
    successful(article, ARTICLE)
    for marker in ("Release field notes", "Start with the installed artifact",
                   "Service readiness", "Keep the evidence with the result"):
        require(marker in article["content"], f"Article heading missing: {marker}")
    require(article.get("title") == "Release field notes", "Article title missing")
    routing = article.get("routing", {})
    require(routing.get("page_type") and routing.get("strategy_used"),
            "Routing diagnostics are missing")

    truncated = dispatch("truncated", {
        "url": ARTICLE, "detail": "structured", "max_chars": 1000,
    })
    successful(truncated, ARTICLE)
    require(len(truncated["content"]) <= 1000 and truncated.get("truncated") is True,
            "Requested content bound or truncation flag was ignored")
    require(truncated.get("content_chars", 0) > len(truncated["content"]),
            "Original content size is missing from truncated output")

    http_board = dispatch("board_http", {"url": BOARD, "detail": "readable"})
    successful(http_board, BOARD)
    require("RENDERED-BOARD-READY" not in http_board["content"],
            "The HTTP fixture unexpectedly contains the JavaScript marker")
    rendered = dispatch("board_rendered", {
        "url": BOARD, "detail": "readable", "render": True,
    })
    successful(rendered, BOARD)
    require("RENDERED-BOARD-READY" in rendered["content"], "Browser did not render the board")
    for marker in ("Documentation", "Search", "Export"):
        require(marker in rendered["content"], f"Rendered card missing: {marker}")
    require(rendered.get("rendered") is True and http_board.get("rendered") is False,
            "HTTP and browser provenance are not distinguished")
    require(rendered.get("from_cache") is False,
            "Rendering unexpectedly reused the HTTP cache")

    documentation = dispatch("python_docs", {"url": DOCUMENTATION, "detail": "readable"})
    successful(documentation, DOCUMENTATION)
    require("asyncio" in documentation["content"] and "async/await" in documentation["content"],
            "Independent documentation did not preserve the subject and syntax")
    blocked = dispatch("private_address", {"url": "http://127.0.0.1/"})
    require(blocked.get("ok") is False and blocked.get("code") == "url_blocked",
            "Private address was not rejected")

    cache = args._home / "cache" / "pulldown"
    cache_files = sorted(path for path in cache.rglob("*") if path.is_file())
    require(bool(cache_files), "HTTP fetches did not use the profile cache")
    manager.unload("pulldown")
    require(registry.get_entry(TOOL) is None, "Unloaded plugin retained its tool")
    return {
        "status": "passed",
        "checks": {
            "native_discovery": True, "model_tool_exposure": True,
            "native_fetch_dispatch": True, "article_headings": True,
            "source_urls": True, "routing_metadata": True,
            "content_limit": True, "explicit_truncation": True,
            "http_without_javascript": True, "rendered_board": True,
            "render_provenance": True, "independent_python_documentation": True,
            "private_address_rejected": True, "profile_cache": True,
            "unload_removes_tool": True, "no_unrelated_external_plugins": True,
        },
        "pages": {
            label: {"url": result.get("url"), "status_code": result.get("status_code"),
                    "title": result.get("title"), "returned_chars": len(result.get("content", "")),
                    "truncated": result.get("truncated"), "rendered": result.get("rendered"),
                    "content_sha256": hashlib.sha256(result.get("content", "").encode()).hexdigest()}
            for label, result in responses.items() if result.get("ok") is True
        },
        "cache_file_count": len(cache_files),
        "hermes": {
            "commit": subprocess.check_output(
                ["git", "-C", str(args.hermes_source), "rev-parse", "HEAD"],
                text=True, encoding="utf-8", stderr=subprocess.PIPE,
            ).strip(),
            "python": sys.version.split()[0],
        },
        "limits": "Native loader and tool dispatch on specified public pages; no model-driven tool-selection claim.",
    }


def parent_check(args: argparse.Namespace) -> dict:
    for key in ("hermes_source", "profile_parent", "output"):
        setattr(args, key, getattr(args, key).resolve())
    # Keep a venv's executable path. Resolving its symlink can select system Python.
    args.python = Path(os.path.abspath(args.python))
    require(args.python.is_file(), "Installed Pulldown Python executable is missing")
    require(args.profile_parent.is_dir(), "The Hermes profiles directory does not exist")
    config = args.profile_parent.parent / "config.yaml"
    active_config_before = config.read_bytes() if config.exists() else None
    with tempfile.TemporaryDirectory(prefix="pulldown-hermes-smoke-", dir=args.profile_parent) as temporary:
        home = Path(temporary).resolve()
        destination = home / "plugins" / "pulldown"
        cache = home / "cache" / "pulldown"
        (home / "config.yaml").write_text(
            "plugins:\n  enabled: [pulldown]\n  disabled: []\n  isolation: in_process\n"
            "security:\n  allow_lazy_installs: false\n", encoding="utf-8",
        )
        permitted = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TMP", "TEMP",
                     "TMPDIR", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
                     "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"}
        env = {key: value for key, value in os.environ.items() if key.upper() in permitted}
        env.update(HERMES_HOME=str(home), HERMES_ENABLE_PROJECT_PLUGINS="0",
                   HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1")
        package = json.loads(run([
            str(args.python), "-I", "-c",
            "import json,sys,pulldown; from pathlib import Path; "
            "print(json.dumps({'version':pulldown.__version__,"
            "'root':str(Path(pulldown.__file__).parent),'python':sys.executable}))",
        ], env=env, cwd=home))
        package_root = Path(package["root"])
        require("site-packages" in package_root.parts, "Acceptance must use an installed package")
        plugin_source = package_root / "hermes_plugin"
        worker_hash = digest(package_root / "hermes_worker.py")
        cli = [str(args.python), "-I", "-c", "from pulldown.cli import main; main()", "hermes"]
        config_before = (home / "config.yaml").read_bytes()
        install_log = run([*cli, "install", "--home", str(home)], env=env, cwd=home)
        require((home / "config.yaml").read_bytes() == config_before,
                "Installer modified Hermes configuration")
        for filename in ("plugin.yaml", "__init__.py"):
            require((destination / filename).read_bytes() == (plugin_source / filename).read_bytes(),
                    f"Installed plugin differs from packaged asset: {filename}")
        installed_runtime = json.loads((destination / "runtime.json").read_text(encoding="utf-8"))
        require(Path(installed_runtime["python"]) == args.python,
                "Installer recorded a different Python interpreter")
        require(Path(installed_runtime["cache_dir"]).resolve() == cache,
                "Installer cache escaped the isolated profile")
        doctor_result = json.loads(run(
            [*cli, "doctor", "--home", str(home), "--json"], env=env, cwd=home,
        ))
        require(doctor_result["installed"] and not doctor_result.get("issues"),
                f"Pulldown doctor reported problems: {doctor_result}")
        runtime = json.loads(run([args.hermes, "--print-runtime-command"], env=env, cwd=home))
        require(runtime[1:3] == ["-I", "-c"], "Unrecognized Hermes runtime command")
        constants = {node.value for node in ast.walk(ast.parse(runtime[3]))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        require(str(args.hermes_source) in constants, "Hermes source does not match the launcher")
        doctor = run([args.hermes, "plugins", "doctor", str(destination), "--ci"], env=env, cwd=home)
        bootstrap = (
            "import os,sys,runpy; "
            "os.environ.pop('PYTHONHOME',None); os.environ.pop('PYTHONPATH',None); "
            "os.environ.pop('VIRTUAL_ENV',None); sys.path.insert(0,sys.argv.pop(1)); "
            "import hermes_bootstrap; runpy.run_path(sys.argv.pop(1),run_name='__main__')"
        )
        run([
            runtime[0], "-I", "-c", bootstrap, str(args.hermes_source), str(Path(__file__).resolve()),
            "--_child", "--_home", str(home), "--hermes-source", str(args.hermes_source),
            "--python", str(args.python), "--profile-parent", str(args.profile_parent),
            "--output", str(args.output),
        ], env=env, cwd=home, timeout=420)
        receipt = json.loads(args.output.read_text(encoding="utf-8"))
        require((home / "config.yaml").read_bytes() == config_before,
                "The acceptance run changed its Hermes configuration")
        require((config.read_bytes() if config.exists() else None) == active_config_before,
                "The active Hermes configuration changed during acceptance testing")
        require(digest(package_root / "hermes_worker.py") == worker_hash,
                "Installed worker changed during acceptance testing")
        receipt["checks"].update(
            plugin_doctor=True, cli_installer=True, installed_assets_match=True,
            runtime_paths=True, installer_preserves_config=True, pulldown_doctor=True,
            active_configuration_unchanged=True,
        )
        receipt["doctor"] = doctor.strip()
        receipt["install_log"] = install_log.strip()
        receipt["package"] = {"version": package["version"], "worker_sha256": worker_hash,
                              "plugin_sha256": digest(destination / "__init__.py")}
        if args.wheel is not None:
            receipt["package"]["wheel_sha256"] = digest(args.wheel)
        return receipt


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        receipt = child_check(args) if args._child else parent_check(args)
    except Exception as error:
        receipt = {"status": "failed", "error": str(error), "type": type(error).__name__}
    receipt["duration_seconds"] = round(time.monotonic() - started, 3)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(args.output),
                      **({"error": receipt["error"]} if "error" in receipt else {})}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
