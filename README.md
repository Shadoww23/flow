# Flow

Flow is the hub for the **Brlinkin** suite of local-first tools for Linux gaming
and game releases. Brlinkin stays the label on the tools; Flow is the place that
connects them.

**Live site:** <https://shadoww23.github.io/flow/>

One manifest per tool drives everything: the catalog, the per-tool pages, the
install channels, the sitemap, and the `flow` CLI. Adding a tool means adding one
JSON file and its assets, not editing the site.

## Tools

- [Proton Picker](https://github.com/Shadoww23/proton-picker) — routes each Steam game to a pinned, recommended, or newest official Proton build.
- [ReleaseGuard](https://github.com/Shadoww23/releaseguard) — release-readiness checks for game builds, including save-corruption scenarios and packaged-build review.

## Status

- Phase 1: public web hub, non-technical copy, OS-aware downloads, honest alpha limits. Live.
- Next: Decky plugin for Steam Deck, then one-click desktop installers
  (`.deb`/`.rpm`/AppImage) and a Windows `.exe` for ReleaseGuard.
- License: no redistribution, so no Flathub. Every installer or plugin downloads
  the official archive at runtime; nothing is mirrored or patched.

## Layout

```
suite.json              suite metadata, URL, tool order
tools/<id>.json         one manifest per tool
assets/                 covers, icons, screenshots, favicon, os.js
style.css               shared styling
build.py                manifest-driven site generator (stdlib only)
flow.py                 hub CLI (stdlib only)
site/                   generated output (gitignored)
tests/                  build + CLI tests
PLAYBOOK.md             how to add the next tool
```

## Use it

```sh
cd flow
python3 build.py --check     # validate every manifest and asset
python3 build.py             # generate site/
python3 -m http.server -d site 8000
```

```sh
./flow.py list               # suite overview and install state
./flow.py info proton-picker # links, channels, installed version
./flow.py channels           # every install recipe, with availability
./flow.py install releaseguard
./flow.py open releaseguard --dry-run
./flow.py update --all
./flow.py doctor             # local setup checks
```

`flow install` downloads the unmodified archive from the official GitHub
release, verifies the manifest SHA-256, and installs under
`$XDG_DATA_HOME/flow/tools/<id>/<version>` (default
`~/.local/share/flow/tools`). It never redistributes or patches a build.
Channels marked `"available": false` (AUR until published) are refused.

To put `flow` on your PATH:

```sh
chmod +x flow.py build.py
ln -s "$PWD/flow.py" ~/.local/bin/flow
```

The CLI is currently repo-based. A packaged `flow-hub` install (pipx/uvx) lands
with Phase 2.

## Install channels

Channel rules come from `../brlinkin-ops/DISTRIBUTION.md`:

- Direct archives come from the official GitHub release and are checksum-verified.
- AUR recipes fetch that same archive; they do not mirror it. They are planned,
  not published yet.
- pipx installs your own published wheel.
- itch.io pages are direct downloads from Brlinkin.
- A Decky plugin will download from the official release rather than bundle it.

The license does **not** allow Flathub, binary caches, distro mirrors, or
rebundling. Do not add a channel that hosts or patches the binaries.

## Publishing

The site is static and builds with no dependencies. The repo deploys to GitHub
Pages with `.github/workflows/pages.yml` (build type: GitHub Actions). If the
repo or URL changes, update `url` in `suite.json` and the Pages setting together.

`ci.yml` validates manifests, runs the tests, and builds the site on every push
and pull request.

## Add a tool

Read [PLAYBOOK.md](PLAYBOOK.md).

## License

MIT for the hub code — see [LICENSE](LICENSE). Tool binaries and Brlinkin cover
art are excluded; see [NOTICE](NOTICE).
