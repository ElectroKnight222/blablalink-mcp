# blablalink-mcp

Unofficial MCP server for your NIKKE account on blablalink.com: roster, builds (OL lines, cubes,
treasures), account overview, union / Union Raid, blablalink points & daily check-in, official
news and the event calendar.

It calls the same endpoints the blablalink website uses, with **your own login cookie**.
The cookie is a live session token: keep it only in Portainer's environment variables, never in
this repo.

> Status: news, posts, calendar and name lookup are tested. Account tools (roster, builds,
> overview, union, points/check-in) are written but not yet tested against a real account.

## Tools

| Tool | What it does |
|---|---|
| `check_login` | Is the cookie valid, which server (area_id) |
| `get_account_overview` | Nickname, level, campaign progress, synchro / outpost level |
| `get_roster` | Every owned Nikke: level, LB, core, CP, class/burst/element/weapon (filterable) |
| `get_character_builds` | Skills, gear tiers, overload lines + values, cube, treasure |
| `get_union` / `get_union_raid` | Union info, members, current raid or season |
| `get_blablalink_tasks` / `daily_checkin` | Points balance, today's tasks, claim check-in |
| `get_news` / `read_post` | Official board (or guides etc.), full post text |
| `get_event_calendar` | Banners, events, arena/raid seasons with dates |
| `call_game_api` | Escape hatch for other read-only `Get*` game methods |

## Deploy with Portainer (from this repo)

1. Portainer → Stacks → Add stack → **Repository**.
   - Repository URL: `http://<NAS IP>:3300/ElectroKnight22/blablalink-mcp.git`
   - Reference: `refs/heads/main`, compose path: `docker-compose.yml`
   - Authentication: your Forgejo username + an access token with repository read access.
2. Environment variables:
   - `BLABLALINK_COOKIE`: the Cookie header from any `api.blablalink.com` request
     (log in at www.blablalink.com → F12 → Network → filter `api.blablalink` → copy `Cookie:`).
   - `BLABLALINK_ALLOWED_HOSTS`: `nikke.engramm.net,<NAS IP>:*,localhost:*,127.0.0.1:*`
3. Deploy, then check from the container's console (or SSH):
   ```
   docker exec blablalink-mcp python -m blablalink_mcp.selftest
   ```
   It prints only OK/FAIL and counts, no personal IDs.

To update after merging a PR: open the stack → **Pull and redeploy**.
When the cookie expires (`check_login` says not logged in), update the env var and redeploy.

The container runs as non-root with a read-only filesystem and no Linux capabilities.

## Use it

- **LAN / VPN** (Claude Code, Gemini CLI): `http://<NAS IP>:8765/mcp` (streamable HTTP)
  - Claude Code: `claude mcp add --transport http blablalink http://<NAS IP>:8765/mcp`
  - Gemini CLI: `"mcpServers": {"blablalink": {"httpUrl": "http://<NAS IP>:8765/mcp"}}`
- **Claude connector** (`nikke.engramm.net`): tunnel route → `http://<NAS IP>:8765`, with a
  Cloudflare Access self-hosted app (only your email, Managed OAuth for MCP) in front.
  Don't publish the hostname without Access: anyone with the URL could read your account.

## Local stdio (no network)

```
pip install .
BLABLALINK_COOKIE='...' blablalink-mcp
```
