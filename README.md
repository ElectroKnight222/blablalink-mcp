# blablalink-mcp

Unofficial MCP server for your NIKKE account on blablalink.com: roster, builds (OL lines, cubes,
treasures), account overview, union / Union Raid, blablalink points & daily check-in, official
news and the event calendar.

It calls the same endpoints the blablalink website uses, with **your own login cookie**.
The cookie is a live session token: keep it only in Portainer's environment variables, never in
this repo.

Everything is in English by default (`BLABLALINK_LANG` to change). Tool shapes, decoded IDs and
how each field maps to blablalink's API are documented in [docs/tool-shapes.md](docs/tool-shapes.md).

Read-only, except two actions that only run when you ask: `daily_checkin` and `redeem_cdk`.

## Tools

| Tool | What it does |
|---|---|
| `check_login` | Is the cookie valid, which server, maintenance / ban flags |
| `get_account_overview` | Commander profile, progress, towers, Overclock, currencies, outpost research, profile team |
| `get_roster` | Every owned Nikke: level, LB, core, CP, class/burst/element/weapon (filterable) |
| `get_character_builds` | Skills, gear, overload lines (stat, tier, value, power), cubes, doll / treasure, bond |
| `get_clear_team` | The squad used to clear a campaign stage (or your latest Normal / Hard clear) |
| `get_daily_progress` | Mission points, arena / interception / tower attempts, outpost storage, dispatch |
| `get_union` | Union info and members |
| `get_union_raid` | Raid seasons, bosses and HP left, damage per member, every attack with its squad |
| `get_cdk_codes` / `redeem_cdk` | Current codes and whether you redeemed them; redeem one (action) |
| `get_blablalink_tasks` / `daily_checkin` | Points balance, today's tasks, claim check-in (action) |
| `get_news` / `read_post` | Official board (or guides etc.), full post text |
| `get_event_calendar` | Banners, events, arena/raid seasons with dates |
| `list_game_api_methods` | Live list of blablalink's read-only game methods |
| `call_game_api` | Call any of those `Get*` methods; IDs in the result get readable names |

## How it's built and deployed

- **Primary repo:** `git.engramm.net/ElectroKnight22/blablalink-mcp`. Changes land on `main` through reviewed PRs.
- **Build:** Forgejo push-mirrors the repo to `github.com/ElectroKnight222/blablalink-mcp`. GitHub Actions
  (`.github/workflows/build-image.yml`) builds the image from `main` only and publishes
  `ghcr.io/electroknight222/blablalink-mcp:latest` (plus a `sha-<commit>` tag).
- **Run:** Portainer → Stacks → Add stack → Web editor → paste `docker-compose.yml`.
  Environment variables:
  - `BLABLALINK_COOKIE`: the Cookie header from any `api.blablalink.com` request
    (log in at www.blablalink.com → F12 → Network → filter `api.blablalink` → copy `Cookie:`).
  - `BLABLALINK_ALLOWED_HOSTS` (optional): defaults to `10.22.10.2:*,localhost:*,127.0.0.1:*`.
  - `BLABLALINK_LANG` (optional): `en` (default), `ja`, `ko`, `zh-TW`.
- **Update** after a merge: wait for the GitHub build to finish, then open the stack →
  Update the stack with **Re-pull image** on.
- **Self-test:** container Console → `python -m blablalink_mcp.selftest` (prints only OK/FAIL and counts).

When the cookie expires (`check_login` says not logged in), update the env var and update the stack.

The container runs as non-root with a read-only filesystem and no Linux capabilities.

## Use it

- **LAN / VPN** (Claude Code, Gemini CLI): `http://<NAS IP>:8765/mcp` (streamable HTTP)
  - Claude Code: `claude mcp add --transport http blablalink http://<NAS IP>:8765/mcp`
  - Gemini CLI: `"mcpServers": {"blablalink": {"httpUrl": "http://<NAS IP>:8765/mcp"}}`
- **Claude connector** (`nikke.engramm.net`): tunnel route → `http://<NAS IP>:8765`, with a
  Cloudflare Access self-hosted app (only your email, Managed OAuth for MCP) in front.
  Don't publish the hostname without Access: anyone with the URL could read your account.

## Tests

```
pip install '.[test]'
pytest
```
Offline tests use made-up data shaped like blablalink's responses.

## Local stdio (no network)

```
pip install .
BLABLALINK_COOKIE='...' blablalink-mcp
```
