# wyt-mcp

**Watch Your Toes**, remade as an MCP server. A wizard has trapped a town in a
daily time loop. You are the only one who carries anything across the resets —
levels, gold, gear, knowledge — while the townsfolk's sanity decays, the economy
rots, and the dungeon beneath the town reshuffles itself every dawn. Claude is
the game master; the server owns every die roll, map, and price.

`DESIGN.md` is the authoritative spec (§1–§19). Read it before touching the code.

## Status: v1 core complete — playtest phase

Everything in the §12 build order exists and is smoke-tested: the full engine,
the renderer, the MCP tool surface (23 tools + the GM prompt), and the headless
balance harness. What remains before calling v1 done is playtesting in Claude
Desktop, then the post-core content (§17 unplayed-class NPCs, §19 the barrier
& shards).

| Module | What it does |
|---|---|
| `db.py` | SQLite schema, save lifecycle, helpers. One save slot. |
| `data/*.json` | NPCs, enemies, gear, consumables, statuses, events, room pool. |
| `engine/player.py` | Classes, slot-enforced equip, XP/leveling (banked stat points), consumables, resolve, conduct. |
| `engine/town.py` | Generated town graph + fog-of-war, sanity decay, gates, memories/rumors, the crime machine (§16), the §15 forged wizard packet. |
| `engine/economy.py` | Loop- and sanity-scaled prices, DB-backed stock, shop-tag sell penalties, the den fence. |
| `engine/dungeon.py` | Per-loop graph generation (spine + branches), movement, traps/treasure, floor clears. |
| `engine/effects.py` | Buffs/debuffs/stuns — combat-scoped and day-long. |
| `engine/combat.py` | Hybrid auto/round combat: initiative, crits, follow-ups, flee, NPC kills, town-enemy hooks. |
| `engine/days.py` | `new_game`, the 8-step overnight reset, the Garrick valve, overnight events, the first midnight. |
| `engine/endings.py` | Breaking point (despot/husk), the reveal, dawn/successor, epilogues. |
| `engine/tuning.py` | Every pacing knob in one file (§18). |
| `render.py` | All ASCII output: status bar, fog-of-war town & dungeon maps, shop tables, combat panel. |
| `server.py` | FastMCP tool surface + the GM-persona prompt. |
| `simulate.py` | Headless balance harness. |

## Requirements

- [uv](https://docs.astral.sh/uv/) — that's it. `uv sync` provisions Python 3.12
  and the two runtime deps (`mcp[cli]`, `platformdirs`).

## Playing it (Claude Desktop)

Add to `claude_desktop_config.json` (local checkout; PyPI publish comes later):

```json
{
  "mcpServers": {
    "wyt": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/wyt-mcp", "wyt-mcp"]
    }
  }
}
```

Then use the `start_game` prompt and let the GM take it from there. The save
lives in your platform user-data dir (e.g. `%LOCALAPPDATA%\wyt-mcp\save.db`).

## Headless balance runs

```powershell
uv sync
uv run python -m wyt_mcp.simulate --loops 18
uv run python -m wyt_mcp.simulate --loops 20 --class mage --runs 5
```

Prints per-loop town average/min sanity, the price of an iron sword, player
resolve, and the Garrick valve state. Tune in `engine/tuning.py`, re-run,
repeat. Point `WYT_MCP_DB` at a throwaway path to experiment by hand without
touching your save:

```powershell
$env:WYT_MCP_DB = "$env:TEMP\wyt-test.db"
```

## Roadmap

1. **Playtest in Claude Desktop** — the last §12 step.
2. **§17 — The Roads Not Taken:** the three classes you didn't pick exist in
   the world (the rival in the dungeon, the hunting party, the scholar at the
   charm stall, the assassin who learns your tricks).
3. **§19 — The Barrier & the Shards:** visible non-resetting outside world,
   perma-death shards, the suspicion gradient, and the Stillness / Refusal /
   Erased endings.
4. Post-v1 (§13): quests & altruism as the despair counterweight, per-class
   S-endings, the socket dashboard, more endings.
