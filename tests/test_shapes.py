"""Offline tests with made-up data shaped like blablalink's responses (no real account data)."""
import asyncio

import pytest

from blablalink_mcp import server as s

CHARS = {
    9001: {"name": "Test Attacker", "rarity": "SSR", "class": "Attacker", "burst": "3", "manufacturer": "Pilgrim",
           "element": "Fire", "weapon": "AR", "id": 900101},
    9002: {"name": "Test Defender", "rarity": "SSR", "class": "Defender", "burst": "1", "manufacturer": "Tetra",
           "element": "Water", "weapon": "MG", "id": 900201},
}
STAGES = {6001002: {"name": "1-2", "mode": "Normal", "standard_power": 1000},
          7001001: {"name": "1-1", "mode": "Hard", "standard_power": 2000}}
OPTS = {7000814: ("Increased ATK", 14), 7001008: ("Increased Charge Speed", 8)}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake(monkeypatch):
    calls = []
    responses = {}

    async def game(method, extra=None):
        calls.append((method, extra))
        r = responses[method]
        return r(extra) if callable(r) else r

    async def chars(lang=None):
        return CHARS

    async def stages():
        return STAGES

    async def opts(lang=None):
        return OPTS

    async def item(kind, tid, lang=None):
        return f"{kind}-{tid}" if tid else None

    monkeypatch.setattr(s.bla, "game", game)
    monkeypatch.setattr(s, "characters", chars)
    monkeypatch.setattr(s, "stages", stages)
    monkeypatch.setattr(s, "option_names", opts)
    monkeypatch.setattr(s, "item_name", item)
    monkeypatch.setattr(s, "load_cookie", lambda: "game_openid=111; game_uid=222")
    return responses, calls


# ---- pure helpers

@pytest.mark.parametrize("tid,expected", [
    (900101, (9001, 0, 0)), (900104, (9001, 3, 0)), (900105, (9001, 3, 1)), (900111, (9001, 3, 7)),
])
def test_decode_squad_tid(tid, expected):
    assert s.decode_squad_tid(tid) == expected


@pytest.mark.parametrize("raw,short", [
    ("40-35 STAGE", "40-35"), ("17-32 HARD STAGE", "17-32"), ("SUB 22-1 STAGE", "SUB 22-1"), ("40-36 BOSS", "40-36"),
])
def test_stage_short_name(raw, short):
    assert s.stage_short_name(raw) == short


def test_favorite_item_doll_and_treasure():
    kind, doll = s.favorite_item(100602, 15, "Some Doll")
    assert kind == "doll" and doll["rarity"] == "SR" and doll["weapon"] == "SR" and doll["level"] == 15
    kind, doll = s.favorite_item(100101, 5, None)
    assert kind == "doll" and doll["rarity"] == "R" and doll["weapon"] == "AR"
    kind, tr = s.favorite_item(200701, 2, "Some Treasure")
    assert kind == "treasure" and tr["phase"] == 3
    assert s.favorite_item(0, 0, None) is None


def test_overload_line_matches_string_effect_ids():
    effects = {"7000814": {"function_details": [{"function_type": "StatAtk", "function_value": 1393,
                                                 "level": 14, "function_battlepower": 966}]},
               "7001008": {"function_details": [{"function_type": "StatChargeTime", "function_value": -404,
                                                 "level": 8, "function_battlepower": 552}]}}
    atk = s.overload_line(7000814, effects, OPTS)
    assert atk == {"line": "Increased ATK", "stat": "StatAtk", "tier": 14, "value_pct": 13.93, "power": 966}
    assert s.overload_line(7001008, effects, OPTS)["value_pct"] == 4.04  # negative charge time shown positive
    assert s.overload_line(7000814, {}, OPTS)["tier"] == 14  # tier from the id when effect data is missing


def test_translate_adds_names_without_removing_raw():
    base = s.base_index(CHARS)
    data = {"name_code": 9001, "squad": [{"slot": 1, "tid": 900206, "combat": 5, "lv": 1}],
            "stage_id": 6001002, "element_id": ["400001"], "created_at": "1794733200"}
    out = s.translate(data, CHARS, base, STAGES)
    assert out["nikke_name"] == "Test Attacker" and out["name_code"] == 9001
    sq = out["squad"][0]
    assert sq["nikke_name"] == "Test Defender" and sq["limit_break"] == 3 and sq["core"] == 2 and sq["tid"] == 900206
    assert out["stage_id_name"] == "1-2 (Normal)"
    assert out["element"] == "Fire"
    assert out["created_at_local"]


# ---- tools with fake API responses

def test_character_builds_shape(fake):
    responses, _ = fake
    detail = {"name_code": 9001, "lv": 1, "grade": 3, "core": 2, "combat": 50000, "arena_combat": 49000,
              "skill1_lv": 10, "skill2_lv": 7, "ulti_skill_lv": 10, "harmony_cube_tid": 1000303,
              "harmony_cube_lv": 9, "arena_harmony_cube_tid": 1000304, "arena_harmony_cube_lv": 5,
              "favorite_item_tid": 200701, "favorite_item_lv": 2, "attractive_lv": 30, "costume_tid": 0,
              "brand_new_field": 1}
    for slot in s.GEAR_SLOTS:
        detail.update({f"{slot}_equip_tid": 3111001, f"{slot}_equip_tier": 10, f"{slot}_equip_lv": 5,
                       f"{slot}_equip_corporation_type": 0, f"{slot}_equip_option1_id": 7000814,
                       f"{slot}_equip_option2_id": 0, f"{slot}_equip_option3_id": 0})
    responses["GetUserCharacterDetails"] = {"character_details": [detail], "state_effects": [
        {"id": "7000814", "function_details": [{"function_type": "StatAtk", "function_value": 1393,
                                                "level": 14, "function_battlepower": 966}]}]}
    responses["GetUserCharacters"] = {"characters": [{"name_code": 9001, "lv": 310}]}
    b = run(s.get_character_builds(["test attacker"]))["builds"][0]
    assert b["name"] == "Test Attacker" and b["level"] == 310 and b["base_level"] == 1
    assert b["treasure"] == {"name": "favorite-200701", "phase": 3, "id": 200701} and "doll" not in b
    assert b["arena_cube"]["name"] == "cube-1000304"
    assert b["gear"]["head"]["overload_lines"][0]["value_pct"] == 13.93
    assert b["gear"]["head"]["manufacturer_bonus"] is None
    assert b["other_fields"] == {"brand_new_field": 1}


def test_overview_shape(fake):
    responses, _ = fake
    responses["GetUserProfileBasicInfo"] = {"basic_info": {
        "role_name": "Tester", "lv": 100, "team_combat": 1, "progress_normal_campaign": 6001002,
        "progress_hard_campaign": 7001001, "profile_team": [{"slot": 2, "name_code": 9002}, {"slot": 1, "name_code": 9001},
                                                            {"slot": 0, "name_code": 0}],
        "currencies": [{"type": 99, "value": "123"}, {"type": 424242, "value": "1"}],
        "corporation_character_counts": [{"corporation_type": 7, "count": 3}], "mystery": True}}
    responses["GetUserProfileOutpostInfo"] = {"outpost_info": {
        "synchro_level": 200, "recycle_room_researches": [{"tid": 1001, "lv": 50}, {"tid": 1102, "lv": 20}],
        "tactic_academy_class": 13000, "tactic_academy_lesson": 13003,
        "memorial_counts": [{"category": "CallLog", "count": 4}], "jukebox_count": 9}}
    o = run(s.get_account_overview())
    assert [t["name"] for t in o["profile_team"]] == ["Test Attacker", "Test Defender"]
    assert o["campaign"]["normal"] == "1-2" and o["campaign"]["hard"] == "1-1"
    assert o["currencies"] == {"Free Gems": 123, "currency 424242": 1}
    assert o["manufacturer_counts"] == {"Abnormal": 3}
    assert o["outpost"]["research"] == {"General": 50, "Defender": 20}
    assert o["outpost"]["tactics_academy"] == "13-3"
    assert o["outpost"]["lost_relics"] == {"call_logs": 4, "bgm": 9}
    assert o["other_fields"] == {"mystery": True}


def test_union_finds_guild_id_under_card(fake):
    responses, calls = fake
    responses["GetMyGuildInfo"] = {"card": {"guild_id": "77", "guild_name": "U", "ranking_percent": 0.05}}
    responses["GetGuildMembers"] = {"items": [{"nickname": "a", "level": 5, "member_id": "111"},
                                              {"nickname": "b", "level": 9, "member_id": "999"}]}
    u = run(s.get_union())
    assert u["union"]["top_percent"] == 5.0
    assert ("GetGuildMembers", {"guild_id": "77"}) in calls
    assert [m["is_me"] for m in u["members"]] == [False, True]


# ---- new tools

def test_clear_team_by_stage_name(fake):
    responses, calls = fake
    responses["GetUserGamePlayerInfo"] = {}
    s.bla._area_id = 99
    responses["GetMainQuestClearLineup"] = {"cleared_at": "1794687114", "list": [
        {"slot": 2, "tid": 900205, "lv": 300, "combat": 20}, {"slot": 1, "tid": 900101, "lv": 300, "combat": 10}]}
    r = run(s.get_clear_team("1-2", "normal"))
    assert ("GetMainQuestClearLineup", {"stage_id": 6001002, "area_id": 99}) in calls
    assert r["stage"] == "1-2" and r["total_power"] == 30 and r["stage_standard_power"] == 1000
    assert [(x["name"], x["limit_break"], x["core"]) for x in r["squad"]] == [("Test Attacker", 0, 0), ("Test Defender", 3, 1)]
    assert "error" in run(s.get_clear_team("99-99", "hard"))


def test_daily_progress(fake):
    responses, _ = fake
    responses["GetUserDailyContentsProgress"] = {"daily_progress": [{
        "daily_mission_received_points": 80, "daily_mission_receivable_points": 20,
        "rookie_arena_remaining_count": 4, "outpost_battle_storage_fullness": 0.837,
        "tower_daily_info_list": [{"type": 1, "is_opened": True, "remaining_count": 3}], "new_thing": 5}]}
    d = run(s.get_daily_progress())
    assert d["daily_missions"] == {"received": 80, "claimable": 20}
    assert d["outpost"]["storage_fullness_pct"] == 83.7
    assert d["manufacturer_towers"][0]["attempts_left"] == 3
    assert d["other_fields"] == {"new_thing": 5}


def test_cdk_codes_unredeemed_first(fake):
    responses, _ = fake
    responses["GetCdkRedemption"] = {"cdk_redemption_list": [
        {"cdk": "OLD", "status": 1, "valid_time_end": "1795532399"},
        {"cdk": "NEW", "status": 0, "valid_time_end": "1795532399"}]}
    r = run(s.get_cdk_codes())
    assert r["unredeemed"] == 1 and r["codes"][0]["code"] == "NEW"


def test_union_raid_season(fake, monkeypatch):
    responses, calls = fake
    s.bla._area_id = 99
    monkeypatch.setattr(s.time, "time", lambda: 2_000_000_000)

    async def cdn(path):
        assert path == "raid/raid_list.json"
        return [{"id": 1, "start_ts": 100, "caculate_ts": 200, "season_start_date": "2026-01-01T20:00:00"},
                {"id": 2, "start_ts": 300, "caculate_ts": 400, "season_start_date": "2026-02-01T20:00:00"}]
    monkeypatch.setattr(s, "cdn_json", cdn)
    responses["GetMyGuildInfo"] = {"card": {"guild_id": "77", "guild_name": "U"}}
    responses["GetUnionRaidLevelDataOfGuildSeason"] = {"level_info": [{"difficulty": 2, "level": 1, "boss_info": [
        {"name_localvalues": {"en": "Boss A"}, "desc_localvalues": {"en": "Code: X.Y.Z.\nGrade: Tyrant"},
         "element_id": ["500001"], "current_hp": "50", "max_hp": "200"}]}]}
    responses["GetUnionRaidDataOfGuildSeason"] = {"participate_data": [
        {"nickname": "me", "openid": "111", "day": 0, "difficulty": 2, "level": 1, "step": 1, "total_damage": "10",
         "name_localvalues": {"en": "Boss A"}, "element_id": ["500001"], "squad": [{"slot": 1, "tid": 900102, "lv": 1, "combat": 1}]},
        {"nickname": "other", "openid": "5", "day": 1, "difficulty": 2, "level": 1, "step": 2, "total_damage": "30",
         "name_localvalues": {"en": "Boss A"}, "element_id": ["500001"], "squad": []}]}
    r = run(s.get_union_raid())
    body = {"area_id": 99, "guild_id": "77", "season_id": "2"}
    assert ("GetUnionRaidDataOfGuildSeason", body) in calls
    boss = r["levels"][0]["bosses"][0]
    assert boss == {"name": "Boss A", "code": "X.Y.Z.", "grade": "Tyrant", "element": "Electric",
                    "hp_left_pct": 25.0, "hp_left": 50, "max_hp": 200}
    assert [m["nickname"] for m in r["members"]] == ["other", "me"] and "attacks" not in r
    mine = run(s.get_union_raid(member="me"))["attacks"]
    assert len(mine) == 1 and mine[0]["day"] == 1 and mine[0]["squad"][0]["name"] == "Test Attacker"
    assert "live" not in r and "live_error" not in r


# ---- dynamic layer

def test_parse_api_methods():
    js = ("var e=`prod`,a=e=>t=>`${e}${t}`,o=a(`/user`),h=a(`/game/proxy/Game`),g=a(`/game/direct/Game`),"
          "x=a(`/game/proxy/Tools`);var C=o(`/Login`),nn=g(`/GetCdkRedemption`),rn=h(`/GetCdkRedemption`),"
          "An=h(`/RecordCdkRedemption`);x(`/GetNikkesOrder`);var q=c(`/Not/A/Base`)")
    assert s.parse_api_methods(js) == ["/game/direct/Game/GetCdkRedemption", "/game/proxy/Game/GetCdkRedemption",
                                       "/game/proxy/Game/RecordCdkRedemption", "/game/proxy/Tools/GetNikkesOrder",
                                       "/user/Login"]


def test_call_game_api_guard_and_area_retry(fake):
    responses, calls = fake
    s.bla._area_id = 99
    assert "error" in run(s.call_game_api("RecordCdkRedemption", {"cdkey": "X"}))

    def strict(extra):
        if "area_id" not in extra:
            raise s.BlablaError("param invalid", 1303001)
        return {"list": [{"slot": 1, "tid": 900103, "combat": 1}]}
    responses["GetMainQuestClearLineup"] = strict
    r = run(s.call_game_api("GetMainQuestClearLineup", {"stage_id": 6001002}))
    assert calls[-1] == ("GetMainQuestClearLineup", {"stage_id": 6001002, "area_id": 99})
    assert r["list"][0]["nikke_name"] == "Test Attacker" and r["list"][0]["limit_break"] == 2


# ---- points tasks / check-in

TASKS = [
    {"task_id": "14", "task_name": "[Daily] Play Game", "task_type": 2, "sign_today": False,
     "reward_infos": [{"is_completed": True, "points": 100, "completed_times": 1, "need_completed_times": 1}]},
    {"task_id": "15", "task_name": "Daily Sign-in", "task_type": 1, "sign_today": False, "details": "<p>Sign in: 100/day</p>",
     "reward_infos": [{"is_completed": False, "points": 100, "completed_times": 0, "need_completed_times": 1}]},
]


def test_checkin_task_by_type_then_name():
    assert s.checkin_task(TASKS)["task_id"] == "15"
    no_type = [{**t, "task_type": None} for t in TASKS]
    assert s.checkin_task(no_type)["task_id"] == "15"  # English name fallback ("Sign-in")
    assert s.checkin_task([TASKS[0]]) is None


def test_tasks_and_checkin(monkeypatch):
    sent = []

    async def tasks():
        return TASKS

    async def call(path, body=None, **kw):
        sent.append((path, body))
        return {"total_points": 5, "total_points_earned": 9, "total_points_consumed": 4}
    monkeypatch.setattr(s, "_tasks", tasks)
    monkeypatch.setattr(s.bla, "call", call)
    r = run(s.get_blablalink_tasks())
    assert r["checked_in_today"] is False and r["earned"] == 9
    assert r["tasks"][1]["details"] == "Sign in: 100/day"
    c = run(s.daily_checkin())
    assert c["claimed"] and ("/api/lip/proxy/lipass/Points/DailyCheckIn", {"task_id": "15"}) in sent
