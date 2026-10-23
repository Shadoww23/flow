#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REQUIRED_TOOL = (
    "id",
    "name",
    "publisher",
    "tagline",
    "description",
    "category",
    "audience",
    "platforms",
    "status",
    "version",
    "license",
    "repo",
    "releases",
    "docs",
    "problems",
    "install",
    "downloads",
    "checks",
    "before",
    "steps",
    "faq",
    "problems_cards",
    "assets",
)

STATUS_LABEL = {"alpha": "Alpha", "beta": "Beta", "stable": "Stable"}
FORMAT_LABEL = {"tar.gz": "tar.gz", "zip": "zip", "whl": "wheel", "txt": "checksums"}
PLATFORM_LABEL = {
    "linux": "Linux",
    "windows": "Windows",
    "macos": "macOS",
    "steamdeck": "Steam Deck",
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def load_suite(root: Path | str = ROOT) -> tuple[dict, list[dict]]:
    root = Path(root)
    suite = json.loads((root / "suite.json").read_text(encoding="utf-8"))
    tools = []
    for tool_id in suite.get("tools", []):
        path = root / "tools" / f"{tool_id}.json"
        tool = json.loads(path.read_text(encoding="utf-8"))
        tool.setdefault("id", tool_id)
        tools.append(tool)
    return suite, tools


def validate(suite: dict, tools: list[dict], root: Path | str = ROOT) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    for key in ("name", "publisher", "tagline", "description", "url"):
        if not suite.get(key):
            errors.append(f"suite.json: missing {key!r}")
    if not tools:
        errors.append("suite.json: no tools listed")

    seen: set[str] = set()
    for tool in tools:
        tool_id = tool.get("id", "<missing>")
        label = f"tools/{tool_id}.json"
        for key in REQUIRED_TOOL:
            if key not in tool:
                errors.append(f"{label}: missing {key!r}")
        if tool_id in seen:
            errors.append(f"{label}: duplicate tool id {tool_id!r}")
        seen.add(tool_id)

        if tool.get("status") not in STATUS_LABEL:
            errors.append(f"{label}: status must be one of {', '.join(STATUS_LABEL)}")
        for platform in tool.get("platforms", []):
            if platform not in PLATFORM_LABEL:
                errors.append(f"{label}: unknown platform {platform!r}")

        for key in ("cover", "icon", "screenshot"):
            asset = tool.get("assets", {}).get(key)
            if asset and not (root / asset).exists():
                errors.append(f"{label}: asset not found: {asset}")

        install = tool.get("install", {})
        if not install:
            errors.append(f"{label}: no install channels")
        archive = install.get("archive")
        if not archive:
            errors.append(f"{label}: install.archive is required")
        else:
            for key in ("url", "sha256", "format"):
                if not archive.get(key):
                    errors.append(f"{label}: install.archive missing {key!r}")
            if archive.get("format") not in FORMAT_LABEL:
                errors.append(f"{label}: unsupported archive format {archive.get('format')!r}")
        for channel, entry in install.items():
            if not isinstance(entry.get("available", True), bool):
                errors.append(f"{label}: install.{channel}.available must be true or false")

        entries = tool.get("entries", {})
        default_entry = tool.get("default_entry")
        if default_entry and default_entry not in entries:
            errors.append(f"{label}: default_entry {default_entry!r} not in entries")

        downloads = tool.get("downloads", [])
        if not downloads:
            errors.append(f"{label}: no downloads listed")
        for index, download in enumerate(downloads):
            if not download.get("url"):
                errors.append(f"{label}: downloads[{index}] missing url")
            if download.get("format") not in FORMAT_LABEL:
                errors.append(f"{label}: downloads[{index}] unsupported format")
            for platform in download.get("platforms", []):
                if platform not in PLATFORM_LABEL:
                    errors.append(f"{label}: downloads[{index}] unknown platform {platform!r}")

        steps = tool.get("steps", [])
        if not steps:
            errors.append(f"{label}: no setup steps listed")
        for index, step in enumerate(steps):
            for key in ("title", "text"):
                if not step.get(key):
                    errors.append(f"{label}: steps[{index}] missing {key!r}")

        for index, item in enumerate(tool.get("faq", [])):
            for key in ("q", "a"):
                if not item.get(key):
                    errors.append(f"{label}: faq[{index}] missing {key!r}")

    return errors


def rel_prefix(depth: int) -> str:
    return "../" * depth


def asset_path(tool: dict, key: str, depth: int) -> str | None:
    path = tool.get("assets", {}).get(key)
    if not path:
        return None
    return f"{rel_prefix(depth)}{path}"


def pill(status: str) -> str:
    label = STATUS_LABEL.get(status, status.title())
    return f'<span class="pill status-{esc(status)}">{esc(label)}</span>'


def human_size(value: object) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def channel_action(entry: dict) -> str:
    if entry.get("command"):
        return f'<code>{esc(entry["command"])}</code>'
    if entry.get("url"):
        return f'<a href="{esc(entry["url"])}">{esc(entry.get("label", "Download"))}</a>'
    return esc(entry.get("label", "See docs"))


def is_available(entry: dict) -> bool:
    return entry.get("available", True) is not False


def primary_download(tool: dict, platform: str) -> dict | None:
    for download in tool.get("downloads", []):
        if download.get("format") in ("tar.gz", "zip") and platform in download.get("platforms", []):
            return download
    return None


def os_download_attrs(tool: dict) -> str:
    itch = tool.get("itch") or tool["release_url"]
    linux = primary_download(tool, "linux")
    windows = primary_download(tool, "windows")
    attrs: list[str] = []
    if linux:
        attrs += [f'data-linux-href="{esc(linux["url"])}"', 'data-linux-label="Download for Linux"']
    else:
        attrs += [f'data-linux-href="{esc(itch)}"', 'data-linux-label="Download from itch.io"']
    if windows:
        attrs += [f'data-windows-href="{esc(windows["url"])}"', 'data-windows-label="Download for Windows"']
    else:
        attrs += [f'data-windows-href="{esc(itch)}"', 'data-windows-label="Linux only - see itch.io"']
    if linux:
        default = linux
        label = "Download for Linux"
    elif windows:
        default = windows
        label = "Download for Windows"
    else:
        default = None
        label = "Download"
    href = default["url"] if default else itch
    attrs += [f'data-default-href="{esc(href)}"', f'data-default-label="{esc(label)}"']
    return " ".join(attrs)


def steps_section(tool: dict) -> str:
    steps = "\n".join(
        f"""<li class="step">
      <span class="step-num">{index}</span>
      <div><h3>{esc(step["title"])}</h3><p>{esc(step["text"])}</p></div>
    </li>"""
        for index, step in enumerate(tool.get("steps", []), start=1)
    )
    before = "".join(f"<li>{esc(item)}</li>" for item in tool.get("before", []))
    return f"""<section class="setup">
  <div class="kicker">GET STARTED</div>
  <h2>Install in 3 steps</h2>
  <ol class="steps">
{steps}
  </ol>
  <div class="before">
    <h3>Before you start</h3>
    <ul>{before}</ul>
  </div>
</section>"""


def faq_section(tool: dict) -> str:
    items = "\n".join(
        f"""<details>
      <summary>{esc(item["q"])}</summary>
      <p>{esc(item["a"])}</p>
    </details>"""
        for item in tool.get("faq", [])
    )
    if not items:
        return ""
    return f"""<section class="faq-section">
  <h2>Questions people ask</h2>
  <div class="faq">
{items}
  </div>
</section>"""


def planned_channels(tool: dict) -> str:
    planned = [
        esc(entry.get("label", key))
        for key, entry in tool.get("install", {}).items()
        if not is_available(entry)
    ]
    if not planned:
        return ""
    return f'<p class="planned"><strong>Planned:</strong> {", ".join(planned)}. These are not published yet.</p>'


def primary_href(tool: dict) -> str:
    download = primary_download(tool, "linux") or primary_download(tool, "windows")
    if download:
        return download["url"]
    return tool.get("itch") or tool["release_url"]


def head_image(suite: dict, tool: dict | None = None, key: str = "cover") -> str | None:
    path = tool.get("assets", {}).get(key) if tool else suite.get("image")
    if not path:
        return None
    return f"{suite['url'].rstrip('/')}/{path}"


def shell(
    suite: dict,
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    depth: int = 0,
    image: str | None = None,
    ld: dict | None = None,
    active: str = "",
) -> str:
    rel = rel_prefix(depth)

    def nav(href: str, label: str, key: str) -> str:
        cls = ' class="on"' if active == key else ""
        return f'<a href="{href}"{cls}>{label}</a>'

    ld_block = ""
    if ld is not None:
        ld_block = (
            '<script type="application/ld+json">\n'
            + json.dumps(ld, indent=2)
            + "\n</script>"
        )
    og_image = (
        f'<meta property="og:image" content="{esc(image)}">' if image else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
{og_image}
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#102a29">
<link rel="icon" type="image/svg+xml" href="{rel}assets/flow-icon.svg">
<link rel="stylesheet" href="{rel}style.css">
{ld_block}
</head>
<body>
<header class="site">
  <div class="brandwrap">
    <a class="brand" href="{rel}index.html"><span>F</span>LOW</a>
    <span class="byline">tools by <a href="{esc(suite.get("itch", "#"))}">{esc(suite.get("publisher", "Brlinkin"))}</a></span>
  </div>
  <nav>
    {nav(rel + "index.html#tools", "Tools", "tools")}
    {nav(rel + "index.html#problems", "Problems", "problems")}
    {nav(rel + "index.html#about", "About", "about")}
    <a href="{esc(suite.get("github", "#"))}" rel="noreferrer">GitHub</a>
  </nav>
</header>

<main>
{body}
</main>

<footer class="site">
  <div><strong>Flow</strong> · the hub for {esc(suite.get("publisher", "Brlinkin"))} tools.</div>
  <div class="foot-links">
    <a href="{rel}index.html#tools">All tools</a>
    <a href="{esc(suite.get("itch", "#"))}">itch.io</a>
    <a href="{esc(suite.get("repo", "#"))}/blob/main/PLAYBOOK.md">Add a tool</a>
    <a href="{esc(suite.get("github", "#"))}">GitHub</a>
  </div>
</footer>
<script defer src="{rel}assets/os.js"></script>
</body>
</html>
"""


def category_line(tool: dict) -> str:
    category = " · ".join(str(part) for part in tool.get("category", []))
    platforms = " / ".join(PLATFORM_LABEL.get(p, p) for p in tool.get("platforms", []))
    audience = str(tool.get("audience", "")).title()
    bits = [category, audience, platforms]
    return " · ".join(bit for bit in bits if bit)


def tool_card(tool: dict, depth: int = 0) -> str:
    rel = rel_prefix(depth)
    cover = asset_path(tool, "cover", depth)
    cover_html = (
        f'<a href="{rel}tools/{esc(tool["id"])}.html"><img src="{esc(cover)}" alt="{esc(tool["name"])} cover" width="630" height="500"></a>'
        if cover
        else ""
    )
    checks = "\n".join(f"<li>{esc(item)}</li>" for item in tool.get("checks", [])[:3])
    return f"""<article class="product {esc(tool.get("accent", ""))}">
  {cover_html}
  <div>
    <div class="kicker">{esc(category_line(tool))} {pill(tool.get("status", "alpha"))}</div>
    <h2><a class="plain" href="{rel}tools/{esc(tool["id"])}.html">{esc(tool["name"])}</a></h2>
    <p class="tagline">{esc(tool["tagline"])}</p>
    <p>{esc(tool["description"])}</p>
    <ul class="checks">
{checks}
    </ul>
    <div class="links">
      <a class="btn primary os-download" href="{esc(primary_href(tool))}" {os_download_attrs(tool)}>Download for Linux</a>
      <a class="btn" href="{rel}tools/{esc(tool["id"])}.html">Setup guide</a>
      <a class="text" href="{esc(tool["repo"])}">Source ↗</a>
    </div>
    <p class="tiny">Alpha. One-click installers are in progress.</p>
  </div>
</article>"""


def render_index(suite: dict, tools: list[dict]) -> str:
    cards = "\n".join(tool_card(tool) for tool in tools)

    hero_buttons = []
    if tools:
        first = tools[0]
        hero_buttons.append(
            f'<a class="btn primary os-download" href="{esc(primary_href(first))}" {os_download_attrs(first)}>Download for Linux</a>'
        )
    for tool in tools[1:3]:
        hero_buttons.append(
            f'<a class="btn" href="tools/{esc(tool["id"])}.html">{esc(tool["name"])}</a>'
        )
    hero_buttons.append('<a class="btn" href="#tools">See all tools</a>')
    hero_buttons_html = "\n    ".join(hero_buttons)

    problem_cards = []
    for tool in tools:
        for card in tool.get("problems_cards", []):
            problem_cards.append(
                f"""<a class="card" href="{esc(card["url"])}">
      <span class="tag">{esc(tool["name"])}</span>
      <h3>{esc(card["title"])}</h3>
      <p>{esc(card["text"])}</p>
    </a>"""
            )
    problems = "\n".join(problem_cards)

    channel_cols = []
    for tool in tools:
        rows = []
        for entry in tool.get("install", {}).values():
            if not is_available(entry):
                continue
            rows.append(
                f'<li><span class="chan">{esc(entry.get("label", "Channel"))}</span>{channel_action(entry)}</li>'
            )
        channel_cols.append(
            f"""<div class="channel-col">
      <h3>{esc(tool["name"])} <span class="ver">v{esc(tool["version"])}</span></h3>
      <ul>{"".join(rows)}</ul>
      {planned_channels(tool)}
    </div>"""
        )

    roadmap = "".join(f"<li>{esc(item)}</li>" for item in suite.get("roadmap", []))
    roadmap_html = (
        f"""<div class="roadmap">
    <h3>Coming next</h3>
    <ul>{roadmap}</ul>
  </div>"""
        if roadmap
        else ""
    )

    ld = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": suite["name"],
        "url": suite["url"],
        "description": suite["description"],
        "sameAs": [suite.get("github"), suite.get("itch")],
        "brand": suite.get("publisher"),
    }

    body = f"""<section class="hero">
  <div class="eyebrow">FREE ALPHA · LINUX + WINDOWS · NO ACCOUNT NEEDED</div>
  <h1>Tools that fix the specific thing. <em>One hub.</em></h1>
  <p class="lede">{esc(suite["description"])}</p>
  <div class="cta">
    {hero_buttons_html}
  </div>
  <p class="alpha-note">One-click desktop installers and a Steam Deck plugin are in progress.
  For now, desktop Linux setup uses a terminal; the steps on each tool page walk through it.</p>
  <p class="small">No accounts. No telemetry. No hosted service required.</p>
</section>

<section class="how">
  <div class="section-head">
    <div class="kicker">HOW IT WORKS</div>
    <h2>Pick a tool. Download it. Follow three steps.</h2>
    <p>No sign-up, no subscription, and nothing running in the cloud.</p>
  </div>
  <div class="how-grid">
    <div class="how-card"><span class="step-num">1</span><h3>Pick the tool for your problem</h3><p>Proton Picker is for Steam players. ReleaseGuard is for game teams checking a build before release.</p></div>
    <div class="how-card"><span class="step-num">2</span><h3>Download the free alpha</h3><p>Grab the archive from itch.io or GitHub. Every file comes from the official release and has a published checksum.</p></div>
    <div class="how-card"><span class="step-num">3</span><h3>Follow the setup steps</h3><p>Each tool page lists exactly what to do, what to close first, and how to undo it. Read the alpha limits before you start.</p></div>
  </div>
</section>

<section id="tools" class="tools">
  <div class="section-head">
    <div class="kicker">THE SUITE</div>
    <h2>Two focused tools</h2>
    <p>Each tool is described once in a manifest. The catalog, install channels, and the
    <code>flow</code> CLI are generated from it, so the next tool plugs in instead of being rebuilt.</p>
  </div>
  {cards}
</section>

<section id="install" class="channels">
  <div class="section-head">
    <div class="kicker">WAYS TO GET IT</div>
    <h2>Download directly. No account needed.</h2>
    <p>Every recipe points at the official release. Nothing is mirrored, patched, or repackaged.
    Terminal-only channels are marked as planned until they are actually published.</p>
  </div>
  <div class="channel-grid">
    {"".join(channel_cols)}
  </div>
  {roadmap_html}
</section>

<section id="problems" class="problems">
  <div class="section-head">
    <div class="kicker">SEARCH BY PROBLEM</div>
    <h2>What are you trying to fix?</h2>
    <p>These are the questions the tools answer. Each links to concrete checks and honest limits.</p>
  </div>
  <div class="grid">
    {problems}
  </div>
</section>

<section class="addtool">
  <div>
    <div class="kicker">FOR BUILDERS</div>
    <h2>Add a tool in one manifest.</h2>
    <p>Drop a JSON file in <code>tools/</code>, add its assets, and run <code>python3 build.py</code>.
    The catalog page, setup steps, sitemap, and CLI entry appear automatically.</p>
  </div>
  <a class="btn primary" href="{esc(suite["repo"])}/blob/main/PLAYBOOK.md">Read the playbook</a>
</section>

<section id="about" class="about">
  <div class="kicker">ABOUT FLOW</div>
  <h2>One hub. A small suite. Shared rules.</h2>
  <p>Flow is the hub. <strong>Brlinkin</strong> stays the label on the tools themselves.
  Every tool follows the same principles: run locally, make the safe choice explicit, keep a fallback,
  and never hide a failure behind a green checkmark.</p>
  <div class="pillars">
    <div><h3>Local first</h3><p>No accounts or telemetry. Proton Picker only sends game app IDs to public compatibility reports; ReleaseGuard runs entirely on your machine or CI.</p></div>
    <div><h3>Honest limits</h3><p>Every tool page states what the alpha does not do. Recommendations are starting points, not guarantees.</p></div>
    <div><h3>Restorable changes</h3><p>Steam config edits are backed up. Build reviews never modify your game. Official archives are checksum-verified.</p></div>
  </div>
  <p class="disclaimer">Flow and Brlinkin are independent and not affiliated with Valve Corporation. Steam and Proton are trademarks of their respective owners.</p>
</section>"""

    return shell(
        suite,
        title=f"{suite['name']} — {suite['tagline']}",
        description=suite["description"],
        canonical=suite["url"],
        body=body,
        depth=0,
        image=head_image(suite, tools[0] if tools else None),
        ld=ld,
        active="",
    )


def install_table(tool: dict) -> str:
    rows = []
    for key, entry in tool.get("install", {}).items():
        if not is_available(entry):
            continue
        rows.append(
            f"""<tr>
      <th scope="row">{esc(entry.get("label", key))}</th>
      <td>{channel_action(entry)}</td>
      <td class="note">{esc(entry.get("note", ""))}</td>
    </tr>"""
        )
    return f"""<div class="table-wrap">
  <table class="data">
    <thead><tr><th>Channel</th><th>How to install</th><th>Notes</th></tr></thead>
    <tbody>
    {"".join(rows)}
    </tbody>
  </table>
</div>"""


def downloads_table(tool: dict) -> str:
    rows = []
    for download in tool.get("downloads", []):
        sha = download.get("sha256")
        sha_html = f'<code class="sha">{esc(sha)}</code>' if sha else "—"
        rows.append(
            f"""<tr>
      <th scope="row">{esc(download.get("label", "Download"))}</th>
      <td>{esc(FORMAT_LABEL.get(download.get("format"), download.get("format", "")))}</td>
      <td><a href="{esc(download["url"])}">Download</a></td>
      <td class="nowrap">{esc(human_size(download.get("size")))}</td>
      <td>{sha_html}</td>
    </tr>"""
        )
    return f"""<div class="table-wrap">
  <table class="data">
    <thead><tr><th>File</th><th>Format</th><th>Link</th><th>Size</th><th>SHA-256</th></tr></thead>
    <tbody>
    {"".join(rows)}
    </tbody>
  </table>
</div>"""


def list_block(items: list[str], class_name: str = "checks") -> str:
    if not items:
        return ""
    lis = "\n".join(f"<li>{esc(item)}</li>" for item in items)
    return f'<ul class="{class_name}">\n{lis}\n</ul>'


def render_tool(suite: dict, tool: dict, tools: list[dict]) -> str:
    depth = 1
    canonical = f"{suite['url'].rstrip('/')}/tools/{tool['id']}.html"
    cover = asset_path(tool, "cover", depth)
    screenshot = asset_path(tool, "screenshot", depth)

    problem_cards = "\n".join(
        f"""<a class="card" href="{esc(card["url"])}">
      <span class="tag">{esc(tool["name"])}</span>
      <h3>{esc(card["title"])}</h3>
      <p>{esc(card["text"])}</p>
    </a>"""
        for card in tool.get("problems_cards", [])
    )

    latest = tool.get("latest")
    latest_html = (
        f'<p class="small">Latest upstream tag: <code>{esc(latest)}</code></p>'
        if latest
        else ""
    )

    screenshot_html = ""
    if screenshot:
        screenshot_html = f"""<figure class="shot">
      <img src="{esc(screenshot)}" alt="{esc(tool["name"])} screenshot">
      <figcaption>{esc(tool["name"])} {esc(tool["version"])} · alpha preview.</figcaption>
    </figure>"""

    index = next((i for i, item in enumerate(tools) if item["id"] == tool["id"]), 0)
    prev_tool = tools[index - 1] if index > 0 else tools[-1]
    next_tool = tools[(index + 1) % len(tools)]
    pager = f"""<nav class="pager">
  <a href="{esc(prev_tool["id"])}.html">← {esc(prev_tool["name"])}</a>
  <a href="../index.html#tools">All tools</a>
  <a href="{esc(next_tool["id"])}.html">{esc(next_tool["name"])} →</a>
</nav>"""

    ld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": tool["name"],
        "applicationCategory": "UtilitiesApplication",
        "operatingSystem": ", ".join(PLATFORM_LABEL.get(p, p) for p in tool["platforms"]),
        "softwareVersion": tool["version"],
        "description": tool["description"],
        "url": canonical,
        "downloadUrl": tool["release_url"],
        "license": tool.get("license_url", tool["license"]),
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "publisher": {"@type": "Organization", "name": tool.get("publisher", "Brlinkin")},
    }

    body = f"""<section class="product-hero">
  <div>
    <a class="back" href="../index.html#tools">← ALL TOOLS</a>
    <div class="kicker">{esc(category_line(tool))} {pill(tool.get("status", "alpha"))}</div>
    <h1>{esc(tool["name"])}</h1>
    <p class="tagline">{esc(tool["tagline"])}</p>
    <p class="lede">{esc(tool["description"])}</p>
    <div class="cta">
      <a class="btn primary os-download" href="{esc(primary_href(tool))}" {os_download_attrs(tool)}>Download for Linux</a>
      <a class="btn" href="{esc(tool.get("itch", tool["repo"]))}">itch.io</a>
      <a class="btn" href="{esc(tool["repo"])}">Source</a>
    </div>
    <p class="fineprint">Free alpha · one-click installer in progress</p>
    <p class="meta">Version <code>{esc(tool["version"])}</code> · {pill(tool.get("status", "alpha"))} · {esc(tool["license"])}</p>
    {latest_html}
  </div>
  {f'<img src="{esc(cover)}" alt="{esc(tool["name"])} cover" width="630" height="500">' if cover else ""}
</section>

<article class="article">
  <h2>What it does</h2>
  {list_block(tool.get("checks", []))}
  {screenshot_html}

  {steps_section(tool)}

  <h3>Alpha limits</h3>
  <div class="callout">
    {list_block(tool.get("limits", []), "limits")}
  </div>

  {faq_section(tool)}

  <h2>Problems it solves</h2>
  <div class="grid">
    {problem_cards}
  </div>

  <details class="advanced">
    <summary>Advanced: package managers, checksums, and the flow CLI</summary>
    <p>Only needed if you already use a terminal. The button above is the normal path.</p>
    <h3>Install channels</h3>
    {install_table(tool)}
    {planned_channels(tool)}
    <h3>Requirements</h3>
    {list_block(tool.get("requirements", []))}
    <h3>All downloads and checksums</h3>
    {downloads_table(tool)}
  </details>

  <h2>Docs and support</h2>
  <ul class="linklist">
    <li><a href="{esc(tool["docs"])}">Documentation and quick start</a></li>
    <li><a href="{esc(tool["problems"])}">Common problems</a></li>
    <li><a href="{esc(tool["releases"])}">All releases and checksums</a></li>
    <li><a href="{esc(tool.get("license_url", tool["repo"]))}">License: {esc(tool["license"])}</a></li>
  </ul>

  {pager}
</article>"""

    return shell(
        suite,
        title=f"{tool['name']} — {tool['tagline']}",
        description=tool["description"],
        canonical=canonical,
        body=body,
        depth=depth,
        image=head_image(suite, tool),
        ld=ld,
        active="tools",
    )


def render_404(suite: dict) -> str:
    body = """<section class="hero">
  <div class="eyebrow">404</div>
  <h1>That tool is not in the flow.</h1>
  <p class="lede">The page moved or never existed. The suite index has every current tool.</p>
  <div class="cta"><a class="btn primary" href="index.html">Back to Flow</a></div>
</section>"""
    return shell(
        suite,
        title=f"Not found — {suite['name']}",
        description="Page not found.",
        canonical=f"{suite['url'].rstrip('/')}/404.html",
        body=body,
    )


def github_latest(repo_url: str, timeout: float = 10.0) -> str | None:
    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=1",
        headers={
            "User-Agent": "flow-build",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        releases = json.load(response)
    if not releases:
        return None
    return releases[0].get("tag_name")


def build(
    root: Path | str = ROOT,
    out: Path | str | None = None,
    *,
    resolve: bool = False,
) -> Path:
    root = Path(root)
    out = Path(out) if out is not None else root / "site"
    suite, tools = load_suite(root)
    errors = validate(suite, tools, root)
    if errors:
        raise ValueError("manifest validation failed:\n  " + "\n  ".join(errors))

    if out.exists():
        if out.resolve() == root.resolve():
            raise ValueError("refusing to write the site into the repository root")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    if resolve:
        for tool in tools:
            try:
                latest = github_latest(tool["repo"])
            except Exception as error:
                print(f"warning: could not resolve latest release for {tool['id']}: {error}", file=sys.stderr)
                continue
            if latest:
                tool["latest"] = latest

    (out / "index.html").write_text(render_index(suite, tools), encoding="utf-8")
    (out / "404.html").write_text(render_404(suite), encoding="utf-8")

    tools_dir = out / "tools"
    tools_dir.mkdir()
    for tool in tools:
        (tools_dir / f"{tool['id']}.html").write_text(
            render_tool(suite, tool, tools), encoding="utf-8"
        )

    shutil.copy2(root / "style.css", out / "style.css")
    shutil.copytree(root / "assets", out / "assets")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    today = dt.date.today().isoformat()
    urls = [suite["url"].rstrip("/") + "/"] + [
        f"{suite['url'].rstrip('/')}/tools/{tool['id']}.html" for tool in tools
    ]
    entries = "\n".join(
        f"""  <url>
    <loc>{esc(url)}</loc>
    <lastmod>{today}</lastmod>
  </url>"""
        for url in urls
    )
    (out / "sitemap.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>
""",
        encoding="utf-8",
    )
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {suite['url'].rstrip('/')}/sitemap.xml\n",
        encoding="utf-8",
    )
    (out / "flow.json").write_text(
        json.dumps(
            {
                "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "suite": suite,
                "tools": tools,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Flow hub site from manifests.")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this file's folder)")
    parser.add_argument("--out", default=None, help="output directory (default: <root>/site)")
    parser.add_argument("--check", action="store_true", help="validate manifests without building")
    parser.add_argument("--resolve", action="store_true", help="resolve the latest GitHub release per tool at build time")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    suite, tools = load_suite(root)
    errors = validate(suite, tools, root)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if args.check:
        print(f"ok: {len(tools)} tool manifest(s) valid")
        return 0

    out = build(root, args.out, resolve=args.resolve)
    print(f"built {out}")
    for tool in tools:
        print(f"  {tool['id']}: {out / 'tools' / (tool['id'] + '.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
