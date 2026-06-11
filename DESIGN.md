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

A loop is one day, and **every day ends the same way: at midnight the barrier's
dark fire sweeps the town and kills everyone — the player included.** Everyone
burns; everyone wakes at dawn; everyone remembers burning (§20). This is the
canonical midnight, the engine of the town's unraveling, and the same substance
as the §19 wall — one pact, one fire. Touching the barrier early is the same
death, just voluntary.

`advance_loop` runs, in order:

1. Log loop summary to `event_log`.
2. Reset: revive `dead_this_loop` / return `missing_this_loop` NPCs, restore player HP/MP,
   regenerate the dungeon graph (new seed, difficulty scaled by `loop_count`), restock shops.
   **Cleared floors stay unlocked — "you remember the way":** the player may descend
   directly to any floor at or below `max_floor_cleared + 1` (difficulty still re-scales).
   Player knowledge is the one thing the loop preserves; this makes it mechanical.
3. Decay all NPC sanity; roll gate flips and status candidates; reprice the economy.
4. **Run the crime valve (§16):** while Garrick holds (holding on / fraying), nothing.
   The night he crosses into unraveling is the **warning day** — no robbery yet, one
   signal in the packet ("nobody has seen Captain Garrick; the watch didn't muster").
   Pull his sanity back over the line by nightfall and order holds; fail, and the valve
   opens **permanently**: every general store robbed every night, premium stock moving
   to its paired den. Robberies are **never announced in the packet** — they write a
   memory on the robbed shopkeeper, and the town tells the player itself.
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

Drains are tuned light (`engine/tuning.py`) — the game should run many loops. Death and
a failed run don't stack: if you died down there, you didn't retreat. A failed run only
derives when the cause is `slept`. Truly idle days (no dungeon, no fight, no conversation
all loop) add +1 despair; ordinary town days are legitimate play and cost nothing.

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
| **The Dawn** | Refuse him, kill him; resolve above threshold; town not fully broken | The loop breaks — and the final midnight is just another midnight: bodies and the town reset, **sanity does not**. Win fast and they can still heal; win slow and you free a village of ghosts. Epilogue generated per-NPC from memory logs *and* final sanity tier. |
| **The Successor** | Kill him with resolve low or brutality high; or take the artifact for yourself | You understand, now, why he did it. The loop doesn't break. It changes hands. |
| **The Despot** | Resolve 0 with brutality dominant (no descent required) | Insanity, outward. You rule the ruins of the day. |
| **The Husk** | Resolve 0 with despair dominant | Insanity, inward. The loop continues without your participation. |
| *(post-v1 slots)* | open | hand the artifact over (the bargain ending), deliberate despot, martyr, escape-alone... |
| *(per-class S-endings)* | post-v1; one per class, criteria + dialog uniquely theirs | Not necessarily good — uniquely theirs. `endings.py` ships with a per-class trigger slot (returns nothing in v1) so the door is structural. |

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
- **Altruism as the despair counterweight:** quest acts for NPCs and institutions (clergy
  work, rebuilding, errands of mercy) reduce the player's despair while shoring up
  townsfolk. Early game you save yourself; late game saving others is what saves you.
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
   **Nobody dies on the ordinary day.** Any lethal outcome on loop 1 — combat, trap,
   anything — becomes a rescue: Captain Garrick drags the player back (1 HP, a cot at the
   watch house, talk and rest). The first death the player experiences must be everyone's,
   at midnight. No explanation for the luck is ever given.
   **The dungeon is sealed on the ordinary day** — chapel wax over old iron, twenty years
   of it (§20). `descend` refuses on loop 1 unless the player holds the **chapel key**
   (Bren keeps it; acquisition ships with the npc_reward/quest plumbing — the game's
   first authored quest). With the key they get the **dead dungeon**: floor 1 only, dust
   and whatever was sealed in, the way down buried under old collapse, one old cache (a
   dead delver's locket Garrick recognizes). A tomb, not a gauntlet — preview, never
   progression. The first midnight splits the seal, and loop 2's morning includes the
   sight of the mouth standing open for the first time in twenty years.
3. **Midnight — the proclamation.** A voice in every head as the nightmare begins, kept
   nearly verbatim from the original game, heard once and never again:
   > *"Foolish mortals! I am Malgor, and your time is mine to command! In the depths of the
   > dungeon lies that which I seek. You will retrieve it today… or suffer eternity within
   > my grasp!"*
   Then the fire comes over the rooftops — the first death, everyone's, all at once — and
   the first wrong dawn. **Loop 2's morning is the worst morning of their lives**: the
   whole town remembers burning, and the packet says so (shared sanity hit, panic notes).
   Loop 2's NPC packets carry the first memories. The rules (death resets, the town
   remembers, gold rots) are never explained — they're inflicted.

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
- **He is NOT excluded from harm.** Overnight events can hit him; he can be kidnapped,
  burned out, killed — he dies and comes back like the baker, because special-casing him
  out of danger is itself a tell. The payoff: the reveal works *even if the player killed
  Wendel that very morning* — you carry the artifact out and the man you've watched die
  is standing at the dungeon mouth, hand out, not a scratch on him. Every death was
  theater he permitted. (The reveal logic ignores his dead/missing flags.)

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

**Garrick's sanity is the crime valve — with exactly one day of warning.**

| Garrick | Crime |
|---|---|
| holding on / fraying | none — the watch holds, even as he erodes invisibly |
| the night he crosses into unraveling | **the warning day**: no robbery yet; the morning packet carries one signal — *"Nobody has seen Captain Garrick. The watch didn't muster."* |
| warning day ends, sanity still under the line | the valve opens **permanently** (flag, irreversible in v1): every store, every night. Word gets out that public order is no longer a thing. |
| warning day ends, sanity pulled back over the line | order holds; the warning can fire again someday |

- The warning day is the singular day support is decisive: talking to Garrick that day
  gives a larger boost than usual (he needed someone), and bloodying den crews stacks
  on top.
- Each general store is secretly **paired with one den** (randomized per save). The pairing
  is learnable — the robbed shopkeeper's account, rumors, Garrick admitting what he can't
  stop, or matching den loot to store tags. Knowledge is the loop-persistent currency.
- Robberies run in `advance_loop` step 4: **premium stock** (`shop_stock.premium`) moves
  store → paired den. The den gatekeeper may consume one looted buff — apply it via the
  effects table; one line of code, and it sells that these goods are dangerous.
- **Robberies are never announced in the overnight packet.** The robbery writes a memory
  on the robbed shopkeeper; the rumor system, the shopkeeper's own account, and the empty
  premium shelf carry the news. The server stops narrating and lets the town do it.
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
- After the valve opens, den raids carry a server-authored **taunt**: the crew says,
  callously, what the game itself never editorializes — that the captain has been sealed
  shut since he came up those stairs alone twenty years ago, and they just waited for the
  wax to crack. His tragedy is narrated by the people profiting from it.
- Once crime maxes, no explicit "shopkeeper leaves" state: a store stripped bare every
  night is functionally dead already.

### Shops & items

- **One accessory equip slot.** The magic shop sells stat-boosting rings/amulets/charms —
  useful on every playthrough, and the secret wizard selling you a +1 ring like it's
  nothing deepens the §15 misdirection.
- Item shop tags and wrong-shop sell penalties per §8.

## 17. The Roads Not Taken — Unplayed Classes in the World

Locked 2026-06-10; **build after the core engine runs end-to-end** (days → endings →
render → server first). The three classes the player didn't pick exist as NPCs — your
alternate lives are out there, living your alternate stories. Seeded conditionally at
`create_save` (skip the one matching the player's class). Each rides on systems that
already exist; one sharp hook apiece:

- **The warrior** (Garrick's son) is in the dungeon trying to clear it himself — a rival
  the player can meet mid-run. He carries a small hope counter that drains on his own
  failed runs; kindness can shore it up. If it empties **he husks** — sits down in the
  square one morning, mirroring the player's own husk ending — and watching his boy fold
  is what breaks Garrick: the warning day (§16) fires early. The cascade needs no player
  involvement at all; the world runs on its own clock.
- **The archer**: a hunting party of three in the outskirts. Attack any one and all three
  go permanently hostile as a unit — the town's only neutral muscle, lost in one swing.
- **The mage**: a scholar who has befriended Wendel and loiters at the charm stall,
  "studying the loop" — standing right next to the answer. Red herring and clue vector
  in one (§15: his packet must never know what he's standing next to).
- **The ninja**: from the mid-game, a stranger starts hunting the *player* —
  assassination attempts via the ambush system in dens and the dungeon. He wants the
  artifact too.

**Each unplayed class carries one of the four shards (§19)** and guards it with their
class's verb — warrior: hope, mage: knowledge, ninja: violence, archer: trust. The
crossover content builds last, after both §17 and §19 cores.

## 18. Tuning

All pacing knobs live in **`engine/tuning.py`** — one file: sanity decay rate, resolve
drains, gold rot, dungeon scaling, ambush curves, robbery thresholds, rest amounts, the
warning-day boost. `simulate.py` sweeps them headlessly. "The game dies too fast" should
always be a one-line change.

## 19. The Barrier & the Shards — Perma-Death

Locked 2026-06-10; build after core, same shelf as §17 (the shard-holder crossover
builds last). The loop's one rule — death isn't permanent — gets an exception, and the
player decides who it applies to. Including themselves.

### The barrier

- A dark-energy wall at the town's edge (a location off the outskirts). The outside
  world is visible through it and **does not reset** — the only place where time is real.
- Touching it = instant death; the loop continues. A discoverable "skip this day" button,
  priced as what it is: full death costs **plus extra despair** (walking into the wall is
  a kind of giving up), and any witness remembers.
- **The outsider arc** — server-authored beats at loop thresholds (the Bren-clue
  machinery, §15), played almost without dialogue (nothing is heard through the wall;
  mouthed words, held-up signs):
  - ~loop 3: figures on the road. They've noticed the town isn't answering.
  - ~loop 5: a sign held up. They're trying to organize help.
  - ~loop 7: one of them — young, brave, impatient — touches the barrier reaching in.
    Nothing outside resets. **His bloodstain is the only permanent mark in the player's
    world**, there every dawn, unchanged.
  - ~loop 10: the road is empty. They buried him, or gave up.

### The shards

- **Four shards exist.** One deep in the dungeon (floor 3+); the other three are carried
  by the unplayed classes (§17). The despot tool and the escape key are the same object.
- **The dark-energy gate before the artifact eats two.** The dungeon shard alone is never
  enough — every playthrough must walk at least one road-not-taken to win.
- **Shards are loop-persistent** — pact-stuff, the same substance as the barrier; the
  loop cannot copy the thing that negates it. They persist like the player's inventory
  and memories. Kill a holder and take theirs: they revive at midnight *without it*.
- **Supercharging:** hold a shard and touch the barrier. The barrier kills you like it
  kills everything; the shard, being the same substance, drinks the discharge — your
  death is the crucible. One charge held at a time, full death costs every time. *You
  spend the loop's gift to buy the one thing the loop forbids.*
- **Erasure — `curse_corpse`:** kill them first, then choose, standing over someone who
  will otherwise live again at midnight. Sets `gone_forever`; the reset skips them
  forever. Consumes the shard. Heavy brutality. The town keeps its memories of the
  erased — Sela setting out a loaf for someone who isn't anywhere.
- Budget: 0–1 erasures, you can still leave; 2, exact with no margin; 3+, you have
  burned the gate — and the town knows.

### Wendel and the shards

- **Pre-reveal, Wendel fights as a frail old man and dies like one** — his cover matters
  more than his life, because death is free for him and the artifact isn't. He maintains
  the disguise *through* death. (A boss-stat Wendel was a §15 leak — one bar fight and
  the player knows.) Post-reveal: full Malgor.
- **Fail an erasure attempt on him** (came at him with a charged shard and didn't finish
  it): one silent retaliation. That night you die without witnesses, and you wake with
  the charged shard **gone**. No packet note, no acknowledgment; his forged packet reads
  normal, his eyes stay on you a beat too long. Never repeated, never a reveal — the
  cover constraint disciplines everything he does.
- **He never erases you, and neither can anyone else** wield it against you but one (see
  The Erased): he *needs* you — the best retriever he's had in years. The most dangerous
  thing in town is the one thing that can't afford to kill you.
- Carry an unused charged shard to the reveal and his hand stays out a beat too long:
  *"You found my splinter. Heavy, isn't it."*

### Consequences

- **The suspicion gradient:** an erasure can't be hidden in a town where everyone comes
  back — when someone doesn't, everybody counts. 1–2 erasures: disappearance rumors,
  fear pricing, a heavy Garrick hit; nobody's sure the impossible is real. **3+: dawn
  ambushes at the tavern.** Tobias alive = you wake in time (his tavern, his door — the
  anchor is also the watchman). Tobias gone = they're already in the room. By
  construction the manhunt only ever targets someone who already spent the way out:
  **the town hunts despots, never escapees.**
- **Proliferation is the player's fault:** recharging at the barrier is public — the
  rumor mill carries "someone died against the wall and rose the next dawn," whoever
  does it. The ninja learns the trick *from you*. Never supercharge, and he likely
  never does.

### Endings this adds

| Ending | Trigger | Flavor |
|---|---|---|
| **The Stillness** | Erase Wendel. 10–20 loops later (randomized — no visible seam), the pact unwinds. | One morning nobody died at midnight. The dead stay dead now; the dungeon stops regenerating (finally, permanently conquerable); memories and bodies agree at last. **The barrier never lifts.** The game does not end, the artifact has no one waiting for it, and the GM is never told whether escape exists — the server defines none. The real ending is the morning the player decides to stop looking. |
| **The Refusal** | Supercharged shard turned inward at the barrier. | You remove yourself from the loop. The town keeps looping without its anomaly. The deliberate-despair mirror of the deliberate despot. |
| **The Erased** | The ninja — armed with his shard and taught by your public recharges — downs you and finishes the ritual before midnight. | The player's one true death. Telegraphed (his barrier death fires a rumor the morning after), interruptible (Tobias), preventable (disarm him first). |

## 20. World Canon & NPC Knowledge

Locked 2026-06-10 after run 01 (`case_study/run_01_findings.md`): the GM invented dungeon
records, a "time-binder" taxonomy, floor counts, and a prior looper — exposing the whole
mystery by loop 2. The fix is the §15 principle generalized: **the GM may not know lore
the server didn't author.** Canon ships in the GM prompt; per-NPC knowledge ships in
packets as `what_they_know`.

**The canon:**

- **Midnight is the fire.** At midnight the barrier's dark energy sweeps the town and
  kills everyone — player included. Everyone burns, everyone wakes, everyone remembers
  burning. Nightly. This is the only canonical form of the reset death.
- **The name Malgor exists only in the proclamation.** There are no records, no
  scholarship, no legends about him. Nobody can research him — not Garrick, not the
  watch, not anyone. The not-knowing is the point.
- **There were no loops before loop 1, and no prior loopers.** Nobody "already knew."
  Nobody scratched warnings before it began.
- **The dungeon was sealed twenty years ago**, after Captain Garrick's expedition came
  back one man strong — him. The chapel performed the seal (Bren keeps the rite and the
  key); the watch enforced it; nobody has been inside since. **The first midnight broke
  the seal from the outside** — Bren knows this, and knows what it implies: whatever
  opened it is stronger than the chapel.
- **Garrick's dungeon knowledge is real and useless.** He reached the second floor once.
  But the loop rebuilds the dungeon nightly — *it didn't used to rearrange* — so his maps
  are twenty years stale and below his old mark nobody knows anything, the GM included.
  Veteran knowledge counts for nothing down there; only loop-knowledge works.
- **Why this town (design-side only — never enters the GM prompt):** Malgor's bargain
  bars him from the depths (§9), and the seal barred everyone else. He has been waiting
  behind a locked door he cannot open. The loop is his crowbar: it splits the seal every
  midnight and breeds someone desperate enough to descend. The seal is the reason the
  loop exists *here*.
- **NPCs know exactly three kinds of things:** what everyone knows (the proclamation,
  the fire, the resets), what their packet says (`what_they_know` + memories), and what
  they personally witnessed this run. Asked beyond that, they don't know — and not
  knowing frightens them. Bren's prophetic clue lines (§15) remain the ONLY scripted
  exception, and the server writes those, not the GM.
