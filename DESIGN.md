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
    │   ├── town.py      # NPCs: sanity decay, disposition, gates, status rolls, memories;
    │   │                #   town graph generation + fog-of-war + crime machine (§16)
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
             brutality, despair,                -- conduct counters feeding the breaking point (§9)
             stat_points)                       -- banked points from leveling, spent via spend_point
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
town_locations (id, key, name, kind,    -- shop|tavern|chapel|landmark|den|park|alley|gate...
             shop_tag,                  -- smith|apothecary|magic|general|NULL (§16)
             visited,                   -- town fog-of-war; pre-set for the local class
             paired_den,                -- general stores only: key of the den that robs them
             risk_kind)                 -- NULL | 'flat' (slums/dens) | 'scaling' (alleys/park)
town_edges  (from_key, to_key)          -- generated ONCE per save (§16); never reset by loops
shop_stock  (id, location_key, item_key, qty,
             premium)                   -- premium rows are what robberies move store → den
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
| `look()` | Server-rendered map (town **and** dungeon are both fog-of-war — §16) + status bar + **legal exits/actions only**. Unvisited town exits render as descriptions ("a street toward chimney smoke"), never names — §15 applies to geography too. |
| `descend(floor)` | "You remember the way" — direct descent to any previously cleared floor +1 (§9). Validated against `max_floor_cleared`. |
| `move(exit)` | Validates against `room_edges` / `town_edges`. Invalid → error. Entering an uncleared enemy room auto-triggers the encounter. Transiting a town risk tile rolls the ambush chance (§16). |
| `ask_directions(npc_key)` | Disposition-gated: a willing NPC reveals one unknown town location (weighted toward shops); withdrawn/hostile NPCs return the refusal packet. Feeds exploration into the social system (§16). |
| `spend_point(stat)` | Spends one banked stat point (earned per level) on str/def/spd/mag. The GM prompt tells Claude to ask the player where it goes. |
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
- **Shop tags.** Every item carries a tag (`smith`/`apothecary`/`magic`/`general`). The
  matching shop buys at the normal sell rate; the wrong shop buys at half that or refuses
  outright (the chapel is not buying your daggers). Dens fence stolen goods back to you at
  an extortion markup (§16).

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
4. **Run the robberies (§16):** Garrick's sanity tier is the crime valve. While he's
   holding on, nothing; fraying, each general store is hit on a coin flip; unraveling or
   gone, every store, every night — premium stock moves to each store's paired den, and the
   den gatekeeper may consume one of the buffs. Irreversible in v1 once he's gone.
5. Spread rumors from yesterday's events.
6. Roll 0–2 **overnight events** from `events.json`, weighted by loop count — early loops
   are quiet; later: thefts (including from the player), fires, disappearances,
   **kidnappings** (an NPC goes `missing_this_loop`; findable in town outskirts or dungeon
   floor 1), mobs, cult meetings, public breakdowns.
7. Check ending triggers (below).
8. Return a structured "what changed" packet → Claude narrates waking up — always at the
   tavern: the player sleeps there, wakes there, and revives there (§16).

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
- Both maps are fog-of-war graphs: visited rooms/locations drawn, seen-but-unexplored exits
  as `(?)`. The town map uses the same renderer as the dungeon (the layout is generated per
  save — §16 — so there is no static hand-drawn town map). The local class starts with the
  town fully revealed.

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
2. `new_game` / `get_state` / `look` / `move` + town generation (§16) & fog-of-war rendering
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
deliberate-despot path, extra endings, Garrick recovery after he's gone (§16), den
barter/quests beyond the fence buy-back, additional Garrick family NPCs.

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

1. **Arrival & class.** The player arrives at the town gate at dawn (loop 1 only — from
   loop 2 on, every day begins waking at the tavern, §16). Class selection is diegetic and
   determines backstory, which seeds starting dispositions (per-class offsets in
   `npcs.json`):
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

## 16. The Town: Layout, Fog-of-War, and the Crime Machine

Locked 2026-06-10. The town gets the same structural respect as the dungeon — a generated
graph in the DB — but with the opposite lifecycle: **the dungeon regenerates every loop;
the town is generated once per save and never again.** The town is the constant that
decays; the player's knowledge of it is the anomaly the loop preserves.

### Layout — randomized once

- `town_locations` / `town_edges` are generated in `new_game()` from the save seed and are
  untouched by loop resets.
- **The local class (warrior) gets the canonical handcrafted layout**, fully revealed from
  loop 1 — the designer's "intended" town, and the local-knowledge perk made mechanical.
  Outsider classes get a generated layout, unrevealed.
- Fixed anchors, never randomized: Market Square is the hub (notice board — where the
  Proclamation physically lives — and the well, the rumor spot, are square features, not
  locations); **the tavern is always on the square**; the `gate → outskirts → dungeon_mouth`
  chain is fixed.
- Generator: deal the remaining buildings between "square-adjacent" and "back streets"
  (reached through slums/alleys), add 2–3 alley shortcut edges between random pairs, then
  BFS-verify connectivity (same check as the dungeon generator).

**Roster:** square, tavern (The Last Hearth — Tobias), **two general stores**, smithy,
apothecary, **magic shop (run by Wendel — sells the stat accessories)**, chapel, graveyard,
watch house (Captain Garrick), **two slum dens**, park, boarded-up house (one-time
scavenge, squatter encounter), 2–3 generated alleys, gate, outskirts, dungeon mouth.

### The tavern is home

The player sleeps at the tavern, wakes at the tavern every loop, and revives there on
death. Tobias is the first face of every single day — the anchor (§9) made spatial.

### Fog-of-war

- `town_locations.visited` drives the render: visited locations drawn, adjacent unvisited
  exits as `(?)` with a server-authored description ("a street toward chimney smoke"),
  never a name. §15 applies to geography: the GM can't leak what isn't in its context.
- Movement along edges from the current location is always legal — you can see the street;
  the fog governs rendering and narration, not physics.
- Reveals: walking there; the **town map item** (sold at a general store); or
  `ask_directions(npc_key)` — disposition-gated, which turns exploration into a social
  mechanic (costly for the distrusted ninja, fitting for the suspected mage).

### Risk tiles — two curves, one message

| Tile | Curve | Why |
|---|---|---|
| Slums / dens | Flat ~15% ambush per transit, from loop 1 | It was always rough; nobody there is surprised |
| Alleys + park | ~0% early, scaling with loop count × town decay | The safe places rotting is the horror beat |

The park additionally restores a sliver of resolve once per loop in early loops — until it
turns. Ambushes are **nameless broken townsfolk** (cutpurse, feral dog, mad penitent,
drunkard) using the NPC tier stat blocks — never the 7 named NPCs. Killing one when flee
was offered: +1 brutality. They drop a few coins.

### The crime machine

**Garrick's sanity tier is the crime valve.**

| Garrick | Robberies |
|---|---|
| holding on | none — the watch holds |
| fraying | each general store hit on a coin flip per night |
| unraveling / gone | every store, every night — **irreversible in v1** |

- Each general store is secretly **paired with one den** (randomized per save). The pairing
  is learnable — the robbed shopkeeper's account, rumors, Garrick admitting what he can't
  stop, or matching den loot to store tags. Knowledge is the loop-persistent currency.
- Robberies run in `advance_loop` step 4: **premium stock** (`shop_stock.premium`) moves
  store → paired den. The den gatekeeper may consume one looted buff — apply it via the
  effects table; one line of code, and it sells that these goods are dangerous.
- **The strong day-long temp buffs are den-loot only.** Day effects are wiped by
  `effects.clear_all()` at every reset, so the den raid and the big dungeon push must
  happen the *same day*. The raid is the pre-boss ritual.
- Den access: **raid** (combat — armed criminals, but killing when they offered to deal
  adds brutality), or **buy back at extortion markup** (the fence — the pacifist tax).
- **Sustaining Garrick:** talking to him (small positive memory, once per loop) or
  defeating den criminals (larger — "the watch isn't alone") fights the ambient decay
  pulling him down. A daily action that competes with dungeon time. Letting him fall on
  purpose so the buffs route predictably to the dens is a legitimate dark strategy — the
  despot path expressed as town policy.
- **Shopkeeper murder** is the shortcut that eats itself: it grants the premium goods, but
  costs brutality, a heavy near-permanent hostile memory at that shop (the victim revives;
  the memory doesn't fade), and a **direct heavy sanity hit to Garrick** — a murder in his
  town. Each murder accelerates the valve until the dens get the goods first and you're
  fighting a gatekeeper who drank what you came for. No cap needed; the exploit consumes
  its own profitability.
- For the warrior, all of this is personal: Garrick is his **father**. Watching him fray —
  or breaking him yourself, as the captain's son, with the rumors naming you — is the
  strongest class-specific arc in the game, at zero extra build cost.
- Once crime maxes, no explicit "shopkeeper leaves" state: a store stripped bare every
  night is functionally dead already.

### Shops & items

- **One accessory equip slot.** The magic shop sells stat-boosting rings/amulets/charms —
  useful on every playthrough, and the secret wizard selling you a +1 ring like it's
  nothing deepens the §15 misdirection.
- Item shop tags and wrong-shop sell penalties per §8.
