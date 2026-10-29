"""Exercise the wheel a user installs, including a real Chromium fetch."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Thread
import venv


ROOT = Path(__file__).resolve().parent.parent
DETAILS = ("minimal", "readable", "structured", "full", "raw")
RENDERED_MARKER = "RENDERED-BOARD-READY"


class NavigationText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_nav = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "nav":
            self.in_nav = True

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_nav = False

    def handle_data(self, data):
        if self.in_nav and data.strip():
            self.text.append(data.strip())


@contextmanager
def fixture_server(directory):
    requests = {"redirect": 0, "target": 0}

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                requests["redirect"] += 1
                self.send_response(302)
                self.send_header("Location", "/redirect-target")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/redirect-target":
                requests["target"] += 1
                content = b"<html><body><p>Redirect target must not be fetched.</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                super().do_GET()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run(args, *, cwd, env, expected_code=0, timeout=120):
    result = subprocess.run(
        [str(arg) for arg in args], cwd=cwd, env=env, text=True,
        encoding="utf-8", errors="replace", capture_output=True, timeout=timeout,
    )
    if result.returncode != expected_code:
        raise RuntimeError(
            f"Command exited {result.returncode}, expected {expected_code}: {args}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def smoke(wheel, work, *, install_browser_deps, output_dir):
    consumer = work / "consumer"
    consumer.mkdir()
    environment = work / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    cli = scripts / ("pulldown.exe" if os.name == "nt" else "pulldown")
    env = os.environ.copy()
    for name in list(env):
        if name in ("PYTHONPATH", "PYTHONHOME") or name.startswith("PULLDOWN_"):
            env.pop(name, None)
    env.update({"PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    # The consumer has no editable install and cannot import the checkout's src tree.
    print("Installing the wheel into a fresh consumer environment...", flush=True)
    run([python, "-m", "pip", "install", "--no-input", f"{wheel}[render,mcp]"], cwd=consumer, env=env, timeout=300)
    identity = json.loads(run([
        python, "-I", "-c",
        "import asyncio,importlib.metadata,importlib.resources,json,pulldown,pulldown.mcp_server; "
        "assets=importlib.resources.files('pulldown'); "
        "assert assets.joinpath('py.typed').is_file(); "
        "assert assets.joinpath('hermes_plugin/plugin.yaml').is_file(); "
        "assert assets.joinpath('hermes_plugin/__init__.py').is_file(); "
        "json.loads(assets.joinpath('routing_model.json').read_text()); "
        "tools=asyncio.run(pulldown.mcp_server.mcp.list_tools()); "
        "assert {tool.name for tool in tools} == {'pulldown','pulldown_many','pulldown_crawl'}; "
        "print(json.dumps({'version':importlib.metadata.version('pulldown'),"
        "'runtime_version':pulldown.__version__,'path':pulldown.__file__,"
        "'playwright':importlib.metadata.version('playwright'),"
        "'html-to-markdown':importlib.metadata.version('html-to-markdown'),"
        "'mcp':importlib.metadata.version('mcp')}))",
    ], cwd=consumer, env=env))
    installed_path = Path(identity["path"]).resolve()
    assert installed_path.is_relative_to(environment.resolve()), "import must come from the installed wheel"
    assert identity["version"] == identity["runtime_version"], "wheel metadata and runtime version must agree"

    # Exercise the installed CLI without depending on a Hermes installation.
    hermes_home = work / "hermes-profile"
    hermes_home.mkdir()
    config = hermes_home / "config.yaml"
    config.write_text("model: preserve-existing-settings\n", encoding="utf-8")
    config_before = config.read_bytes()
    run([cli, "hermes", "install", "--home", hermes_home], cwd=consumer, env=env)
    doctor = json.loads(run([
        cli, "hermes", "doctor", "--home", hermes_home, "--json-output",
    ], cwd=consumer, env=env))
    assert doctor["installed"] and not doctor["issues"], doctor
    runtime = json.loads((hermes_home / "plugins/pulldown/runtime.json").read_text())
    assert Path(runtime["python"]) == python.absolute(), runtime
    assert config.read_bytes() == config_before, "installer changed Hermes configuration"
    run([cli, "hermes", "uninstall", "--home", hermes_home], cwd=consumer, env=env)
    assert config.read_bytes() == config_before, "uninstaller changed Hermes configuration"
    assert not (hermes_home / "plugins/pulldown/__init__.py").exists()

    browser_command = [python, "-m", "playwright", "install"]
    if install_browser_deps:
        browser_command.append("--with-deps")
    browser_command.append("chromium")
    print("Installing Chromium for the rendered example...", flush=True)
    run(browser_command, cwd=consumer, env=env, timeout=600)

    fixtures = ROOT / "website" / "examples"
    article = (fixtures / "field-notes.html").read_text(encoding="utf-8")
    navigation = NavigationText()
    navigation.feed(article)
    assert navigation.text, "the article fixture must include navigation"
    report = {
        "package": "pulldown", "version": identity["version"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "Local HTTP server serving website/examples from this checkout; installed wheel CLI",
        "fixtures": {"article": "field-notes.html", "board": "live-board.html"},
        "dependencies": {name: identity[name] for name in ("playwright", "html-to-markdown", "mcp")},
        "modes": {}, "render": {},
    }
    captures = {}
    with fixture_server(fixtures) as (base, requests):
        article_url = f"{base}/field-notes.html"
        board_url = f"{base}/live-board.html"

        def get(url, detail="readable", *, extra=(), private=True, expected_code=0):
            args = [cli, "get", url, "--detail", detail, "--json-output", "--meta", "--timeout", "20"]
            if private:
                args.append("--allow-private")
            return json.loads(run([*args, *extra], cwd=consumer, env=env, expected_code=expected_code))

        for extra in ((), ("--render",)):
            blocked = get(article_url, private=False, extra=extra, expected_code=1)
            assert not blocked["ok"] and "URL blocked" in blocked.get("error", ""), "private URLs must be blocked by default"

        for detail in DETAILS:
            result = get(article_url, detail)
            content = result["content"]
            assert result["ok"] and result["status_code"] == 200, result
            assert content.strip() and "Release field notes" in content, f"{detail} must retain useful article content"
            assert not content.startswith("ConversionResult("), f"{detail} must return content rather than a dependency object representation"
            routing = result.get("meta", {}).get("routing", {})
            assert routing.get("strategy_used"), f"{detail} must include routing metadata"
            if detail == "minimal":
                assert "](" not in content and "<p>" not in content, "minimal must return plain text"
                assert not re.search(r"^#{1,6}\s", content, re.MULTILINE), "minimal must omit Markdown headings"
            elif detail == "readable":
                assert re.search(r"\[[^\]]+\]\([^)]+\)", content), "readable must retain article links"
                assert "(https://pulldown.making-minds.ai/examples/live-board.html)" in content, "readable must retain the article's live release board link"
            elif detail == "structured":
                assert re.search(r"^#{1,6}\s+Release field notes", content, re.MULTILINE), "structured must retain heading hierarchy"
                assert re.search(r"Service\s*\|\s*Owner\s*\|\s*Status", content), "structured must retain table columns"
                for row in (
                    ("Documentation", "Alex", "Ready for review"),
                    ("Search", "Morgan", "Needs fixture check"),
                    ("Export", "Sam", "Ready for review"),
                ):
                    pattern = r"\s*\|\s*".join(re.escape(cell) for cell in row)
                    assert re.search(pattern, content), f"structured must retain every cell in the {row[0]} row"
            elif detail == "full":
                assert any(text in content for text in navigation.text), "full must include navigation"
            else:
                assert "<html" in content.lower() and "<nav" in content.lower(), "raw must retain HTML"
                assert content.replace("\r\n", "\n") == article.replace("\r\n", "\n"), "raw must preserve the response body"
            report["modes"][detail] = {"characters": len(content), "strategy": routing["strategy_used"]}
            captures[f"field-notes-{detail}.json"] = result

        # Reuse a cache directory to prove rendering does not return an HTTP-only shell.
        cache_args = ("--cache-dir", str(work / "cache"))
        http = get(board_url, extra=cache_args)
        rendered = get(board_url, extra=(*cache_args, "--render", "--wait", "100"))
        assert http["ok"] and rendered["ok"], (http, rendered)
        assert RENDERED_MARKER not in http["content"], "HTTP extraction must not execute page JavaScript"
        assert RENDERED_MARKER in rendered["content"], "rendering must capture the JavaScript-generated board"
        for service in ("Documentation", "Search", "Export"):
            assert service in rendered["content"], f"rendering must retain the {service} board card"
        assert not rendered["from_cache"], "rendering must not reuse an HTTP-only cache entry"
        report["render"] = {"http_characters": len(http["content"]), "rendered_characters": len(rendered["content"]), "marker_present": True}
        captures["live-board-http.json"] = http
        captures["live-board-rendered.json"] = rendered

        redirect = get(f"{base}/redirect", extra=("--render",), expected_code=1)
        assert not redirect["ok"], "rendering must reject HTTP redirects"
        assert "Rendering does not follow HTTP redirects" in redirect.get("error", ""), redirect
        assert requests["redirect"] >= 1, "the browser must receive the fixture's redirect response"
        assert requests["target"] == 0, "rendering must reject redirects before requesting their target"
        report["render"]["redirect_blocked_before_target"] = True

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, result in {**captures, "manifest.json": report}.items():
            (output_dir / name).write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    print("Installed wheel passed: five extraction modes, metadata, private-URL guard, Chromium rendering, and redirect rejection.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, help="Parent directory for isolated consumer files")
    parser.add_argument("--output-dir", type=Path, help="Keep CLI JSON captures and a version/source receipt after a successful run")
    parser.add_argument("--install-browser-deps", action="store_true", help="Install Chromium OS dependencies on CI Linux")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    assert wheel.suffix == ".whl", "pass a built wheel"
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    temp_parent = (args.work_dir or Path(tempfile.gettempdir())).resolve()
    # TemporaryDirectory owns only this fresh directory, never the checkout or caller's parent.
    with tempfile.TemporaryDirectory(prefix="pulldown-installed-", dir=args.work_dir) as temp:
        work = Path(temp).resolve()
        assert work.parent == temp_parent, "temporary consumer must remain under its requested parent"
        assert not work.is_relative_to(ROOT.resolve()), "consumer environment must be outside the checkout"
        smoke(wheel, work, install_browser_deps=args.install_browser_deps, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
