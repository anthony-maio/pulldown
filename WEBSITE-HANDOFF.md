# pulldown Website Handoff

## Objective

Build a single-page product landing site for **pulldown** at `pulldown.making-minds.ai`. It should match the design language, quality, and structure of the sibling product site at `mnemos.making-minds.ai` — same brand family, different product identity.

---

## Reference Site

**mnemos.making-minds.ai** is the definitive reference. The pulldown site must feel like it belongs to the same product family while having its own distinct character.

### Design system (inherit exactly)

| Token | Dark value | Light value | Notes |
|---|---|---|---|
| `--color-bg` | `#0a0a0f` | `#f8f8fb` | Near-black with blue-purple undertone |
| `--color-surface` | `#0f0f15` | `#f0f0f5` | Alternating section backgrounds |
| `--color-surface-2` | `#141420` | `#e8e8f0` | Cards, table headers |
| `--color-surface-code` | `#0d0d14` | `#f5f5fa` | Code blocks |
| `--color-accent` | `#d4a853` | `#b5851e` | Warm gold — all highlights, CTAs, interactive elements |
| `--color-accent-hover` | `#e0b86a` | darkened | Button hover |
| `--color-accent-glow` | `rgba(212,168,83,0.15)` | adjusted | Radial halos, selection |
| `--color-text` | `#e8e8ed` | `#18181f` | Primary text |
| `--color-text-muted` | `#8e8ea0` | adjusted | Secondary text, nav links |
| `--color-text-faint` | `#4a4a5a` | adjusted | Decorative labels |
| `--color-success` | `#4ade80` | same | Check marks |
| `--color-error` | `#f87171` | same | Cross marks |

### Typography (inherit exactly)

- **Body**: Inter (300-700), system-ui fallback
- **Mono**: JetBrains Mono (400, 500), Fira Code fallback
- **All sizes use `clamp()`** for fluid scaling — no hardcoded px
- **Hero title**: `clamp(2.5rem, 1rem + 4vw, 5rem)`, weight 700, tracking `-0.03em`
- **Section headings**: `clamp(1.5rem, 1rem + 1.5vw, 2.25rem)`
- **Section labels**: JetBrains Mono, uppercase, `letter-spacing: 0.1em`, gold, preceded by `::before` horizontal rule
- **Body text**: `clamp(0.875rem, 0.8rem + 0.35vw, 1rem)`, line-height 1.65

### Shared patterns (inherit exactly)

- **Sticky nav**: `backdrop-filter: blur(16px)`, `oklch()`-based semi-transparent bg, scroll shadow class
- **Nav layout**: left = `making-minds.ai >` breadcrumb at 40% opacity, then logo + product name; center = section anchor links; right = theme toggle, GitHub ghost button, primary CTA
- **Section labels**: `— SECTION NAME` in gold monospace uppercase above every h2, with `::before` short horizontal rule
- **Scroll reveal**: `IntersectionObserver` adds `.visible` to `.reveal-pending` cards with staggered delay (80ms/card)
- **Code blocks**: dark header bar with mono title + copy button, `<pre><code>` body with syntax classes
- **Theme toggle**: `data-theme` on `<html>`, localStorage-persisted
- **Footer**: three-column flex — brand + "Created by Anthony Maio", product links with inline SVG icons, docs links
- **Bottom strip**: MIT License, GitHub link, Privacy link
- **Accessibility**: skip link, aria labels, `prefers-reduced-motion`, `aria-hidden` on decorative SVGs

### Tech stack (match exactly)

- **Pure vanilla HTML + CSS + JS** — no React, no Tailwind, no bundler
- **Single `style.css`**, single page HTML, inline `<script>` at bottom
- **Google Fonts** via `<link>` for Inter + JetBrains Mono
- **Inline SVG illustrations** with SMIL `<animate>` elements
- **CSS custom properties** as the design token layer
- **Static HTML hosting** — no SSR, no build step

---

## What's Different for pulldown

### Product identity

pulldown is a **web fetching library** — it pulls web pages down into clean Markdown. The visual metaphor should reflect **pulling content down** from the web, **filtering/distilling** raw HTML into clean output, or a **pipeline/funnel** from messy web to clean text.

Where mnemos uses neural-circuit-style SVG illustrations (nodes, connections, pulses), pulldown should use illustrations that convey **data flow / transformation**: raw HTML flowing in one side, clean Markdown emerging from the other. Think: funnel diagrams, layered extraction, flowing text being cleaned.

### Brand accent color

The gold `#d4a853` accent stays — it's the making-minds.ai family color. Don't change it.

### Hero SVG illustration

Create a bespoke animated inline SVG for the hero (right column) that represents pulldown's core concept. Suggested approach: an animated visualization showing raw HTML tags/noise flowing in from one side, passing through a filter/extractor layer, and emerging as clean readable text on the other side. Use the same animation style as mnemos — SMIL `<animate>` with `stroke-dashoffset` flowing lines, pulsing gold nodes, `repeatCount="indefinite"`.

---

## Page Sections (in DOM order)

### 1. Navigation (sticky)

- Left: `making-minds.ai >` breadcrumb, then pulldown logo mark + "pulldown"
- Logo mark: design a simple SVG mark that suggests "pulling down" — an arrow pointing down, or a descending bracket/chevron motif. Keep it geometric and monoline like the mnemos mu-circuit mark.
- Center links: Features, Detail Levels, Quick Start, Security, Integrations
- Right: theme toggle, GitHub ghost button, primary CTA: `pip install pulldown`

### 2. Hero

- **Overline**: `OPEN SOURCE  ·  HTTP-FIRST  ·  CLI + PYTHON + MCP`
- **Title**: `pulldown` (same hero scale as mnemos)
- **Tagline** (bold): "Web pages in. Clean Markdown out."
- **Description paragraph**: "Fetch any URL as agent-ready Markdown with one call. Four detail levels from minimal tokens to full-page extraction. Browser rendering when JavaScript matters. SSRF-safe, size-capped, and cache-aware by default."
- **CTAs**: `View on GitHub` (primary, with GitHub SVG icon) + `pip install pulldown` (ghost)
- **Right column**: bespoke animated SVG (see above)

### 3. Features Section (id="features")

- Section label: `— WHAT IT DOES`
- h2: "One library. Every web page. Agent-ready Markdown."
- **Feature cards** — use the same card structure as mnemos architecture cards but simpler (no neuroscience citations). Each card has:
  - Left: feature name (h3), description paragraph
  - Right: small illustrative SVG or code snippet

Six feature cards:

| # | Name | Description |
|---|---|---|
| 1 | HTTP-First with Browser Fallback | Browser-like defaults out of the box. Optional Chromium rendering for JS-heavy SPAs — just add `render=True`. |
| 2 | Four Detail Levels | `minimal` for lowest tokens, `readable` for article Markdown, `full` for complete pages, `raw` for custom parsing. Pick the right fidelity for your agent's task. |
| 3 | Concurrent Batch Fetching | `fetch_many()` fetches dozens of URLs concurrently with configurable parallelism. Retries transient errors with exponential backoff. |
| 4 | Bounded Site Crawling | `crawl()` walks a site within depth and page-count bounds. Respects `robots.txt`, enforces per-domain politeness delays, and deduplicates automatically. |
| 5 | Validator Caching | ETag and Last-Modified conditional requests. Expired entries are revalidated, not discarded. Atomic writes prevent corruption under concurrent access. |
| 6 | SSRF-Safe by Default | Private, loopback, link-local, and cloud-metadata addresses are blocked before any connection. Size caps prevent memory exhaustion. Only http/https schemes accepted. |

### 4. Detail Levels Section (id="detail-levels")

- Section label: `— DETAIL LEVELS`
- h2: "Right-sized output for every task."
- Visual comparison: show the same URL fetched at all four levels with truncated sample output. Use a tabbed or side-by-side layout with code blocks.
- Below: the detail levels table from the README:

| Level | Output | Best for |
|---|---|---|
| `minimal` | Title + plain text | Lowest-token summarisation |
| `readable` | Article Markdown with links | RAG, reading (default) |
| `full` | Full-page Markdown incl. chrome | Pages without clear article body |
| `raw` | Untouched HTML | Custom parsing downstream |

### 5. Quick Start Section (id="quickstart")

- Section label: `— QUICK START`
- h2: "Fetching in three lines."
- Two-column code block grid (same style as mnemos):
  - **Left column**: CLI examples
    ```bash
    pip install pulldown
    pulldown get https://example.com
    pulldown get https://example.com --detail minimal
    pulldown get https://example.com --render
    pulldown crawl https://docs.example.com --max-pages 20
    ```
  - **Right column**: Python API
    ```python
    from pulldown import fetch, Detail

    result = await fetch(
        "https://example.com",
        detail=Detail.readable,
    )
    print(result.title)
    print(result.content)
    ```

### 6. Security Section (id="security")

- Background: `var(--color-surface)` (alternating)
- Section label: `— SECURITY`
- h2: "Safe by default. No opt-in required."
- Three cards in a row:
  - **SSRF Guard**: Resolves hostnames, checks every IP against private/loopback/link-local/metadata predicates, blocks before any connection.
  - **Size Caps**: 10 MiB default. Checked against Content-Length header and decoded body. Configurable via `max_bytes`.
  - **Scheme Validation**: Only `http` and `https`. No `file:`, `ftp:`, or exotic protocols.

### 7. Integrations Section (id="integrations")

- Section label: `— INTEGRATIONS`
- h2: "CLI. Python. MCP. Pick your interface."
- Three integration panels:
  - **CLI**: show the `pulldown` command with flags
  - **Python API**: show `fetch()`, `fetch_many()`, `crawl()` imports
  - **MCP Server**: show the JSON config block for Claude Desktop / Claude Code

### 8. Comparison Table (id="compare") — optional but recommended

- Section label: `— WHY PULLDOWN`
- h2: "How it compares."
- Table comparing pulldown vs raw httpx vs requests+BeautifulSoup vs Playwright-only:

| Feature | pulldown | httpx | requests+BS4 | Playwright |
|---|---|---|---|---|
| Markdown output | yes | no | manual | no |
| JS rendering | opt-in | no | no | always |
| SSRF protection | default | no | no | no |
| Caching with validators | built-in | manual | manual | no |
| robots.txt | built-in | no | manual | no |
| MCP server | built-in | no | no | no |

### 9. Footer

- Same three-column structure as mnemos
- Left: pulldown logo + "Pull down web pages as clean Markdown for LLM agents." + "Created by Anthony Maio"
- Center "Project" links: making-minds.ai, GitHub, PyPI, Quick Start, Security
- Right "Docs" links: README, CHANGELOG, MCP Setup, Agent Skill
- Bottom: MIT License, pulldown on GitHub, Privacy

---

## Product Details (for accurate copy)

### Version
0.2.0

### GitHub repo
https://github.com/anthony-maio/pulldown

### Install commands
```
pip install pulldown
pip install 'pulldown[render]'
pip install 'pulldown[mcp]'
pip install 'pulldown[all]'
```

### Public API surface
```python
from pulldown import fetch, fetch_many, crawl, Detail, FetchResult, CrawlResult, PageCache
```

### Key parameters for fetch()
- `url: str` — the URL to fetch
- `detail: Detail` — minimal, readable, full, raw
- `render: bool` — use Playwright for JS rendering
- `headers: dict` — custom HTTP headers
- `cookies: list[dict]` — cookies to send
- `timeout: float` — request timeout in seconds (default 30)
- `cache: PageCache | None` — cache instance
- `max_bytes: int` — max response size (default 10 MiB)
- `allow_private_addresses: bool` — disable SSRF guard (default False)
- `retries: int` — retry count for transient errors (default 0)
- `retry_base_delay: float` — base delay for exponential backoff (default 1.0)
- `verify_ssl: bool` — verify TLS certificates (default True)

### CLI commands
- `pulldown get <url>` — fetch a single URL
- `pulldown crawl <url>` — crawl a site
- `pulldown bench <url>` — benchmark fetching
- `pulldown cache stats` — show cache statistics
- `pulldown cache clear` — clear the cache

### MCP tools exposed
- `fetch` — fetch a single URL as Markdown
- `fetch_many` — fetch multiple URLs concurrently
- `crawl` — bounded site crawl

---

## Files to Create

```
website/
├── index.html          # single-page landing
├── style.css           # all styles, CSS custom properties
└── (no JS file — inline <script> at bottom of HTML)
```

The site should be deployable as static files to any hosting (GitHub Pages, Cloudflare Pages, Vercel static, etc.).

---

## Quality Checklist

- [ ] Dark theme is default, light theme toggle works and persists
- [ ] All typography uses `clamp()` — fully fluid, no media-query breakpoints for type
- [ ] `prefers-reduced-motion` suppresses all animations
- [ ] Skip link present and functional
- [ ] All interactive elements have aria labels
- [ ] All decorative SVGs have `aria-hidden="true"`
- [ ] Copy buttons on all code blocks with "copied!" feedback
- [ ] Sticky nav with backdrop blur and scroll shadow
- [ ] Mobile hamburger menu
- [ ] No external JS dependencies
- [ ] No framework, no bundler — pure HTML/CSS/JS
- [ ] Valid HTML5, no console errors
- [ ] SVG illustrations are inline (not external files)
- [ ] Gold accent used with restraint — only on interactive elements, highlights, animations
- [ ] Parent brand breadcrumb in nav at low opacity
- [ ] Footer credits "Created by Anthony Maio"
