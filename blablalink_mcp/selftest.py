"""Quick check from the NAS: python -m blablalink_mcp.selftest (prints counts, no personal IDs)."""
import asyncio
from . import server as s


async def main() -> None:
    print("login:", {k: v for k, v in (await s.check_login()).items() if k != "game_uid"})
    for name, coro in [
        ("roster", s.get_roster()),
        ("overview", s.get_account_overview()),
        ("builds", s.get_character_builds(["Crown"])),
        ("union", s.get_union(include_members=False)),
        ("points", s.get_blablalink_tasks()),
        ("news", s.get_news(limit=1)),
        ("calendar", s.get_event_calendar()),
    ]:
        try:
            r = await coro
            summary = {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()} if isinstance(r, dict) else type(r).__name__
            print(f"OK   {name}: {summary}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {name}: {e}")


asyncio.run(main())
