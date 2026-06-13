"""new_game orchestration and the nightly reset (§9, §14, §16).

A loop is a day. advance_loop closes the books on the old loop BEFORE the
world resets — retreats derive from the dungeon the player actually walked,
so step 1 must run while it still stands. Then the night: reset, decay, the
crime valve, silent robberies, rumors, overnight events, ending checks, and
the morning packet. The player always wakes at the tavern.
"""

import random

from wyt_mcp import db
from wyt_mcp.engine import dungeon, economy, effects, player, town, tuning

# §14 — heard once at the first midnight, and never again.
PROCLAMATION = (
    '"Foolish mortals! I am Malgor, and your time is mine to command! '
    "In the depths of the dungeon lies that which I seek. You will "
    'retrieve it today… or suffer eternity within my grasp!"'
)

FIRST_DAWN_MEMORY = (
    "The first wrong dawn. The voice at midnight, then the barrier's fire "
    "coming over the rooftops — burning alive — and waking to the same "
    "morning anyway, remembering all of it."
)

# §20 canon: the only form the nightly death ever takes.
MIDNIGHT_CANON = ("At midnight the barrier's dark fire swept the town. "
                  "Everyone died. Everyone woke. Everyone remembers burning.")

FIRST_DAWN_SANITY_HIT = 8   # the worst morning of their lives, on top of decay


def new_game(name: str, klass: str, skip_intro: bool = False) -> dict:
    klass = klass.strip().lower()
    if klass not in player.CLASSES:
        return {"error": f"No such class: '{klass}'. "
                         "Choose warrior, mage, archer or ninja."}
    spec = player.CLASSES[klass]
    seed = random.randrange(2 ** 31)
    db.create_save(name, klass, spec, seed)
    town.generate(seed, spec["local"])
    dungeon.generate(seed)
    dungeon.build_tutorial()    # §14: the pre-loop upper dark, day 1 only
    town.restock_shops(random.Random(f"stock:{seed}:1"))

    packet = {
        "name": name, "class": klass, "local": spec["local"],
        "backstory": spec["backstory"],
        "loop": 1, "location": "gate",
        "gm_notes": [
            "Loop 1 is the ordinary day (one playable tutorial day): town "
            "whole, prices fair, everyone sane. Narrate arrival at the gate "
            "at dawn and weave the backstory in.",
            "Do not foreshadow anything. Nothing is wrong yet.",
            "When the player goes to sleep, call advance_loop('slept'). "
            "Midnight handles itself.",
        ],
    }
    if skip_intro:
        packet.update(_skip_intro())
    return packet


def _skip_intro() -> dict:
    """Replay start: run the first midnight silently and hand back a recap."""
    overnight = advance_loop("slept")
    return {
        "loop": 2, "location": "tavern",
        "recap": {
            "proclamation_you_remember": PROCLAMATION,
            "gm_note": ("Replay start — loop 1 already happened. The player "
                        "remembers the ordinary day, the voice at midnight, "
                        "the first death, and this wrong dawn. Open on loop 2 "
                        "waking at the tavern."),
            "overnight": overnight,
        },
    }


def first_day_rescue() -> dict:
    """§14: nobody dies on the ordinary day. Whatever drops the player on
    loop 1, Captain Garrick gets them out — barely. The first death the
    player experiences must be everyone's, at midnight."""
    underground = db.game()["area"] == "dungeon"
    saw_seal = db.conn().execute(
        "SELECT 1 FROM rooms WHERE floor=0 AND room_type='seal' AND visited=1"
    ).fetchone() is not None
    db.set_player(hp=1)
    db.set_game(area="town", location="watch_house")
    town.visit("watch_house")
    mem = ("He went back into that place for you — twenty years after it "
           "took his men — and carried you out himself."
           if underground else
           "He carried you in himself, your blood on his uniform. "
           "You weren't breathing right.")
    town.add_memory("garrick", mem, "witnessed", +2)
    db.log_event("Pulled back from the brink; woke at the watch house. (loop 1)")
    gm = ("The player does not die today. The world goes black — then they "
          "wake on a cot in the watch house, Captain Garrick nearby. He got "
          "to them in time. Barely. They are at 1 HP: let them talk with "
          "him, then rest or sleep. Do not explain why today of all days "
          "they were lucky.")
    if underground:
        gm += (" And understand what it cost him: he went back down into the "
               "dungeon that killed his men, alone, after twenty years, to "
               "drag them out. He will not talk about it first.")
    if saw_seal:
        # The player stood in the seal room and watched the door open from
        # the other side — the one report Garrick can't carry himself.
        gm += (" The player saw the inner seal open from the other side, and "
               "Garrick knows better than anyone alive what that means. He "
               "does not ask them to rest — he orders it, and he leaves them "
               "with an errand: at first light, walk to the chapel and tell "
               "Father Bren exactly what came through that door. Bren made "
               "the seal; he has to hear it opened from the inside. Let the "
               "errand stand as the player's plan when they sleep.")
        db.log_event("Garrick's errand: at first light, tell Father Bren "
                     "what came through the seal. (loop 1)")
    return {"saved_by": "garrick", "hp": 1, "location": "watch_house",
            "gm_note": gm}


# ---------------------------------------------------------------- the night

def advance_loop(cause: str) -> dict:
    """The eight steps of midnight (§9). cause: 'slept' from the narrator;
    'died' only ever from whatever killed the player."""
    if cause not in ("slept", "died"):
        return {"error": "cause must be 'slept' or 'died' — nothing else exists."}
    g = db.game()
    if g["ended"]:
        return {"error": "The game has ended.", "ending": g["ending_key"]}
    loop = g["loop_count"]
    rng = random.Random(f"night:{g['dungeon_seed']}:{loop}")

    # 1. Close the books while the dungeon still stands. Death supersedes a
    #    failed run — if you died down there, you didn't retreat.
    entered = dungeon.entered_this_loop()
    retreated = (cause == "slept" and entered
                 and g["max_floor_cleared"] == g["floor_at_dawn"])
    idle = not entered and not db.events_for_loop(loop)
    db.log_event(f"Loop {loop} ended: {cause}.")
    if cause == "died":
        player.change_resolve(tuning.RESOLVE_DEATH, "you died. Again.")
        player.add_conduct(despair=1)
    elif retreated:
        player.change_resolve(tuning.RESOLVE_RETREAT,
                              "the dungeon spat you back with nothing")
        player.add_conduct(despair=1)
    elif idle:
        player.add_conduct(despair=tuning.IDLE_DESPAIR)

    # 2. Reset the world: bodies, not memories.
    new_loop = loop + 1
    new_seed = random.Random(f"day:{g['dungeon_seed']}:{new_loop}").randrange(2 ** 31)
    c = db.conn()
    c.execute("UPDATE npcs SET dead_this_loop=0, missing_this_loop=0")
    c.commit()
    p = db.player()
    db.set_player(hp=p["max_hp"], mp=p["max_mp"])
    effects.clear_all()                       # the night takes the blessings too
    db.set_game(loop_count=new_loop, dungeon_seed=new_seed, area="town",
                location="tavern", floor_at_dawn=g["max_floor_cleared"])
    dungeon.generate(new_seed)
    town.restock_shops(rng)

    # 3. Decay, repricing, and the crime valve.
    notes = town.decay_all(new_loop, rng)
    notes += economy.roll_gold_acceptance(new_loop, rng)
    valve = town.check_garrick_valve(new_loop)
    if valve == "warning":
        notes.append("Nobody has seen Captain Garrick. The watch didn't "
                     "muster this morning.")
    elif valve == "opened":
        notes.append("Word is going mouth to mouth: the watch is done. "
                     "Public order is no longer a thing.")

    # 4. Robberies — silent by design (§16). The town tells the player itself.
    town.run_robberies(new_loop, rng)

    # 5. Yesterday's sins leak.
    notes += town.spread_rumors(new_loop, rng)

    # 6. Overnight events.
    notes += _roll_events(new_loop, rng)

    # 7. Ending triggers.
    from wyt_mcp.engine import endings  # local import breaks the cycle
    ending = endings.check_breaking_point()

    # The first wrong dawn (§14/§20): everyone remembers burning. Applied
    # before the packet so its sanity numbers reflect the panic.
    first_midnight = None
    if loop == 1 and not g["intro_done"]:
        db.set_game(intro_done=1)
        # The loop unmakes the chapel key an instant before the voice (§14).
        c.execute("DELETE FROM inventory WHERE item_key='chapel_key'")
        c.commit()
        for n in db.npcs_all():
            town.add_memory(n["key"], FIRST_DAWN_MEMORY, "witnessed", -4, 1)
            fresh = db.npc(n["key"])
            db.update("npcs", fresh["id"],
                      sanity=max(0, fresh["sanity"] - FIRST_DAWN_SANITY_HIT))
        first_midnight = {
            "proclamation": PROCLAMATION,
            "gm_note": ("Narrate, in order: midnight; the voice in every head "
                        "(the proclamation, verbatim — heard once and never "
                        "again); then the barrier's dark fire coming over the "
                        "rooftops and killing everyone, the player included — "
                        "let them feel it; then waking to the same morning. "
                        "Never explain the rules. Inflict them."),
            "the_first_dawn": (
                "This morning the town is in open panic — everyone remembers "
                "burning to death hours ago. Shutters stay closed. Someone is "
                "wailing in the square. People grab the player's arm: did you "
                "feel it too. Nobody calls it a dream — too many of them "
                "remember the same fire. The calm of loop 1 is GONE and never "
                "fully comes back."),
            "the_seal_died": (
                "An instant before the voice spoke, the chapel key — wherever "
                "it was — burned to nothing. Bren felt it go. He is the first "
                "person in town to understand that whatever did this wants "
                "the dungeon open, and the way down now stands unsealed."),
        }

    # 8. The morning packet.
    p = db.player()
    packet = {
        "loop": new_loop, "cause": cause,
        "midnight": MIDNIGHT_CANON,
        "wake": "You wake at the Last Hearth. Again.",
        "notes": notes,
        "town_avg_sanity": round(town.town_avg_sanity()),
        "tavern_still_standing": _tavern_alive(),
        "player": {"hp": p["hp"], "max_hp": p["max_hp"],
                   "mp": p["mp"], "max_mp": p["max_mp"],
                   "resolve": p["resolve"], "gold": p["gold"],
                   "stat_points": p["stat_points"]},
    }
    if retreated:
        packet["retreated"] = True
    if first_midnight:
        packet["first_midnight"] = first_midnight
    if ending:
        packet["ended"] = ending
    return packet


def _tavern_alive() -> bool:
    t = db.npc("tobias")
    return (t is not None and not t["dead_this_loop"]
            and not t["withdrawn"] and town.tier(t["sanity"]) != "gone")


# ---------------------------------------------------------------- events

def _event_count(loop_count: int, rng: random.Random) -> int:
    """0–2, weighted by loop: early loops are quiet."""
    w0 = max(1, 9 - loop_count)
    w1 = min(loop_count, 6)
    w2 = max(0, min(loop_count - 5, 4))
    return rng.choices([0, 1, 2], weights=[w0, w1, w2])[0]


def _event_target(rng: random.Random):
    """Anyone present can be hit — except Tobias: the anchor falls only to
    decay or the player's own hand. Wendel is NOT excluded; sparing him from
    every misfortune would itself be a tell (§15)."""
    pool = [n for n in db.npcs_all()
            if n["key"] != "tobias"
            and not n["dead_this_loop"] and not n["missing_this_loop"]]
    return rng.choice(pool) if pool else None


def _roll_events(loop_count: int, rng: random.Random) -> list[str]:
    pool = [e for e in db.load_data("events") if e["min_loop"] <= loop_count]
    notes = []
    count = min(_event_count(loop_count, rng), tuning.EVENTS_MAX, len(pool))
    for _ in range(count):
        ev = rng.choices(pool, weights=[e["weight"] for e in pool])[0]
        pool.remove(ev)
        note = _apply_event(ev, rng)
        if note:
            db.log_event(note)
            notes.append(note)
    return notes


def _apply_event(ev: dict, rng: random.Random) -> str | None:
    # NOTE: events never use add_memory — memory weight moves disposition
    # toward the PLAYER, and a house fire is not the player's fault.
    effect, text = ev["effect"], ev["text"]
    if effect == "steal_player_gold":
        p = db.player()
        if p["gold"] <= 0:
            return None
        loss = max(5, p["gold"] // 6)
        db.set_player(gold=max(0, p["gold"] - loss))
        return text + f" ({loss} gold gone.)"
    if effect in ("npc_sanity_hit", "npc_sanity_hit_big"):
        n = _event_target(rng)
        if n is None:
            return None
        hit = (tuning.NPC_EVENT_HIT if effect == "npc_sanity_hit"
               else tuning.NPC_EVENT_HIT_BIG)
        db.update("npcs", n["id"], sanity=max(0, n["sanity"] - hit))
        return text.replace("{npc}", n["name"])
    if effect == "npc_missing":
        n = _event_target(rng)
        if n is None:
            return None
        db.update("npcs", n["id"], missing_this_loop=1)
        return text.replace("{npc}", n["name"])
    if effect == "town_sanity_hit":
        for n in db.npcs_all():
            if n["key"] != "tobias":
                db.update("npcs", n["id"],
                          sanity=max(0, n["sanity"] - tuning.TOWN_EVENT_HIT))
        return text
    if effect == "town_sanity_heal":
        for n in db.npcs_all():
            db.update("npcs", n["id"],
                      sanity=min(100, n["sanity"] + tuning.TOWN_EVENT_HIT + 1))
        return text
    if effect == "player_despair":
        player.add_conduct(despair=1)
        return text
    return text
