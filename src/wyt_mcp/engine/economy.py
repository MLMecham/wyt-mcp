"""Gold rots: prices scale with the loop and the merchant's fraying mind."""

import random

from wyt_mcp import db


def gold_multiplier(loop_count: int) -> float:
    return 1.0 + 0.15 * (loop_count - 1)


def price_for(base_price: int, npc) -> dict:
    """Returns {'price': int, 'fear_priced': bool}."""
    g = db.game()
    mult = gold_multiplier(g["loop_count"])
    mult *= 1.5 - npc["sanity"] / 200.0          # saner merchant, saner prices
    fear = npc["sanity"] < 40 and npc["disposition"] < -40
    if fear:
        mult *= 0.5                               # they're afraid to say no to you
    return {"price": max(1, int(base_price * mult)), "fear_priced": fear}


def shop_stock(npc_key: str) -> list[dict]:
    items = db.load_data("gear") + db.load_data("consumables")
    return [i for i in items if i.get("sold_by") == npc_key]


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
