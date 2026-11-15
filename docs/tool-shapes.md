# blablalink-mcp tool shapes

Checked field by field against blablalink's live API responses (2026-11-16).

## Principles
- **English by default** (`BLABLALINK_LANG` switches it).
- **Curated tools** define every known field explicitly. Anything blablalink sends that is not
  mapped still appears under `other_fields`, run through the ID translator, so nothing is lost.
- **Only add fields, never rename or remove** them.
- **Names come from blablalink's CDN tables at runtime** (units, cubes, dolls, treasures, stages,
  overload lines, raid seasons). Only small, stable enums with no table anywhere are hardcoded
  (currencies, manufacturers, research, boss elements). Unknown values pass through raw.
- **Read-only**, except actions approved one by one: `daily_checkin`, `redeem_cdk`.

## Decoded IDs and enums

| What | Encoding |
|---|---|
| Unit in a squad (`tid`) | `tid // 100` = CDN character `id // 100`; `tid % 100`: 1-4 = LB0-3, 5-11 = LB3 + core 1-7 |
| Stage id | `6` Normal / `7` Hard / `8` Story, then chapter (3 digits), index (3 digits). Names from CDN `stage/stage_list.json` (index ≠ displayed stage number) |
| Gear item id | `3` · slot (1 head, 2 torso, 3 arm, 4 leg) · class (1 Attacker, 2 Defender, 3 Supporter) · tier (2 digits) · `01` |
| Favorite item | `1xxxxx` = doll: `100` · weapon (1 AR, 2 MG, 3 RL, 4 SG, 5 SMG, 6 SR) · rarity (`01` R, `02` SR); level 0-15 as in game |
| | `2xxxxx` = treasure; blablalink level is 0-based, phase = level + 1 |
| Manufacturer (`corporation_type`) | 1 Elysion, 2 Missilis, 3 Tetra, 4 Pilgrim, 7 Abnormal (gear bonus uses the same numbers, 0 = none) |
| Recycle room research `tid` | 1001 General; 1101/1102/1103 Attacker/Defender/Supporter; 1201-1205 Elysion/Missilis/Tetra/Pilgrim/Abnormal |
| Currency `type` | 98 Paid Gems, 99 Free Gems, 1000 Battle Data, 2000 Credits, 3000 Core Dust, 5100 Regular Recruit Vouchers, 5200 Advanced Recruit Vouchers, 11000 Silver Mileage, 12000 Gold Mileage |
| Boss `element_id` | 100001 Water, 200001 Wind, 300001 Iron, 400001 Fire, 500001 Electric |
| Tactics academy | `class 13000, lesson 13003` = 13-3 |
| Overload value | `state_effects[].function_details[0]`: `abs(function_value) / 100` = percent, `level` = tier 1-15, `function_type` = stat. Match on `str(option_id)` (blablalink sends effect ids as strings) |

## What blablalink does not expose
The site code defines 24 read-only game methods in total (`list_game_api_methods`). None of
them covers inventory/items, arena squads, saved squad presets, tower or Solo Raid squads.
Squads are only available from campaign clears and Union Raid attacks.

## Tools

### Account and roster
**check_login()** ← `CheckLogin`, `GetUserGamePlayerInfo`
`{ logged_in, area_id, game_uid, nickname, is_maintenance, is_banned }`

**get_account_overview()** ← `GetUserProfileBasicInfo`, `GetUserProfileOutpostInfo`
```
{ commander: { nickname, level, team_power, nikke_count, costume_count, created, last_active,
               union_id, is_maintenance, is_banned },
  profile_team: [{ slot, name }],
  campaign: { normal, hard, story },              # "40-35"
  towers: { tribe, elysion, missilis, tetra, pilgrim },
  overclock: { current_sub_season_high, latest_season_high, history: [{ season, level, options }] },
  manufacturer_counts: { Elysion: n, … },
  currencies: { "Free Gems": n, … },
  outpost: { synchro_level, synchro_slots, outpost_level, infra_core_level,
             research: { general, Attacker, Defender, Supporter, Elysion, … },
             tactics_academy: "13-3",
             lost_relics: { memoirs, call_logs, data, bgm } },
  other_fields }
```

**get_roster(filter="", min_level=0)** ← `GetUserCharacters`
`{ count, nikkes: [{ name, rarity, class, burst, element, weapon, manufacturer, level, limit_break, core, combat_power, costume_id, name_code }] }`

**get_character_builds(names, lang)** ← `GetUserCharacterDetails`, `GetUserCharacters`
```
{ builds: [{ name, name_code, class, element, burst, weapon, manufacturer, rarity,
             level (effective), base_level, limit_break, core, combat_power, arena_combat_power,
             bond_level, costume_id,
             skills: { skill1, skill2, burst },
             cube: { name, level }, arena_cube: { name, level },
             doll: { name, rarity, level } | treasure: { name, phase },
             gear: { head|torso|arm|leg: { item_id, tier, level, manufacturer_bonus,
                     overload_lines: [{ line, stat, tier, value_pct, power }] } },
             other_fields }],
  not_found }
```

**get_clear_team(stage="", mode="normal")** ← `GetMainQuestClearLineup {stage_id, area_id}`
`{ stage, mode, stage_id, cleared_at, total_power, stage_standard_power, squad: [{ slot, name, limit_break, core, level, combat_power }] }`
Empty stage → latest Normal and Hard clears.

**get_daily_progress()** ← `GetUserDailyContentsProgress`
```
{ daily_missions: { received, claimable }, weekly_missions: { received, claimable },
  interception_tickets, rookie_arena_left, special_arena_left, counseling_left,
  manufacturer_towers: [{ tower_type, opened, attempts_left }],   # tower_type 1-4 not mapped yet
  outpost: { storage_fullness_pct, storage_excess, efficiency },
  dispatch: { in_progress, completed },
  simulation_room_best: { chapter, difficulty } }
```

### Union
**get_union(include_members=True)** ← `GetMyGuildInfo`, `GetGuildMembers {guild_id}`
`{ union: { id, name, level, rank, ranking, top_percent, activity, damage_rank, members, max_members, locale, description, entry_level, join_type }, members: [{ nickname, level, synchro_level, is_me }] }`

**get_union_raid(season="latest", member="", include_attacks=False)**
← CDN `raid/raid_list.json`, `GetUnionRaidLevelDataOfGuildSeason`, `GetUnionRaidDataOfGuildSeason {area_id, guild_id, season_id}`, and `GetUnionRaidData` while a raid is running
```
{ season: { id, start, end, results },
  levels: [{ difficulty, level, bosses: [{ name, code, grade, element, hp_left_pct, hp_left, max_hp }] }],
  members: [{ nickname, attacks, total_damage }],
  attacks: [{ member, day, difficulty, level, step, boss, element, damage, final_hit,
              squad: [{ slot, name, limit_break, core, level, combat_power }] }] }   # when asked
```
Raid records belong to the union, not the player (raids in a previous union are not included).

### Codes and points
**get_cdk_codes()** ← `GetCdkRedemption` → `{ unredeemed, codes: [{ code, redeemed, valid_from, expires }] }`
**redeem_cdk(code)** ← `RecordCdkRedemption {cdkey}` (action; untested, built as read from the site code)
**get_blablalink_tasks()** ← `GetTaskListWithStatusV2`, `GetUserTotalPoints`
`{ total_points, earned, spent, checked_in_today, tasks: [{ task_id, name, details, points, completed, progress }] }`
**daily_checkin()**: unchanged.

### Public info
**get_event_calendar(lang)**: per module `{ items: [{ name, description, start_time, end_time, … }] }` (local time), ids and subscribe flags dropped.
**get_news(board, limit, lang, cursor)** → `{ posts: [{ post_uuid, title, posted, preview, views, likes, comments, pinned, url }], next_cursor }`
**read_post(post_uuid, lang)** → `{ title, posted, text, views, likes, comments, url }`

### Dynamic layer
**list_game_api_methods(filter="")** → `{ fetched_at, source, count, methods: [{ name, path, callable }] }`
**call_game_api(method, extra_body)**: `Get*` only; fills `area_id`; translates `name_code`, squad `tid`,
stage ids, boss elements and timestamps in the result.
