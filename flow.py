#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0"
USER_AGENT = f"flow/{VERSION}"


class FlowError(RuntimeError):
    pass


def _load_site_build():
    path = ROOT / "build.py"
    if path.exists():
        spec = importlib.util.spec_from_file_location("_flow_site_build", path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    try:
        import build as module
        return module
    except ImportError as error:
        raise FlowError("build.py not found next to flow.py") from error


site_build = _load_site_build()


def load_tools(root: Path | str = ROOT) -> tuple[dict, list[dict]]:
    return site_build.load_suite(root)


def find_tool(tools: list[dict], tool_id: str) -> dict:
    for tool in tools:
        if tool["id"] == tool_id:
            return tool
    known = ", ".join(tool["id"] for tool in tools) or "none"
    raise FlowError(f"unknown tool {tool_id!r}; known: {known}")


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "flow"


def state_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "state.json"


def load_state(root: Path | None = None) -> dict:
    path = state_path(root)
    if not path.exists():
        return {"version": 1, "tools": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FlowError(f"corrupt state file: {path}: {error}") from error
    state.setdefault("version", 1)
    state.setdefault("tools", {})
    return state


def save_state(state: dict, root: Path | None = None) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def version_key(value: str) -> tuple:
    text = str(value).strip().lower().lstrip("v")
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[-._]?(alpha|beta|rc)[-._]?(\d*))?", text)
    if not match:
        return ((), 99, 0)
    numbers = tuple(int(part) for part in match.group(1).split("."))
    stage = {"alpha": 0, "beta": 1, "rc": 2, None: 3}[match.group(2)]
    return (numbers, stage, int(match.group(3) or 0))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, timeout: float = 60.0) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith(("http://", "https://")):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    elif url.startswith("file://"):
        source = Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
        shutil.copyfile(source, destination)
    else:
        shutil.copyfile(Path(url).expanduser(), destination)
    return destination


def _is_inside(base: Path, target: Path) -> bool:
    base = base.resolve()
    target = target.resolve()
    return target == base or base in target.parents


def safe_extract(archive: Path, destination: Path, fmt: str | None = None) -> None:
    archive = Path(archive)
    destination = Path(destination)
    fmt = fmt or ("zip" if archive.suffix.lower() == ".zip" else "tar.gz")
    destination.mkdir(parents=True, exist_ok=True)

    if fmt == "zip":
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                member = Path(name)
                if member.is_absolute() or ".." in member.parts:
                    raise FlowError(f"unsafe path in archive: {name}")
                if not _is_inside(destination, destination / name):
                    raise FlowError(f"unsafe path in archive: {name}")
            bundle.extractall(destination)
        return

    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise FlowError(f"unsafe path in archive: {member.name}")
            if not _is_inside(destination, destination / member.name):
                raise FlowError(f"unsafe path in archive: {member.name}")
            if member.issym() or member.islnk():
                raise FlowError(f"archive contains a link, refusing: {member.name}")
        kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
        bundle.extractall(destination, **kwargs)


def latest_release(repo_url: str, timeout: float = 10.0) -> str | None:
    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=1",
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        releases = json.load(response)
    if not releases:
        return None
    return releases[0].get("tag_name")


def install_tool(
    tool: dict,
    channel: str = "archive",
    *,
    force: bool = False,
    dry_run: bool = False,
    run_command: bool = False,
    data_root: Path | None = None,
) -> int:
    install = tool.get("install", {})
    if channel not in install:
        known = ", ".join(install)
        raise FlowError(f"{tool['id']} has no {channel!r} channel; channels: {known}")
    entry = install[channel]

    if not site_build.is_available(entry):
        raise FlowError(
            f"{tool['id']} {channel} channel is planned but not published yet"
        )

    if channel != "archive":
        action = entry.get("command") or entry.get("url") or entry.get("label")
        note = entry.get("note", "")
        if dry_run:
            print(f"would use {entry.get('label', channel)}: {action}")
            return 0
        if entry.get("command") and run_command:
            helper = shlex.split(entry["command"])[0]
            if shutil.which(helper) is None:
                raise FlowError(f"{helper} is not installed; run: {entry['command']}")
            print(f"running: {entry['command']}")
            return subprocess.run(entry["command"], shell=True).returncode
        print(f"{tool['name']} via {entry.get('label', channel)}: {action}")
        if note:
            print(f"  {note}")
        if entry.get("command"):
            print(f"  run: {entry['command']}")
        return 0

    for key in ("url", "sha256", "format"):
        if not entry.get(key):
            raise FlowError(f"{tool['id']} archive channel is missing {key!r}")

    data_root = Path(data_root) if data_root is not None else data_dir()
    destination = data_root / "tools" / tool["id"] / str(tool["version"])
    suffix = ".zip" if entry["format"] == "zip" else ".tar.gz"

    if dry_run:
        print(f"would download {entry['url']}")
        print(f"would verify sha256 {entry['sha256']}")
        print(f"would extract and install to {destination}")
        return 0

    with tempfile.TemporaryDirectory(prefix="flow-install-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / f"download{suffix}"
        print(f"downloading {entry['url']}")
        download(entry["url"], archive)
        digest = sha256_file(archive)
        if digest.lower() != entry["sha256"].lower():
            raise FlowError(
                f"checksum mismatch for {tool['id']}:\n  expected {entry['sha256']}\n  got      {digest}"
            )
        print("checksum ok")

        staging = temporary_path / "staging"
        safe_extract(archive, staging, entry["format"])
        contents = list(staging.iterdir())
        source = contents[0] if len(contents) == 1 and contents[0].is_dir() else staging

        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not force:
                raise FlowError(f"{tool['id']} {tool['version']} is already installed at {destination}; use --force")
            shutil.rmtree(destination)
        shutil.move(str(source), str(destination))

    state = load_state(data_root)
    state["tools"][tool["id"]] = {
        "version": tool["version"],
        "channel": channel,
        "path": str(destination),
        "sha256": entry["sha256"],
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    save_state(state, data_root)

    print(f"installed {tool['name']} {tool['version']} -> {destination}")
    entries = tool.get("entries", {})
    if entries.get("setup"):
        print(f"next: flow open {tool['id']} --entry setup")
    return 0


def open_tool(
    tool: dict,
    entry_name: str | None = None,
    *,
    data_root: Path | None = None,
    dry_run: bool = False,
) -> int:
    data_root = Path(data_root) if data_root is not None else data_dir()
    record = load_state(data_root)["tools"].get(tool["id"])
    if not record:
        raise FlowError(f"{tool['name']} is not installed; run: flow install {tool['id']}")
    directory = Path(record["path"])
    if not directory.exists():
        raise FlowError(f"install directory is missing: {directory}; run: flow install {tool['id']} --force")

    entries = tool.get("entries", {})
    name = entry_name or tool.get("default_entry")
    if not name:
        print(directory)
        for key, value in entries.items():
            print(f"  {key}: python3 {value}")
        return 0
    if name not in entries:
        raise FlowError(f"{tool['id']} has no entry {name!r}; entries: {', '.join(entries)}")

    command = shlex.split(entries[name])
    target = directory / command[0]
    if not target.exists():
        raise FlowError(f"entry file not found: {target}")
    argv = [sys.executable, str(target), *command[1:]]
    print("running:", " ".join(shlex.quote(part) for part in argv))
    if dry_run:
        return 0
    return subprocess.run(argv, cwd=directory).returncode


def table(headers: list[str], rows: list[list[object]]) -> str:
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for row in cells:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    state = load_state()
    if args.json:
        payload = {
            "tools": tools,
            "installed": state["tools"],
        }
        print(json.dumps(payload, indent=2))
        return 0
    rows = []
    for tool in tools:
        record = state["tools"].get(tool["id"])
        rows.append(
            [
                tool["id"],
                tool["name"],
                tool["version"],
                tool["status"],
                "/".join(tool["platforms"]),
                record["version"] if record else "-",
            ]
        )
    print(table(["ID", "NAME", "VERSION", "STATUS", "PLATFORMS", "INSTALLED"], rows))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    tool = find_tool(tools, args.tool)
    if args.json:
        print(json.dumps(tool, indent=2))
        return 0
    record = load_state()["tools"].get(tool["id"])
    print(f"{tool['name']}  {tool['version']}  ({tool['status']})")
    print(tool["tagline"])
    print()
    print(f"publisher  {tool.get('publisher', 'Brlinkin')}")
    print(f"platforms  {', '.join(tool['platforms'])}")
    print(f"license    {tool['license']}")
    print(f"repo       {tool['repo']}")
    print(f"itch       {tool.get('itch', '-')}")
    print(f"docs       {tool['docs']}")
    print(f"problems   {tool['problems']}")
    print(f"installed  {record['version'] + ' at ' + record['path'] if record else 'no'}")
    print()
    print("channels")
    for key, entry in tool.get("install", {}).items():
        action = entry.get("command") or entry.get("url") or entry.get("label")
        status = "available" if site_build.is_available(entry) else "planned"
        print(f"  {entry.get('label', key):<20} {status:<10} {action}")
    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    if args.tool:
        tools = [find_tool(tools, args.tool)]
    if args.json:
        print(json.dumps({tool["id"]: tool.get("install", {}) for tool in tools}, indent=2))
        return 0
    rows = []
    for tool in tools:
        for key, entry in tool.get("install", {}).items():
            rows.append(
                [
                    tool["id"],
                    entry.get("label", key),
                    "available" if site_build.is_available(entry) else "planned",
                    entry.get("command") or entry.get("url") or "",
                ]
            )
    print(table(["TOOL", "CHANNEL", "STATUS", "ACTION"], rows))
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    tool = find_tool(tools, args.tool)
    channel = args.channel
    if channel == "auto":
        channel = "archive"
    return install_tool(
        tool,
        channel,
        force=args.force,
        dry_run=args.dry_run,
        run_command=args.run,
    )


def cmd_open(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    tool = find_tool(tools, args.tool)
    return open_tool(tool, args.entry, dry_run=args.dry_run)


def cmd_update(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    state = load_state()
    if args.all:
        targets = tools
    else:
        if not args.tool:
            raise FlowError("update needs a tool id or --all")
        targets = [find_tool(tools, args.tool)]

    status = 0
    for tool in targets:
        record = state["tools"].get(tool["id"])
        if not record:
            print(f"{tool['name']}: not installed")
            continue
        installed = record["version"]
        if version_key(tool["version"]) > version_key(installed):
            print(f"{tool['name']}: {installed} -> {tool['version']}")
            if args.yes:
                status |= install_tool(tool, "archive", force=True)
            else:
                print("  run again with --yes to install")
        else:
            print(f"{tool['name']}: {installed} is current with the manifest")

        if not args.offline:
            try:
                tag = latest_release(tool["repo"])
            except Exception as error:
                print(f"  upstream check failed: {error}")
                continue
            if tag and version_key(tag) > version_key(tool["version"]):
                print(f"  upstream {tag} is newer than the manifest; update tools/{tool['id']}.json")
            elif tag:
                print(f"  upstream tag: {tag}")
    return status


def cmd_doctor(args: argparse.Namespace) -> int:
    _, tools = load_tools()
    state = load_state()
    rows: list[list[str]] = []
    failures = 0

    def add(level: str, name: str, detail: str = "") -> None:
        nonlocal failures
        if level == "FAIL":
            failures += 1
        rows.append([level, name, detail])

    if sys.version_info >= (3, 10):
        add("OK", "python", sys.version.split()[0])
    else:
        add("FAIL", "python", "3.10 or newer required")

    try:
        probe_dir = data_dir()
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("OK", "data dir", str(probe_dir))
    except OSError as error:
        add("FAIL", "data dir", str(error))

    errors = site_build.validate(*load_tools(), ROOT)
    if errors:
        add("FAIL", "manifests", f"{len(errors)} problem(s); run python3 build.py --check")
    else:
        add("OK", "manifests", f"{len(tools)} tool(s) valid")

    for tool in tools:
        record = state["tools"].get(tool["id"])
        if not record:
            add("INFO", tool["id"], "not installed")
            continue
        directory = Path(record["path"])
        if not directory.exists():
            add("FAIL", tool["id"], f"missing install dir {directory}")
            continue
        missing = [
            name
            for name, entry in tool.get("entries", {}).items()
            if not (directory / shlex.split(entry)[0]).exists()
        ]
        if missing:
            add("FAIL", tool["id"], f"missing entries: {', '.join(missing)}")
        else:
            add("OK", tool["id"], f"{record['version']} at {directory}")

    if any(tool["id"] == "proton-picker" for tool in tools):
        steam = Path.home() / ".local" / "share" / "Steam"
        if steam.exists():
            add("OK", "steam", str(steam))
        else:
            add("INFO", "steam", "not found at the default path; Proton Picker needs desktop Steam")

    if not args.offline:
        try:
            latest_release(tools[0]["repo"]) if tools else None
            add("OK", "network", "api.github.com reachable")
        except Exception as error:
            add("WARN", "network", f"GitHub API check failed: {error}")

    print(table(["LEVEL", "CHECK", "DETAIL"], rows))
    return 1 if failures else 0


def cmd_build(args: argparse.Namespace) -> int:
    out = site_build.build(ROOT, ROOT / "site", resolve=args.resolve)
    print(f"built {out}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    print(data_dir())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow",
        description="Hub CLI for Brlinkin tools: list, install, update, open.",
    )
    parser.add_argument("--version", action="version", version=f"flow {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list", help="list tools in the suite")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    info = subparsers.add_parser("info", help="show one tool")
    info.add_argument("tool")
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=cmd_info)

    channels = subparsers.add_parser("channels", help="show install channels from the manifest")
    channels.add_argument("tool", nargs="?")
    channels.add_argument("--json", action="store_true")
    channels.set_defaults(func=cmd_channels)

    install = subparsers.add_parser("install", help="install a tool from an official channel")
    install.add_argument("tool")
    install.add_argument("--channel", default="auto", help="archive (default), aur, pipx, itch")
    install.add_argument("--force", action="store_true", help="replace an installed version")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--run", action="store_true", help="run the channel command when available")
    install.set_defaults(func=cmd_install)

    open_parser = subparsers.add_parser("open", help="run an installed tool entry point")
    open_parser.add_argument("tool")
    open_parser.add_argument("--entry", default=None)
    open_parser.add_argument("--dry-run", action="store_true")
    open_parser.set_defaults(func=cmd_open)

    update = subparsers.add_parser("update", help="compare installed tools with manifests and upstream")
    update.add_argument("tool", nargs="?")
    update.add_argument("--all", action="store_true")
    update.add_argument("--yes", action="store_true", help="install manifest updates")
    update.add_argument("--offline", action="store_true")
    update.set_defaults(func=cmd_update)

    doctor = subparsers.add_parser("doctor", help="check the local setup")
    doctor.add_argument("--offline", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    site = subparsers.add_parser("build", help="rebuild the catalog site")
    site.add_argument("--resolve", action="store_true", help="resolve latest GitHub releases")
    site.set_defaults(func=cmd_build)

    path = subparsers.add_parser("path", help="print the data directory")
    path.set_defaults(func=cmd_path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FlowError as error:
        print(f"flow: {error}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(f"flow: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
