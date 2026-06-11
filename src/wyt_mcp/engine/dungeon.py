"""Dungeon: per-loop graph generation, movement validation, room events.

Four floors. Floors 1-3 are a seeded spine-plus-branches graph rebuilt every
loop: a guaranteed path from the floor entrance to the stairs-down room
(solvable by construction), with dead-end branches where treasure lives.
The last spine room is always an enemy room — the gatekeeper — so descent
always costs a fight. Floor 4 is fixed: The Warden's Gate (boss) opens into
The Hollow Heart (artifact).

The graph lives in rooms/room_edges; movement validates against edges, so
the narrator can never invent corridors. Trap and treasure rooms resolve
one-shot on first entry, seeded from (dungeon_seed, room id).
"""

import random

from wyt_mcp import db
from wyt_mcp.engine import player

FLOORS = 4

# Spine rooms keep the pressure on; reward lives off the path, so exploring
# is a choice with a payoff.
SPINE_WEIGHTS = [("enemy", 50), ("trap", 20), ("empty", 30)]
BRANCH_WEIGHTS = [("treasure", 35), ("enemy", 25), ("trap", 25), ("empty", 15)]


def scale(loop_count: int) -> float:
    from wyt_mcp.engine import tuning
    return 1 + tuning.DUNGEON_SCALE_PER_LOOP * (loop_count - 1)


def enemy_index() -> dict:
    return {e["key"]: e for e in db.load_data("enemies")}


def enemies_for_floor(floor: int) -> list[dict]:
    return [
        e for e in db.load_data("enemies")
        if e["floor"] == floor and not e.get("boss")
    ]


def enemy_stats(key: str, loop_count: int) -> dict:
    """Enemy entry with hp/str/def/spd scaled for the current loop."""
    e = dict(enemy_index()[key])
    f = scale(loop_count)
    for stat in ("hp", "str", "def", "spd"):
        e[stat] = max(1, round(e[stat] * f))
    return e


# ---------------------------------------------------------------- generation

def generate(seed: int) -> None:
    """Rebuild the whole dungeon for this loop. Wipes rooms and edges
    (including the day-1 tutorial floor — the loop owns the architecture)."""
    c = db.conn()
    c.execute("DELETE FROM rooms")
    c.execute("DELETE FROM room_edges")
    rng = random.Random(seed)
    pool = db.load_data("room_pool")
    prev_gate = None
    for floor in (1, 2, 3):
        entrance, gate = _gen_floor(c, floor, rng, pool)
        if prev_gate is not None:
            _link(c, prev_gate, entrance, "down", "up")
        prev_gate = gate
    boss_name, heart_name = pool["boss"][0], pool["artifact"][0]
    boss_id = _make_room(c, 4, "boss", boss_name, "the_warden")
    heart_id = _make_room(c, 4, "artifact", heart_name)
    _link(c, prev_gate, boss_id, "down", "up")
    _link(c, boss_id, heart_id, heart_name, boss_name)
    # §14: what you killed before the loop began, the loop can bring back.
    # Rarely, deep, somewhere else — proof of what owns death down here.
    if db.game()["loop_count"] >= 5 and rng.random() < 0.12:
        rooms3 = c.execute(
            "SELECT id FROM rooms WHERE floor=3 AND room_type='enemy'"
        ).fetchall()
        if rooms3:
            c.execute("UPDATE rooms SET enemy_key='the_sealed_thing' WHERE id=?",
                      (rng.choice(rooms3)["id"],))
    c.commit()


SEAL_DOOR_TEXT = (
    "The way down is a single slab door, older than the town, chapel wax "
    "thick in its seams. The wax is sweating black. Thin veins of dark fire "
    "crawl the stone like something breathing behind it — and then the door "
    "opens, from the other side. What comes through could never get out. "
    "Tonight, every lock in this town is being undone, one by one, and it "
    "knew before anyone."
)


def build_tutorial() -> None:
    """§14: the pre-loop upper dark — handcrafted, identical for every save.
    Floor 0. Dies with the first regeneration at midnight."""
    c = db.conn()
    mouth = _make_room(c, 0, "empty", "Boarded Mouth")
    ante = _make_room(c, 0, "empty", "Picked-Over Antechamber")
    camp = _make_room(c, 0, "treasure", "Old Expedition Camp")
    gallery = _make_room(c, 0, "enemy", "Collapsed Gallery", "rat_swarm")
    crawl = _make_room(c, 0, "enemy", "Bone Crawl", "giant_spider")
    seal = _make_room(c, 0, "seal", "The Inner Seal", "the_sealed_thing")
    _link(c, mouth, ante, "Picked-Over Antechamber", "Boarded Mouth")
    _link(c, ante, camp, "Old Expedition Camp", "Picked-Over Antechamber")
    _link(c, ante, gallery, "Collapsed Gallery", "Picked-Over Antechamber")
    _link(c, gallery, crawl, "Bone Crawl", "Collapsed Gallery")
    _link(c, gallery, seal, "The Inner Seal", "Collapsed Gallery")
    _link(c, seal, floor_entrance(1)["id"], "down", "up")
    c.commit()


def _tutorial_mouth():
    return db.conn().execute(
        "SELECT * FROM rooms WHERE floor=0 ORDER BY id LIMIT 1"
    ).fetchone()


def seal_fight_won() -> bool:
    return db.conn().execute(
        "SELECT 1 FROM rooms WHERE floor=0 AND room_type='seal' AND cleared=1"
    ).fetchone() is not None


def _gen_floor(c, floor: int, rng: random.Random, pool: dict) -> tuple[int, int]:
    """Build one floor; returns (entrance_id, gatekeeper_id)."""
    names = {
        t: rng.sample(pool[t], len(pool[t]))
        for t in ("enemy", "trap", "treasure", "empty")
    }
    tier = enemies_for_floor(floor)
    spine_n = 4 if floor == 1 else rng.randint(4, 5)
    branch_n = rng.randint(1, 3) if floor == 1 else rng.randint(2, 3)

    # Entrance is always a landing; the gatekeeper guards the stairs.
    spine_types = (["empty"]
                   + [_pick(rng, SPINE_WEIGHTS) for _ in range(spine_n - 2)]
                   + ["enemy"])
    branch_types = [_pick(rng, BRANCH_WEIGHTS) for _ in range(branch_n)]

    if floor == 1:
        # Mercy rule: one trap at most before the player has gear.
        seen_trap = False
        for types in (spine_types, branch_types):
            for i, t in enumerate(types):
                if t == "trap":
                    if seen_trap:
                        types[i] = "empty"
                    seen_trap = True

    if (spine_types + branch_types).count("enemy") < 2:
        # It's a dungeon: guarantee a second fight somewhere.
        idxs = [i for i, t in enumerate(branch_types) if t != "enemy"]
        if idxs:
            branch_types[rng.choice(idxs)] = "enemy"
        else:
            spine_types[1] = "enemy"

    def make(rtype: str) -> tuple[int, str]:
        enemy_key = rng.choice(tier)["key"] if rtype == "enemy" else None
        name = names[rtype].pop() if names[rtype] else f"Unmarked Chamber {floor}"
        return _make_room(c, floor, rtype, name, enemy_key), name

    spine = [make(t) for t in spine_types]
    gate_id = spine[-1][0]
    gatekeeper = max(tier, key=lambda e: e["xp"])
    c.execute("UPDATE rooms SET enemy_key=? WHERE id=?", (gatekeeper["key"], gate_id))

    for (a_id, a_name), (b_id, b_name) in zip(spine, spine[1:]):
        _link(c, a_id, b_id, b_name, a_name)
    for t in branch_types:
        b_id, b_name = make(t)
        a_id, a_name = rng.choice(spine)
        _link(c, a_id, b_id, b_name, a_name)
        others = [s for s in spine if s[0] != a_id]
        if others and rng.random() < 0.25:
            o_id, o_name = rng.choice(others)
            _link(c, o_id, b_id, b_name, o_name)
    return spine[0][0], gate_id


def _pick(rng: random.Random, weights: list[tuple[str, int]]) -> str:
    types, w = zip(*weights)
    return rng.choices(types, weights=w)[0]


def _make_room(c, floor: int, room_type: str, name: str,
               enemy_key: str | None = None) -> int:
    cur = c.execute(
        "INSERT INTO rooms (floor, room_type, name, enemy_key) VALUES (?,?,?,?)",
        (floor, room_type, name, enemy_key),
    )
    return cur.lastrowid


def _link(c, a: int, b: int, label_ab: str, label_ba: str) -> None:
    c.execute("INSERT INTO room_edges (from_room, to_room, label) VALUES (?,?,?)",
              (a, b, label_ab))
    c.execute("INSERT INTO room_edges (from_room, to_room, label) VALUES (?,?,?)",
              (b, a, label_ba))


# ---------------------------------------------------------------- queries

def room(room_id: int):
    return db.conn().execute(
        "SELECT * FROM rooms WHERE id=?", (room_id,)
    ).fetchone()


def floor_rooms(floor: int) -> list:
    return db.conn().execute(
        "SELECT * FROM rooms WHERE floor=? ORDER BY id", (floor,)
    ).fetchall()


def floor_entrance(floor: int):
    # Spine is created entrance-first, so MIN(id) per floor is the entrance.
    return db.conn().execute(
        "SELECT * FROM rooms WHERE floor=? ORDER BY id LIMIT 1", (floor,)
    ).fetchone()


def edges_from(room_id: int) -> list:
    return db.conn().execute(
        "SELECT e.to_room, e.label, r.visited, r.room_type, r.floor "
        "FROM room_edges e JOIN rooms r ON r.id = e.to_room "
        "WHERE e.from_room=?",
        (room_id,),
    ).fetchall()


def exits(room_id: int) -> list[dict]:
    """Legal exits only; `known` False = render as (?) stub."""
    out = [
        {"label": e["label"], "to_room": e["to_room"], "known": bool(e["visited"])}
        for e in edges_from(room_id)
    ]
    r = room(room_id)
    if ((r["floor"] == 1 and floor_entrance(1)["id"] == room_id)
            or (r["floor"] == 0 and _tutorial_mouth()["id"] == room_id)):
        out.append({"label": "out", "to_room": None, "known": True})
    return out


def entered_this_loop() -> bool:
    """Rooms are wiped each loop, so any visited row means 'entered today'."""
    return db.conn().execute(
        "SELECT 1 FROM rooms WHERE visited=1 LIMIT 1"
    ).fetchone() is not None


# ---------------------------------------------------------------- movement

def descend(floor: int) -> dict:
    """'You remember the way' — jump to a floor's entrance (§9).

    On loop 1 (§14): you can pry the boards and enter the upper dark — the
    handcrafted tutorial floor. The way deeper is the chapel's inner seal."""
    g = db.game()
    if g["area"] == "town" and g["location"] != "dungeon_mouth":
        return {"error": "You must be at the dungeon mouth to descend."}
    if g["loop_count"] == 1:
        out = enter(_tutorial_mouth()["id"])
        out["pre_loop_dungeon"] = (
            "The upper dark, boarded for twenty years: dust, old bones, a "
            "dead expedition's leavings, and a few things that fed on the "
            "dark. The way deeper is the chapel's sealed door. Narrate a "
            "tomb — until it isn't. (The watch would not approve of the "
            "pried boards.)")
        return out
    if not 1 <= floor <= FLOORS:
        return {"error": f"There is no floor {floor}."}
    if floor > g["max_floor_cleared"] + 1:
        return {"error": "You don't know the way down that deep. Not yet."}
    return enter(floor_entrance(floor)["id"])


def move(label: str) -> dict:
    g = db.game()
    if g["area"] != "dungeon":
        return {"error": "You are not in the dungeon."}
    cur = int(g["location"])
    r = room(cur)
    at_exit = (floor_entrance(1)["id"] == cur if r["floor"] == 1
               else (r["floor"] == 0 and _tutorial_mouth()["id"] == cur))
    if label.lower() in ("out", "leave") and at_exit:
        db.set_game(area="town", location="dungeon_mouth")
        return {"area": "town", "location": "dungeon_mouth",
                "note": "You climb back out into the grey daylight."}
    for e in edges_from(cur):
        if e["label"].lower() == label.lower():
            if e["label"] == "down" and r["floor"] == 0:
                # The inner seal: the thing must be dead and the key in hand.
                if not r["cleared"]:
                    return {"error": "The seal door hangs broken — and what "
                                     "came through it is still in the room."}
                has_key = db.conn().execute(
                    "SELECT 1 FROM inventory WHERE item_key='chapel_key'"
                ).fetchone()
                if not has_key:
                    return {"error": "Past the broken slab, the chapel's "
                                     "inner wards still hold the stair. "
                                     "Bren keeps what's left of the seal."}
            return enter(e["to_room"])
    return {"error": f"No exit '{label}' from here.", "exits": exits(cur)}


def enter(room_id: int) -> dict:
    """Move into a room and resolve what's in it (one-shot for trap/treasure)."""
    r = room(room_id)
    g = db.game()
    came_from = g["location"] if g["area"] == "dungeon" else None
    db.set_game(area="dungeon", location=str(room_id))
    first = not r["visited"]
    if first:
        db.update("rooms", room_id, visited=1)
    out = {
        "room": {"id": r["id"], "name": r["name"], "floor": r["floor"],
                 "type": r["room_type"], "cleared": bool(r["cleared"])},
        "first_visit": first,
        "exits": exits(room_id),
    }
    if r["room_type"] in ("enemy", "boss") and not r["cleared"]:
        out["combat_required"] = True
        out["enemy_key"] = r["enemy_key"]
        out["from_location"] = came_from  # a successful flee retreats here
    elif r["room_type"] == "seal" and not r["cleared"]:
        # §14: the loop is picking the locks hours early. Bone-chilling, then
        # the fight — nobody, Bren included, knew this was at the door.
        out["the_door"] = SEAL_DOOR_TEXT
        out["combat_required"] = True
        out["enemy_key"] = r["enemy_key"]
        out["from_location"] = came_from
    elif r["room_type"] == "trap" and not r["cleared"]:
        out.update(_spring_trap(r))
    elif r["room_type"] == "treasure" and not r["cleared"]:
        if r["floor"] == 0:
            # The dead expedition's camp: the locket is always here.
            db.update("rooms", r["id"], cleared=1)
            player.add_item("delvers_locket")
            db.set_player(gold=db.player()["gold"] + 12)
            out["treasure"] = {
                "item": "delvers_locket", "gold": 12,
                "note": ("A dead delver's locket, twenty years in the dust. "
                         "Garrick will know whose it was."),
            }
        else:
            out.update(_open_cache(r))
    elif r["room_type"] == "artifact":
        out["artifact_here"] = not db.game()["has_artifact"]
    return out


# ---------------------------------------------------------------- room events

def _spring_trap(r) -> dict:
    g = db.game()
    rng = random.Random(g["dungeon_seed"] * 31 + r["id"])
    raw = (rng.randint(3, 6) + 2 * r["floor"]) * scale(g["loop_count"])
    dmg = max(1, round(raw) - player.defense() // 3)
    p = db.player()
    hp = p["hp"] - dmg
    db.set_player(hp=max(0, hp))
    db.update("rooms", r["id"], cleared=1)
    out = {"trap": {"name": r["name"], "damage": dmg, "hp": max(0, hp)}}
    if hp <= 0:
        from wyt_mcp.engine import days  # local import breaks the cycle
        if g["loop_count"] == 1 and not g["ended"]:
            out["rescued"] = days.first_day_rescue()
        else:
            out["died"] = True
            out["overnight"] = days.advance_loop("died")
    return out


def _open_cache(r) -> dict:
    g = db.game()
    rng = random.Random(g["dungeon_seed"] * 37 + r["id"])
    db.update("rooms", r["id"], cleared=1)
    if rng.random() < 0.4:
        if r["floor"] >= 3 and rng.random() < 0.3:
            item = rng.choice(db.load_data("gear"))["key"]
        else:
            item = rng.choice(db.load_data("consumables"))["key"]
        player.add_item(item)
        return {"treasure": {"item": item}}
    gold = rng.randint(8, 18) * r["floor"]
    db.set_player(gold=db.player()["gold"] + gold)
    return {"treasure": {"gold": gold}}


def mark_cleared(room_id: int) -> list[str]:
    """Called by combat on victory. Clearing the stairs guard clears the floor."""
    r = room(room_id)
    db.update("rooms", room_id, cleared=1)
    notes = []
    g = db.game()
    guards_stairs = (r["room_type"] == "boss"
                     or any(e["label"] == "down" for e in edges_from(room_id)))
    if g["loop_count"] == 1 and guards_stairs and r["floor"] > 0:
        # §14: nothing you prove before the first midnight is remembered.
        notes.append("Whatever you cleared down here, the night will not "
                     "keep it. (No floor progress is saved on loop 1.)")
        return notes
    if guards_stairs and r["floor"] > g["max_floor_cleared"]:
        db.set_game(max_floor_cleared=r["floor"])
        from wyt_mcp.engine import tuning
        player.change_resolve(tuning.RESOLVE_FLOOR_CLEAR,
                              f"cleared dungeon floor {r['floor']}")
        notes.append(f"Floor {r['floor']} cleared — you'll remember the way.")
        bonus = 25 * r["floor"]  # first-ever clear only; never re-grindable
        notes.append(f"+{bonus} xp for charting the way down.")
        notes += player.gain_xp(bonus)
    return notes
