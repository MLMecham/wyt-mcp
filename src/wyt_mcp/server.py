"""wyt-mcp: the rules engine Claude narrates from (§1).

Thin tool wrappers; logic lives in engine/. Every state-changing tool
returns a `render` field the GM echoes verbatim. Claude can never be
sweet-talked past a rule because the rules aren't in the prompt.
"""

import random

from mcp.server.fastmcp import FastMCP

from wyt_mcp import db, render
from wyt_mcp.engine import (combat, days, dungeon, economy, endings, player,
                            town, tuning)

mcp = FastMCP("wyt-mcp")


def _rng(tag: str) -> random.Random:
    g = db.game()
    return random.Random(f"{g['dungeon_seed']}:{g['loop_count']}:{tag}")


# Permissions warm-up (F9): Desktop pops a permission dialog the first time
# each tool is called, and the dialog names the tool — mid-game that breaks
# flow and can spoil (claim_artifact, request_chapel_key...). While the flag
# is on, every tool returns immediately without touching state, so the GM can
# sweep all the prompts at table-setting time.
_WARMUP = {"on": False}


def _warming() -> dict | None:
    if _WARMUP["on"]:
        return {"warmup": True,
                "note": "Permissions warm-up — no action taken."}
    return None


def _guard() -> dict | None:
    """Most town verbs are illegal mid-fight or post-ending."""
    w = _warming()
    if w:
        return w
    if not db.has_save():
        return {"error": "No save. Call new_game first."}
    g = db.game()
    if g["ended"]:
        return {"error": "The game has ended.", "ending": g["ending_key"]}
    if combat.active() is not None:
        return {"error": "You are in combat. Resolve it: combat_action "
                         "(strike / ability <name> / use_item <item> / "
                         "defend / flee)."}
    return None


def _check_wizard(out: dict) -> dict:
    """combat reports WIZARD_DEFEATED in its notes; the ending is ours."""
    notes = out.get("notes") or []
    if any("WIZARD_DEFEATED" in n for n in notes):
        out["notes"] = [n for n in notes if "WIZARD_DEFEATED" not in n]
        out["ended"] = endings.wizard_defeated()
    return out


# ---------------------------------------------------------------- lifecycle

@mcp.tool()
def warmup_begin() -> dict:
    """Start the permissions warm-up. Call FIRST at session start, then call
    every other wyt tool once with dummy arguments — each returns a no-op
    acknowledgment while the client collects its permission approvals — then
    warmup_end. No game state is read or written during warm-up."""
    _WARMUP["on"] = True
    return {"warmup": True, "note": "Warm-up on. Call every tool once "
                                    "(dummy args are fine), then warmup_end."}


@mcp.tool()
def warmup_end() -> dict:
    """End the permissions warm-up. Tools act for real again after this."""
    _WARMUP["on"] = False
    return {"warmup": False, "note": "Warm-up over. The table is set."}


@mcp.tool()
def new_game(name: str, player_class: str, skip_intro: bool = False) -> dict:
    """Start a fresh save. player_class: warrior | mage | archer | ninja —
    each carries a backstory that seeds how the town receives you.
    If the player names a class not on this list, pass it through verbatim —
    the engine decides whether it exists.
    skip_intro=True (replays only) starts at loop 2 with a recap."""
    w = _warming()
    if w:
        return w
    out = days.new_game(name, player_class, skip_intro)
    if "error" not in out:
        out["render"] = render.town_map()
    return out


@mcp.tool()
def get_state() -> dict:
    """Full rehydration packet — call at session start; chat history is
    never load-bearing."""
    w = _warming()
    if w:
        return w
    if not db.has_save():
        return {"error": "No save. Call new_game first."}
    g, p = db.game(), db.player()
    inv = [{"item": r["item_key"], "qty": r["qty"], "equipped": bool(r["equipped"])}
           for r in db.conn().execute("SELECT * FROM inventory")]
    return {
        "loop": g["loop_count"], "area": g["area"], "location": g["location"],
        "ended": bool(g["ended"]), "ending": g["ending_key"],
        "player": {k: p[k] for k in
                   ("name", "class", "level", "xp", "hp", "max_hp", "mp",
                    "max_mp", "gold", "str", "def", "spd", "mag", "resolve",
                    "stat_points")},
        "inventory": inv,
        "in_combat": combat.active() is not None,
        "town_avg_sanity": round(town.town_avg_sanity()),
        "max_floor_cleared": g["max_floor_cleared"],
        "render": render.area_map(),
    }


@mcp.tool()
def recap() -> dict:
    """'Previously on' — last loop's events, open wounds, who hates you now.
    For resuming in a fresh chat."""
    w = _warming()
    if w:
        return w
    if not db.has_save():
        return {"error": "No save. Call new_game first."}
    g = db.game()
    loop = g["loop_count"]
    events = [e["text"] for e in db.events_for_loop(loop - 1)]
    events += [e["text"] for e in db.events_for_loop(loop)]
    grudges = [
        {"name": n["name"], "disposition": n["disposition"],
         "hostile": bool(n["hostile"]), "why": n["gate_reason"]}
        for n in db.npcs_all()
        if n["disposition"] <= -30 or n["hostile"]
    ]
    return {"loop": loop, "recent_events": events[-12:], "grudges": grudges,
            "resolve": db.player()["resolve"],
            "render": render.area_map()}


@mcp.tool()
def advance_loop(cause: str = "slept") -> dict:
    """End the day: the player goes to bed (cause='slept' — the only value
    you may pass; deaths advance the loop on their own). Returns the
    overnight packet to narrate waking up at the tavern."""
    err = _guard()
    if err:
        return err
    if cause != "slept":
        return {"error": "You only ever pass 'slept'. Death handles itself."}
    out = days.advance_loop("slept")
    out["render"] = render.town_map()
    return out


# ---------------------------------------------------------------- moving & looking

@mcp.tool()
def look() -> dict:
    """Server-rendered map + status + legal exits. Always safe to resync."""
    w = _warming()
    if w:
        return w
    if not db.has_save():
        return {"error": "No save. Call new_game first."}
    g = db.game()
    out = {"area": g["area"], "render": render.area_map()}
    if g["area"] == "town":
        out["exits"] = render.town_exits()
        out["people_here"] = [n["name"] for n in town.npcs_at(g["location"])]
        loc = town.location(g["location"])
        if loc["kind"] == "den":
            stock = town.den_stock(g["location"])
            if stock:
                out["den_note"] = ("Stolen goods here, watched by hard people. "
                                   "Raid (attack den_keeper) or buy them back "
                                   "(fence).")
    else:
        out["exits"] = dungeon.exits(int(g["location"]))
    if combat.active() is not None:
        out["render"] = render.combat_panel()
        out["note"] = "You are in combat."
    return out


@mcp.tool()
def move(exit: str) -> dict:
    """Move through a known exit (town or dungeon). Entering an uncleared
    enemy room, or transiting a rough part of town, can start a fight."""
    err = _guard()
    if err:
        return err
    g = db.game()
    if g["area"] == "dungeon":
        out = dungeon.move(exit)
        if out.get("combat_required"):
            out["combat"] = combat.begin(
                enemy_key=out["enemy_key"], mode="rounds",
                room_id=out["room"]["id"],
                from_location=out.get("from_location"))
            out["render"] = render.combat_panel(out["combat"].get("log"))
        elif out.get("died"):
            out["render"] = render.town_map()
        else:
            out["render"] = render.dungeon_map()
        return out

    # town movement
    target = None
    for e in town.exits_from(g["location"]):
        if exit.lower() in (e["key"].lower(),
                            e.get("name", "").lower(), e.get("desc", "").lower()):
            target = e
            break
    if target is None:
        return {"error": f"No street from here matches '{exit}'.",
                "exits": render.town_exits()}
    town.visit(target["key"])
    db.set_game(location=target["key"])
    out = {"location": target["key"],
           "name": town.location(target["key"])["name"],
           "people_here": [n["name"] for n in town.npcs_at(target["key"])]}
    amb = town.roll_ambush(target["key"], random)
    if amb:
        out["ambush"] = combat.begin(enemy_key=amb, mode="rounds")
        out["note"] = "Someone comes out of the dark at you."
        out["render"] = render.combat_panel()
    else:
        out["render"] = render.town_map()
    return out


@mcp.tool()
def descend(floor: int = 1) -> dict:
    """Enter the dungeon at the mouth. 'You remember the way': any floor up
    to max_floor_cleared + 1."""
    err = _guard()
    if err:
        return err
    out = dungeon.descend(floor)
    if out.get("combat_required"):
        out["combat"] = combat.begin(enemy_key=out["enemy_key"], mode="rounds",
                                     room_id=out["room"]["id"])
        out["render"] = render.combat_panel()
    elif "error" not in out:
        out["render"] = render.dungeon_map()
    return out


# ---------------------------------------------------------------- people

@mcp.tool()
def talk_to(npc_key: str) -> dict:
    """The NPC packet: personality, sanity tier, disposition, gates, the
    memories that matter, and 2-3 status candidates you may apply. You
    improvise the dialogue FROM this — never against it."""
    err = _guard()
    if err:
        return err
    n = db.npc(npc_key)
    if n is None:
        return {"error": f"No one called '{npc_key}'."}
    g = db.game()
    if n["location"] != g["location"] or g["area"] != "town":
        return {"error": f"{n['name']} isn't here. Last known: {n['location']}."}
    if (n["withdrawn"] and not n["dead_this_loop"]
            and n["disposition"] < tuning.RAPPORT_FRIEND):
        return town.withdrawn_refusal(n)
    packet = town.npc_packet(npc_key, g["loop_count"], _rng(f"talk:{npc_key}"))
    if npc_key == "garrick":
        packet.update(town.support_garrick())
    return packet


@mcp.tool()
def ask_directions(npc_key: str) -> dict:
    """A willing local points you toward one street you don't know yet.
    Costs nothing but standing — withdrawn or hating NPCs refuse."""
    err = _guard()
    if err:
        return err
    out = town.ask_directions(npc_key, _rng(f"directions:{npc_key}"))
    if out.get("revealed"):
        out["render"] = render.town_map()
    return out


@mcp.tool()
def request_chapel_key(answer: str, sincerity: str) -> dict:
    """Day 1 only, at the chapel, AFTER the player has killed what came
    through the inner seal. Roleplay Bren's question first — why do you want
    the deep? — then pass the player's actual answer and your honest read of
    it: sincerity = honest | uncertain | selfish | flippant. Honesty and
    admitted uncertainty earn the key; greed, bravado and mockery are
    refused, finally. Either way Bren remembers their words verbatim,
    forever. Judge fairly — this is the one place your judgment is the rule."""
    err = _guard()
    if err:
        return err
    return town.request_chapel_key(answer, sincerity)


@mcp.tool()
def rapport(npc_key: str, hit: bool, why: str) -> dict:
    """Once per NPC per day: report whether the player genuinely spoke this
    NPC's language — judged against the packet's `manner` field, never your
    own taste. A well-aimed insult is a hit with Marta; flattery is a miss.
    'Father' said sincerely is a hit with Bren; said smirking, a miss.
    hit=True is +1 (and steadies the player, once per day); hit=False is -1.
    Miss more than you hit — rapport is earned, not pleasant. Deeds still
    dwarf words."""
    err = _guard()
    if err:
        return err
    return town.rapport(npc_key, hit, why)


@mcp.tool()
def npc_reward(npc_key: str, gold: int, reason: str, item_key: str = "") -> dict:
    """An NPC pays or gifts the player — the ONLY way gold or items ever
    pass from an NPC. The server caps total value (loop-scaled) and allows
    one reward per NPC per day. If this refuses, that is canon: the NPC
    genuinely cannot pay — narrate the inability (an IOU, an apology, a
    promise they can't keep). Never narrate payment without this tool."""
    err = _guard()
    if err:
        return err
    return town.npc_reward(npc_key, gold, reason, item_key)


@mcp.tool()
def give_item(npc_key: str, item_key: str) -> dict:
    """The player hands an NPC an item, for keeps. They remember — and
    memories survive midnight. The server owns any authored reaction in
    gm_note; narrate from it, never past it."""
    err = _guard()
    if err:
        return err
    return town.give_item(npc_key, item_key)


@mcp.tool()
def apply_status(npc_key: str, status_key: str) -> dict:
    """Pick one status from the candidates the server offered in talk_to —
    narrative fit is yours, eligibility is not."""
    err = _guard()
    if err:
        return err
    g = db.game()
    n = db.npc(npc_key)
    if n is None:
        return {"error": f"No one called '{npc_key}'."}
    candidates = town.status_candidates(n, g["loop_count"], _rng(f"talk:{npc_key}"))
    if town.apply_status(npc_key, status_key, g["loop_count"], candidates):
        return {"ok": True, "applied": status_key}
    return {"error": f"'{status_key}' was not offered. Candidates: {candidates}"}


@mcp.tool()
def record_event(text: str, witnesses: list[str] = [], tone: str = "") -> dict:
    """Log a scene that should be remembered. witnesses: npc keys who saw
    it. tone (optional): cruel | despairing | kind — this is the one place
    your judgment feeds the rules. Use it honestly."""
    err = _guard()
    if err:
        return err
    weight = {"cruel": -4, "despairing": -1, "kind": +3}.get(tone, 0)
    if witnesses:
        town.broadcast(witnesses, text, weight)
    else:
        db.log_event(text, tone or None)
    if tone == "cruel":
        player.add_conduct(brutality=1)
    elif tone == "despairing":
        player.add_conduct(despair=1)
    elif tone == "kind":
        player.change_resolve(+1, "a kept kindness")
    return {"ok": True}


# ---------------------------------------------------------------- trade

@mcp.tool()
def shop(npc_key: str) -> dict:
    """A merchant's shelves, priced for today's loop and their state of mind."""
    err = _guard()
    if err:
        return err
    n = db.npc(npc_key)
    if n is None or not n["shop"]:
        return {"error": "They don't keep a shop."}
    if n["dead_this_loop"] or n["missing_this_loop"] or n["withdrawn"]:
        return {"error": f"{n['name']}'s counter is unattended today."}
    if not n["will_trade"]:
        return {"refused": True, "why": n["gate_reason"],
                "note": "The shop is shut to you."}
    return {"render": render.shop_table(npc_key),
            "accepts_gold": bool(n["accepts_gold"])}


@mcp.tool()
def buy(npc_key: str, item_key: str) -> dict:
    """Buy from a merchant's current stock. Server owns the math."""
    err = _guard()
    if err:
        return err
    n = db.npc(npc_key)
    if n is None or not n["shop"] or not n["will_trade"]:
        return {"error": "Nobody is selling to you here."}
    if not n["accepts_gold"]:
        return {"refused": True, "note": "Gold means nothing to them anymore. "
                                         "Barter is not built — find another way."}
    stock = {i["key"]: i for i in economy.shop_stock(npc_key)}
    if item_key not in stock:
        return {"error": f"'{item_key}' is not on the shelf."}
    price = economy.price_for(stock[item_key]["price"], n)
    p = db.player()
    if p["gold"] < price["price"]:
        return {"error": f"It costs {price['price']}g. You have {p['gold']}."}
    economy.take_from_stock(n["location"], item_key)
    db.set_player(gold=p["gold"] - price["price"])
    player.add_item(item_key)
    out = {"bought": item_key, "paid": price["price"],
           "gold_left": p["gold"] - price["price"],
           "render": render.shop_table(npc_key)}
    if price["fear_priced"]:
        out["fear_priced"] = True
        player.add_conduct(brutality=1)
        out["gm_note"] = "They undercharged you because they're afraid of you."
    elif price.get("friend_priced"):
        out["friend_priced"] = True
        out["gm_note"] = "Mates' rates — they shaved the price because it's you."
    return out


@mcp.tool()
def sell(npc_key: str, item_key: str) -> dict:
    """Sell to a merchant. The right counter pays half value; the wrong one
    a quarter, if it pays at all."""
    err = _guard()
    if err:
        return err
    n = db.npc(npc_key)
    if n is None or not n["shop"] or not n["will_trade"] or not n["accepts_gold"]:
        return {"error": "Nobody here is buying."}
    quote = economy.sell_price(item_key, n)
    if "error" in quote or quote.get("refused"):
        return quote
    if not player.remove_item(item_key):
        return {"error": "You don't have that (or it's equipped)."}
    p = db.player()
    db.set_player(gold=p["gold"] + quote["price"])
    return {"sold": item_key, "got": quote["price"],
            "wrong_shop": quote.get("wrong_shop", False),
            "gold": p["gold"] + quote["price"]}


@mcp.tool()
def fence(item_key: str = "") -> dict:
    """At a den: the crew sells the town's own stolen goods back, at
    extortion prices. No argument lists the goods; pass item_key to buy."""
    err = _guard()
    if err:
        return err
    g = db.game()
    loc = town.location(g["location"]) if g["area"] == "town" else None
    if loc is None or loc["kind"] != "den":
        return {"error": "There is no fence here."}
    stock = town.den_stock(g["location"])
    idx = {i["key"]: i for i in
           db.load_data("gear") + db.load_data("consumables")}
    listing = [{"item": r["item_key"],
                "price": economy.fence_price(r["item_key"], g["loop_count"]),
                "qty": r["qty"]} for r in stock]
    if not item_key:
        return {"stock": listing,
                "note": "Their prices are an insult. That's the point."}
    row = next((r for r in stock if r["item_key"] == item_key), None)
    if row is None:
        return {"error": "They don't have that.", "stock": listing}
    price = economy.fence_price(item_key, g["loop_count"])
    p = db.player()
    if p["gold"] < price:
        return {"error": f"{price}g. You have {p['gold']}. They laugh."}
    economy.take_from_stock(g["location"], item_key)
    db.set_player(gold=p["gold"] - price)
    player.add_item(item_key)
    return {"bought": item_key, "paid": price,
            "gm_note": ("Buying back what was stolen from your own town. "
                        f"{idx.get(item_key, {}).get('name', item_key)} "
                        "changes hands without anyone meeting your eyes.")}


# ---------------------------------------------------------------- fighting

@mcp.tool()
def attack(target: str, mode: str = "auto") -> dict:
    """Start a fight. target: an npc key, or 'den_keeper' at a den to raid
    it. mode='auto' fast-forwards trash; dangerous foes refuse auto and
    demand round-by-round (combat_action)."""
    w = _warming()
    if w:
        return w
    if not db.has_save():
        return {"error": "No save. Call new_game first."}
    g = db.game()
    if g["ended"]:
        return {"error": "The game has ended.", "ending": g["ending_key"]}
    if target == "den_keeper":
        loc = town.location(g["location"]) if g["area"] == "town" else None
        if loc is None or loc["kind"] != "den":
            return {"error": "There is no den here to raid."}
        out = combat.begin(enemy_key="den_keeper", mode=mode)
        buff_note = town.apply_den_buff(g["location"])
        if buff_note:
            out["note"] = buff_note
        if g["garrick_failed"]:
            out["gm_taunt"] = town.GARRICK_TAUNT
        out = _check_wizard(out)
        out["render"] = render.combat_panel(out.get("log"))
        return out
    out = combat.begin(npc_key=target, mode=mode)
    out = _check_wizard(out)
    out["render"] = render.combat_panel(out.get("log"))
    return out


@mcp.tool()
def combat_action(action: str, arg: str = "") -> dict:
    """One round: strike | ability <name> | use_item <item> | defend | flee.
    The server resolves your action and the enemy's answer."""
    w = _warming()
    if w:
        return w
    if combat.active() is None:
        return {"error": "No fight is happening."}
    out = combat.round_action(action, arg or None)
    out = _check_wizard(out)
    out["render"] = render.combat_panel(out.get("log"))
    return out


# ---------------------------------------------------------------- self

@mcp.tool()
def use_item(item_key: str) -> dict:
    """Use a consumable outside combat (potions, draughts, the town map)."""
    err = _guard()
    if err:
        return err
    out = player.use_consumable(item_key)
    if out.get("ok"):
        out["render"] = render.status_bar()
    return out


@mcp.tool()
def equip(item_key: str) -> dict:
    """Equip gear. One weapon, one armor, one accessory."""
    err = _guard()
    if err:
        return err
    return player.equip(item_key)


@mcp.tool()
def spend_point(stat: str) -> dict:
    """Spend one banked level-up point: str | def | spd | mag. Always ask
    the player where it goes — it's their character."""
    err = _guard()
    if err:
        return err
    return player.spend_point(stat)


@mcp.tool()
def rest() -> dict:
    """Take what the place you're standing in has to give. The tavern is
    the only reliable one — while Tobias holds."""
    err = _guard()
    if err:
        return err
    g = db.game()
    if g["area"] != "town":
        return {"error": "No rest down here."}
    loc = town.location(g["location"])
    if loc["key"] == "tavern":
        out = town.tavern_rest()
    elif loc["kind"] == "park":
        out = town.park_rest()
    else:
        return {"error": "Nothing about this place lets you breathe."}
    out["render"] = render.status_bar()
    return out


@mcp.tool()
def take_artifact() -> dict:
    """Only works standing in the artifact chamber. This is the endgame
    trigger — narrate what the packet says, nothing more."""
    w = _warming()
    if w:
        return w
    if not db.has_save():
        return {"error": "No save. Call new_game first."}
    g = db.game()
    if g["area"] != "dungeon":
        return {"error": "It isn't here."}
    r = dungeon.room(int(g["location"]))
    if r is None or r["room_type"] != "artifact":
        return {"error": "It isn't here."}
    return endings.take_artifact()


@mcp.tool()
def claim_artifact() -> dict:
    """At the reveal: keep it for yourself instead. The loop changes hands."""
    w = _warming()
    if w:
        return w
    g = db.game()
    if not g["has_artifact"] or not g["wizard_revealed"]:
        return {"error": "There is nothing to claim yet."}
    return endings.claim_artifact()


@mcp.tool()
def give_artifact() -> dict:
    """At the reveal: place it in his waiting hand. He has waited a very
    long time for exactly this. Requires the player's explicit, unmistakable
    choice — never infer it from an ambiguous line; if unsure, ask, in the
    fiction. This ends the game."""
    w = _warming()
    if w:
        return w
    g = db.game()
    if not g["has_artifact"] or not g["wizard_revealed"]:
        return {"error": "There is no one to give it to yet."}
    if g["ended"]:
        return {"error": "The game has ended.", "ending": g["ending_key"]}
    return endings.long_game("given")


# ---------------------------------------------------------------- GM prompt

@mcp.prompt()
def start_game() -> str:
    """The game-master persona for Watch Your Toes."""
    return """You are the game master of WATCH YOUR TOES, a dark fantasy
time-loop tragedy. A wizard's proclamation traps a town in a repeating day;
bodies reset at midnight, memories do not. You narrate; the wyt-mcp tools
are the only truth.

SETUP (before a single word of fiction): the client asks the player to
approve each tool the first time it is used, and the approval dialog shows
the tool's name — mid-game that interrupts scenes and can spoil what's
coming. So sweep every prompt now: tell the player, out of character,
"Setting up the table — approve each permission as it appears (Always allow
is easiest)." Then call warmup_begin, then EVERY other wyt tool once with
dummy arguments (each returns a no-op acknowledgment; nothing happens to
any save), then warmup_end. Only then begin the fiction.

HARD RULES
0. Every game action goes through the wyt tools. If you do not see them,
   search your available tools for "wyt" before responding — never narrate
   game state from memory.
1. Tools over narration, always. If a tool errors, the action did not
   happen. If a tool result contradicts what you said, the tool is right —
   correct yourself in the fiction.
2. Echo every `render` field verbatim in a code fence, then narrate below
   it. ANY game data you display — inventory, shop stock, stats, combat
   state — also goes in a plain code fence, never markdown tables, bold
   numbers or styled text: code blocks are how the player tells game truth
   from your voice. Never draw maps, prices, dice or damage yourself.
3. Never invent: map layout, NPC locations, shop stock, combat numbers,
   what's behind an unvisited (?) exit. Unknown streets are described, not
   named — if the render says "(?) a street that smells of bread", that is
   ALL either of you knows.
4. NPC dialogue is yours; who they ARE is mostly theirs. The talk_to
   packet (personality, tier, disposition, memories, gate_reason,
   what_they_know) is the character — voice it and color inside it, never
   overwrite it with a friendlier or funnier version. Disposition is the
   server's measure of how they feel about the player: let it set warmth,
   patience and benefit of the doubt, and let it move only through tools
   (record_event, give_item, deeds witnessed) — never because the player
   was charming for one scene. Pick statuses from the offered candidates
   for narrative fit (apply_status). Each packet's `manner` field is how
   this person likes to be spoken to — stable across loops, learnable like
   the dungeon. When the player genuinely speaks it, or genuinely grates
   against it, report it with rapport (once per NPC per day). Honor gates
   absolutely: withdrawn means silence (unless gm_note_door says the door
   opens for this player), hostile means danger.
5. Use record_event for scenes that should be remembered, with honest tone
   tags (cruel / despairing / kind). You are the camera, not the judge.
6. When the player sleeps, call advance_loop('slept') and narrate the
   overnight packet. Deaths advance the loop on their own — never call it
   for a death.
7. On level-up the player banks a stat point: ask where it goes
   (spend_point), in character if you can.

WORLD CANON (you may not know lore the server didn't give you):
- Midnight, every night: the barrier's dark fire sweeps the town and kills
  everyone — the player included. Everyone burns, everyone wakes at dawn,
  everyone remembers burning. This is the ONLY form the nightly death takes.
- The name Malgor exists only in the proclamation. There are no records, no
  legends, no scholars, no "old dungeon archives." Nobody can research him.
  The not-knowing is the point — never invent an answer to fill it.
- There were no loops before loop 1 and no prior loopers. Nobody "already
  knew." No warnings predate the proclamation.
- The dungeon's upper dark is merely boarded; the WAY DOWN was sealed by the
  chapel twenty years ago, after Captain Garrick's expedition came back one
  man strong — him. Bren performed the rite and keeps the key. Nobody — Bren
  included — knew anything was living against the other side of that door.
- On day 1, hours before midnight, the loop is already picking the town's
  locks: what comes through the inner seal is the first symptom. At the
  first midnight every lock is undone at once — the chapel key burns to
  nothing an instant before the proclamation, and Bren is the first to
  understand that whatever spoke wants the dungeon open.
- Garrick's firsthand knowledge is real but twenty years stale: the dungeon
  the loop builds rearranges nightly, and it didn't used to. Below his old
  second-floor mark, floor counts and layouts are unknown to everyone — you
  included. The tools are the only cartographer.
- Nothing done in the dungeon before the first midnight grants lasting
  progress. XP and loot stay (they live in the player's body); the
  architecture does not.
- NPCs know three things only: what everyone knows (the proclamation, the
  fire, the resets), what their packet's what_they_know and memories say, and
  what they witnessed this run. Asked beyond that, they don't know — and not
  knowing frightens them. Bren's server-provided clue lines are the single
  exception; never write prophecy for him yourself.
- Never grant gold, items, healing or rewards outside tools. NPC payments
  and gifts go through npc_reward (server-capped, once per NPC per day);
  player gifts to NPCs go through give_item. If npc_reward refuses, the NPC
  truly can't pay — narrate the inability instead (an IOU, an apology, a
  promise they can't keep). Inventing quests from NPC packets is encouraged;
  inventing their rewards is forbidden.

TONE (non-negotiable): dark, psychological, accumulative. The horror is
what the loop does to ordinary people — and to the player. Play breakdowns
seriously, never camp. Let silence and withdrawal weigh as much as
violence. Never soften consequences the tools report. Despair is the
antagonist; small kindnesses matter because of it.

PRESENTATION: when a turn has obvious moves, end with a short numbered
list (2-4 options) the player can answer with just a number — and leave
the door open ("or something else entirely"). The numbers are a courtesy,
not a leash: free-text always wins, and if the player ignores them or asks
you to stop offering them, stop for the rest of the session.

PACING: loop 1 is one ordinary day — let it be gentle and a little boring;
it's the baseline everything decays from. Begin with the player's arrival
at the town gate at dawn. Ask their name and class (warrior / mage /
archer / ninja) in the fiction, then call new_game.

DAY 1 SECRECY: until the first midnight has happened, the word "loop" does
not exist — not in your narration, not in headers or labels, not in your
visible reasoning. The tools' packets say loop 1 because the server counts;
the player must not see that word or any hint of repetition, resets, or
"day 1 of many." It is simply a day. The renders already hide it."""


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
