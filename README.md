# wyt-mcp

**Watch Your Toes**, remade as an MCP server. A wizard has trapped a town in a
daily time loop. You are the only one who carries anything across the resets —
levels, gold, gear, knowledge — while the townsfolk's sanity decays, the economy
rots, and the dungeon beneath the town reshuffles itself every dawn. Claude is
the game master; the server owns every die roll, map, and price.

`DESIGN.md` is the authoritative spec (§1–§15). Read it before touching the code.

## Status: engine in progress, not yet playable

The MCP server (`server.py`) doesn't exist yet, so there is nothing to connect a
client to. What's built and smoke-tested so far:

| Module | What it does |
|---|---|
| `db.py` | SQLite schema, save lifecycle, helpers. One save slot. |
| `data/*.json` | NPCs, enemies, gear, consumables, statuses, events, room pool. |
| `engine/player.py` | Classes, gear bonuses, XP/leveling (banked stat points), resolve, conduct. |
| `engine/town.py` | Town graph, nightly sanity decay, gates, memories/rumors, the §15 forged wizard packet. |
| `engine/economy.py` | Loop- and sanity-scaled prices, shop stock, gold-acceptance decay. |
| `engine/dungeon.py` | Per-loop graph generation (spine + branches), movement validation, traps/treasure, floor clears. |
| `engine/effects.py` | Buffs/debuffs/stuns — combat-scoped and day-long. |
| `engine/combat.py` | Hybrid auto/round combat: initiative, crits, crit-stuns, follow-ups, flee, NPC kills. |

## Requirements

- [uv](https://docs.astral.sh/uv/) — that's it. `uv sync` provisions Python 3.12
  and the two runtime deps (`mcp[cli]`, `platformdirs`). SQLite needs no install;
  it ships in Python's standard library (`sqlite3`).

## Poking at the engine today

There's no UI yet, but the engine runs headless:

```powershell
uv sync
uv run python
```

```python
from wyt_mcp import db
from wyt_mcp.engine import combat, dungeon, player

db.create_save("Ren", "warrior", player.CLASSES["warrior"], seed=7)
dungeon.generate(7)

dungeon.descend(1)                      # enter the dungeon at floor 1
dungeon.move("Bone Hall")               # exits are edge labels; see dungeon.exits()
combat.begin(enemy_key="rat_swarm")     # auto-resolves trash; bosses refuse auto
combat.round_action("strike")           # round-by-round when it matters
```

The save lives in your platform user-data dir (e.g.
`%LOCALAPPDATA%\wyt-mcp\save.db`). Point `WYT_MCP_DB` at a throwaway path to
experiment without touching it:

```powershell
$env:WYT_MCP_DB = "$env:TEMP\wyt-test.db"
```

## Roadmap

Remaining v1 work, in build order (details in `DESIGN.md` §12):

1. `engine/days.py` — `new_game()` and the seven-step overnight loop reset:
   revive/restock/regenerate, sanity decay, rumors, overnight events, the
   "what changed" packet Claude narrates from.
2. `engine/endings.py` — the breaking point, the artifact retrieval, the reveal
   at the dungeon mouth, the four v1 endings, per-NPC epilogues.
3. `render.py` — all ASCII output: status bar, town map, fog-of-war dungeon map,
   shop tables. Rendered server-side, shown verbatim.
4. `server.py` — the FastMCP tool surface (`new_game`, `look`, `move`, `attack`,
   `advance_loop`, `spend_point`, …) plus the GM-persona prompt.
5. `simulate.py` — headless balance harness: run N loops, watch sanity, prices,
   and gate flips drift.

Post-v1 ideas live in `DESIGN.md` §13: more endings, a deliberate despot path,
difficulty settings, a deeper dungeon.
