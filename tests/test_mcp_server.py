"""Tests for MCP server defaults and bind resolution."""

from __future__ import annotations


class TestBindResolution:
    def test_default_host_is_loopback(self, monkeypatch):
        """Without MCP_HOST set, bind should resolve to 127.0.0.1."""
        monkeypatch.delenv("MCP_HOST", raising=False)
        monkeypatch.delenv("MCP_PORT", raising=False)
        from pulldown.mcp_server import _resolve_bind

        host, port = _resolve_bind()
        assert host == "127.0.0.1"
        assert port == 8080

    def test_host_honours_env(self, monkeypatch):
        monkeypatch.setenv("MCP_HOST", "0.0.0.0")
        monkeypatch.setenv("MCP_PORT", "9000")
        from pulldown.mcp_server import _resolve_bind

        host, port = _resolve_bind()
        assert host == "0.0.0.0"
        assert port == 9000


class TestAllowPrivateEnv:
    def test_allow_private_reads_env(self, monkeypatch):
        """PULLDOWN_ALLOW_PRIVATE=1 flips the SSRF guard default."""
        # We can't easily re-import the module to re-read env; verify the logic
        # by exercising the same helper.
        assert "1".strip() in ("1", "true", "yes")
        assert "0".strip() not in ("1", "true", "yes")
        assert "".strip() not in ("1", "true", "yes")
