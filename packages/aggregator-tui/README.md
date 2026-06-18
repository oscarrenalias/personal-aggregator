# aggregator-tui

A terminal (TUI) reader client for **personal-aggregator**, built with
[Textual](https://textual.textualize.io/). It talks only to the aggregator's
JSON API (`/api/v1`) over HTTP — it is a pure client with no database access.

Three-pane layout (nav sidebar · article list · reader) mirroring the web UI,
with the same keyboard model.

## Install

Requires Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

**From a GitHub release** (each release ships a wheel):

```bash
# download aggregator_tui-<version>-py3-none-any.whl from the release, then:
uv tool install ./aggregator_tui-<version>-py3-none-any.whl
```

**From source** (latest `main`):

```bash
uv tool install "git+https://github.com/oscarrenalias/personal-aggregator.git#subdirectory=packages/aggregator-tui"
```

**From a local checkout:**

```bash
uv tool install ./packages/aggregator-tui
```

This installs the `aggregator-tui` command. (To run without installing, from the
repo: `uv run --package aggregator-tui aggregator-tui`.)

## Usage

```bash
aggregator-tui                      # uses http://localhost:8000/api/v1
aggregator-tui --api-url http://raspberrypi.local:8000/api/v1
AGGREGATOR_API_URL=http://host:8000/api/v1 aggregator-tui
```

Resolution order for the API base URL: `--api-url` flag → `AGGREGATOR_API_URL`
env var → default `http://localhost:8000/api/v1`.

> The aggregator API has no authentication yet, so point the TUI at an instance
> reachable only over your trusted network (Tailscale / localhost).

## Keys

| Key | Action |
|---|---|
| `j` / `k` | next / previous in the list |
| `g` / `G` | top / bottom |
| `Enter` / `o` | open the selected article/thread in the reader |
| `n` | mark read and open the next |
| `m` | toggle read / unread |
| `s` | save / unsave |
| `v` | open the article's source URL in the browser |
| `u` | toggle the read filter (Show all / Unread) |
| `r` | toggle thread sort (Importance / Recent) |
| `R` | refresh (sidebar + current list) |
| `d` | dismiss / restore a thread (threads view) |
| `/` | search |
| `Tab` | cycle panes (in the reader, focuses a thread's member list) |
| `←` | from a member-opened article, back to its thread |
| `?` | keyboard help · `q` quit |

The nav sidebar also auto-refreshes every 60s.
