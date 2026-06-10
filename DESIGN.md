# Watch Your Toes — MCP Remake: Design Spec

A remake of [watchYourToes](https://github.com/MLMecham/watchYourToes) as a Python MCP server
played through Claude Desktop. Claude is the game master and narrator; the MCP server is the
rules engine and the only source of truth.

## Premise

A wizard has trapped the town in a daily loop. Until the player conquers the dungeon, every
day ends in death and resets. The entire town **knows** about the loop, and as loops pile up,
the villagers unravel — shops close, people stop talking, "safe zones" stop being safe.

**Signature mechanic:** the loop resets bodies, not memories. Kill the baker on loop 12; on
loop 13 he's alive, and he remembers. So does everyone who saw it.

**Tone: dark, and it gets darker.** The game is about what surviving the same day does to
people — including the player.

---

## 1. Architecture

```
Claude Desktop  ──stdio──►  wyt-mcp (FastMCP server)  ──►  SQLite
   (narrator)                  (rules engine)              (state)
```

**The dividing line — the one rule that governs every design decision:**

| Server owns (authoritative)              | Claude owns (improvised)            |
|------------------------------------------|-------------------------------------|
| All dice rolls and combat math           | Narration, descriptions, atmosphere |
| Map rendering and legal movement         | NPC dialogue and personality        |
| Prices, inventory, gold (and its decay)  | Interpreting player free-text intent|
| Sanity decay, status rolls, behavior gates | Picking statuses from server-rolled candidates |
| NPC memories and the event log           | Turning memory packets into drama   |
| Loop progression, resets, ending triggers | The "what happened overnight" scene |

One deliberate exception: Claude feeds *demeanor signals* (cruelty, despair, kindness) into
the server via tagged events — see §9. The server still owns the trigger; Claude only
supplies evidence.

Claude can never be sweet-talked past a rule because the rules aren't in the prompt — they're
in the tools. Invalid actions return errors; the GM prompt instructs Claude to treat tool
results as ground truth over its own narration.

## 2. Packaging & Distribution

Same pattern as `canvasconnector-mcp`:

- `uv` project, hatchling build, published to PyPI as `wyt-mcp`.
- Entry point: `[project.scripts] wyt-mcp = "wyt_mcp.server:main"` → `mcp.run(transport="stdio")`.
- Users add to `claude_desktop_config.json`:
  ```json
  { "mcpServers": { "wyt": { "command": "uvx", "args": ["wyt-mcp"] } } }
  ```
- Dependencies: `mcp[cli]`, `platformdirs`. No network access needed at runtime.
- **SQLite lives in the user data dir** (`platformdirs.user_data_dir("wyt-mcp")`), never the
  package dir — uvx installs are ephemeral.
- Static game data (enemies, gear, statuses, NPC roster, dungeon room pool, events) ships as
  JSON package data and seeds the DB on first run. Ported/adapted from the C# repo's
  `EnemyTable.json` / `GearTable.json` / `ConsumableTable.json`.

## 3. Package Layout

```
wyt-mcp/
├── pyproject.toml
├── DESIGN.md
└── src/wyt_mcp/
    ├── server.py        # FastMCP tool definitions + @mcp.prompt() GM prompt — thin layer
    ├── db.py            # connection, schema, seeding, save/load (platformdirs)
    ├── engine/
    │   ├── player.py    # stats, leveling, inventory, equip, resolve
    │   ├── combat.py    # attack resolution, dice, XP, loot, death, fast-forward logic
    │   ├── town.py      # NPCs: sanity decay, disposition, gates, status rolls, memories
    │   ├── economy.py   # price scaling, gold rot, barter gating
    │   ├── dungeon.py   # graph generation, movement validation, room events
    │   ├── days.py      # advance_loop, reset, overnight events
    │   └── endings.py   # ending triggers, breaking-point branch, epilogue packet
    ├── render.py        # ALL ASCII output: town map, dungeon map, status bar, shop tables
    ├── simulate.py      # headless balance harness — run N loops without Claude (§12)
    └── data/            # enemies.json, gear.json, consumables.json, statuses.json,
                         # npcs.json, room_pool.json, events.json
```

`render.py` is isolated so a future socket dashboard (see §13) can reuse it unchanged.

## 4. Database Schema

A loop **is** a day — there is no separate day counter, only `loop_count`.

```sql
game        (id, loop_count, location, dungeon_seed, created_at, ended, ending_key)
player      (id, name, class, level, xp,
             hp, max_hp, mp, max_mp,            -- mp powers mage/ability use
             gold,
             str, def, spd, mag,                -- mag: magic power, scales spells
             resolve,                           -- 0–100: the player's own sanity (§9)
             brutality, despair)                -- conduct counters feeding the breaking point (§9)
inventory   (id, item_key, equipped, qty)       -- item stats from data/gear.json
npcs        (id, key, name, role, baseline_personality,   -- seeded from npcs.json
             sanity,            -- 0–100
             disposition,       -- -100..100 toward the player
             dead_this_loop,    -- bool, cleared on reset
             missing_this_loop, -- bool: kidnapped/fled, cleared on reset
             withdrawn,         -- bool: stops interacting
             hostile,           -- bool: will attack on sight
             will_trade,        -- bool: shop gate
             accepts_gold,      -- bool: economy gate (§8)
             gate_reason)       -- text: why the latest gate flipped, set at flip time
                                --   e.g. "sanity collapse" / "watched you kill her, loop 12"
npc_statuses(npc_id, status_key, applied_loop)  -- defs in statuses.json
npc_memories(id, npc_id, loop_count, event_text,
             source,            -- 'witnessed' | 'rumor'
             weight)            -- how much it moves disposition/sanity
rooms       (id, loop_count, floor, room_type,  -- enemy|trap|treasure|boss|empty|artifact
             cleared, visited, enemy_key)
room_edges  (from_room, to_room, label)         -- the dungeon graph
event_log   (id, loop_count, seq, text,
             tone)              -- optional: 'cruel'|'despairing'|'kind'|null — see §9
```

Notes:
- `gate_reason` is set by the server whenever a gate flips, derived from the dominant
  triggering memory (or "sanity collapse" for pure decay). `npc_memories` holds the history;
  `gate_reason` is the headline, so Claude narrates *why* without inferring.
- Conventions: one save slot in v1 (`game` has a single row). All randomness via a seeded
  `random.Random` stored per loop (`dungeon_seed`) so a loop is internally consistent.

## 5. Tool Surface (v1)

Thin wrappers in `server.py`; logic in `engine/`. Every state-changing tool returns a
`render` field (server-drawn ASCII block) plus structured data.

| Tool | Contract |
|---|---|
| `new_game(name, class, skip_intro)` | Creates save. Classes from the original: warrior/mage/archer/ninja — each carries a backstory (local vs. outsider) that seeds starting NPC dispositions (§14). `skip_intro=True` starts at loop 2 with a "what you remember" recap packet, for replays. |
| `get_state()` | Full rehydration packet: player (incl. resolve), loop, location, town summary, economy state. Called at session start so chat history isn't load-bearing. |
| `recap()` | Narrative "previously on" packet built from `event_log`: last loop's events, open wounds, who hates you now. For resuming the game in a fresh chat session. |
| `look()` | Server-rendered map (town or dungeon fog-of-war) + status bar + **legal exits/actions only**. |
| `descend(floor)` | "You remember the way" — direct descent to any previously cleared floor +1 (§9). Validated against `max_floor_cleared`. |
| `move(exit)` | Validates against `room_edges` / town locations. Invalid → error. Entering an uncleared enemy room auto-triggers the encounter. |
| `talk_to(npc_key)` | Returns the NPC packet (§6). Claude improvises the dialogue from it. Withdrawn NPCs return a refusal packet; missing NPCs return where they were last seen. |
| `shop(npc_key)` / `buy(item)` / `sell(item)` | Gold/inventory math server-side, prices from `economy.py` (§8). Refuses if `will_trade` is false or the NPC no longer accepts gold. |
| `attack(target, mode)` | Works on enemies **and NPCs**. `mode="auto"` or `"rounds"` — see §7. Player death → loop reset. NPC death → `dead_this_loop`, memory broadcast to witnesses, resolve cost. |
| `combat_action(action)` | Round-by-round combat only: `strike` / `ability(name)` / `use_item(item)` / `defend` / `flee`. Server resolves the round and the enemy's answer. |
| `use_item(item)` / `equip(item)` | Consumables and gear outside combat. |
| `apply_status(npc_key, status_key)` | Only accepts a status from the candidate list the server offered (§6). Claude picks for narrative fit; server enforces eligibility. |
| `advance_loop(cause)` | cause: `slept` is the only value Claude ever passes (the player goes to bed). `died` is triggered internally by any lethal source — combat, traps, NPC attacks — never by the narrator. Failed runs are not a cause: the server derives them (§9). Runs the reset (§9). Returns the "overnight changes" packet for Claude to narrate. |
| `record_event(text, witnesses, tone)` | Logs a noteworthy scene into `event_log` + witness memories. `tone` (optional: cruel/despairing/kind) feeds the brutality/despair counters (§9). The only narrative-write tool. |

**MCP prompt:** `@mcp.prompt() start_game` ships the GM persona: tone (dark, unflinching —
see §11), the verbatim-render rule (§10), the trust-tools-over-narration rule, and pacing.

## 6. The Insanity System

The core of the game; everything is data-driven.

**Decay.** Each `advance_loop`, every NPC loses `base_decay + roll(0, loop_count // 3)` sanity.
A handful of NPCs have traits (`resilient`, `fragile`) that scale this. Player actions move
it too (kindness +, witnessed violence −−).

**Tiers → hard gates** (enforced server-side, not suggestions):

| Sanity | Tier | Gates |
|---|---|---|
| 70–100 | holding on | normal behavior |
| 40–69 | fraying | prices spike, rumors spread, status rolls begin |
| 15–39 | unraveling | `will_trade` may flip off, `withdrawn` or `hostile` may roll on |
| 0–14 | gone | guaranteed withdrawn or hostile; safe-zone attacks possible |

**Statuses (hybrid selection).** `statuses.json` defines the pool (paranoid, hoarding,
fanatic, mute, violent, clingy, prophetic...) with eligibility rules (min loop, sanity band,
role). On decay events the server rolls **2–3 eligible candidates** and includes them in the
NPC packet; Claude calls `apply_status` to pick the one that fits the scene. Authorial
control of the possibility space, narrative agency inside it.

**The NPC packet** (what `talk_to` returns) — everything Claude needs, nothing it can fake:

```json
{
  "name": "Marta", "role": "blacksmith",
  "personality": "gruff, fair, secretly sentimental about her late husband's forge",
  "sanity": 32, "tier": "unraveling",
  "disposition": -45,
  "statuses": ["paranoid", "hoarding"],
  "gates": {"will_trade": false, "hostile": false, "withdrawn": false},
  "gate_reason": "watched you kill her, loop 12",
  "accepts_gold": false,
  "memories": [
    {"loop": 9,  "source": "witnessed", "text": "Watched you die to the troll at the gate."},
    {"loop": 12, "source": "witnessed", "text": "You killed her. She woke up."},
    {"loop": 12, "source": "rumor",     "text": "Heard you robbed the apothecary."}
  ],
  "status_candidates": ["violent", "mute"]
}
```

Packets stay token-lean: `memories` is capped to the ~5 most relevant (most recent plus
heaviest-weight). The full log stays in SQLite.

## 7. Combat: fast-forward mobs, slow-burn bosses

Hybrid model — the player never gets bogged down on trash, but powerful foes command the
table:

- The server computes a **threat ratio** (enemy power vs. player level + gear).
- **Below the threshold** (`mode="auto"` allowed): the fight auto-resolves round by round
  internally until it ends **or the player's HP crosses a danger threshold (~30%)** — at
  which point the server stops, returns the fight-so-far, and hands control back for
  round-by-round decisions (fight on, item, flee). One tool call kills a mob; a mob that
  gets lucky still gets to scare you.
- **Above the threshold**: `mode="auto"` is refused with
  `"a dangerous presence demands your attention"` — round-by-round only, via
  `combat_action`. Bosses, the wizard, and any NPC notably stronger than you are always
  in this band.
- Player intent matters: even against trash the player can ask to fight it out blow by blow;
  Claude just passes `mode="rounds"`.

All dice, damage, crits, ability/MP costs, loot, and XP are server-side. Claude narrates
from the structured round log the tools return.

## 8. Economy: gold rots

One currency, and it's dying. The loop teaches the town that coin is meaningless — **might
and madness are the only things the loop preserves.**

- Server-side price multiplier grows with `loop_count` and shrinks with the merchant's
  sanity: early loops are normal; by mid-game a sword costs a fortune; eventually
  `accepts_gold` flips false per-NPC and shops go **barter-only** (items for items) or close
  entirely (`will_trade` off).
- Gold itself persists across loops (it's yours), but what it buys decays — hoarding it is
  a trap the game quietly punishes.
- Late game, the real currencies are: gear, favors (memories of kindness), and fear
  (disposition + your kill record). A terrified merchant "trades" at extortion prices —
  which feeds the despot path (§9).

## 9. The Loop, Resolve & Endings

### The loop

A loop is one day. `advance_loop` runs, in order:

1. Log loop summary to `event_log`.
2. Reset: revive `dead_this_loop` / return `missing_this_loop` NPCs, restore player HP/MP,
   regenerate the dungeon graph (new seed, difficulty scaled by `loop_count`), restock shops.
   **Cleared floors stay unlocked — "you remember the way":** the player may descend
   directly to any floor at or below `max_floor_cleared + 1` (difficulty still re-scales).
   Player knowledge is the one thing the loop preserves; this makes it mechanical.
3. Decay all NPC sanity; roll gate flips and status candidates; reprice the economy.
4. Spread rumors from yesterday's events.
5. Roll 0–2 **overnight events** from `events.json`, weighted by loop count — early loops
   are quiet; later: thefts (including from the player), fires, disappearances,
   **kidnappings** (an NPC goes `missing_this_loop`; findable in town outskirts or dungeon
   floor 1), mobs, cult meetings, public breakdowns.
6. Check ending triggers (below).
7. Return a structured "what changed" packet → Claude narrates waking up.

Player death calls `advance_loop(cause="died")` automatically from whatever killed the
player — combat, traps, NPC attacks, or any future lethal source. The cause is never the
narrator's call: Claude only ever passes `slept`.

There is no `dungeon_failed` cause. A failed run is **derived server-side** during step 1:
if the player entered the dungeon this loop (any `rooms.visited` row) but `max_floor_cleared`
didn't rise, apply the failed-run resolve drain and set `retreated: true` in the overnight
packet so Claude can color the narration accordingly.

### Resolve — the player's own sanity

The town isn't the only thing unraveling. `resolve` (0–100) drains on: dying, killing
townsfolk, witnessing overnight horrors, failed dungeon runs. It recovers slightly from:
kept promises, kindness, clearing dungeon floors — and the tavern.

**The tavern is the resolve anchor, and it can die.** Tobias the tavern keeper is the
town's slowest-decaying NPC (`resilient`), and an evening in his tavern is the only
*reliable* resolve restore. When he finally breaks — or someone kills him — the well dries
up. The player gets exactly one NPC they are selfishly invested in protecting.

Alongside resolve, the server keeps two **conduct counters**: `brutality` (killings, thefts,
extortion-trades — mostly auto-incremented by tools) and `despair` (fed by Claude's `tone`
tags on `record_event` when the player's words and choices read as hopeless, plus passive
signals like loops spent doing nothing). This is the one place the LLM's judgment feeds the
rules: Claude supplies evidence; the server owns the trigger.

### The breaking point

**Resolve hitting 0 is an ending trigger, not a debuff — but *which* ending depends on how
you broke.** The server branches on the conduct counters:

- `brutality` dominant → **The Despot.** You go insane *outward*. The same madness eating
  the town crowns you: you stop descending, because down there is risk and up here they
  remember what you are.
- `despair` dominant → **The Husk.** You go insane *inward*. You sit down in the square one
  morning and don't get up. The game narrates a few dawns without you in them, then ends.

*(Open: whether Despot is also deliberately claimable before resolve bottoms out — "seize
the town" as a choice rather than a collapse. Leaning yes post-v1; v1 ships it as a
breaking-point branch only.)*

### Endings

**The win mechanic — retrieval, not a boss kill.** Malgor's proclamation (§14) is a
commission: *"In the depths of the dungeon lies that which I seek. You will retrieve it."*
He needs someone else to fetch it — whatever bargain gave him the loop barred him from the
depths. The bottom floor holds **the artifact**, not a wizard. The endgame triggers when the
player takes it.

**The twist (committed):** the wizard *is a townsman* — Malgor has been in the village the
whole game, one of the NPCs, unremarkable, resetting with everyone, watching loop after loop
for someone desperate enough to descend. When the player carries the artifact out of the
dungeon, **the boring townsman is waiting at the dungeon mouth, hand out.** Reveal, final
choice, and ending branch in one scene. Clues surface late: `prophetic`-status NPCs say too
much, his memories don't line up, he never screams at midnight. *(See §15 for keeping the
twist hidden from Claude itself.)*

| Ending | Trigger | Flavor |
|---|---|---|
| **The Dawn** | Refuse him, kill him; resolve above threshold; town not fully broken | The loop breaks. The town wakes up remembering everything. They look at you and decide what you were. Epilogue generated per-NPC from their memory logs. |
| **The Successor** | Kill him with resolve low or brutality high; or take the artifact for yourself | You understand, now, why he did it. The loop doesn't break. It changes hands. |
| **The Despot** | Resolve 0 with brutality dominant (no descent required) | Insanity, outward. You rule the ruins of the day. |
| **The Husk** | Resolve 0 with despair dominant | Insanity, inward. The loop continues without your participation. |
| *(post-v1 slots)* | open | hand the artifact over (the bargain ending), deliberate despot, martyr, escape-alone... |

Many ways to lose; one narrow way to actually win — and the player has to stay sane enough,
and human enough, to take it.

## 10. Rendering & UI

Chat-native v1. The chat window is the terminal.

- **The server draws every map, status bar, and shop table** (`render.py`). Claude never
  generates ASCII art.
- The GM prompt's hard rule: *render blocks are echoed verbatim inside a code fence, then
  narrate below.*
- Dungeon map = fog-of-war graph: visited rooms drawn, seen-but-unexplored exits as `(?)`.

```
Loop 14 · HP 42/60 · MP 8/12 · Resolve 61 · Gold 118 (worth less every day)
        [Entrance]
            │
        [Hall ☠ cleared]
        ┌───┴────┐
   [Armory]   [Flooded Passage]
      │            │
     (?)      [@ Shrine ← you]
Exits: north (?), back (passage)
```

- Hallucination control: movement/actions only exist as tools; illegal calls error; `look()`
  is always available to resync.

## 11. Tone

Super dark, and the GM prompt says so explicitly: the horror is psychological and
accumulative — what the loop does to ordinary people, and to the player. Despair is the
antagonist. The prompt directs Claude to play NPC breakdowns seriously (never camp), let
silence and withdrawal be as heavy as aggression, and never soften consequences the tools
report. Standard model-side safety still applies; the GM prompt shouldn't need to fight it —
dark ≠ gratuitous.

## 12. V1 Scope (the build order)

1. `db.py` + schema + seed data (small: ~6 NPCs incl. the disguised wizard, ~10 enemies,
   ~15 items, ~8 statuses, ~10 overnight events)
2. `new_game` / `get_state` / `look` / `move` + town & dungeon rendering
3. Combat: hybrid auto/rounds, death → reset
4. Economy: price scaling + gold rot + barter gating
5. Sanity decay + gates + NPC packet + `talk_to` + memories/rumors
6. Resolve + conduct counters + `advance_loop` overnight events (incl. thefts/kidnappings)
   + GM prompt
7. Endings: Dawn / Successor / breaking-point branch (Despot/Husk) + epilogue packet
8. `recap()` + Malgor packet forging (§15) + floor shortcuts + tavern anchor
9. `simulate.py` headless harness → tune decay rates, prices, resolve drains in seconds
10. Playtest in Claude Desktop

**Explicitly out of v1:** multiple save slots, the socket dashboard, quests/promises system,
deliberate-despot path, extra endings.

## 13. Later (post-v1)

- **Socket dashboard:** a Textual TUI subscribed to a localhost socket; server pushes state
  after every tool call. Display-only second screen — instant map/HP while chat narrates.
  `render.py` reused as-is.
- **MCP Apps** (interactive HTML in chat) once support matures — clickable map.
- Quests/promises system ("you said you'd save his daughter") — memories already support it.
- Deliberate despot path, more endings, difficulty settings, more NPCs, deeper dungeon.

## 14. The Intro

**Loop 1 is the playable tutorial — one ordinary day — and it's skippable on replay**
(`new_game(skip_intro=True)`).

1. **Arrival & class.** The player arrives at the town gate at dawn. Class selection is
   diegetic and determines backstory, which seeds starting dispositions (per-class offsets
   in `npcs.json`):
   - **Warrior** — the guard captain's son. Local; town starts warm. Watching it unravel
     hurts more.
   - **Mage** — a traveling scholar. Outsider; neutral, with early suspicion ("the loop
     started when *you* arrived").
   - **Archer** — a hunter from the hills. Half-known; mixed.
   - **Ninja** — a stranger nobody can place. Distrusted from loop 1. Hard mode.
2. **The ordinary day.** Town whole, prices fair, everyone sane. The player experiences the
   baseline they'll watch decay — including the unremarkable townsman (Malgor) doing
   unremarkable things. He must be *boring*, not mysterious.
3. **Midnight — the proclamation.** A voice in every head as the nightmare begins, kept
   nearly verbatim from the original game, heard once and never again:
   > *"Foolish mortals! I am Malgor, and your time is mine to command! In the depths of the
   > dungeon lies that which I seek. You will retrieve it today… or suffer eternity within
   > my grasp!"*
   Then the first death, and the first wrong dawn. Loop 2's NPC packets carry the first
   memories. The rules (death resets, the town remembers, gold rots) are never explained —
   they're inflicted.

## 15. Keeping the Twist from the Narrator

Claude narrates everything the tools return — **so the tools must lie to Claude about
Malgor until the reveal.** If his NPC packet showed `sanity` frozen at 100, Claude would
foreshadow it into the ground by loop 5. Server-side measures:

- Malgor's `talk_to` packet is **forged**: plausible decaying sanity, mundane statuses,
  ordinary memories — generated to look median for the town.
- The late-game clues (prophetic NPC lines, his inconsistent memories) are **server-authored
  strings** injected into packets at scripted loop thresholds — never left for Claude to
  infer, so they land exactly as often and as hard as designed.
- The reveal scene at the dungeon mouth arrives as an explicit packet from the
  artifact-pickup tool; until that moment, nothing in any tool output names him as anything
  but a townsman.

General principle: **information the narrator shouldn't narrate must never enter its
context.** The GM is also an audience.
