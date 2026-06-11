"""ALL ASCII output (§10). The chat window is the terminal: every
state-changing tool returns one of these blocks in its `render` field and
the GM echoes it verbatim in a code fence.

Rules: deterministic (no RNG), token-lean (~20 lines per block), and draw
only what the DB hands over — fog is enforced upstream, so these functions
cannot leak what they never receive. Isolated so the post-v1 socket
dashboard (§13) reuses them unchanged.
"""

from wyt_mcp import db
from wyt_mcp.engine import dungeon, economy, town

ROOM_MARKS = {"boss": " ☠", "artifact": " ✦"}


def status_bar() -> str:
    g, p = db.game(), db.player()
    bar = (f"Loop {g['loop_count']} · HP {p['hp']}/{p['max_hp']} · "
           f"MP {p['mp']}/{p['max_mp']} · Resolve {p['resolve']} · "
           f"Gold {p['gold']} (worth less every day)")
    if p["stat_points"]:
        bar += f" · {p['stat_points']} stat point(s) to spend"
    return bar


# ---------------------------------------------------------------- town

def town_map() -> str:
    """Fog-of-war town map: an indented tree of KNOWN streets, walked
    outward from where the player stands. Unvisited exits are nameless."""
    g = db.game()
    here = g["location"] if g["area"] == "town" else "dungeon_mouth"
    lines = [status_bar(), ""]
    seen = {here}
    _walk_town(here, 0, seen, lines, here)
    return "\n".join(lines)


def _walk_town(key: str, depth: int, seen: set, lines: list, here: str) -> None:
    loc = town.location(key)
    pad = "  " * depth
    mark = " ← you" if key == here else ""
    lines.append(f"{pad}[{loc['name']}]{mark}")
    if depth >= 3:                      # keep the block small; look() again deeper
        return
    for e in town.exits_from(key):
        if not e["known"]:
            lines.append(f"{pad}  (?) {e['desc']}")
        elif e["key"] not in seen:
            seen.add(e["key"])
            _walk_town(e["key"], depth + 1, seen, lines, here)


def town_exits() -> list[str]:
    """The legal-moves footer for look()."""
    g = db.game()
    out = []
    for e in town.exits_from(g["location"]):
        out.append(e["name"] if e["known"] else f"(?) {e['desc']}")
    return out


# ---------------------------------------------------------------- dungeon

def dungeon_map() -> str:
    """Fog-of-war floor map: visited rooms with their exits; unexplored
    edges as (?) stubs. Only the current floor is drawn."""
    g = db.game()
    if g["area"] != "dungeon":
        return town_map()
    cur = int(g["location"])
    floor = dungeon.room(cur)["floor"]
    lines = [status_bar(), "", f"— Floor {floor} —"]
    for r in dungeon.floor_rooms(floor):
        if not r["visited"]:
            continue
        mark = ROOM_MARKS.get(r["room_type"], "")
        state = " (cleared)" if r["cleared"] else ""
        you = " ← you" if r["id"] == cur else ""
        lines.append(f"[{r['name']}{mark}]{state}{you}")
        for e in dungeon.edges_from(r["id"]):
            if e["visited"]:
                continue
            stub = "stairs down" if e["label"] == "down" else e["label"]
            lines.append(f"   └─ (?) {stub}")
    lines.append("")
    lines.append("Exits: " + ", ".join(
        e["label"] + ("" if e["known"] else " (?)")
        for e in dungeon.exits(cur)))
    return "\n".join(lines)


def area_map() -> str:
    g = db.game()
    return dungeon_map() if g["area"] == "dungeon" else town_map()


# ---------------------------------------------------------------- shop

def shop_table(npc_key: str) -> str:
    n = db.npc(npc_key)
    stock = economy.shop_stock(npc_key)
    loc = town.location(n["location"])
    lines = [f"— {loc['name']} · {n['name']} —"]
    if not stock:
        lines.append("The shelves are bare.")
        return "\n".join(lines)
    for it in stock:
        p = economy.price_for(it["price"], n)
        tag = " [!]" if p["fear_priced"] else ""
        prem = " ★" if it.get("premium") else ""
        lines.append(f"{it['name']:<22}{prem:<2} {p['price']:>4}g  x{it['qty']}{tag}")
    if loc["shop_tag"] == "general" and not any(i.get("premium") for i in stock):
        lines.append("The good shelf is empty.")
    if not n["accepts_gold"]:
        lines.append("(Coin refused — barter only.)")
    return "\n".join(lines)


# ---------------------------------------------------------------- combat

def _bar(cur: int, top: int, width: int = 12) -> str:
    filled = max(0, min(width, round(width * cur / max(1, top))))
    return "▓" * filled + "░" * (width - filled)


def combat_panel(log: list[str] | None = None) -> str:
    row = db.conn().execute("SELECT * FROM combat WHERE id=1").fetchone()
    p = db.player()
    if row is None:
        return status_bar()
    lines = [
        f"⚔ {row['enemy_name']}" + ("  [BOSS]" if row["boss"] else ""),
        f"   {_bar(row['enemy_hp'], row['enemy_max_hp'])} "
        f"{row['enemy_hp']}/{row['enemy_max_hp']}",
        f"You {_bar(p['hp'], p['max_hp'])} {p['hp']}/{p['max_hp']}"
        f" · MP {p['mp']}/{p['max_mp']} · round {row['round']}",
    ]
    for line in (log or [])[-6:]:
        lines.append(f"  · {line}")
    return "\n".join(lines)
