"""blablalink MCP server: NIKKE account data from blablalink.com for Claude (or any MCP client).

Unofficial. Uses the same endpoints the blablalink website calls, authenticated with
your own blablalink session cookie. Treat that cookie like a password.

Design (see docs/tool-shapes.md):
- English by default (BLABLALINK_LANG to change).
- Curated tools define known fields explicitly; anything unmapped is kept in `other_fields`.
- Names come from blablalink's CDN tables at runtime; only small stable enums are hardcoded.
- Read-only, except actions approved one by one (daily check-in, CDK redemption).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP

API = "https://api.blablalink.com"
CDN = "https://sg-tools-cdn.blablalink.com"
GAME_ID = "29080"  # NIKKE's intl game id on blablalink
LANG = os.environ.get("BLABLALINK_LANG", "en")
logging.getLogger("httpx").setLevel(logging.WARNING)

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

# ---------------------------------------------------------------- stable enums (no table exists)

MANUFACTURERS = {1: "Elysion", 2: "Missilis", 3: "Tetra", 4: "Pilgrim", 7: "Abnormal"}
CLASSES = {1: "Attacker", 2: "Defender", 3: "Supporter"}
GEAR_SLOTS = ("head", "torso", "arm", "leg")
DOLL_WEAPONS = {1: "AR", 2: "MG", 3: "RL", 4: "SG", 5: "SMG", 6: "SR"}  # 5 inferred from the sequence
DOLL_RARITY = {1: "R", 2: "SR"}
CURRENCIES = {
    98: "Paid Gems", 99: "Free Gems", 1000: "Battle Data", 2000: "Credits", 3000: "Core Dust",
    5100: "Regular Recruit Vouchers", 5200: "Advanced Recruit Vouchers",
    11000: "Silver Mileage", 12000: "Gold Mileage",
}
RESEARCH = {
    1001: "General", 1101: "Attacker", 1102: "Defender", 1103: "Supporter",
    1201: "Elysion", 1202: "Missilis", 1203: "Tetra", 1204: "Pilgrim", 1205: "Abnormal",
}
BOSS_ELEMENTS = {"100001": "Water", "200001": "Wind", "300001": "Iron", "400001": "Fire", "500001": "Electric"}
LOST_RELICS = {"HandWriting": "memoirs", "CallLog": "call_logs", "Data": "data"}
STAGE_MODES = {"normal": "Normal", "hard": "Hard", "story": "Story"}

mcp = FastMCP(
    "blablalink",
    instructions=(
        "Tools for the user's own NIKKE: Goddess of Victory account via blablalink.com: roster, "
        "builds, campaign clear squads, daily progress, union and Union Raid (incl. squads used), "
        "CDK codes, blablalink points, plus public news and the event calendar. Everything is in "
        "English. Call get_roster first if you are unsure of a unit's name. For anything the "
        "curated tools do not cover, list_game_api_methods and call_game_api reach the rest of "
        "blablalink's read-only game API. daily_checkin and redeem_cdk change account state: "
        "only call them when the user asks."
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
    def __init__(self, msg: str, code: Any = None):
        super().__init__(msg)
        self.code = code


class Blabla:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=20.0, http2=False)
        self._player: dict | None = None
        self._area_id: int | None = None

    @property
    def cookie(self) -> str:
        return load_cookie()

    def headers(self, lang: str | None = None, auth: bool = True) -> dict[str, str]:
        lang = lang or LANG
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
                   params: dict | None = None, auth: bool = True, lang: str | None = None) -> Any:
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
                    "Log in on www.blablalink.com again and refresh the cookie.", code)
            if code not in (0, "0"):
                raise BlablaError(f"{path}: code {code} {j.get('msg')}", code)
            return j.get("data")
        raise BlablaError(f"{path}: rate limited")

    # ---- identity
    @property
    def open_id(self) -> str:
        return cookie_value(self.cookie, "game_openid")

    async def player(self) -> dict:
        if self._player is None:
            d = await self.call("/api/ugc/direct/standalonesite/User/GetUserGamePlayerInfo", {})
            self._player = d or {}
        return self._player

    async def area_id(self) -> int:
        if self._area_id is None:
            env = os.environ.get("BLABLALINK_AREA_ID")
            if env:
                self._area_id = int(env)
            else:
                p = await self.player()
                if not p.get("area_id"):
                    raise BlablaError("Could not find your NIKKE server (area_id). Is a game role bound on blablalink?")
                self._area_id = int(p["area_id"])
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


def _cdn_lang(lang: str | None) -> str:
    lang = (lang or LANG)
    return {"zh-tw": "zh-TW", "zh_tw": "zh-TW"}.get(lang.lower(), lang)


async def characters(lang: str | None = None) -> dict[int, dict]:
    """name_code -> static info."""
    lg = _cdn_lang(lang)
    try:
        rows = await cdn_json(f"character/{lg}/nikke_list_{lg}_v2.json")
    except BlablaError:
        rows = await cdn_json("character/en/nikke_list_en_v2.json")
    out = {}
    for c in rows:
        el = (c.get("element_id") or {}).get("element") or {}
        shot = (c.get("shot_id") or {}).get("element") or {}
        out[c["name_code"]] = {
            "name": c["name_localkey"]["name"],
            "rarity": c.get("original_rare"),
            "class": c.get("class"),
            "burst": (c.get("use_burst_skill") or "").replace("Step", "") or None,
            "manufacturer": (c.get("corporation") or "").title() or None,
            "element": el.get("element"),
            "weapon": shot.get("weapon_type"),
            "id": c.get("id"),
        }
    return out


def base_index(chars: dict[int, dict]) -> dict[int, int]:
    """squad tid // 100 -> name_code (CDN character id is base*100 + 1)."""
    return {c["id"] // 100: nc for nc, c in chars.items() if c.get("id")}


def decode_squad_tid(tid: int) -> tuple[int, int, int]:
    """Squad unit tid -> (base id, limit_break 0-3, core 0-7). Suffix 1-4 = LB0-3, 5-11 = core 1-7."""
    suffix = tid % 100
    return tid // 100, max(0, min(suffix - 1, 3)), max(0, suffix - 4)


def stage_short_name(name: str) -> str:
    """'40-35 STAGE' -> '40-35', '17-32 HARD STAGE' -> '17-32', 'SUB 22-1 STAGE' -> 'SUB 22-1'."""
    return " ".join(w for w in (name or "").split() if w.upper() not in ("STAGE", "HARD", "BOSS")).strip()


async def stages() -> dict[int, dict]:
    rows = await cdn_json("stage/stage_list.json")
    return {r["id"]: {"name": stage_short_name((r.get("name_localkey") or {}).get("name", "")),
                      "mode": r.get("chapter_mod"),
                      "standard_power": r.get("standard_battle_power")} for r in rows}


def stage_label(st: dict[int, dict], sid: Any) -> str | None:
    s = st.get(sid) if isinstance(sid, int) else None
    return f"{s['name']} ({s['mode']})" if s else None


async def option_names(lang: str | None = None) -> dict[int, tuple[str, int]]:
    """overload option id -> (line name, tier 1-15)."""
    lg = (lang or LANG).lower()
    try:
        rows = await cdn_json(f"equip/equip_option_table_v2-{lg}.json")
    except BlablaError:
        rows = await cdn_json("equip/equip_option_table_v2-en.json")
    out = {}
    for r in rows:
        for i, sid in enumerate(r.get("state_effect_id_list") or []):
            out[sid] = (r.get("description_localkey", "").strip("【】"), i + 1)
    return out


async def item_name(kind: str, tid: int, lang: str | None = None) -> str | None:
    if not tid:
        return None
    try:
        d = await cdn_json(f"equip/{(lang or LANG).lower()}/{kind}_{tid}.json")
    except BlablaError:
        if (lang or LANG).lower() == "en":
            return None
        return await item_name(kind, tid, "en")
    n = d.get("name_localkey")
    return n.get("name") if isinstance(n, dict) else n


def _norm(s: str) -> str:
    return re.sub(r"[\s:：·.\-_'’]", "", s or "").lower()


async def resolve_names(names: list[str]) -> tuple[list[int], list[str]]:
    chars = await characters()
    codes, missing = [], []
    for raw in names:
        q = _norm(raw)
        exact = [nc for nc, c in chars.items() if q == _norm(c["name"])]
        partial = [nc for nc, c in chars.items() if q and q in _norm(c["name"])]
        if exact:
            codes.append(exact[0])
        elif partial:
            codes.extend(partial)
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


def _iso_local(s: str | None) -> str | None:
    """blablalink season dates ('2026-10-29T20:00:00') are already local server time."""
    return s.replace("T", " ")[:16] if isinstance(s, str) and "T" in s else s


def _strip_html(s: str, limit: int | None = None) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</div>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s).strip()
    return s[:limit] + "…" if limit and len(s) > limit else s


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _rest(d: dict, used: set[str]) -> dict:
    return {k: v for k, v in d.items() if k not in used}


# ---------------------------------------------------------------- generic ID translation

TIME_KEYS = re.compile(r"(_at|_time|_ts|_on)$")


def translate(o: Any, chars: dict[int, dict], base: dict[int, int], st: dict[int, dict]) -> Any:
    """Add readable names next to IDs anywhere in a blablalink response (keeps the raw values)."""
    if isinstance(o, list):
        return [translate(x, chars, base, st) for x in o]
    if not isinstance(o, dict):
        return o
    out = {k: translate(v, chars, base, st) for k, v in o.items()}
    nc = out.get("name_code")
    if isinstance(nc, int) and nc in chars:
        out["nikke_name"] = chars[nc]["name"]
    tid = out.get("tid")
    if isinstance(tid, int) and ("combat" in out or "slot" in out):
        b, lb, core = decode_squad_tid(tid)
        if b in base:
            out["nikke_name"] = chars[base[b]]["name"]
            out["limit_break"], out["core"] = lb, core
    for k, v in list(out.items()):
        if (k == "stage_id" or (k.startswith("progress_") and k.endswith("_campaign"))):
            label = stage_label(st, _int(v))
            if label:
                out[k + "_name"] = label
        elif k == "element_id" and isinstance(v, list) and v and str(v[0]) in BOSS_ELEMENTS:
            out["element"] = BOSS_ELEMENTS[str(v[0])]
        elif TIME_KEYS.search(k) and isinstance(v, (str, int)) and not isinstance(v, bool):
            local = _ts(v)
            if local:
                out[k + "_local"] = local
    return out


async def translator():
    chars, st = await asyncio.gather(characters(), stages())
    base = base_index(chars)
    return lambda data: translate(data, chars, base, st)


def squad_entry(s: dict, chars: dict[int, dict], base: dict[int, int]) -> dict:
    b, lb, core = decode_squad_tid(int(s.get("tid") or 0))
    nc = base.get(b)
    return {"slot": s.get("slot"), "name": chars[nc]["name"] if nc else f"unknown #{s.get('tid')}",
            "limit_break": lb, "core": core, "level": s.get("lv"), "combat_power": s.get("combat")}


# ---------------------------------------------------------------- tools: account

@mcp.tool()
async def check_login() -> dict:
    """Check whether the configured blablalink login works, which NIKKE server it maps to, and
    whether the game is in maintenance."""
    if not bla.cookie:
        return {"logged_in": False, "reason": "no cookie configured"}
    try:
        await bla.call("/api/user/CheckLogin", {})
        bla._player = None  # refresh maintenance/ban flags
        p = await bla.player()
        return {"logged_in": True, "area_id": await bla.area_id(),
                "game_uid": cookie_value(bla.cookie, "game_uid"), "nickname": p.get("role_name"),
                "is_maintenance": p.get("is_maintenance"), "is_banned": p.get("is_banned")}
    except BlablaError as e:
        return {"logged_in": False, "reason": str(e)}


@mcp.tool()
async def get_account_overview() -> dict:
    """The user's NIKKE commander profile: level, power, campaign and tower progress, Overclock,
    profile team, currencies (gems, credits, vouchers...), Synchro Device, outpost research,
    Tactics Academy and lost relics."""
    basic, outpost, chars, st = await asyncio.gather(
        bla.game("GetUserProfileBasicInfo"), bla.game("GetUserProfileOutpostInfo"),
        characters(), stages())
    bi = dict((basic or {}).get("basic_info") or {})
    op = dict((outpost or {}).get("outpost_info") or {})

    def stage(k: str) -> str | None:
        s = st.get(_int(bi.get(k)) or 0)
        return s["name"] if s else bi.get(k)

    research = {}
    for r in op.get("recycle_room_researches") or []:
        research[RESEARCH.get(r.get("tid"), f"research {r.get('tid')}")] = r.get("lv")
    relics = {LOST_RELICS.get(m.get("category"), m.get("category")): m.get("count")
              for m in op.get("memorial_counts") or []}
    relics["bgm"] = op.get("jukebox_count")
    cls, lesson = _int(op.get("tactic_academy_class")), _int(op.get("tactic_academy_lesson"))

    used_b = {"role_name", "nickname", "lv", "team_combat", "character_count", "character_costume_count",
              "created_at", "last_action_at", "gsn", "is_maintenance", "is_banned", "profile_team",
              "progress_normal_campaign", "progress_hard_campaign", "progress_easy_campaign",
              "progress_tribe_tower", "progress_elysion_tower", "progress_missilis_tower",
              "progress_tetra_tower", "progress_pilgrim_tower",
              "sim_room_overclock_current_sub_season_high_score",
              "sim_room_overclock_latest_season_high_score", "sim_room_overclock_high_score_history",
              "corporation_character_counts", "currencies", "area_id"}
    used_o = {"synchro_level", "synchro_nonempty_slot_count", "outpost_battle_level", "infra_core_level",
              "recycle_room_researches", "tactic_academy_class", "tactic_academy_lesson",
              "memorial_counts", "jukebox_count"}
    return {
        "commander": {
            "nickname": bi.get("role_name") or bi.get("nickname"), "level": bi.get("lv"),
            "team_power": bi.get("team_combat"), "nikke_count": bi.get("character_count"),
            "costume_count": bi.get("character_costume_count"),
            "created": _ts(bi.get("created_at")), "last_active": _ts(bi.get("last_action_at")),
            "union_id": bi.get("gsn"), "server_area_id": _int(bi.get("area_id")),
            "is_maintenance": bool(bi.get("is_maintenance")), "is_banned": bool(bi.get("is_banned")),
        },
        "profile_team": [{"slot": t.get("slot"), "name": (chars.get(t.get("name_code")) or {}).get("name")}
                         for t in sorted(bi.get("profile_team") or [], key=lambda t: t.get("slot") or 0)
                         if t.get("name_code")],
        "campaign": {"normal": stage("progress_normal_campaign"), "hard": stage("progress_hard_campaign"),
                     "story": stage("progress_easy_campaign")},
        "towers": {k: bi.get(f"progress_{k}_tower") for k in ("tribe", "elysion", "missilis", "tetra", "pilgrim")},
        "overclock": {
            "current_sub_season_high": bi.get("sim_room_overclock_current_sub_season_high_score"),
            "latest_season_high": bi.get("sim_room_overclock_latest_season_high_score"),
            "history": [{"season": h.get("season"), "level": h.get("option_level"), "options": h.get("option_list")}
                        for h in bi.get("sim_room_overclock_high_score_history") or []],
        },
        "manufacturer_counts": {MANUFACTURERS.get(c.get("corporation_type"), str(c.get("corporation_type"))): c.get("count")
                                for c in bi.get("corporation_character_counts") or []},
        "currencies": {CURRENCIES.get(c.get("type"), f"currency {c.get('type')}"): _int(c.get("value"))
                       for c in bi.get("currencies") or []},
        "outpost": {
            "synchro_level": op.get("synchro_level"), "synchro_slots": op.get("synchro_nonempty_slot_count"),
            "outpost_level": op.get("outpost_battle_level"), "infra_core_level": op.get("infra_core_level"),
            "research": research,
            "tactics_academy": f"{cls // 1000}-{lesson % 1000}" if cls and lesson else None,
            "lost_relics": relics,
        },
        "other_fields": {**_rest(bi, used_b), **{f"outpost.{k}": v for k, v in _rest(op, used_o).items()}},
    }


@mcp.tool()
async def get_roster(filter: str = "", min_level: int = 0) -> dict:
    """List every Nikke the user owns with level (incl. Synchro Device), limit break (0-3),
    core (0-7), combat power and static info (rarity, class, burst, element, weapon, manufacturer).

    filter: optional case-insensitive text matched against name/class/element/weapon/burst/
    manufacturer/rarity (e.g. "Fire", "SSR", "Pilgrim", "3").
    """
    data, chars = await asyncio.gather(bla.game("GetUserCharacters"), characters())
    rows = []
    for c in (data or {}).get("characters") or []:
        info = chars.get(c.get("name_code"), {"name": f"unknown #{c.get('name_code')}"})
        row = {
            "name": info.get("name"),
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
            "costume_id": c.get("costume_id"),
            "name_code": c.get("name_code"),
        }
        if row["level"] is not None and row["level"] < min_level:
            continue
        if filter:
            hay = " ".join(str(row[k]) for k in ("name", "rarity", "class", "burst",
                                                  "element", "weapon", "manufacturer")).lower()
            if filter.lower() not in hay:
                continue
        rows.append(row)
    rows.sort(key=lambda r: -(r["combat_power"] or 0))
    return {"count": len(rows), "nikkes": rows}


def favorite_item(tid: int, lv: Any, name: str | None) -> tuple[str, dict] | None:
    """favorite_item_tid -> ('doll', {...}) or ('treasure', {...})."""
    if not tid:
        return None
    if tid // 100000 == 1:
        return "doll", {"name": name, "rarity": DOLL_RARITY.get(tid % 100),
                        "weapon": DOLL_WEAPONS.get((tid // 100) % 10), "level": lv, "id": tid}
    if tid // 100000 == 2:
        return "treasure", {"name": name, "phase": (lv or 0) + 1, "id": tid}
    return "favorite_item", {"name": name, "level": lv, "id": tid}


def overload_line(sid: int, effects: dict[str, dict], opts: dict[int, tuple[str, int]]) -> dict:
    name = opts.get(sid, (f"option {sid}", None))[0]
    fd = ((effects.get(str(sid)) or {}).get("function_details") or [{}])[0]
    val = fd.get("function_value")
    # The option id ends in the tier (7000814 = tier 14); the CDN option table's order does not.
    return {"line": name, "stat": fd.get("function_type"), "tier": fd.get("level") or sid % 100,
            "value_pct": round(abs(val) / 100, 2) if isinstance(val, (int, float)) else None,
            "power": fd.get("function_battlepower")}


BUILD_KNOWN = {"name_code", "lv", "grade", "core", "combat", "arena_combat", "skill1_lv", "skill2_lv",
               "ulti_skill_lv", "harmony_cube_tid", "harmony_cube_lv", "arena_harmony_cube_tid",
               "arena_harmony_cube_lv", "favorite_item_tid", "favorite_item_lv", "attractive_lv",
               "costume_tid"}


@mcp.tool()
async def get_character_builds(names: list[str], lang: str = LANG) -> dict:
    """Full loadouts for specific Nikkes the user owns: skill levels, gear (tier, level,
    manufacturer bonus, overload lines with stat, tier, value and power), harmony cube and arena
    cube, doll or treasure, bond level, limit break, core, level and combat power.

    names: Nikke names, e.g. ["Snow White: Heavy Arms", "Helm"]. Up to ~10 per call.
    """
    codes, missing = await resolve_names(names)
    if not codes:
        return {"error": "no matching Nikke names", "missing": missing}
    data, roster, chars, opts = await asyncio.gather(
        bla.game("GetUserCharacterDetails", {"name_codes": codes[:15]}),
        bla.game("GetUserCharacters"), characters(lang), option_names(lang))
    effects = {str(e.get("id")): e for e in (data or {}).get("state_effects") or []}
    levels = {c.get("name_code"): c.get("lv") for c in (roster or {}).get("characters") or []}
    out = []
    for d in (data or {}).get("character_details") or []:
        info = chars.get(d.get("name_code"), {})
        cube_tid, acube_tid, fav_tid = d.get("harmony_cube_tid"), d.get("arena_harmony_cube_tid"), d.get("favorite_item_tid")
        cube_n, acube_n, fav_n = await asyncio.gather(
            item_name("cube", cube_tid, lang), item_name("cube", acube_tid, lang), item_name("favorite", fav_tid, lang))
        gear, used = {}, set(BUILD_KNOWN)
        for slot in GEAR_SLOTS:
            keys = [f"{slot}_equip_{k}" for k in ("tid", "tier", "lv", "corporation_type",
                                                  "option1_id", "option2_id", "option3_id")]
            used.update(keys)
            corp = d.get(f"{slot}_equip_corporation_type")
            gear[slot] = {
                "item_id": d.get(f"{slot}_equip_tid"),
                "tier": d.get(f"{slot}_equip_tier"),
                "level": d.get(f"{slot}_equip_lv"),
                "manufacturer_bonus": MANUFACTURERS.get(corp) if corp else None,
                "overload_lines": [overload_line(sid, effects, opts) for sid in
                                   (d.get(f"{slot}_equip_option{i}_id") for i in (1, 2, 3)) if sid],
            }
        build = {
            "name": info.get("name"), "name_code": d.get("name_code"),
            **{k: info.get(k) for k in ("class", "element", "burst", "weapon", "manufacturer", "rarity")},
            "level": levels.get(d.get("name_code"), d.get("lv")), "base_level": d.get("lv"),
            "limit_break": d.get("grade"), "core": d.get("core"),
            "combat_power": d.get("combat"), "arena_combat_power": d.get("arena_combat"),
            "bond_level": d.get("attractive_lv"), "costume_id": d.get("costume_tid"),
            "skills": {"skill1": d.get("skill1_lv"), "skill2": d.get("skill2_lv"), "burst": d.get("ulti_skill_lv")},
            "cube": {"name": cube_n, "level": d.get("harmony_cube_lv"), "id": cube_tid} if cube_tid else None,
            "arena_cube": {"name": acube_n, "level": d.get("arena_harmony_cube_lv"), "id": acube_tid} if acube_tid else None,
        }
        fav = favorite_item(fav_tid, d.get("favorite_item_lv"), fav_n)
        if fav:
            build[fav[0]] = fav[1]
        build["gear"] = gear
        build["other_fields"] = _rest(d, used)
        out.append(build)
    return {"builds": out, "not_found": missing}


# ---------------------------------------------------------------- tools: union

async def _my_union() -> dict:
    info = await bla.game("GetMyGuildInfo")
    return (info or {}).get("card") or {}


@mcp.tool()
async def get_union(include_members: bool = True) -> dict:
    """The user's union: name, level, rank, ranking, activity and optionally the member list
    (nickname, commander level, synchro level)."""
    card = await _my_union()
    if not card.get("guild_id"):
        return {"union": None, "reason": "not in a union"}
    used = {"guild_id", "guild_name", "guild_level", "guild_rank", "ranking", "ranking_percent",
            "guild_activity", "damage_rank", "guild_member_cnt", "guild_member_max_cnt", "guild_locale",
            "guild_description", "guild_entry_level", "guild_join_type"}
    result: dict[str, Any] = {"union": {
        "id": card.get("guild_id"), "name": card.get("guild_name"), "level": card.get("guild_level"),
        "rank": card.get("guild_rank"), "ranking": card.get("ranking"),
        "top_percent": round(card["ranking_percent"] * 100, 2) if isinstance(card.get("ranking_percent"), (int, float)) else None,
        "activity": card.get("guild_activity"), "damage_rank": card.get("damage_rank"),
        "members": card.get("guild_member_cnt"), "max_members": card.get("guild_member_max_cnt"),
        "locale": card.get("guild_locale"), "description": card.get("guild_description"),
        "entry_level": card.get("guild_entry_level"), "join_type": card.get("guild_join_type"),
        "other_fields": _rest(card, used | {"intl_open_id", "nikke_area_id"}),
    }}
    if include_members:
        try:
            m = await bla.game("GetGuildMembers", {"guild_id": card["guild_id"]})
            me = bla.open_id
            result["members"] = sorted(({"nickname": i.get("nickname"), "level": i.get("level"),
                                         "synchro_level": i.get("synchro_level"), "is_me": i.get("member_id") == me}
                                        for i in (m or {}).get("items") or []), key=lambda x: -(x["level"] or 0))
        except BlablaError as e:
            result["members_error"] = str(e)
    return result


@mcp.tool()
async def get_union_raid(season: bool = False) -> dict:
    """Union Raid data for the user's union: current raid (default) or the whole season (season=True)."""
    method = "GetUnionRaidDataOfGuildSeason" if season else "GetUnionRaidData"
    return (await translator())(await bla.game(method))


# ---------------------------------------------------------------- tools: blablalink points

async def _tasks() -> list[dict]:
    d = await bla.call("/api/lip/proxy/lipass/Points/GetTaskListWithStatusV2", method="GET",
                       params={"get_top": "false", "intl_game_id": GAME_ID})
    return (d or {}).get("tasks") or []


@mcp.tool()
async def get_blablalink_tasks() -> dict:
    """blablalink community points (balance, earned, spent), whether today's check-in is done,
    and today's point tasks with progress and completion."""
    tasks, pts = await asyncio.gather(
        _tasks(), bla.call("/api/lip/proxy/lipass/Points/GetUserTotalPoints", method="GET"))
    rows = []
    for t in tasks:
        ri = t.get("reward_infos") or []
        rows.append({
            "task_id": t.get("task_id"), "name": t.get("task_name"), "details": t.get("details") or None,
            "points": [r.get("points") for r in ri],
            "completed": all(r.get("is_completed") for r in ri) if ri else None,
            "progress": [f"{r.get('completed_times', 0)}/{r.get('need_completed_times', 0)}" for r in ri],
        })
    return {
        "total_points": (pts or {}).get("total_points"),
        "earned": (pts or {}).get("total_points_earned"),
        "spent": (pts or {}).get("total_points_consumed"),
        "checked_in_today": any(t.get("sign_today") for t in tasks),
        "tasks": rows,
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

def _post_stats(p: dict) -> dict:
    return {"views": _int(p.get("browse_count")), "likes": _int(p.get("upvote_count")),
            "comments": _int(p.get("comment_count"))}


@mcp.tool()
async def get_news(board: str = "official", limit: int = 10, lang: str = LANG, cursor: str = "") -> dict:
    """Latest posts from a blablalink board. board: official (announcements, events, patch
    notes, codes), guides, recommend, outpost, nikkeart, creatorhub. lang: en, ja, ko, zh-TW.
    Pass the returned next_cursor as cursor to get older posts."""
    if board not in PLATES:
        return {"error": f"unknown board; choose one of {list(PLATES)}"}
    pid, uid = PLATES[board]
    d = await bla.call("/api/ugc/direct/standalonesite/Dynamics/GetPostList", {
        "search_type": 0, "plate_id": pid, "plate_unique_id": uid,
        "nextPageCursor": cursor, "order_by": 1, "limit": str(max(1, min(limit, 30))),
    }, auth=bool(bla.cookie), lang=lang)
    pi = (d or {}).get("page_info") or {}
    return {
        "posts": [{
            "post_uuid": p.get("post_uuid"),
            "title": p.get("title"),
            "posted": _ts(p.get("created_on")),
            "preview": _strip_html(p.get("content", ""), 280),
            **_post_stats(p),
            "pinned": bool(p.get("is_top")),
            "url": f"https://www.blablalink.com/post?post_uuid={p.get('post_uuid')}",
        } for p in ((d or {}).get("list") or [])[:limit]],
        "next_cursor": None if pi.get("is_finish") else pi.get("next_page_cursor"),
    }


@mcp.tool()
async def read_post(post_uuid: str, lang: str = LANG) -> dict:
    """Full text of one blablalink post (use post_uuid from get_news)."""
    d = await bla.call("/api/ugc/direct/standalonesite/Dynamics/GetPost", {"post_uuid": post_uuid},
                       auth=bool(bla.cookie), lang=lang)
    p = (d or {}).get("post") or d or {}
    return {"title": p.get("title"), "posted": _ts(p.get("created_on")),
            "text": _strip_html(p.get("content", ""), 12000), **_post_stats(p),
            "url": f"https://www.blablalink.com/post?post_uuid={post_uuid}"}


CALENDAR_DROP = {"module_id", "subscribe_status", "module_type", "resource_id", "skin_index", "banner"}


@mcp.tool()
async def get_event_calendar(lang: str = LANG) -> dict:
    """Current and upcoming NIKKE events, banners (with featured units), costume gachas, arena
    and raid seasons and Simulation Room seasons from the blablalink activity calendar, with
    start/end times in Taiwan time (or BLABLALINK_TZ)."""
    d = await bla.call("/api/ugc/direct/standalonesite/Dynamics/GetCalendarDetail", {},
                       auth=bool(bla.cookie), lang=lang)

    def fix(o: Any) -> Any:
        if isinstance(o, dict):
            o = {k: fix(v) for k, v in o.items() if k not in CALENDAR_DROP}
            for k in list(o):
                if k.endswith("_time") and isinstance(o[k], (str, int)):
                    o[k] = _ts(o[k]) or o[k]
            return o
        if isinstance(o, list):
            return [fix(x) for x in o]
        return o

    return fix(d)


# ---------------------------------------------------------------- tools: raw access

@mcp.tool()
async def call_game_api(method: str, extra_body: dict | None = None) -> Any:
    """Escape hatch: call a read-only blablalink game method (POST /api/game/proxy/Game/<method>)
    with your intl_open_id and server filled in. Only Get* methods are allowed."""
    if not re.fullmatch(r"Get[A-Za-z0-9]+", method):
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
