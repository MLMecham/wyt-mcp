"""Ending triggers (§9): the breaking point, the reveal, and the epilogue.

Many ways to lose; one narrow way to win. The final midnight is just another
midnight — bodies and the town reset, sanity does not. Win fast and they can
still heal; win slow and you free a village of ghosts.
"""

from wyt_mcp import db
from wyt_mcp.engine import town, tuning


def _end(key: str) -> None:
    db.set_game(ended=1, ending_key=key)


# ---------------------------------------------------------------- breaking point

def check_breaking_point() -> dict | None:
    """Resolve at 0 is an ending trigger, not a debuff. Which ending depends
    on how you broke: outward (brutality) or inward (despair)."""
    p = db.player()
    g = db.game()
    if g["ended"] or p["resolve"] > 0:
        return None
    if p["brutality"] > p["despair"]:
        _end("despot")
        return {
            "ending": "despot",
            "gm_note": ("THE DESPOT. The player goes insane outward. The same "
                        "madness eating the town crowns them: they stop "
                        "descending, because down there is risk and up here "
                        "they remember what you are. Narrate the coronation "
                        "no one applauds, then the epilogue."),
            "epilogue": epilogue(),
        }
    _end("husk")
    return {
        "ending": "husk",
        "gm_note": ("THE HUSK. The player goes insane inward. They sit down "
                    "in the square one morning and don't get up. Narrate a "
                    "few dawns without them in it — the town flowing around "
                    "a stone — then end."),
        "epilogue": epilogue(),
    }


# ---------------------------------------------------------------- the reveal

def take_artifact() -> dict:
    """The endgame trigger (§9): the bottom floor holds the artifact, not a
    wizard. Taking it puts the boring townsman at the dungeon mouth."""
    g = db.game()
    if g["has_artifact"]:
        return {"error": "You already carry it."}
    db.set_game(has_artifact=1, wizard_revealed=1)
    wendel = db.npc("wendel")
    # The reveal ignores his dead/missing flags — even if the player killed
    # Wendel this very morning, he is standing there, not a scratch on him.
    # Every death was theater he permitted (§15).
    killed_today = bool(wendel and (wendel["dead_this_loop"]
                                    or wendel["missing_this_loop"]))
    if wendel is not None:
        db.update("npcs", wendel["id"], dead_this_loop=0, missing_this_loop=0,
                  location="dungeon_mouth")
    packet = {
        "artifact_taken": True,
        "reveal": {
            "who": "Wendel — the chicken keeper. He is Malgor. He always was.",
            "scene": ("When the player carries the artifact out of the "
                      "dungeon, Wendel is waiting at the dungeon mouth, hand "
                      "out. Unhurried. Terribly reasonable. He has watched "
                      "every loop from inside it, waiting for someone "
                      "desperate enough to descend."),
            "gm_note": ("Play the reveal cold, not theatrical. Three doors, "
                        "all real, all the player's to open with full "
                        "consent: fight him (attack wendel — the fight is "
                        "real now), keep it (claim_artifact), or place it in "
                        "his waiting hand (give_artifact). NEVER map an "
                        "ambiguous line onto a door — if you are not certain "
                        "which they chose, ask, in the fiction. He can wait. "
                        "He is good at it."),
        },
    }
    if killed_today:
        packet["reveal"]["twist_of_the_knife"] = (
            "The player killed Wendel earlier today. He stands there anyway, "
            "not a scratch on him. Every one of his deaths was permitted."
        )
    return packet


def wizard_defeated() -> dict:
    """Called when combat reports WIZARD_DEFEATED. Dawn or Successor."""
    p = db.player()
    dawn = (p["resolve"] >= tuning.DAWN_RESOLVE_MIN
            and p["brutality"] < tuning.DAWN_BRUTALITY_MAX
            and town.town_avg_sanity() > tuning.TOWN_BROKEN_AVG)
    if dawn:
        _end("dawn")
        return {
            "ending": "dawn",
            "gm_note": ("THE DAWN. The loop breaks. The final midnight is "
                        "just another midnight: bodies and the town wake "
                        "reset — but minds do not. They remember everything, "
                        "including what the player was. Narrate the first "
                        "real morning at whatever state of brokenness the "
                        "epilogue reports. The town they saved is exactly as "
                        "sane as they left it."),
            "epilogue": epilogue(),
        }
    _end("successor")
    return {
        "ending": "successor",
        "gm_note": ("THE SUCCESSOR. You understand, now, why he did it. The "
                    "loop doesn't break; it changes hands. Narrate the player "
                    "feeling the days settle onto them like a coat that fits."),
        "epilogue": epilogue(),
    }


def claim_artifact() -> dict:
    """Keeping it for yourself at the reveal: the loop changes hands."""
    _end("successor")
    return {
        "ending": "successor",
        "gm_note": ("THE SUCCESSOR — chosen, not lost. The player keeps the "
                    "artifact. Wendel almost smiles: someone always keeps it. "
                    "The loop doesn't break; it changes hands."),
        "epilogue": epilogue(),
    }


def long_game(via: str = "given") -> dict:
    """THE LONG GAME (§21): Malgor gets the artifact — placed in his hand,
    or taken off the player's corpse. He does not gloat and does not
    explain; the timing of the fire is the only answer the world gives."""
    _end("long_game")
    db.set_game(has_artifact=0)
    wendel = db.npc("wendel")
    befriended = (wendel is not None
                  and wendel["disposition"] >= tuning.RAPPORT_FRIEND)
    if via == "taken":
        how = ("The fight ends with the player down. He kneels — unhurried — "
               "and takes it off them the way a man retrieves his own coat. "
               "They die at his feet. They wake at dawn as always, the "
               "artifact gone — and the town wakes wrong around them: last "
               "night, while they were dead, the fire came before the stars. "
               "Someone tells them. Or no one does.")
    else:
        how = ("He takes it with both hands, the way you take back something "
               "that was always yours. He does not gloat, he does not "
               "explain, and he says nothing worth remembering — that would "
               "be giving the player something. He walks down the road and "
               "through the barrier like a door only he ever had. Narrate "
               "the rest of the day ordinary. Then, at DUSK — light still in "
               "the sky, hours early — the fire comes. Everyone burns. The "
               "player too. They wake at dawn, like every morning.")
    packet = {
        "ending": "long_game",
        "gm_note": (
            "THE LONG GAME. " + how + " The game is over; they are not. "
            "Close on the epilogue. Hard rules for this ending: he was never "
            "waiting for them — he was waiting for MORE, and you never say "
            "so. Never explain what it was, what it opens, why the fire "
            "moved, or what happens next. The broken schedule is the only "
            "answer the world gives. No Wendel at the stall, nothing at the "
            "bottom of the dungeon, every dawn still coming."),
        "epilogue": epilogue(),
    }
    if befriended:
        packet["one_human_thing"] = (
            "All those mornings at his stall counted for something even he "
            "did not expect. Before he goes, he pauses — once — and says: "
            "'The counting was the only thing I ever did that wasn't "
            "waiting.' It changes nothing. He leaves anyway. It is the only "
            "true thing he ever gave away for free.")
    return packet


def class_ending_check() -> dict | None:
    """Per-class S-endings (§9, post-v1). The door is structural: criteria
    and dialog uniquely theirs, not necessarily good. Returns None in v1."""
    return None


# ---------------------------------------------------------------- epilogue

def epilogue() -> list[dict]:
    """Per-NPC closing material: final sanity tier + the memories that
    defined them. The GM writes the goodbyes from this, never from memory."""
    out = []
    for n in db.npcs_all():
        out.append({
            "name": n["name"], "role": n["role"],
            "tier": town.tier(n["sanity"]),
            "disposition": n["disposition"],
            "defining_memories": town.memories_for(n["id"], limit=3),
        })
    return out
