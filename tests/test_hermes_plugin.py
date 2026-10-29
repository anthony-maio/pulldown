from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from pulldown import hermes_plugin as plugin


class Context:
    def __init__(self, config=None):
        self.tools = {}
        self.config = config or {}

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs

    def get_config(self, key, default):
        return self.config.get(key, default)


def test_copied_plugin_loads_without_pulldown_or_hermes_imports(tmp_path):
    copied = tmp_path / "__init__.py"
    copied.write_bytes(Path(plugin.__file__).read_bytes())
    spec = importlib.util.spec_from_file_location("standalone_pulldown", copied)
    standalone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone)
    context = Context()
    standalone.register(context)
    tool = context.tools["pulldown_fetch"]
    assert tool["toolset"] == "pulldown"
    assert tool["schema"]["parameters"]["additionalProperties"] is False
    assert "untrusted" in tool["schema"]["description"]
    assert json.loads(tool["handler"]({"url": "file:///etc/passwd"}, task_id="test"))["code"] == "invalid_arguments"


@pytest.mark.parametrize("arguments", [
    None, [], {}, {"url": "https://example.com", "allow_private": True},
    {"url": "https://user:password@example.com"}, {"url": "https://example.com:99999"},
    {"url": "https://example.com\n"}, {"url": "https://example.com\\@localhost"},
    {"url": "ftp://example.com"}, {"url": "https://[bad"},
    {"url": "https://example.com/" + "a" * 4096},
    {"url": "https://example.com", "render": "false"},
    {"url": "https://example.com", "detail": "raw"},
    {"url": "https://example.com", "max_chars": True},
    {"url": "https://example.com", "max_chars": 40001},
    {"url": "https://example.com", "max_chars": 999},
])
def test_invalid_arguments_never_start_a_worker(monkeypatch, arguments):
    monkeypatch.setattr(plugin, "_runtime", lambda: pytest.fail("Worker must not be started"))
    result = json.loads(plugin._handle(Context(), arguments))
    assert result["ok"] is False
    assert result["code"] == "invalid_arguments"


def test_dispatch_uses_fixed_worker_and_strips_ambient_secrets(monkeypatch, tmp_path):
    runtime = {"python": sys.executable, "cache_dir": str(tmp_path / "cache")}
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-pass")
    monkeypatch.setenv("HTTP_PROXY", "http://secret-proxy")
    monkeypatch.setenv("HTTPS_PROXY", "http://secret-proxy")
    monkeypatch.setenv("PYTHONPATH", "untrusted-code")
    monkeypatch.setenv("PULLDOWN_ALLOW_PRIVATE", "1")
    monkeypatch.setattr(plugin, "_runtime", lambda: runtime)
    calls = []

    def capture(argv, payload, env, timeout):
        calls.append((argv, json.loads(payload), env, timeout))
        return 0, b'{"ok":true,"content":"Markdown"}'

    monkeypatch.setattr(plugin, "_capture", capture)
    context = Context()
    plugin.register(context)
    result = json.loads(context.tools["pulldown_fetch"]["handler"]({"url": "https://example.com"}))
    assert result == {"ok": True, "content": "Markdown"}
    argv, request, env, timeout = calls[0]
    assert argv == [sys.executable, "-I", "-m", "pulldown.hermes_worker"]
    assert request == {"url": "https://example.com", "detail": "readable", "render": False,
                       "max_chars": 20000, "cache_dir": runtime["cache_dir"], "timeout_seconds": 60}
    assert timeout == 60
    assert not ({"OPENAI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "PYTHONPATH", "PULLDOWN_ALLOW_PRIVATE"} & env.keys())


@pytest.mark.parametrize("returncode,output,code", [
    (1, b"secret failure", "runtime_failed"),
    (0, b"not JSON secret", "invalid_output"),
    (0, b"[]", "invalid_output"),
    (0, b'{"content":"missing ok"}', "invalid_output"),
    (0, json.dumps({"ok": True, "content": "x" * 1001}).encode(), "invalid_output"),
])
def test_worker_failure_is_bounded_and_does_not_echo_stderr(monkeypatch, tmp_path, returncode, output, code):
    monkeypatch.setattr(plugin, "_runtime", lambda: {"python": sys.executable, "cache_dir": str(tmp_path)})
    monkeypatch.setattr(plugin, "_capture", lambda *args: (returncode, output))
    result = json.loads(plugin._handle(Context(), {"url": "https://example.com", "max_chars": 1000}))
    assert result["code"] == code
    assert "secret" not in result["error"]


def test_missing_runtime_returns_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "__file__", str(tmp_path / "__init__.py"))
    result = json.loads(plugin._handle(Context(), {"url": "https://example.com"}))
    assert result["code"] == "runtime_unavailable"
    assert "pulldown hermes install" in result["error"]


@pytest.mark.parametrize("timeout", [True, 0, 121, "60"])
def test_invalid_host_timeout_fails_before_start(monkeypatch, timeout):
    monkeypatch.setattr(plugin, "_runtime", lambda: pytest.fail("Invalid timeout must not start a worker"))
    assert json.loads(plugin._handle(Context({"timeout_seconds": timeout}), {"url": "https://example.com"}))["ok"] is False


def test_capture_reads_json_after_process_exit():
    code, output = plugin._capture(
        [sys.executable, "-I", "-c", "import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*100000)"],
        b"{}", dict(os.environ), 5,
    )
    assert code == 0
    assert output == b"x" * 100000


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_capture_stops_child_when_output_overflows(monkeypatch, stream):
    monkeypatch.setattr(plugin, "_MAX_CAPTURE_BYTES", 1024)
    monkeypatch.setattr(plugin, "_MAX_STDERR_BYTES", 1024)
    with pytest.raises(plugin.PluginError, match="capture limit") as error:
        plugin._capture([sys.executable, "-I", "-c", f"import sys,time;sys.{stream}.buffer.write(b'x'*100000);time.sleep(10)"], b"{}", dict(os.environ), 5)
    assert error.value.code == "output_too_large"


def test_capture_timeout_stops_worker():
    with pytest.raises(plugin.PluginError) as error:
        plugin._capture([sys.executable, "-I", "-c", "import time;time.sleep(10)"], b"{}", dict(os.environ), 0.1)
    assert error.value.code == "fetch_timeout"
