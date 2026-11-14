"""blablalink MCP server: NIKKE account data from blablalink.com for Claude (or any MCP client).

Unofficial. Uses the same endpoints the blablalink website calls, authenticated with
your own blablalink session cookie. Treat that cookie like a password.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API = "https://api.blablalink.com"
CDN = "https://sg-tools-cdn.blablalink.com"
GAME_ID = "29080"
logging.getLogger("httpx").setLevel(logging.WARNING)  # NIKKE's intl game id on blablalink

DEFAULT_COMMON_PARAMS = {
    "game_id": "16",
    "area_id": "global",
    "source": "pc_web",
    "intl_game_id": GAME_ID,
    "env": "prod",
}

PLATES = {  # community boards: name -> (plate_id, unique id)
    "official": (43, "official"),
    "recommend": (46, "recommend"),
    "guides": (45, "guides"),
    "outpost": (38, "outpost"),
    "nikkeart": (39, "nikkeart"),
    "creatorhub": (40, "creatorhub"),
}

mcp = FastMCP(
    "blablalink",
    instructions=(
        "Tools for the user's own NIKKE: Goddess of Victory account via blablalink.com "
        "(roster, builds, outpost, union, union raid, blablalink daily check-in) plus public "
        "news and the event calendar. Character names come back in English and Traditional "
        "Chinese. Call get_roster before get_character_builds if you are unsure of a name."
    ),
)


# ---------------------------------------------------------------- credentials

def _config_dir() -> Path:
    return Path(os.environ.get("BLABLALINK_CONFIG_DIR", Path.home() / ".config" / "blablalink-mcp"))


def _parse_cookie_text(text: str) -> str:
    """Accept a raw Cookie header value, a 'cookie: ...' line, or a whole 'Copy as cURL' paste."""
    text = text.strip()
    for pat in (r"-b\s+'([^']+)'", r'-b\s+"([^"]+)"', r"-H\s+'[Cc]ookie:\s*([^']+)'",
                r'-H\s+"[Cc]ookie:\s*([^"]+)"', r"^[Cc]ookie:\s*(.+)$"):
        m = re.search(pat, text, re.M)
        if m:
            return m.group(1).strip()
    return text


def load_cookie() -> str:
    raw = os.environ.get("BLABLALINK_COOKIE", "")
    if not raw:
        f = Path(os.environ.get("BLABLALINK_COOKIE_FILE", _config_dir() / "cookie.txt"))
        if f.exists():
            raw = f.read_text(encoding="utf-8")
    return _parse_cookie_text(raw) if raw else ""


def cookie_value(cookie: str, name: str) -> str:
    m = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]*)", cookie)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- HTTP client

class BlablaError(RuntimeError):
    pass


class Blabla:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=20.0, http2=False)
        self._area_id: int | None = None

    @property
    def cookie(self) -> str:
        return load_cookie()

    def headers(self, lang: str = "zh-TW", auth: bool = True) -> dict[str, str]:
        common = dict(DEFAULT_COMMON_PARAMS, language=lang)
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.blablalink.com",
            "Referer": "https://www.blablalink.com/",
            "x-channel-type": "2",
            "x-language": lang,
            "x-common-params": json.dumps(common, separators=(",", ":")),
            "User-Agent": os.environ.get(
                "BLABLALINK_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            ),
        }
        if auth:
            c = self.cookie
            if not c:
                raise BlablaError(
                    "No blablalink login configured. Set BLABLALINK_COOKIE or put the Cookie "
                    f"header from an api.blablalink.com request into {_config_dir() / 'cookie.txt'}."
                )
            h["Cookie"] = c
        return h

    async def call(self, path: str, body: dict | None = None, *, method: str = "POST",
                   params: dict | None = None, auth: bool = True, lang: str = "zh-TW") -> Any:
        url = f"{API}{path}"
        for attempt in range(4):
            if method == "GET":
                r = await self.client.get(url, params=params, headers=self.headers(lang, auth))
            else:
                r = await self.client.post(url, params=params, json=body or {},
                                           headers=self.headers(lang, auth))
            try:
                j = r.json()
            except ValueError:
                raise BlablaError(f"{path}: HTTP {r.status_code}, non-JSON response")
            code = j.get("code")
            if code == 212000 and attempt < 3:  # "request too frequently"
                await asyncio.sleep(0.5 * 2 ** attempt)
                continue
            if code in (300001, 300004, 11002):
                raise BlablaError(
                    f"blablalink session is not logged in or expired ({j.get('msg')}). "
                    "Log in on www.blablalink.com again and refresh the cookie."
                )
            if code not in (0, "0"):
                raise BlablaError(f"{path}: code {code} {j.get('msg')}")
            return j.get("data")
        raise BlablaError(f"{path}: rate limited")

    # ---- identity
    @property
    def open_id(self) -> str:
        return cookie_value(self.cookie, "game_openid")

    async def area_id(self) -> int:
        if self._area_id is None:
            env = os.environ.get("BLABLALINK_AREA_ID")
            if env:
                self._area_id = int(env)
            else:
                d = await self.call("/api/ugc/direct/standalonesite/User/GetUserGamePlayerInfo", {})
                if not d or not d.get("area_id"):
                    raise BlablaError("Could not find your NIKKE server (area_id). Is a game role bound on blablalink?")
                self._area_id = int(d["area_id"])
        return self._area_id

    async def game(self, method: str, extra: dict | None = None) -> Any:
        body = {"intl_open_id": self.open_id, "nikke_area_id": await self.area_id()}
        body.update(extra or {})
        return await self.call(f"/api/game/proxy/Game/{method}", body)


bla = Blabla()


# ---------------------------------------------------------------- static data (CDN)

_PRIMES = (224737, 1000639, 2654435761, 2654435769, 1000621, 4294967291)


def _i32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def _djb(s: str, seed: int) -> int:
    h = seed
    for ch in s:
        h = _i32(h * 33 + ord(ch))
    return h


def cdn_path(path: str) -> str:
    """blablalink's CDN obfuscates file paths; this mirrors the site's own scheme."""
    path = path.lstrip("/")
    segs = [s for s in path.split("/") if s]
    out = []
    for i, seg in enumerate(segs):
        if i == len(segs) - 1:
            name, _, ext = seg.partition(".")
            out.append(f"{hashlib.md5(path.encode()).hexdigest()}.{ext or name}")
        else:
            h = _djb(path, _PRIMES[i]) % _PRIMES[i]
            out.append(f"{chr(97 + (h // 26) % 26)}{chr(97 + h % 26)}-{str(h % 99).zfill(2)}")
    return "/".join(out)


_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 6 * 3600


async def cdn_json(path: str) -> Any:
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    r = await bla.client.get(f"{CDN}/{cdn_path(path)}")
    if r.status_code != 200:
        raise BlablaError(f"CDN {path}: HTTP {r.status_code}")
    data = json.loads(r.content.decode("utf-8-sig"))
    _cache[path] = (time.time(), data)
    return data


async def characters() -> dict[int, dict]:
    """name_code -> static info with en + zh-TW names."""
    en, tw = await asyncio.gather(
        cdn_json("character/en/nikke_list_en_v2.json"),
        cdn_json("character/zh-TW/nikke_list_zh-TW_v2.json"),
    )
    tw_names = {c["name_code"]: c["name_localkey"]["name"] for c in tw}
    out = {}
    for c in en:
        nc = c["name_code"]
        el = (c.get("element_id") or {}).get("element") or {}
        shot = (c.get("shot_id") or {}).get("element") or {}
        out[nc] = {
            "name": c["name_localkey"]["name"],
            "name_zh": tw_names.get(nc, ""),
            "rarity": c.get("original_rare"),
            "class": c.get("class"),
            "burst": (c.get("use_burst_skill") or "").replace("Step", "") or None,
            "manufacturer": c.get("corporation"),
            "element": el.get("element"),
            "weapon": shot.get("weapon_type"),
            "resource_id": c.get("resource_id"),
        }
    return out


async def stage_names() -> dict[int, str]:
    rows = await cdn_json("stage/stage_list.json")
    out = {}
    for r in rows:
        name = (r.get("name_localkey") or {}).get("name", "")
        short = name.split()[0] if name else ""
        out[r["id"]] = f"{short} ({r.get('chapter_mod', '')})".strip()
    return out


async def option_names(lang: str = "en") -> dict[int, tuple[str, int]]:
    """state effect id -> (overload line name, tier 1-15)."""
    rows = await cdn_json(f"equip/equip_option_table_v2-{lang.lower()}.json")
    out = {}
    for r in rows:
        for i, sid in enumerate(r.get("state_effect_id_list") or []):
            out[sid] = (r.get("description_localkey", "").strip("【】"), i + 1)
    return out


async def item_name(kind: str, tid: int, lang: str = "en") -> str | None:
    if not tid:
        return None
    try:
        d = await cdn_json(f"equip/{lang.lower()}/{kind}_{tid}.json")
    except BlablaError:
        return None
    n = d.get("name_localkey")
    return n.get("name") if isinstance(n, dict) else n


def _norm(s: str) -> str:
    return re.sub(r"[\s:：·.\-_'’]", "", s).lower()


async def resolve_names(names: list[str]) -> tuple[list[int], list[str]]:
    chars = await characters()
    codes, missing = [], []
    for raw in names:
        q = _norm(raw)
        exact = [nc for nc, c in chars.items() if q in (_norm(c["name"]), _norm(c["name_zh"]))]
        partial = [nc for nc, c in chars.items() if q and (q in _norm(c["name"]) or q in _norm(c["name_zh"]))]
        hit = exact or partial
        if hit:
            codes.extend(hit if not exact else exact[:1])
        else:
            missing.append(raw)
    return list(dict.fromkeys(codes)), missing


def _ts(v: Any) -> str | None:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 10**11:  # milliseconds
        v //= 1000
    tz = ZoneInfo(os.environ.get("BLABLALINK_TZ", "Asia/Taipei"))
    return datetime.fromtimestamp(v, tz).strftime("%Y-%m-%d %H:%M")


def _strip_html(s: str, limit: int | None = None) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</div>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s).strip()
    return s[:limit] + "…" if limit and len(s) > limit else s


# ---------------------------------------------------------------- tools: account

@mcp.tool()
async def get_account_overview() -> dict:
    """The user's NIKKE commander profile: nickname, level, server, campaign progress,
    synchro device level, outpost level, roster counts and other headline stats."""
    basic, outpost, stages = await asyncio.gather(
        bla.game("GetUserProfileBasicInfo"),
        bla.game("GetUserProfileOutpostInfo"),
        stage_names(),
    )
    bi = dict((basic or {}).get("basic_info") or {})
    op = (outpost or {}).get("outpost_info") or {}
    for k in list(bi):
        if "campaign" in k and isinstance(bi[k], int):
            bi[k + "_name"] = stages.get(bi[k], str(bi[k]))
        if k.endswith("_time") or k.endswith("_at"):
            bi[k + "_local"] = _ts(bi[k])
    return {"area_id": await bla.area_id(), "basic_info": bi, "outpost_info": op}


@mcp.tool()
async def get_roster(filter: str = "", min_level: int = 0) -> dict:
    """List every Nikke the user owns with level, limit break (grade 0-3), core (+0..+7),
    combat power and static info (rarity, class, burst, element, weapon, manufacturer).

    filter: optional case-insensitive text matched against name/class/element/weapon/burst/
    manufacturer (e.g. "Fire", "SSR", "Pilgrim", "3").
    """
    data, chars = await asyncio.gather(bla.game("GetUserCharacters"), characters())
    rows = []
    for c in (data or {}).get("characters") or []:
        info = chars.get(c.get("name_code"), {"name": f"unknown #{c.get('name_code')}"})
        row = {
            "name": info.get("name"),
            "name_zh": info.get("name_zh"),
            "rarity": info.get("rarity"),
            "class": info.get("class"),
            "burst": info.get("burst"),
            "element": info.get("element"),
            "weapon": info.get("weapon"),
            "manufacturer": info.get("manufacturer"),
            "level": c.get("lv"),
            "limit_break": c.get("grade"),
            "core": c.get("core"),
            "combat_power": c.get("combat"),
            "name_code": c.get("name_code"),
        }
        if row["level"] is not None and row["level"] < min_level:
            continue
        if filter:
            f = filter.lower()
            hay = " ".join(str(row[k]) for k in ("name", "name_zh", "rarity", "class", "burst",
                                                  "element", "weapon", "manufacturer")).lower()
            if f not in hay:
                continue
        rows.append(row)
    rows.sort(key=lambda r: -(r["combat_power"] or 0))
    return {"count": len(rows), "nikkes": rows}


@mcp.tool()
async def get_character_builds(names: list[str], lang: str = "en") -> dict:
    """Detailed builds for specific Nikkes the user owns: skill levels, gear tiers,
    overload (OL) lines on each gear piece with values, harmony cube, favorite item/treasure,
    limit break/core, combat power.

    names: Nikke names in English or Chinese, e.g. ["Snow White: Heavy Arms", "紅蓮"].
    Up to ~10 per call. lang: "en" or "zh-TW" for gear-line and item names.
    """
    codes, missing = await resolve_names(names)
    if not codes:
        return {"error": "no matching Nikke names", "missing": missing}
    data, chars, opts = await asyncio.gather(
        bla.game("GetUserCharacterDetails", {"name_codes": codes[:15]}),
        characters(),
        option_names(lang),
    )
    effects = {e.get("id"): e for e in (data or {}).get("state_effects") or []}
    out = []
    for d in (data or {}).get("character_details") or []:
        info = chars.get(d.get("name_code"), {})
        gear = {}
        for slot in ("head", "torso", "arm", "leg"):
            lines = []
            for i in (1, 2, 3):
                sid = d.get(f"{slot}_equip_option{i}_id")
                if not sid:
                    continue
                name, tier = opts.get(sid, (f"option {sid}", None))
                vals = [round(abs(fd.get("function_value", 0)) / 100, 2)
                        for fd in (effects.get(sid) or {}).get("function_details") or []]
                lines.append({"line": name, "tier": tier, "value_pct": vals[0] if len(vals) == 1 else vals})
            gear[slot] = {
                "tier": d.get(f"{slot}_equip_tier"),
                "level": d.get(f"{slot}_equip_lv"),
                "corporation_bonus": d.get(f"{slot}_equip_corporation_type"),
                "overload_lines": lines,
            }
        cube_tid = d.get("harmony_cube_tid")
        fav_tid = d.get("favorite_item_tid")
        known = {"name_code", "lv", "grade", "core", "combat", "skill1_lv", "skill2_lv", "ulti_skill_lv",
                 "harmony_cube_tid", "harmony_cube_lv", "favorite_item_tid", "favorite_item_lv"}
        extra = {k: v for k, v in d.items() if k not in known
                 and not any(k.startswith(s + "_equip") for s in ("head", "torso", "arm", "leg"))}
        out.append({
            "name": info.get("name"),
            "name_zh": info.get("name_zh"),
            "level": d.get("lv"),
            "limit_break": d.get("grade"),
            "core": d.get("core"),
            "combat_power": d.get("combat"),
            "skills": {"skill1": d.get("skill1_lv"), "skill2": d.get("skill2_lv"), "burst": d.get("ulti_skill_lv")},
            "cube": {"name": await item_name("cube", cube_tid, lang), "id": cube_tid,
                     "level": d.get("harmony_cube_lv")} if cube_tid else None,
            "favorite_item": {"name": await item_name("favorite", fav_tid, lang), "id": fav_tid,
                              "level": d.get("favorite_item_lv")} if fav_tid else None,
            "gear": gear,
            "other_fields": extra,
        })
    return {"builds": out, "not_found": missing}


# ---------------------------------------------------------------- tools: union

@mcp.tool()
async def get_union(include_members: bool = True) -> dict:
    """The user's union (guild): name, level, rank and optionally the member list."""
    info = await bla.game("GetMyGuildInfo")
    result = {"guild": info}
    gid = None
    if isinstance(info, dict):
        g = info.get("guild_info") or info.get("guild") or info
        gid = g.get("guild_id") if isinstance(g, dict) else None
    if include_members and gid:
        try:
            result["members"] = await bla.game("GetGuildMembers", {"guild_id": gid})
        except BlablaError as e:
            result["members_error"] = str(e)
    return result


@mcp.tool()
async def get_union_raid(season: bool = False) -> dict:
    """Union Raid data for the user's union: current raid (default) or the whole season (season=True),
    including member attacks/damage where blablalink exposes it."""
    method = "GetUnionRaidDataOfGuildSeason" if season else "GetUnionRaidData"
    data = await bla.game(method)
    chars = await characters()

    def name_codes(o: Any) -> Any:  # attach readable names to any name_code fields
        if isinstance(o, dict):
            o = {k: name_codes(v) for k, v in o.items()}
            if isinstance(o.get("name_code"), int) and o["name_code"] in chars:
                o["nikke_name"] = chars[o["name_code"]]["name"]
            return o
        if isinstance(o, list):
            return [name_codes(x) for x in o]
        return o

    return name_codes(data)


# ---------------------------------------------------------------- tools: blablalink points

async def _tasks() -> list[dict]:
    d = await bla.call("/api/lip/proxy/lipass/Points/GetTaskListWithStatusV2", method="GET",
                       params={"get_top": "false", "intl_game_id": GAME_ID})
    return (d or {}).get("tasks") or []


@mcp.tool()
async def get_blablalink_tasks() -> dict:
    """blablalink community points balance and today's point tasks (daily check-in, likes,
    views, comments) with completion status."""
    tasks, pts = await asyncio.gather(
        _tasks(), bla.call("/api/lip/proxy/lipass/Points/GetUserTotalPoints", method="GET"))
    return {
        "total_points": (pts or {}).get("total_points"),
        "tasks": [{
            "task_id": t.get("task_id"),
            "name": t.get("task_name"),
            "points": [r.get("points") for r in t.get("reward_infos") or []],
            "completed": all(r.get("is_completed") for r in t.get("reward_infos") or []) if t.get("reward_infos") else None,
        } for t in tasks],
    }


@mcp.tool()
async def daily_checkin() -> dict:
    """Claim today's blablalink daily check-in points (does nothing if already claimed).
    This changes account state, so only call it when the user asks."""
    tasks = await _tasks()
    task = next((t for t in tasks if re.search(r"簽到|签到|check.?in|출석|ログイン|ボーナス",
                                              t.get("task_name") or "", re.I)), None)
    if not task:
        return {"ok": False, "reason": "check-in task not found", "tasks": [t.get("task_name") for t in tasks]}
    rewards = task.get("reward_infos") or []
    if rewards and all(r.get("is_completed") for r in rewards):
        return {"ok": True, "already_claimed": True, "task": task.get("task_name")}
    await bla.call("/api/lip/proxy/lipass/Points/DailyCheckIn", {"task_id": task.get("task_id")})
    pts = await bla.call("/api/lip/proxy/lipass/Points/GetUserTotalPoints", method="GET")
    return {"ok": True, "claimed": True, "task": task.get("task_name"),
            "total_points": (pts or {}).get("total_points")}


# ---------------------------------------------------------------- tools: public content

@mcp.tool()
async def get_news(board: str = "official", limit: int = 10, lang: str = "zh-TW") -> dict:
    """Latest posts from a blablalink board. board: official (announcements, events, patch
    notes), guides, recommend, outpost, nikkeart, creatorhub. lang: zh-TW, en, ja, ko."""
    if board not in PLATES:
        return {"error": f"unknown board; choose one of {list(PLATES)}"}
    pid, uid = PLATES[board]
    d = await bla.call("/api/ugc/direct/standalonesite/Dynamics/GetPostList", {
        "search_type": 0, "plate_id": pid, "plate_unique_id": uid,
        "nextPageCursor": "", "order_by": 1, "limit": str(max(1, min(limit, 30))),
    }, auth=bool(bla.cookie), lang=lang)
    return {"posts": [{
        "post_uuid": p.get("post_uuid"),
        "title": p.get("title"),
        "posted": _ts(p.get("created_on")),
        "preview": _strip_html(p.get("content", ""), 280),
        "url": f"https://www.blablalink.com/post?post_uuid={p.get('post_uuid')}",
    } for p in ((d or {}).get("list") or [])[:limit]]}


@mcp.tool()
async def read_post(post_uuid: str, lang: str = "zh-TW") -> dict:
    """Full text of one blablalink post (use post_uuid from get_news)."""
    d = await bla.call("/api/ugc/direct/standalonesite/Dynamics/GetPost", {"post_uuid": post_uuid},
                       auth=bool(bla.cookie), lang=lang)
    p = (d or {}).get("post") or d or {}
    return {"title": p.get("title"), "posted": _ts(p.get("created_on")),
            "text": _strip_html(p.get("content", ""), 12000),
            "url": f"https://www.blablalink.com/post?post_uuid={post_uuid}"}


@mcp.tool()
async def get_event_calendar(lang: str = "zh-TW") -> dict:
    """Current and upcoming NIKKE events, banners, arena and raid seasons from the blablalink
    activity calendar, with start/end times in Taiwan time (or BLABLALINK_TZ)."""
    d = await bla.call("/api/ugc/direct/standalonesite/Dynamics/GetCalendarDetail", {},
                       auth=bool(bla.cookie), lang=lang)

    def fix(o: Any) -> Any:
        if isinstance(o, dict):
            o = {k: fix(v) for k, v in o.items()}
            for k in list(o):
                if k.endswith("_time") and isinstance(o[k], (str, int)):
                    o[k] = _ts(o[k]) or o[k]
            return o
        if isinstance(o, list):
            return [fix(x) for x in o]
        return o

    return fix(d)


# ---------------------------------------------------------------- tools: diagnostics

@mcp.tool()
async def check_login() -> dict:
    """Check whether the configured blablalink login works and which NIKKE server it maps to."""
    if not bla.cookie:
        return {"logged_in": False, "reason": "no cookie configured"}
    try:
        await bla.call("/api/user/CheckLogin", {})
        area = await bla.area_id()
        return {"logged_in": True, "area_id": area, "game_uid": cookie_value(bla.cookie, "game_uid")}
    except BlablaError as e:
        return {"logged_in": False, "reason": str(e)}


@mcp.tool()
async def call_game_api(method: str, extra_body: dict | None = None) -> Any:
    """Escape hatch: call a read-only blablalink game proxy method (POST /api/game/proxy/Game/<method>)
    with your intl_open_id and server filled in. Only Get* methods are allowed."""
    if not re.fullmatch(r"Get[A-Za-z]+", method):
        return {"error": "only Get* methods are allowed"}
    return await bla.game(method, extra_body)


def main() -> None:
    ap = argparse.ArgumentParser(description="blablalink MCP server")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.http:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.host, mcp.settings.port = a.host, a.port
        # Host-header allowlist (DNS rebinding protection). Add your hostname / NAS IP here.
        hosts = [h.strip() for h in os.environ.get(
            "BLABLALINK_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",") if h.strip()]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts,
            allowed_origins=[f"https://{h.split(':')[0]}" for h in hosts] + ["https://claude.ai"])
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
