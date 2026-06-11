# Case Study — Run 01 (first playthrough, 2026-06-10)

Source: `first_playthrought.txt` (raw Desktop transcript, ~50 min, warrior "Mitch").
Outcome: **The Successor**, reached on loop 3.

## What worked (keep, don't touch)

- **Class disposition seeding.** Marta and Tobias treated the captain's son with
  rough warmth even when insulted ("How are you today loser" → affectionate
  history lesson). Exactly the §14 intent.
- **Emergent quest from NPC packets.** Tobias rumor → Sela's cellar → den raid
  repurposed as the cellar monster (the GM staged `attack(den_keeper)` at
  Cellar Row as "something with hands" under the bakery). Composable systems +
  LLM GM = free content. The den fight at level 1 was genuinely tense (two
  crits, potion clutch at 20/20 HP).
- **Tone.** Bren's "do you know what you're fighting for?" scene, Sela's guilt
  ("I told myself I was imagining it"), the first-midnight proclamation, the
  reveal at the dungeon mouth ("He was never trapped. He is the trap.") — all
  landed. apply_status fired on Sela after the bad news.
- **Fog-of-war exploration** read well; ask_directions got used naturally.
- **The reveal packet (§15) worked**: zero foreshadowing of Wendel all run,
  then the turn. Player was surprised.

## Failures and leaks

### F1 — Game beaten in one dive (loop 3)
Cleared floors 1–4, the Warden, and took the artifact in a single day. Zero
player deaths all run. Decay/economy/resolve/crime systems never engaged.
Drivers: level-up full heal (5 mid-dungeon full heals), fast XP curve,
soft enemy stats at loop-2 scaling (×1.08), Warden hitting ~7/round.
**Decision: PARKED.** Keep it easy while we iterate on content; retune with a
combat-sim extension to simulate.py before any public playtest.

### F2 — GM invented lore (the real hardening problem)
- Garrick "knows Malgor from the old dungeon records," calls him a
  "time-binder," knows of the artifact ("a key, a crown, a wound in the
  world"), knows "five floors confirmed," claims his own past dungeon runs and
  that the player's father "cleared the first floors."
- Invented a *prior looper* ("IT COMES BACK" scratched in The Cut).
- Invented "every night someone in town dies" (close to a rule we hadn't
  written yet, but not canon at the time).
Effect: the entire mystery was exposited by loop 2. Bren is the ONLY designed
clue channel (prophetic status, scripted lines).
**Fix: server-authored canon.** A `what_they_know` field in every NPC packet +
a WORLD CANON block in the GM prompt. NPCs know what the server says they
know, nothing more. No records, no prior loopers, no floor counts.

### F3 — Loop 2 morning had no panic
The morning after the first death of the entire town, sanity read 97 and the
square was "a gift day." §14 says the first wrong dawn should be the worst
morning of their lives.
**Fix:** first-midnight packet gets a real first-dawn event: big shared sanity
hit, panic notes for the GM, and canon for what midnight IS (see F4).

### F4 — Midnight death had no canonical form
The GM improvised the midnight death (a scream near the Green). Locked now:
**midnight is the barrier's fire.** The same dark energy as the §19 wall
sweeps the town at midnight and kills everyone — player included — every
night. Everyone burns; everyone wakes; everyone remembers burning. This is
why the town unravels, and it unifies the daily reset with the §19 barrier
(one pact, one substance). Player touching the wall early = the same death,
just voluntary.

### F5 — Phantom rewards
GM granted Sela's 20g, "provisions," "Bren's Token," Garrick's potion — none
exist in the engine. Renders showed true gold (10g) while narration claimed
30g; GM papered over the gap silently.
**Fix (queued):** `npc_reward` tool with server caps (loop-scaled gold, once
per NPC per loop, disposition-gated) + prompt rule: no rewards outside tools.

### F6 — "You can have it" → Successor without consent
Player tried to hand Malgor the artifact (the post-v1 bargain ending). GM
silently called claim_artifact and explained the meta afterward. Player:
"what? I thought I let him win..."
**Fix (queued):** make the limitation diegetic in the reveal packet — the
pact's terms: *"It must be kept or taken, never given."* Wendel cannot accept
a willing hand.

### F7 — Renders paraphrased ~80% of the time
GM redrew maps/shop tables and embellished numbers (told the player SPD went
"8 → 13 → 14"; engine did +1 per point). Token-expensive and drift-prone.
**Fix (queued):** 2D coordinate town map worth echoing + stronger verbatim
rule. Map render stays free; the town-map *item* stays the name-reveal.

### F8 — Barrier touched before §19 was built
GM improvised it well (death + reset) but it cost nothing (resolve stayed
100). Evidence the barrier should be implemented earlier than planned.

### F9 — Desktop permission prompts
Per-tool confirmations broke flow (one `buy` got blocked). Client-side only:
choose "Always allow" per tool when prompted.

## Agreed priority (post-run-01)

1. **Lore hardening** — canon block + per-NPC `what_they_know` (F2)
2. **First midnight = barrier fire + first-dawn panic** (F3, F4)
3. npc_reward (F5), reveal terms (F6), 2D town map + verbatim (F7)
4. Barrier v1 (F8)
5. Difficulty retune + combat sim — LAST, after content iteration (F1)
