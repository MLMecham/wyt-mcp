# Case Study — Run 02 (2026-06-11)

Source: `second_playthrough.txt` (raw Desktop transcript, ~50 min, debug
class "mitch"). Outcome: **The Successor — on loop 1.** Cleared floors 0–4,
took the artifact, triggered the reveal, and claimed the ending before the
first midnight ever fired. The loop never activated.

Note: this run predates the warm-up sweep and DAY 1 SECRECY fixes (built the
same evening) — the "Loop 1" leaks and missing permissions setup visible in
the transcript are already addressed but unverified in play.

## What worked

- **Day-1 arc v2, end to end.** Boards, tutorial floor 0, the guaranteed
  locket cache, the Inner Seal opening *from the other side*, the seal fight,
  deed-then-question with Bren, the key opening the ward. Bren judged "I
  will come back. I have too much to lose" honest — correctly.
- **Boss-armed combat rule saved the fiction.** The chasm sequence (kicking
  Wendel in, the fall) was pure narration — and the armed combat row yanked
  him back into the doorway at exactly 25/140 when the player tried to walk
  away. The engine remembered what the improv hand-waved.
- **"Can I give it to him?" → refused** (F6 working mechanically; the
  diegetic terms are still queued).
- **Mitch debug class** did its job: combat noise gone, logic exposed.

## Findings

### F11 — Game completable on loop 1 — DECIDED: NO GATE
A strong build reaches the Hollow Heart, triggers the reveal, and ends the
game before the first midnight. **User decision: leave it.** The §19 shards
already gate the true ending (the artifact gate eats 2 shards → must engage
the roads-not-taken), so day-1 wins die naturally when shards ship. Until
then it's an accepted soft spot, not a bug.

### F12 — Engine baited a fabrication (Edder/Mira) — FIXED
The locket's own text said "Garrick will know whose it was" — daring the GM
to invent. It did: "Edder," nineteen, sister "Mira," then had to confess
mid-scene when the player asked, gutting the moment. **Fixed by authoring
it** (see F5 below): the locket is engraved EDDAR; Tam Eddar was the
youngest of Garrick's eight; his sister Eddar is a real NPC.

### F5 — Phantom rewards (second offense) — FIXED
Sela's bread errand to invented "old Fen": fake quest, fake 15g the server
never saw; player caught the desync ("how much gold do I really have?").
**Built:**
- **`npc_reward` tool** — the only channel for NPC-given gold/items.
  Loop-scaled value cap (`REWARD_GOLD_BASE 12 + 3/loop` → 15g at loop 1),
  one reward per NPC per loop (event_log marker), disposition/presence
  gated. A refusal is canon: the NPC genuinely cannot pay.
- **`give_item` tool** — player→NPC transfers are real now; the item leaves
  inventory, the NPC remembers (+2), memories survive midnight. chapel_key
  is guarded.
- **Eddar, authored** (`npcs.json`): Tam's sister, recluse on Crooked Lane
  (alley_2 — one door from Cellar Row, in Sela's shadow). base_decay 1 +
  resilient: the already-broken decay slowest. Garrick has looked out for
  her for twenty years; neither has ever said why out loud.
- **Sela's authored quest** (her packet, repeatable daily): bread for Eddar,
  15g via npc_reward. Both prior runs proved Sela attracts emergent quests;
  now she holds the real one.
- **The locket comes home**: garrick packet carries the authored ID beat
  when the player holds it; `give_item(eddar, delvers_locket)` is server-
  authored — eddar +12 (verbatim memory, forever), garrick +6, +4 resolve,
  kind event. GM is told not to pre-narrate her reaction.
- Prompt rule sharpened: inventing quests from packets is encouraged;
  inventing their rewards is forbidden.

### F13 — Debug classes had no hometown standing — FIXED
create_save looked up dispositions by class key, so "mitch" got 0 across
the board and the GM improvised the warmth run-02 showed. Debug variants
now carry `disposition_as` → base class standing.

### F14 — Manner & rapport system — BUILT (post-run-02)
Run-02's "sup loesr" landing as charm was GM dice, not character. Built:
- `MANNER` dict (town.py, served in every talk_to packet): how each of the
  9 NPCs likes to be spoken to. Authored, stable across loops — social
  knowledge is loop knowledge, learnable like the dungeon. Marta takes
  banter and light insults; Bren takes "Father" and honest doubt; Eddar
  takes brevity and nothing she didn't invite; pity grates worst.
- `rapport` tool: ±1 disposition, once per NPC per loop (event_log marker),
  judged against manner, never GM taste. A hit also gives +1 player resolve,
  once per loop town-wide. Prompt: "miss more than you hit."
- Effects wired into existing systems: disposition ≥40 → −1 nightly decay
  (befriending someone literally slows their unraveling) AND a withdrawn
  NPC still opens the door for the player (gm_note_door; talk_to and
  ask_directions bypass); >20 halves hostile flips on sanity collapse;
  ≥30 → friend pricing (×0.9 at their own counter, mirror of fear pricing,
  never both). Knobs in tuning.py (RAPPORT_FRIEND/GUARD, FRIEND_PRICE_*).
- GM prompt: rule 4 rewritten ("dialogue is yours; who they ARE is mostly
  theirs"), code-fence rule for all game data, numbered-options default.

### F6 — "you can have it" — RESOLVED as THE LONG GAME (supersedes the
### "kept or taken, never given" pact-law, which was rejected in design)
Giving Malgor the artifact is now a real, consented third ending instead of
a refusal. DESIGN.md §21 holds the glue (death charges the substance, shards
are splinters of the artifact, why-he-never-acts explains despot/husk,
greenhouse flagged as IDEA). Built: `give_artifact` tool (explicit consent
only — GM must ask in the fiction if ambiguous), reveal packet rewritten to
three doors, dying to revealed Malgor while carrying it = same ending (he
takes it off the corpse, no rescue, no loop), the dusk-fire closing image
(one broken rule, nothing explained), player wakes at dawn (only ERASED
ever truly kills), hidden befriended-Malgor line at disposition ≥40.

### Still queued (priority order)
1. F7 — 2D coordinate town map + verbatim hardening (GM redrew status bars
   and shop tables all run; player asked for code chunks unprompted)
2. F8 — barrier v1
3. F1 — difficulty + combat sim, LAST
4. Verify in play: warm-up sweep, DAY 1 SECRECY, npc_reward, rapport, and
   the three-door reveal → run-03 should reach midnight.
