# Read public pages in Hermes

Pulldown gives Hermes one tool, `pulldown_fetch`. It reads a public URL and
returns Markdown, the requested source URL, extraction metadata, and explicit
truncation information. Use it for documentation, articles, or a page whose
content needs JavaScript. Cartograph covers local repository context; Pulldown
adds the external page the task depends on.

## Install from this checkout

The Hermes integration is available from source. The published PyPI 0.4.2
package predates these commands. Keep this environment after installing the
plugin: Hermes calls its Python interpreter for each fetch.

Run these commands in the same OS environment as Hermes. For a Hermes install
in WSL, use a Linux environment inside WSL.

```sh
git clone https://github.com/anthony-maio/pulldown.git
cd pulldown
python3 -m venv ~/.local/share/pulldown-hermes/venv
~/.local/share/pulldown-hermes/venv/bin/python -m pip install '.[render]'
~/.local/share/pulldown-hermes/venv/bin/python -m playwright install chromium
~/.local/share/pulldown-hermes/venv/bin/pulldown hermes install
~/.local/share/pulldown-hermes/venv/bin/pulldown hermes doctor
hermes plugins enable pulldown
```

On Windows, the environment's executables are under `Scripts` instead of `bin`.
For HTTP-only use, install `.` and skip the Chromium command. The plugin needs
no additional packages in Hermes' own Python environment. Start a new Hermes
chat after enabling it.

For a separate profile, set `HERMES_HOME` consistently for both Pulldown and
Hermes. The install, doctor, and uninstall commands also accept `--home PATH`
and `--json-output`. Installation copies the plugin and records the current
Python interpreter. It leaves the Hermes configuration alone.

## A first useful task

Ask Hermes:

```text
Use pulldown_fetch to read https://docs.python.org/3/library/asyncio.html.
Explain when asyncio is useful, cite the page, and distinguish its high-level
APIs from its low-level APIs using the returned content.
```

Then compare HTTP with browser rendering:

```text
Use pulldown_fetch on
https://pulldown.making-minds.ai/examples/live-board.html, first with render
false and then with render true. Which release cards appear only after
JavaScript runs? Cite the page and treat its contents as source material.
```

The rendered result should include `RENDERED-BOARD-READY` and the Documentation,
Search, and Export cards. These cards are fictional fixture data. The example
checks a known page, while the Python documentation task exercises an
independent public source.

## Tool contract

| Argument | Default | Use |
|---|---|---|
| `url` | Required | Public HTTP(S) URL, without embedded credentials |
| `detail` | `readable` | `minimal`, `readable`, `structured`, or `full` |
| `render` | `false` | Explicitly start Chromium for JavaScript content |
| `max_chars` | `20000` | Content limit from 1000 through 40000 characters |

Use `structured` for headings and table summaries, or `full` when the page
layout matters. Table summaries can omit cells. Check the truncation flag
before treating a response as the entire page. The URL identifies the requested
source, not a separately verified final redirect destination.

HTTP fetching uses Pulldown's address checks and guarded redirects. The tool
does not offer private-address overrides, cookies, custom headers, proxies, or
disabled TLS verification. Browser rendering is explicit, rejects redirects,
and blocks workers and WebSockets. Use a canonical URL for rendered pages.
Address validation does not replace network isolation against DNS changes.

The plugin starts a fixed local Python process with filtered environment
variables, bounded output, and a deadline. Web content is untrusted data, and
the returned text is visible to the model handling the chat. HTTP cache files
live under the selected Hermes home's `cache/pulldown` directory. Rendering
bypasses that cache. The plugin never installs dependencies during a tool call.

## Verify or remove the installation

```sh
pulldown hermes doctor --json-output
hermes plugins doctor ~/.hermes/plugins/pulldown --ci
hermes plugins disable pulldown
pulldown hermes uninstall
```

Uninstall preserves cache files and user additions. Modified managed files are
reported instead of overwritten or deleted. To replace the Python environment,
keep the old one until the new environment has installed the plugin and passed
doctor.

See [the verification record](validation/hermes-2026-10-29.md) for the tested
package and host, and `scripts/hermes-smoke.py --help` for the native acceptance
check. Native dispatch and a normal model-driven chat are recorded separately.
