"""The town: locations, NPC sanity decay, gates, statuses, memories, rumors.

Includes the §15 rule: Malgor's packet is forged so the narrator can't leak
the twist. Information Claude shouldn't narrate never enters its context.
"""

import random

from wyt_mcp import db
from wyt_mcp.engine import player

TOWN = {
    "gate":       {"name": "Town Gate",      "exits": ["square", "outskirts"]},
    "square":     {"name": "Market Square",  "exits": ["gate", "smith", "tavern", "bakery", "apothecary", "chapel"]},
    "smith":      {"name": "Smithy",         "exits": ["square"]},
    "tavern":     {"name": "The Last Hearth","exits": ["square"]},
    "bakery":     {"name": "Bakery",         "exits": ["square"]},
    "apothecary": {"name": "Apothecary",     "exits": ["square"]},
    "chapel":     {"name": "Chapel",         "exits": ["square"]},
    "outskirts":  {"name": "Outskirts",      "exits": ["gate", "dungeon_mouth"]},
    "dungeon_mouth": {"name": "Dungeon Mouth", "exits": ["outskirts"]},
}


def tier(sanity: int) -> str:
    if sanity >= 70:
        return "holding on"
    if sanity >= 40:
        return "fraying"
    if sanity >= 15:
        return "unraveling"
    return "gone"


def npcs_at(location: str) -> list:
    return db.conn().execute(
        "SELECT * FROM npcs WHERE location=? AND dead_this_loop=0 AND missing_this_loop=0",
        (location,),
    ).fetchall()


def witnesses_at(location: str) -> list[str]:
    return [n["key"] for n in npcs_at(location)]


# ---------------------------------------------------------------- memories

def add_memory(npc_key: str, text: str, source: str, weight: int,
               loop_count: int | None = None) -> None:
    """Record a memory and apply its disposition/sanity effect."""
    n = db.npc(npc_key)
    if n is None:
        return
    loop_count = loop_count if loop_count is not None else db.game()["loop_count"]
    c = db.conn()
    c.execute(
        "INSERT INTO npc_memories (npc_id, loop_count, event_text, source, weight) "
        "VALUES (?,?,?,?,?)",
        (n["id"], loop_count, text, source, weight),
    )
    fields = {"disposition": max(-100, min(100, n["disposition"] + weight))}
    if weight < 0:
        fields["sanity"] = max(0, n["sanity"] + weight // 2)
    db.update("npcs", n["id"], **fields)


def broadcast(witness_keys: list[str], text: str, weight: int) -> None:
    """A witnessed event: full weight for everyone present."""
    for key in witness_keys:
        add_memory(key, text, "witnessed", weight)
    db.log_event(text)


def memories_for(npc_id: int, limit: int = 5) -> list[dict]:
    """Token-lean: most recent + heaviest-weight memories only."""
    rows = db.conn().execute(
        "SELECT * FROM npc_memories WHERE npc_id=? "
        "ORDER BY ABS(weight) DESC, loop_count DESC LIMIT ?",
        (npc_id, limit),
    ).fetchall()
    rows.sort(key=lambda r: r["loop_count"])
    return [
        {"loop": r["loop_count"], "source": r["source"], "text": r["event_text"]}
        for r in rows
    ]


def spread_rumors(loop_count: int, rng: random.Random) -> list[str]:
    """Yesterday's witnessed memories leak to NPCs who weren't there."""
    spread = []
    c = db.conn()
    fresh = c.execute(
        "SELECT * FROM npc_memories WHERE loop_count=? AND source='witnessed' "
        "AND ABS(weight) >= 4",
        (loop_count - 1,),
    ).fetchall()
    for mem in fresh:
        for n in db.npcs_all():
            if n["id"] == mem["npc_id"] or n["is_wizard"]:
                continue
            already = c.execute(
                "SELECT 1 FROM npc_memories WHERE npc_id=? AND event_text=?",
                (n["id"], mem["event_text"]),
            ).fetchone()
            if already is None and rng.random() < 0.4:
                add_memory(n["key"], f"Heard a rumor: {mem['event_text']}",
                           "rumor", mem["weight"] // 2, loop_count)
                spread.append(f"{n['name']} heard about it.")
    return spread


# ---------------------------------------------------------------- decay & gates

def decay_all(loop_count: int, rng: random.Random) -> list[str]:
    """Nightly sanity decay + gate flips. Returns notes for the overnight packet."""
    notes = []
    for n in db.npcs_all():
        traits = n["traits"].split(",") if n["traits"] else []
        decay = n["base_decay"] + rng.randint(0, max(0, loop_count // 3))
        if "resilient" in traits:
            decay = max(1, decay // 2)
        if "fragile" in traits:
            decay = int(decay * 1.5)
        sanity = max(0, n["sanity"] - decay)
        db.update("npcs", n["id"], sanity=sanity)
        if not n["is_wizard"]:
            notes += _roll_gates(db.npc_by_id(n["id"]), rng)
    return notes


def _roll_gates(n, rng: random.Random) -> list[str]:
    notes = []
    t = tier(n["sanity"])
    fields = {}
    if t == "unraveling":
        if n["will_trade"] and n["shop"] and rng.random() < 0.25:
            fields.update(will_trade=0, gate_reason="sanity collapse — the shop just stopped mattering")
            notes.append(f"{n['name']}'s shop did not open this morning.")
        if not n["withdrawn"] and rng.random() < 0.15:
            fields.update(withdrawn=1, gate_reason="sanity collapse — gone quiet")
            notes.append(f"{n['name']} has stopped speaking to people.")
        hostile_p = 0.10 * (2 if n["disposition"] < -20 else 1)
        if not n["hostile"] and rng.random() < hostile_p:
            reason = ("old grudges sharpened by the loops"
                      if n["disposition"] < -20 else "sanity collapse — fear turned outward")
            fields.update(hostile=1, gate_reason=reason)
            notes.append(f"{n['name']} has a look that says keep your distance.")
    elif t == "gone":
        if not n["hostile"] and not n["withdrawn"]:
            if n["disposition"] < -20:
                fields.update(hostile=1, gate_reason="nothing left but the anger")
                notes.append(f"{n['name']} is past words now. Watch yourself.")
            else:
                fields.update(withdrawn=1, will_trade=0,
                              gate_reason="nothing left at all")
                notes.append(f"{n['name']} sits and stares at the wall.")
    if fields:
        db.update("npcs", n["id"], **fields)
    return notes


def status_candidates(n, loop_count: int, rng: random.Random) -> list[str]:
    """2-3 eligible statuses the GM may apply for narrative fit (§6)."""
    have = {
        r["status_key"]
        for r in db.conn().execute(
            "SELECT status_key FROM npc_statuses WHERE npc_id=?", (n["id"],)
        )
    }
    pool = [
        s["key"] for s in db.load_data("statuses")
        if s["key"] not in have
        and loop_count >= s["min_loop"]
        and n["sanity"] <= s["sanity_max"]
    ]
    if "prophetic" in pool and "prophetic_prone" not in (n["traits"] or ""):
        # prophetic is rare; mostly reserved for Father Bren
        if rng.random() < 0.7:
            pool.remove("prophetic")
    rng.shuffle(pool)
    return pool[:3]


def apply_status(npc_key: str, status_key: str, loop_count: int,
                 candidates: list[str]) -> bool:
    if status_key not in candidates:
        return False
    n = db.npc(npc_key)
    db.conn().execute(
        "INSERT OR IGNORE INTO npc_statuses (npc_id, status_key, applied_loop) "
        "VALUES (?,?,?)",
        (n["id"], status_key, loop_count),
    )
    db.conn().commit()
    return True


def statuses_of(npc_id: int) -> list[str]:
    return [
        r["status_key"]
        for r in db.conn().execute(
            "SELECT status_key FROM npc_statuses WHERE npc_id=?", (npc_id,)
        )
    ]


# ---------------------------------------------------------------- packets

def _forged_wizard_view(n, loop_count: int) -> dict:
    """§15: the tools lie to the narrator about Malgor until the reveal.

    His real sanity never moves; the packet shows a plausible median decline
    so Claude has nothing to foreshadow.
    """
    jitter = (n["id"] * 7) % 5
    fake_sanity = max(38, 96 - 2 * loop_count - jitter)
    return {
        "sanity": fake_sanity,
        "tier": tier(fake_sanity),
        "statuses": ["hoarding"] if loop_count >= 8 else [],
        "gates": {"will_trade": False, "hostile": False, "withdrawn": False},
        "gate_reason": None,
    }


def npc_packet(npc_key: str, loop_count: int, rng: random.Random) -> dict | None:
    n = db.npc(npc_key)
    if n is None:
        return None
    g = db.game()
    if n["dead_this_loop"]:
        return {"key": n["key"], "name": n["name"], "state": "dead_this_loop",
                "note": "They are dead — until midnight, anyway."}
    if n["missing_this_loop"]:
        return {"key": n["key"], "name": n["name"], "state": "missing",
                "note": "Not where they should be. Last seen near the outskirts."}

    packet = {
        "key": n["key"], "name": n["name"], "role": n["role"],
        "personality": n["baseline_personality"],
        "disposition": n["disposition"],
        "accepts_gold": bool(n["accepts_gold"]),
        "memories": memories_for(n["id"]),
    }
    if n["is_wizard"] and not g["wizard_revealed"]:
        packet.update(_forged_wizard_view(n, loop_count))
        packet["status_candidates"] = []
    else:
        packet.update(
            sanity=n["sanity"], tier=tier(n["sanity"]),
            statuses=statuses_of(n["id"]),
            gates={
                "will_trade": bool(n["will_trade"]),
                "hostile": bool(n["hostile"]),
                "withdrawn": bool(n["withdrawn"]),
            },
            gate_reason=n["gate_reason"],
            status_candidates=status_candidates(n, loop_count, rng),
        )
    if n["is_wizard"] and g["wizard_revealed"]:
        packet["gm_note"] = (
            "REVEALED: this is Malgor. The mask is off. Play him as ancient, "
            "tired, and terribly reasonable."
        )

    # §15: server-authored clues, injected at scripted thresholds — never inferred.
    if n["key"] == "bren" and "prophetic" in statuses_of(n["id"]):
        clues = []
        if loop_count >= 10:
            clues.append(
                "Bren says, apropos of nothing: 'Have you noticed Wendel never "
                "screams at midnight? Everyone screams. I listen for him.'"
            )
        if loop_count >= 14:
            clues.append(
                "Bren grips your sleeve: 'I asked Wendel what he lost. He said "
                "\"nothing, yet.\" Yet. What does YET mean, when every day is the same?'"
            )
        if clues:
            packet["gm_clue_lines"] = clues
    return packet


def withdrawn_refusal(n) -> dict:
    return {
        "key": n["key"], "name": n["name"], "state": "withdrawn",
        "gate_reason": n["gate_reason"],
        "note": ("They register you, maybe. They do not answer. "
                 "Narrate the silence; do not invent dialogue for them."),
    }
