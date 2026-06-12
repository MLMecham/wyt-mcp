"""Gold rots: prices scale with the loop and the merchant's fraying mind."""

import random

from wyt_mcp import db
from wyt_mcp.engine import tuning


def gold_multiplier(loop_count: int) -> float:
    return 1.0 + tuning.GOLD_ROT_PER_LOOP * (loop_count - 1)


def price_for(base_price: int, npc) -> dict:
    """Returns {'price': int, 'fear_priced': bool, 'friend_priced': bool}.
    Prices are a tell: fear undercuts, friendship shaves. Never both."""
    g = db.game()
    mult = gold_multiplier(g["loop_count"])
    mult *= 1.5 - npc["sanity"] / 200.0          # saner merchant, saner prices
    fear = npc["sanity"] < 40 and npc["disposition"] < -40
    friend = (not fear
              and npc["disposition"] >= tuning.FRIEND_PRICE_DISPOSITION)
    if fear:
        mult *= 0.5                               # they're afraid to say no to you
    elif friend:
        mult *= tuning.FRIEND_PRICE_RATE          # mates' rates, because it's you
    return {"price": max(1, int(base_price * mult)),
            "fear_priced": fear, "friend_priced": friend}


SELL_RATE = tuning.SELL_RATE
WRONG_SHOP_RATE = tuning.WRONG_SHOP_RATE
FENCE_MARKUP = tuning.FENCE_MARKUP


def _item_index() -> dict:
    return {i["key"]: i
            for i in db.load_data("gear") + db.load_data("consumables")}


def shop_stock(npc_key: str) -> list[dict]:
    """What's on this merchant's shelves right now — shop_stock rows joined
    with item data. Robberies (§16) may have emptied the good shelf."""
    n = db.npc(npc_key)
    if n is None:
        return []
    idx = _item_index()
    rows = db.conn().execute(
        "SELECT * FROM shop_stock WHERE location_key=?", (n["location"],)
    ).fetchall()
    return [dict(idx[r["item_key"]], qty=r["qty"], premium=bool(r["premium"]))
            for r in rows if r["item_key"] in idx]


def take_from_stock(location_key: str, item_key: str) -> bool:
    """Decrement a stock row after a sale; False if it isn't on the shelf."""
    c = db.conn()
    row = c.execute(
        "SELECT id, qty FROM shop_stock WHERE location_key=? AND item_key=?",
        (location_key, item_key),
    ).fetchone()
    if row is None:
        return False
    if row["qty"] > 1:
        c.execute("UPDATE shop_stock SET qty=qty-1 WHERE id=?", (row["id"],))
    else:
        c.execute("DELETE FROM shop_stock WHERE id=?", (row["id"],))
    c.commit()
    return True


def sell_price(item_key: str, npc) -> dict:
    """Selling: the right counter pays half; the wrong one a quarter — and
    Wendel only deals in his own kind of strange (§8/§16)."""
    from wyt_mcp.engine import town

    item = _item_index().get(item_key)
    if item is None:
        return {"error": f"'{item_key}' is not a sellable item."}
    loc = town.location(npc["location"])
    if loc is None or loc["kind"] != "shop":
        return {"refused": True, "note": f"{npc['name']} doesn't buy things."}
    match = item.get("shop") == loc["shop_tag"]
    if not match and loc["shop_tag"] == "magic":
        return {"refused": True,
                "note": "Wendel glances at it, then back at his chickens."}
    base = price_for(item["price"], npc)["price"]
    rate = SELL_RATE if match else WRONG_SHOP_RATE
    return {"price": max(1, int(base * rate)), "wrong_shop": not match}


def fence_price(item_key: str, loop_count: int) -> int:
    """Buying your own town's goods back from the den that stole them."""
    item = _item_index()[item_key]
    return max(1, int(item["price"] * gold_multiplier(loop_count) * FENCE_MARKUP))


def roll_gold_acceptance(loop_count: int, rng: random.Random) -> list[str]:
    """Per-night chance that a broken merchant stops believing in coin."""
    notes = []
    for n in db.npcs_all():
        if n["shop"] and n["accepts_gold"] and n["sanity"] < 30 and rng.random() < 0.25:
            db.update("npcs", n["id"], accepts_gold=0,
                      gate_reason="gold means nothing to the dead")
            notes.append(
                f"{n['name']} swept a customer's coins off the counter this morning. "
                f"'Bring me something REAL.' Barter only now."
            )
    return notes
