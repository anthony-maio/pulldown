# Pulldown for Hermes

Read a public web page as Markdown with a source URL, routing metadata, and
explicit truncation. The native `pulldown_fetch` tool uses HTTP by default and
can render JavaScript when requested. Requires Hermes 0.22.2 or newer and an
external Python 3.10+ environment containing Pulldown.

## Install

Create a persistent runtime environment in the same OS as Hermes:

```sh
python3 -m venv ~/.local/share/pulldown-hermes/venv
~/.local/share/pulldown-hermes/venv/bin/python -m pip install 'pulldown==0.5.0'
~/.local/share/pulldown-hermes/venv/bin/pulldown hermes install
~/.local/share/pulldown-hermes/venv/bin/pulldown hermes doctor
hermes plugins enable pulldown
```

For optional browser rendering, explicitly install the render extra and
Chromium through Playwright's official installer:

```sh
~/.local/share/pulldown-hermes/venv/bin/python -m pip install 'pulldown[render]==0.5.0' 'playwright==1.64.1'
~/.local/share/pulldown-hermes/venv/bin/python -m playwright install chromium
```

Playwright's pinned release selects its corresponding Chromium build. This is
an explicit setup download from the official Playwright browser distribution;
the plugin never downloads a browser during a tool call. No browser binary is
bundled with the plugin source.

Alternatively, let Hermes manage the plugin source. Copy the full 40-character
commit from the Pulldown 0.5.0 GitHub release and use it for `FULL_COMMIT_SHA`:

```sh
hermes plugins install anthony-maio/pulldown/src/pulldown/hermes_plugin --ref FULL_COMMIT_SHA --no-enable
hermes config set plugins.entries.pulldown.settings.python_path /absolute/path/to/pulldown/venv/bin/python
hermes plugins enable pulldown
```

Install the external runtime first, then choose either the product installer
or Hermes for the plugin directory. Do not run the product installer over a
Hermes-managed clone. `python_path` takes precedence over the product
installer's `runtime.json`. Preserve the virtualenv's executable path rather
than resolving its symlink to system Python. On Windows, use the `Scripts`
directory; for Hermes in WSL, use a Linux environment and Linux paths.

Start a new chat after enabling the plugin. Catalog submission and maintainer
acceptance are separate; the pinned Git source command works before acceptance.

## Use

```text
Use pulldown_fetch to read https://docs.python.org/3/library/asyncio.html.
Explain when asyncio is useful and cite the page.
```

`detail` accepts `minimal`, `readable`, `structured`, or `full` and defaults to
`readable`. `render` defaults to false. `max_chars` defaults to 20000 and accepts
1000 through 40000. `structured` summarizes tables; use `full` when every cell
matters. Check `ok` and `truncated` before treating a result as complete. The
source URL identifies the requested page, not a verified final redirect URL.

The active profile's `plugins.entries.pulldown.settings` accepts `python_path`
and `timeout_seconds` (default 60, range 5 through 120).

## Behavior and access

- Starts a fixed Python worker with JSON input and bounded output, without a
  shell. It can start Chromium only when `render=true`; workers and browser
  children are terminated on timeout. No persistent background service runs.
- Sends HTTP requests to the requested public site. Rendering also fetches
  that site's subresources. It does not offer login cookies, custom headers,
  proxies, private-address overrides, or disabled TLS verification.
- Checks destinations and HTTP redirects for private addresses. Rendering
  rejects redirects and blocks workers and WebSockets. DNS checks are not a
  replacement for network isolation against rebinding or proxy resolution.
- Rejects page content above 2 MiB after download and bounds returned text.
  This is not a streaming network-transfer or process-memory limit.
- Reads its external Python installation and Chromium installation. Caches
  fetched public pages in the active profile's plugin data directory for a
  configured runtime, or `cache/pulldown` for the product installer. Rendered
  fetches bypass that cache. Uninstall preserves caches.
- Returned page text enters the Hermes conversation and can reach its model
  provider. Treat page text as untrusted source material, not instructions.
- Reads no credential stores, strips ambient credentials and proxies from the
  worker environment, and sends no telemetry.
- Uses public `ctx.register_tool`, `ctx.get_config`, and `ctx.state.data_dir`.
  It does not override Hermes core functions or built-in tools.

The external PyPI runtime and optional Playwright dependencies are installed
explicitly by the user. No packages install during a tool call. The plugin does
not update itself; Hermes-managed updates use pinned source revisions.

## Verification

The repository includes contract tests, an installed-wheel check, and
`scripts/hermes-smoke.py` for actual Hermes discovery and dispatch. The release
checks include public Python documentation, HTTP versus Chromium output on the
fixture board, private-address rejection, and normal Hermes chat.

```sh
hermes plugins validate /path/to/pulldown/src/pulldown/hermes_plugin --install-deps
```

See the [full setup guide](https://github.com/anthony-maio/pulldown/blob/main/docs/hermes.md)
and [example gallery](https://pulldown.making-minds.ai/examples/).
