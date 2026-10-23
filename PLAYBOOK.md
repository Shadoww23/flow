# Playbook: add a tool to Flow

A tool is live in the hub when its manifest validates. The site, sitemap, install
table, and CLI all derive from that file.

## Checklist

1. Pick an id: lowercase, hyphenated, matches the repo name (`proton-picker`).
2. Create `tools/<id>.json` (copy an existing manifest).
3. Add assets under `assets/`:
   - `<id>-cover.png` — 630×500, used on the catalog and tool page.
   - `<id>-icon.png` — square, at least 256×256.
   - `<id>-screenshot.png` — optional, shown on the tool page.
4. Publish the release:
   - GitHub tag `v<version>` with an archive (`.tar.gz` and/or `.zip`) plus a
     `SHA256SUMS.txt` asset.
   - itch.io page at `brlinkin.itch.io/<id>` (if it is a Brlinkin tool).
   - For AUR, keep the PKGBUILD in the product repo; it must fetch the official
     release URL and verify the published checksum.
 5. Fill the manifest, including:
    - `install.archive` with the official URL, exact SHA-256, and format.
    - at least one extra channel (`aur`, `pipx`, `itch`) when one exists.
    - `available: false` on any channel that is not actually published yet; the
      public page hides it and `flow install` refuses it.
    - `entries` and, if useful, `default_entry` for `flow open`.
    - `before`, `steps`, and `faq` — the non-technical path. Plain language, no
      jargon, three steps maximum.
    - `limits` — the honest alpha constraints. Every tool page shows them.
6. Add the id to `suite.json` → `tools` in the order it should appear.
7. Verify:

```sh
python3 build.py --check
python3 -m unittest discover -s tests -v
python3 build.py
./flow.py list
./flow.py channels <id>
./flow.py install <id> --dry-run
./flow.py install <id>
./flow.py open <id> --dry-run
./flow.py doctor
```

8. Commit, push, let `pages.yml` deploy, then check the live tool page and the
   download/checksum links.

## Manifest fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Must match the file name and `suite.json`. |
| `name`, `publisher` | yes | Publisher is the product-family label (usually `Brlinkin`). |
| `tagline`, `description` | yes | Plain text; escaped at render time. |
| `category`, `audience` | yes | Category is a short tag list; audience is `players` or `teams`. |
| `platforms` | yes | `linux`, `windows`, `macos`, or `steamdeck`. |
| `status` | yes | `alpha`, `beta`, or `stable` — drives the pill. |
| `version` | yes | Must match the release tag without the leading `v`. |
| `license`, `license_url` | yes | Shown on the page and in the CLI. |
| `repo`, `releases`, `release_url` | yes | Official links only. |
| `itch`, `docs`, `problems` | yes | `problems` is the PROBLEMS.md link. |
| `requirements`, `limits` | yes | Lists; limits render as a callout. |
| `before` | yes | Plain-language prerequisites shown before setup. |
| `steps` | yes | Exactly the setup path: `title`, `text`; keep it to three. |
| `faq` | yes | `q`, `a` pairs; covers free, saves, platform, uninstall. |
| `install` | yes | Map of channel → entry. `archive` is required. Every entry needs `available: true/false`. |
| `downloads` | yes | Every published file; include `platforms` and `sha256` for archives. |
| `entries`, `default_entry` | no | Relative paths used by `flow open`. |
| `checks` | yes | Three to four outcome bullets. |
| `problems_cards` | yes | Search-by-problem cards: `title`, `text`, `url`. |
| `assets` | yes | `cover`, `icon`, optional `screenshot`; paths relative to the repo root. |
| `accent` | no | `teal` or `orange` for styling hooks. |

## Channel rules

- **Official archive only.** `install.archive.url` points at your GitHub release;
  `sha256` must match the published checksum. `flow install` refuses a mismatch.
- **OS-aware buttons** come from `downloads[].platforms`. The first archive whose
  platform matches the visitor is the primary download; otherwise the button
  falls back to the itch page.
- **No mirroring.** AUR/winget/Scoop-style entries contain a recipe and fetch the
  vendor archive. pipx entries point at your own wheel.
- **No Flathub, Nix caches, distro binary repos, or rebundled builds.** The
  proprietary freeware license forbids redistribution. If that changes, edit the
  product license first, then add the channel.
- **No hosted service required.** If a tool needs a server, a login, or
  telemetry, it does not belong in this suite without an explicit decision.

## When a version changes

1. Bump `version`, `release_url`, `install.*.url`, `downloads[].url`, and every
   `sha256` in `tools/<id>.json`.
2. Update the product repo's PKGBUILD checksum if it changed.
3. Run `python3 build.py --check`, then rebuild and deploy.
4. `flow update --all` reports drift between installed versions, the manifest,
   and the latest upstream tag.

## Design rules

- No build dependencies beyond the Python standard library.
- The generated site is static; no client-side JavaScript is required.
- Every external link is official: GitHub release, repo docs, itch page.
- Do not hide alpha limits. The limits block is part of the definition of done.
