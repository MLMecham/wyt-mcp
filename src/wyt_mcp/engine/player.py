"""Player stats, classes, leveling, inventory, resolve and conduct counters."""

from wyt_mcp import db

CLASSES = {
    "warrior": {
        "hp": 60, "mp": 4, "str": 8, "def": 6, "spd": 4, "mag": 1, "gold": 40,
        "start_items": ["rusty_sword", "leather_armor", "potion"],
        "start_equipped": ["rusty_sword", "leather_armor"],
        "backstory": (
            "Captain Garrick's son, raised inside these walls. The town starts warm "
            "toward you — which means you get to watch people who love you unravel."
        ),
        "local": True,
        "abilities": {"cleave": {"mp": 2, "mult": 1.6, "stat": "str"}},
        "growth": {"hp": 8, "mp": 1},
    },
    "mage": {
        "hp": 40, "mp": 16, "str": 3, "def": 3, "spd": 5, "mag": 8, "gold": 50,
        "start_items": ["oak_staff", "potion", "mana_draught"],
        "start_equipped": ["oak_staff"],
        "backstory": (
            "A traveling scholar who arrived the night before the sky split open. "
            "The town is polite, but someone will eventually say it out loud: the "
            "loop started when you did."
        ),
        "local": False,
        "abilities": {"firebolt": {"mp": 3, "mult": 2.0, "stat": "mag"}},
        "growth": {"hp": 4, "mp": 3},
    },
    "archer": {
        "hp": 48, "mp": 8, "str": 6, "def": 4, "spd": 7, "mag": 2, "gold": 45,
        "start_items": ["hunting_bow", "potion"],
        "start_equipped": ["hunting_bow"],
        "backstory": (
            "A hunter from the hills above town. Half-known: they buy your game, "
            "they don't invite you in. You've watched the town from outside for "
            "years; now you're locked in with it."
        ),
        "local": False,
        "abilities": {"aimed_shot": {"mp": 2, "mult": 1.8, "stat": "str"}},
        "growth": {"hp": 6, "mp": 2},
    },
    "ninja": {
        "hp": 44, "mp": 8, "str": 7, "def": 3, "spd": 9, "mag": 2, "gold": 35,
        "start_items": ["twin_daggers", "potion"],
        "start_equipped": ["twin_daggers"],
        "backstory": (
            "A stranger nobody can place, carrying steel nobody asked about. "
            "Distrusted from the first dawn. Hard mode."
        ),
        "local": False,
        "abilities": {"shadow_strike": {"mp": 3, "mult": 2.0, "stat": "str"}},
        "growth": {"hp": 5, "mp": 2},
    },
}


def gear_index() -> dict:
    return {g["key"]: g for g in db.load_data("gear")}


def consumable_index() -> dict:
    return {c["key"]: c for c in db.load_data("consumables")}


def item_index() -> dict:
    idx = gear_index()
    idx.update(consumable_index())
    return idx


def equipped_items() -> list[dict]:
    gear = gear_index()
    rows = db.conn().execute(
        "SELECT item_key FROM inventory WHERE equipped=1"
    ).fetchall()
    return [gear[r["item_key"]] for r in rows if r["item_key"] in gear]


def _bonus(stat: str) -> int:
    return sum(g.get(stat, 0) for g in equipped_items())


def attack_power() -> int:
    p = db.player()
    return p["str"] + _bonus("atk")


def magic_power() -> int:
    p = db.player()
    return p["mag"] + _bonus("mag")


def defense() -> int:
    p = db.player()
    return p["def"] + _bonus("def")


def speed() -> int:
    p = db.player()
    return p["spd"] + _bonus("spd")


def power_score() -> int:
    """Rough overall power, used for combat threat ratios.

    Folds in active damage buffs/debuffs so a weakened day reads as the
    danger it actually is when auto mode sizes up a fight.
    """
    from wyt_mcp.engine import effects

    p = db.player()
    base = p["level"] * 3 + max(attack_power(), magic_power()) + defense() // 2
    return max(1, round(base * effects.mult("player", "damage_mult")))


def xp_needed(level: int) -> int:
    return 25 * level


def gain_xp(amount: int) -> list[str]:
    """Apply XP; returns human-readable level-up notes.

    Per level: +1 to all four stats, class-specific HP/MP, full heal,
    and 1 banked stat point the player assigns via spend_point().
    """
    p = db.player()
    xp, level = p["xp"] + amount, p["level"]
    growth = CLASSES[p["class"]]["growth"]
    cur = dict(p)
    notes = []
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
        cur["max_hp"] += growth["hp"]
        cur["max_mp"] += growth["mp"]
        for s in ("str", "def", "spd", "mag"):
            cur[s] += 1
        cur["stat_points"] += 1
        notes.append(f"Level up! You are now level {level}. "
                     "(+1 stat point to spend — ask where it goes)")
    fields = {}
    if notes:
        fields = {k: cur[k] for k in
                  ("max_hp", "max_mp", "str", "def", "spd", "mag", "stat_points")}
        fields["hp"], fields["mp"] = cur["max_hp"], cur["max_mp"]
    db.set_player(xp=xp, level=level, **fields)
    return notes


def spend_point(stat: str) -> dict:
    """Assign one banked level-up point to str/def/spd/mag."""
    if stat not in ("str", "def", "spd", "mag"):
        return {"error": f"'{stat}' is not a stat. Choose str, def, spd or mag."}
    p = db.player()
    if p["stat_points"] < 1:
        return {"error": "No stat points to spend."}
    db.set_player(stat_points=p["stat_points"] - 1, **{stat: p[stat] + 1})
    return {"ok": True, "stat": stat, "new_value": p[stat] + 1,
            "points_left": p["stat_points"] - 1}


def equip(item_key: str) -> dict:
    """Equip gear; one item per slot (weapon / armor / accessory — §16)."""
    gear = gear_index()
    if item_key not in gear:
        return {"error": f"'{item_key}' is not equippable gear."}
    c = db.conn()
    row = c.execute(
        "SELECT id FROM inventory WHERE item_key=?", (item_key,)
    ).fetchone()
    if row is None:
        return {"error": "You don't have that."}
    slot = gear[item_key]["slot"]
    swapped = None
    for r in c.execute("SELECT item_key FROM inventory WHERE equipped=1").fetchall():
        if r["item_key"] != item_key and gear.get(r["item_key"], {}).get("slot") == slot:
            swapped = gear[r["item_key"]]["name"]
            c.execute("UPDATE inventory SET equipped=0 WHERE item_key=?",
                      (r["item_key"],))
    c.execute("UPDATE inventory SET equipped=1 WHERE id=?", (row["id"],))
    c.commit()
    out = {"ok": True, "equipped": gear[item_key]["name"], "slot": slot}
    if swapped:
        out["unequipped"] = swapped
    return out


def use_consumable(item_key: str) -> dict:
    """Out-of-combat item use: heal/mana/resolve, day buffs, the town map."""
    from wyt_mcp.engine import effects, town  # function-local: avoids cycles

    cons = consumable_index()
    if item_key not in cons:
        return {"error": f"'{item_key}' is not usable."}
    if not remove_item(item_key):
        return {"error": "You don't have that."}
    item, notes = cons[item_key], []
    if item.get("heal"):
        heal(hp=item["heal"])
        notes.append(f"+{item['heal']} HP")
    if item.get("mana"):
        heal(mp=item["mana"])
        notes.append(f"+{item['mana']} MP")
    if item.get("resolve"):
        change_resolve(item["resolve"])
        notes.append(f"+{item['resolve']} resolve")
    if item.get("buff"):
        b = item["buff"]
        effects.add("player", b["kind"], b["value"], rounds=None)
        notes.append(f"{b['kind']} {b['value']} until you sleep")
    if item.get("reveal_town"):
        notes.append(town.reveal_map())
    return {"ok": True, "used": item["name"], "effects": notes}


def add_item(item_key: str, qty: int = 1) -> None:
    c = db.conn()
    row = c.execute(
        "SELECT id FROM inventory WHERE item_key=? AND equipped=0", (item_key,)
    ).fetchone()
    if row:
        c.execute("UPDATE inventory SET qty=qty+? WHERE id=?", (qty, row["id"]))
    else:
        c.execute("INSERT INTO inventory (item_key, qty) VALUES (?,?)",
                  (item_key, qty))
    c.commit()


def remove_item(item_key: str) -> bool:
    """Consume one of an unequipped item; False if none held."""
    c = db.conn()
    row = c.execute(
        "SELECT id, qty FROM inventory WHERE item_key=? AND equipped=0", (item_key,)
    ).fetchone()
    if row is None:
        return False
    if row["qty"] > 1:
        c.execute("UPDATE inventory SET qty=qty-1 WHERE id=?", (row["id"],))
    else:
        c.execute("DELETE FROM inventory WHERE id=?", (row["id"],))
    c.commit()
    return True


def heal(hp: int = 0, mp: int = 0) -> None:
    p = db.player()
    db.set_player(
        hp=min(p["max_hp"], p["hp"] + hp),
        mp=min(p["max_mp"], p["mp"] + mp),
    )


def change_resolve(delta: int, reason: str = "") -> int:
    """Clamp resolve to 0..100 and return the new value.

    The breaking-point check itself lives in engine.endings — callers that
    can push resolve to zero should call endings.check_breaking_point() after.
    """
    p = db.player()
    new = max(0, min(100, p["resolve"] + delta))
    db.set_player(resolve=new)
    if reason:
        db.log_event(f"[resolve {delta:+d}] {reason}",
                     tone="despairing" if delta < 0 else None)
    return new


def add_conduct(brutality: int = 0, despair: int = 0) -> None:
    p = db.player()
    db.set_player(
        brutality=p["brutality"] + brutality,
        despair=p["despair"] + despair,
    )
