"""The town: layout, fog-of-war, NPC decay, memories, and the crime machine (§16).

The town graph lives in town_locations/town_edges — generated ONCE per save
in new_game and never touched by loop resets. The dungeon regenerates every
night; the town only decays. The local class gets the canonical handcrafted
layout fully revealed; outsiders get a seeded layout and the fog.

Includes the §15 rule: Malgor's packet is forged so the narrator can't leak
the twist. Information Claude shouldn't narrate never enters its context —
and that applies to geography: unvisited places render as descriptions,
never names.
"""

import random

from wyt_mcp import db
from wyt_mcp.engine import player, tuning

# What each location IS. Where it sits comes from town_edges.
# desc = what the GM may say about it before it's been visited.
LOCATIONS = {
    "square":        {"name": "Market Square", "kind": "landmark",
                      "desc": "the sound of a market"},
    "tavern":        {"name": "The Last Hearth", "kind": "tavern",
                      "desc": "warm light and the smell of a hearth"},
    "gate":          {"name": "Town Gate", "kind": "gate",
                      "desc": "the town gate"},
    "outskirts":     {"name": "Outskirts", "kind": "landmark",
                      "desc": "the road out of town"},
    "dungeon_mouth": {"name": "Dungeon Mouth", "kind": "landmark",
                      "desc": "the broken hill where the dungeon opens"},
    "smith":         {"name": "Smithy", "kind": "shop", "shop_tag": "smith",
                      "desc": "a street ringing with hammer-blows"},
    "apothecary":    {"name": "Apothecary", "kind": "shop", "shop_tag": "apothecary",
                      "desc": "a doorway smelling of dried herbs"},
    "magic_shop":    {"name": "Wendel's Charms & Chickens", "kind": "shop",
                      "shop_tag": "magic",
                      "desc": "a cluttered stall with chickens pecking around it"},
    "bakery":        {"name": "Sela's Bakery & Provisions", "kind": "shop",
                      "shop_tag": "general",
                      "desc": "a street that smells of bread"},
    "general_store": {"name": "Dorrin's General Goods", "kind": "shop",
                      "shop_tag": "general",
                      "desc": "a doorway stacked with crates"},
    "chapel":        {"name": "Chapel", "kind": "chapel",
                      "desc": "a narrow lane toward a bell tower"},
    "graveyard":     {"name": "Graveyard", "kind": "landmark",
                      "desc": "a low iron gate behind the chapel"},
    "watch_house":   {"name": "Watch House", "kind": "landmark",
                      "desc": "a squat stone building flying the town colors"},
    "park":          {"name": "The Green", "kind": "park", "risk": "scaling",
                      "desc": "a stretch of green between the houses"},
    "boarded_house": {"name": "Boarded-Up House", "kind": "landmark",
                      "risk": "scaling",
                      "desc": "a house with its windows boarded over"},
    "den_west":      {"name": "The Rookery", "kind": "den", "risk": "flat",
                      "desc": "a row of leaning houses where the lamps don't reach"},
    "den_east":      {"name": "Cellar Row", "kind": "den", "risk": "flat",
                      "desc": "stairs descending under a row of poor houses"},
    "alley_1":       {"name": "Tanner's Alley", "kind": "alley", "risk": "scaling",
                      "desc": "a gap between buildings"},
    "alley_2":       {"name": "Crooked Lane", "kind": "alley", "risk": "scaling",
                      "desc": "a crooked gap between buildings"},
    "alley_3":       {"name": "The Cut", "kind": "alley", "risk": "scaling",
                      "desc": "a shortcut someone has used recently"},
}

GENERAL_STORES = ("bakery", "general_store")
DENS = ("den_west", "den_east")

# The designer's town — what the local (warrior) grew up in.
CANONICAL_EDGES = [
    ("gate", "square"), ("gate", "outskirts"), ("outskirts", "dungeon_mouth"),
    ("square", "tavern"), ("square", "smith"), ("square", "bakery"),
    ("square", "apothecary"), ("square", "magic_shop"), ("square", "chapel"),
    ("square", "watch_house"), ("square", "park"),
    ("chapel", "graveyard"),
    ("park", "general_store"),
    ("smith", "alley_1"), ("alley_1", "den_west"), ("den_west", "boarded_house"),
    ("bakery", "alley_2"), ("alley_2", "den_east"),
    ("park", "alley_3"), ("alley_3", "graveyard"),
]


# ---------------------------------------------------------------- generation

def generate(seed: int, local: bool) -> None:
    """Build the town once per save (§16). Loop resets never call this."""
    rng = random.Random(f"town:{seed}")
    edges = CANONICAL_EDGES if local else _generate_edges(rng)
    assert _connected(edges), "town generator produced a disconnected graph"

    c = db.conn()
    c.execute("DELETE FROM town_locations")
    c.execute("DELETE FROM town_edges")

    dens = list(DENS)
    rng.shuffle(dens)
    pairing = dict(zip(GENERAL_STORES, dens))  # hidden; learnable, not shown

    for key, spec in LOCATIONS.items():
        c.execute(
            "INSERT INTO town_locations (key, name, kind, shop_tag, visited, "
            "paired_den, risk_kind) VALUES (?,?,?,?,?,?,?)",
            (key, spec["name"], spec["kind"], spec.get("shop_tag"),
             1 if local else (1 if key == "gate" else 0),
             pairing.get(key), spec.get("risk")),
        )
    for a, b in edges:
        c.execute("INSERT INTO town_edges (from_key, to_key) VALUES (?,?)", (a, b))
        c.execute("INSERT INTO town_edges (from_key, to_key) VALUES (?,?)", (b, a))
    c.commit()


def _generate_edges(rng: random.Random) -> list[tuple[str, str]]:
    """Outsider layout: square hub, some buildings tucked behind the rough parts."""
    edges = [
        ("gate", "square"), ("gate", "outskirts"), ("outskirts", "dungeon_mouth"),
        ("square", "tavern"), ("chapel", "graveyard"),
    ]
    buildings = ["smith", "apothecary", "magic_shop", "bakery",
                 "general_store", "chapel", "watch_house", "park"]
    rng.shuffle(buildings)
    front, back = buildings[:5], buildings[5:]
    edges += [("square", b) for b in front]

    # Each den hides behind an alley that opens off a front-street building.
    for den, alley in zip(DENS, ("alley_1", "alley_2")):
        edges += [(rng.choice(front), alley), (alley, den)]
    # Back-street buildings are reached through the rough parts.
    for b in back:
        edges.append((rng.choice(list(DENS) + ["alley_1", "alley_2"]), b))
    edges.append((rng.choice(DENS), "boarded_house"))
    # One spare alley as a shortcut between two random buildings.
    a, b = rng.sample(buildings, 2)
    edges += [(a, "alley_3"), ("alley_3", b)]
    return edges


def _connected(edges: list[tuple[str, str]]) -> bool:
    adj: dict[str, set] = {k: set() for k in LOCATIONS}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, queue = {"square"}, ["square"]
    while queue:
        for nxt in adj[queue.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen == set(LOCATIONS)


# ---------------------------------------------------------------- graph & fog

def location(key: str):
    return db.conn().execute(
        "SELECT * FROM town_locations WHERE key=?", (key,)
    ).fetchone()


def all_locations() -> list:
    return db.conn().execute("SELECT * FROM town_locations ORDER BY id").fetchall()


def exits_from(key: str) -> list[dict]:
    """Exits with fog applied: visited → name, unvisited → description only."""
    rows = db.conn().execute(
        "SELECT l.* FROM town_edges e JOIN town_locations l ON l.key = e.to_key "
        "WHERE e.from_key=? ORDER BY l.id", (key,)
    ).fetchall()
    out = []
    for r in rows:
        if r["visited"]:
            out.append({"key": r["key"], "name": r["name"], "known": True})
        else:
            out.append({"key": r["key"], "desc": LOCATIONS[r["key"]]["desc"],
                        "known": False})
    return out


def visit(key: str) -> None:
    db.conn().execute("UPDATE town_locations SET visited=1 WHERE key=?", (key,))
    db.conn().commit()


def reveal_map() -> str:
    db.conn().execute("UPDATE town_locations SET visited=1")
    db.conn().commit()
    return "The map unfolds — the whole town, named and placed."


def ask_directions(npc_key: str, rng: random.Random) -> dict:
    """A willing NPC reveals one unknown location, shops first (§16)."""
    n = db.npc(npc_key)
    if n is None or n["dead_this_loop"] or n["missing_this_loop"]:
        return {"error": "There is nobody to ask."}
    if n["withdrawn"] or n["hostile"]:
        return withdrawn_refusal(n)
    if n["disposition"] < -30:
        return {"refused": True, "name": n["name"],
                "note": "They look at you and decide you can stay lost."}
    unknown = [r for r in all_locations() if not r["visited"]]
    if not unknown:
        return {"name": n["name"], "note": "You already know every street they could name."}
    shops = [r for r in unknown if r["kind"] == "shop"]
    pick = rng.choice(shops or unknown)
    visit(pick["key"])
    return {"name": n["name"], "revealed": pick["key"],
            "revealed_name": pick["name"],
            "note": f"{n['name']} points the way to {pick['name']}."}


# ---------------------------------------------------------------- risk tiles

AMBUSH_DEN = ["cutpurse", "den_thug", "mad_penitent"]
AMBUSH_STREET = ["cutpurse", "feral_dog", "mad_penitent", "drunkard"]


def town_avg_sanity() -> float:
    rows = [n["sanity"] for n in db.npcs_all() if not n["is_wizard"]]
    return sum(rows) / max(1, len(rows))


def ambush_chance(key: str) -> float:
    """Two curves, one message (§16): dens were always rough; the safe
    places rot as the town does."""
    loc = location(key)
    if loc is None or loc["risk_kind"] is None:
        return 0.0
    if loc["risk_kind"] == "flat":
        return tuning.FLAT_RISK
    loop = db.game()["loop_count"]
    base = max(0.0, (loop - tuning.SCALING_GRACE_LOOPS) * tuning.SCALING_PER_LOOP)
    decay_factor = 1.5 - town_avg_sanity() / 100.0   # 0.5 healthy → 1.5 broken
    return min(tuning.SCALING_CAP, base * decay_factor)


def roll_ambush(key: str, rng: random.Random) -> str | None:
    """Returns an enemy_key if a transit through this tile turns bad."""
    if rng.random() >= ambush_chance(key):
        return None
    loc = location(key)
    pool = AMBUSH_DEN if loc["kind"] == "den" else AMBUSH_STREET
    return rng.choice(pool)


def park_rest() -> dict:
    """A sliver of resolve, once per loop, while the green still feels safe."""
    g, p = db.game(), db.player()
    if p["park_rested_loop"] == g["loop_count"]:
        return {"note": "You have already taken what the green has to give today."}
    if town_avg_sanity() < tuning.PARK_SAFE_SANITY:
        return {"note": ("You sit a while. It doesn't help anymore — the grass "
                         "is trodden and somebody is crying two benches down.")}
    db.set_player(park_rested_loop=g["loop_count"])
    new = player.change_resolve(tuning.PARK_REST, "an hour on the green, pretending")
    return {"resolve": new,
            "note": f"An hour where the loop feels far away. +{tuning.PARK_REST} resolve."}


def tavern_rest() -> dict:
    """An evening with Tobias — the only reliable resolve restore (§9).
    Once per loop, and the well can dry up."""
    g, p = db.game(), db.player()
    t = db.npc("tobias")
    if t is None or t["dead_this_loop"] or t["missing_this_loop"]:
        return {"note": "The tavern is dark. No fire, no Tobias. Not tonight."}
    if t["withdrawn"] or tier(t["sanity"]) == "gone":
        return {"note": ("Tobias pours without looking at you. The fire is lit "
                         "and the room is still cold. The well is dry.")}
    if p["tavern_rested_loop"] == g["loop_count"]:
        return {"note": "You've already had your evening. The night is the night."}
    db.set_player(tavern_rested_loop=g["loop_count"])
    new = player.change_resolve(tuning.TAVERN_REST, "an evening at the Last Hearth")
    return {"resolve": new,
            "note": (f"Tobias keeps the cup full and the talk small. "
                     f"+{tuning.TAVERN_REST} resolve.")}


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
        decay = n["base_decay"] + rng.randint(
            0, max(0, loop_count // tuning.DECAY_LOOP_DIVISOR))
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


# ---------------------------------------------------------------- the crime machine (§16)

def garrick_tier() -> str:
    g = db.npc("garrick")
    return tier(g["sanity"]) if g is not None else "gone"


def restock_shops(rng: random.Random) -> None:
    """Nightly restock (advance_loop step 2). Every shop refills by tag; the
    premium buffs are dealt between the two general stores. Dens start the
    day empty — what a den holds is only ever last night's takings."""
    c = db.conn()
    c.execute("DELETE FROM shop_stock")
    c.execute("UPDATE town_locations SET den_buff=NULL")
    gear_keys = {g["key"] for g in db.load_data("gear")}
    items = db.load_data("gear") + db.load_data("consumables")
    for loc in all_locations():
        if loc["kind"] != "shop":
            continue
        for it in items:
            if it.get("shop") != loc["shop_tag"] or it.get("premium"):
                continue
            qty = 1 if it["key"] in gear_keys else 3
            c.execute(
                "INSERT INTO shop_stock (location_key, item_key, qty, premium) "
                "VALUES (?,?,?,0)", (loc["key"], it["key"], qty),
            )
    premiums = [i for i in items if i.get("premium")]
    rng.shuffle(premiums)
    for i, it in enumerate(premiums):
        c.execute(
            "INSERT INTO shop_stock (location_key, item_key, qty, premium) "
            "VALUES (?,?,1,1)", (GENERAL_STORES[i % len(GENERAL_STORES)], it["key"]),
        )
    c.commit()


def check_garrick_valve(loop_count: int) -> str | None:
    """advance_loop step 3.5: the valve with one day of warning (§16).

    Returns 'warning' (the singular signal for the packet), 'opened' (word
    gets out that public order is over — permanent), or None.
    """
    g = db.game()
    if g["garrick_failed"]:
        return None
    if garrick_tier() in ("unraveling", "gone"):
        if g["garrick_warning_loop"] is None:
            db.set_game(garrick_warning_loop=loop_count)
            return "warning"
        if g["garrick_warning_loop"] < loop_count:
            db.set_game(garrick_failed=1)
            return "opened"
    elif g["garrick_warning_loop"] is not None:
        # Pulled back over the line in time. The warning can fire again someday.
        db.set_game(garrick_warning_loop=None)
    return None


def run_robberies(loop_count: int, rng: random.Random) -> None:
    """advance_loop step 4. Once the valve is open: every store, every night.

    Never announced — the robbery writes a memory on the shopkeeper and the
    rumor system, their own account, and the empty shelf carry the news.
    The server stops narrating and lets the town do it (§16).
    """
    if not db.game()["garrick_failed"]:
        return
    c = db.conn()
    consumables = {i["key"]: i for i in db.load_data("consumables")}
    for store_key in GENERAL_STORES:
        store = location(store_key)
        taken = c.execute(
            "SELECT * FROM shop_stock WHERE location_key=? AND premium=1",
            (store_key,),
        ).fetchall()
        if not taken:
            continue
        c.execute(
            "UPDATE shop_stock SET location_key=? WHERE location_key=? AND premium=1",
            (store["paired_den"], store_key),
        )
        keeper = c.execute(
            "SELECT key FROM npcs WHERE location=?", (store_key,)
        ).fetchone()
        if keeper:
            add_memory(keeper["key"],
                       "They came in the night again and took the good stock. "
                       "Everyone knows the watch won't come.", "witnessed", -3,
                       loop_count)
        # The crew samples its own takings: tonight's gatekeeper fights enhanced.
        buffs = [r for r in taken if consumables.get(r["item_key"], {}).get("buff")]
        if buffs and rng.random() < 0.5:
            drunk = rng.choice(buffs)
            c.execute("DELETE FROM shop_stock WHERE id=?", (drunk["id"],))
            c.execute("UPDATE town_locations SET den_buff=? WHERE key=?",
                      (drunk["item_key"], store["paired_den"]))
    c.commit()


def den_stock(den_key: str) -> list:
    return db.conn().execute(
        "SELECT * FROM shop_stock WHERE location_key=?", (den_key,)
    ).fetchall()


def apply_den_buff(den_key: str) -> str | None:
    """Call right after combat.begin for a den fight: the gatekeeper drank
    something from last night's takings. Enemy effects die with the fight."""
    from wyt_mcp.engine import effects

    loc = location(den_key)
    if loc is None or not loc["den_buff"]:
        return None
    item = {i["key"]: i for i in db.load_data("consumables")}[loc["den_buff"]]
    b = item["buff"]
    effects.add("enemy", b["kind"], b["value"], rounds_left=None)
    return f"The keeper's eyes are wrong — they've had the {item['name']}."


def loot_den(den_key: str) -> list[str]:
    """The keeper is down: everything in the den goes home with you."""
    rows = den_stock(den_key)
    if not rows:
        return ["The den is picked clean. Last night they got nothing — or sold it."]
    items = {i["key"]: i for i in db.load_data("gear") + db.load_data("consumables")}
    notes = []
    for r in rows:
        player.add_item(r["item_key"], r["qty"])
        name = items.get(r["item_key"], {}).get("name", r["item_key"])
        notes.append(f"You take {name}" + (f" x{r['qty']}" if r["qty"] > 1 else "") + ".")
    db.conn().execute("DELETE FROM shop_stock WHERE location_key=?", (den_key,))
    db.conn().commit()
    return notes


GARRICK_SUPPORT_TEXT = "You came by the watch house just to stand with him."


def support_garrick() -> dict:
    """Talking to Garrick holds him together a little — once per loop (§16).
    On the warning day it counts for much more: he needed someone."""
    n = db.npc("garrick")
    g = db.game()
    loop = g["loop_count"]
    if n is None or n["dead_this_loop"]:
        return {}
    already = db.conn().execute(
        "SELECT 1 FROM npc_memories WHERE npc_id=? AND loop_count=? AND event_text=?",
        (n["id"], loop, GARRICK_SUPPORT_TEXT),
    ).fetchone()
    if already:
        return {}
    warning_day = (not g["garrick_failed"]
                   and g["garrick_warning_loop"] == loop)
    boost = tuning.GARRICK_WARNING_SUPPORT if warning_day else tuning.GARRICK_SUPPORT
    add_memory("garrick", GARRICK_SUPPORT_TEXT, "witnessed", +2)
    db.update("npcs", n["id"], sanity=min(100, n["sanity"] + boost))
    if warning_day:
        return {"gm_note": ("He looks at you like a man pulled back from a "
                            "ledge. Today, of all days, someone came. "
                            f"(+{boost} sanity)")}
    return {"gm_note": f"Standing with Garrick steadies him a little. (+{boost} sanity)"}


def garrick_heartened(amount: int, text: str) -> None:
    """The watch isn't alone: fighting the den crews shores Garrick up."""
    g = db.npc("garrick")
    if g is None:
        return
    add_memory("garrick", text, "rumor", +3)
    db.update("npcs", g["id"],
              sanity=min(100, db.npc("garrick")["sanity"] + amount))


def shopkeeper_murdered(n) -> list[str]:
    """The shortcut that eats itself (§16): the goods are yours, the memory
    is forever, and Garrick takes the hit that speeds the collapse."""
    notes = []
    loc = db.conn().execute(
        "SELECT * FROM town_locations WHERE key=?", (n["location"],)
    ).fetchone()
    if loc is not None and loc["kind"] == "shop":
        rows = db.conn().execute(
            "SELECT * FROM shop_stock WHERE location_key=? "
            "ORDER BY premium DESC, id LIMIT 3", (loc["key"],)
        ).fetchall()
        items = {i["key"]: i
                 for i in db.load_data("gear") + db.load_data("consumables")}
        for r in rows:
            player.add_item(r["item_key"], r["qty"])
            db.conn().execute("DELETE FROM shop_stock WHERE id=?", (r["id"],))
            name = items.get(r["item_key"], {}).get("name", r["item_key"])
            notes.append(f"You take {name} from behind the counter.")
        db.conn().commit()
    add_memory("garrick",
               f"A murder in his town — {n['name']}, behind their own counter. "
               "He knows whose hands did it.", "rumor", -10)
    notes.append("Word of this will reach the watch house. It always does.")
    return notes
