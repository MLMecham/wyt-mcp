"""Combat: fast-forward mobs, slow-burn bosses (§7).

One round = both sides act once, faster side first (ties to the player).
The turn loop skips a side whose turn is flagged (stun, or combat already
over after a flee/kill) — that single rule carries flee ordering, stuns,
and crit-stuns. Speed also feeds initiative, follow-up strikes, crit
chance and flee odds; it never grants extra tool-call turns.

Damage pipeline, in order: stat x mult - def/2, buff/debuff multipliers,
±10% variance, x2 crit (1/16 base + speed diff, 30% of crits stun — bosses
immune), then the defender's defend/defense multipliers. Floor of 1.

Auto mode loops these same rounds server-side until the fight ends or the
player drops below 30% max HP, then hands control back. Threat at or above
AUTO_THREAT_MAX (or any boss) refuses auto outright.
"""

import random

from wyt_mcp import db
from wyt_mcp.engine import dungeon, effects, player, town

DANGER_FRACTION = 0.30
AUTO_THREAT_MAX = 1.25
AUTO_ROUND_CAP = 50
BASE_CRIT = 1 / 16
CRIT_SPD_BONUS = 0.005          # per point of speed advantage
CRIT_CAP = 0.25
CRIT_STUN_CHANCE = 0.30
FOLLOWUP_RATIO = 1.5            # spd >= 1.5x other side -> chance of a bonus strike
FOLLOWUP_CHANCE = 0.30
FOLLOWUP_DAMAGE = 0.5
FLEE_BASE = 0.50
FLEE_PER_SPD = 0.05
FLEE_FLOOR = 0.05               # no upper clamp: fast enough means gone

NPC_TIERS = {
    "weak":    {"hp": 16, "str": 4,  "def": 2, "spd": 4, "xp": 8,  "gold": 6},
    "average": {"hp": 30, "str": 7,  "def": 4, "spd": 5, "xp": 18, "gold": 14},
    "strong":  {"hp": 48, "str": 10, "def": 6, "spd": 6, "xp": 35, "gold": 25},
}

# §16: human town enemies (killing one when flee was an option = brutality);
# den crews additionally hearten Garrick when beaten — the watch isn't alone.
TOWN_HUMANS = {"cutpurse", "mad_penitent", "drunkard", "den_thug", "den_keeper"}
DEN_CREW = {"den_thug", "den_keeper"}

_rng: random.Random | None = None


def _ensure_rng() -> random.Random:
    global _rng
    if _rng is None:
        g = db.game()
        row = active()
        _rng = random.Random(
            f"{g['dungeon_seed']}:{g['loop_count']}:{row['round'] if row else 0}"
        )
    return _rng


def active():
    return db.conn().execute("SELECT * FROM combat WHERE id=1").fetchone()


def enemy_power(stats: dict) -> int:
    return stats["str"] * 2 + stats["def"] + stats["spd"] // 2 + stats["hp"] // 6


# ---------------------------------------------------------------- lifecycle

def begin(enemy_key: str | None = None, npc_key: str | None = None,
          room_id: int | None = None, from_location: str | None = None,
          mode: str = "auto") -> dict:
    global _rng
    if active() is not None:
        return {"error": "Already in combat — resolve it first."}
    g = db.game()

    npc_id = None
    if npc_key is not None:
        n = db.npc(npc_key)
        if n is None or n["dead_this_loop"]:
            return {"error": "No such opponent."}
        npc_id = n["id"]
        if n["combat_tier"] == "wizard":
            stats = dungeon.enemy_stats("malgor", g["loop_count"])
            enemy_key, boss = "malgor", True
        else:
            stats = dict(NPC_TIERS[n["combat_tier"]], boss=False)
            boss = False
        name = n["name"]
    else:
        stats = dungeon.enemy_stats(enemy_key, g["loop_count"])
        boss = bool(stats.get("boss"))
        name = stats["name"]

    db.conn().execute(
        "INSERT INTO combat (id, enemy_key, enemy_name, enemy_hp, enemy_max_hp, "
        "enemy_str, enemy_def, enemy_spd, enemy_xp, enemy_gold, boss, npc_id, "
        "room_id, from_location, forced_rounds) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (enemy_key, name, stats["hp"], stats["hp"], stats["str"], stats["def"],
         stats["spd"], stats.get("xp", 0), stats.get("gold", 0), 1 if boss else 0,
         npc_id, room_id, from_location, 0),
    )
    db.conn().commit()
    _rng = None
    _ensure_rng()

    threat = enemy_power(stats) / max(1, player.power_score())
    out = {"enemy": name, "threat": round(threat, 2), "log": []}
    if boss or threat >= AUTO_THREAT_MAX:
        db.update("combat", 1, forced_rounds=1)
        if mode == "auto":
            out["refused"] = "A dangerous presence demands your attention."
            out["note"] = "Round-by-round only: use combat_action."
            return out
    if mode == "auto":
        return _auto_loop(out)
    out["note"] = "Round-by-round: use combat_action."
    return out


def round_action(action: str, arg: str | None = None) -> dict:
    row = active()
    if row is None:
        return {"error": "There is no fight."}
    err = _validate(action, arg)
    if err:
        return {"error": err}  # no round consumed on a bad ask
    out = {"enemy": row["enemy_name"], "log": []}
    outcome = _run_round(out["log"], action, arg)
    return _report(out, outcome)


def _auto_loop(out: dict) -> dict:
    p = db.player()
    for _ in range(AUTO_ROUND_CAP):
        outcome = _run_round(out["log"], "strike", None)
        if outcome:
            return _report(out, outcome)
        p = db.player()
        if p["hp"] < p["max_hp"] * DANGER_FRACTION:
            out["interrupted"] = True
            out["note"] = ("You are hurt. The fight is yours to finish "
                           "round by round — or to run from.")
            return _report(out, None)
    out["note"] = "The fight drags on."  # cap reached; should not happen
    return _report(out, None)


def _report(out: dict, outcome: str | None) -> dict:
    if outcome:
        out.update(_finish(outcome, out["log"]))
    else:
        row, p = active(), db.player()
        out["enemy_hp"] = f"{row['enemy_hp']}/{row['enemy_max_hp']}"
        out["player_hp"] = f"{p['hp']}/{p['max_hp']}"
        out["player_mp"] = f"{p['mp']}/{p['max_mp']}"
    return out


# ---------------------------------------------------------------- the round

def _run_round(log: list[str], action: str, arg: str | None) -> str | None:
    rng = _ensure_rng()
    row = active()
    pspd = player.speed() + effects.add_sum("player", "speed_add")
    espd = row["enemy_spd"] + effects.add_sum("enemy", "speed_add")
    order = ["player", "enemy"] if pspd >= espd else ["enemy", "player"]

    outcome = None
    for actor in order:
        if outcome:
            break  # the fight ended before this turn came up
        if effects.consume_stun(actor):
            who = "You are" if actor == "player" else f"{row['enemy_name']} is"
            log.append(f"{who} stunned — the turn is lost.")
            continue
        if actor == "player":
            outcome = _player_turn(log, rng, action, arg, pspd, espd)
        else:
            outcome = _enemy_turn(log, rng, pspd, espd)
    effects.tick_round()
    row = active()
    if row is not None:
        db.update("combat", 1, round=row["round"] + 1)
    return outcome


def _validate(action: str, arg: str | None) -> str | None:
    p = db.player()
    if action == "ability":
        abilities = player.CLASSES[p["class"]]["abilities"]
        if arg not in abilities:
            return f"You know no ability called '{arg}'."
        if p["mp"] < abilities[arg]["mp"]:
            return "Not enough MP."
    elif action == "use_item":
        c = player.consumable_index().get(arg)
        if c is None:
            return f"'{arg}' is not something you can use."
        held = db.conn().execute(
            "SELECT 1 FROM inventory WHERE item_key=? AND equipped=0", (arg,)
        ).fetchone()
        if held is None:
            return f"You are not carrying a {c['name']}."
    elif action not in ("strike", "defend", "flee"):
        return f"Unknown action '{action}'."
    return None


def _player_turn(log, rng, action, arg, pspd, espd) -> str | None:
    row = active()
    p = db.player()
    if action == "strike":
        return _player_attack(log, rng, player.attack_power(), 1.0,
                              "You strike", pspd, espd)
    if action == "ability":
        ab = player.CLASSES[p["class"]]["abilities"][arg]
        db.set_player(mp=p["mp"] - ab["mp"])
        power = player.magic_power() if ab["stat"] == "mag" else player.attack_power()
        return _player_attack(log, rng, power, ab["mult"],
                              f"{arg.replace('_', ' ').title()}!", pspd, espd)
    if action == "defend":
        effects.add("player", "defend", 0.5, rounds_left=1)
        log.append("You brace behind your guard.")
        return None
    if action == "use_item":
        _use_consumable(log, arg)
        return None
    if action == "flee":
        chance = max(FLEE_FLOOR, FLEE_BASE + FLEE_PER_SPD * (pspd - espd))
        if rng.random() < chance:
            log.append("You break away and run.")
            return "fled"
        log.append(f"{row['enemy_name']} cuts off your escape.")
        return None
    return None


def _player_attack(log, rng, power, mult, verb, pspd, espd) -> str | None:
    row = active()
    dmg, crit, stun = _damage(rng, power, mult, row["enemy_def"], pspd, espd,
                              attacker="player", defender="enemy",
                              defender_boss=bool(row["boss"]))
    hp = row["enemy_hp"] - dmg
    db.update("combat", 1, enemy_hp=max(0, hp))
    log.append(f"{verb} {row['enemy_name']} for {dmg}{' — CRITICAL' if crit else ''}.")
    if stun:
        log.append(f"{row['enemy_name']} reels, stunned.")
    if hp <= 0:
        return "won"
    if pspd >= FOLLOWUP_RATIO * espd and rng.random() < FOLLOWUP_CHANCE:
        extra, _, _ = _damage(rng, power * FOLLOWUP_DAMAGE, mult, row["enemy_def"],
                              pspd, espd, attacker="player", defender="enemy",
                              defender_boss=bool(row["boss"]), can_crit=False)
        hp -= extra
        db.update("combat", 1, enemy_hp=max(0, hp))
        log.append(f"You are faster — a second cut lands for {extra}.")
        if hp <= 0:
            return "won"
    return None


def _enemy_turn(log, rng, pspd, espd) -> str | None:
    row = active()
    p = db.player()
    dmg, crit, stun = _damage(rng, row["enemy_str"], 1.0, player.defense(),
                              espd, pspd, attacker="enemy", defender="player",
                              defender_boss=False)
    hp = p["hp"] - dmg
    db.set_player(hp=max(0, hp))
    log.append(f"{row['enemy_name']} hits you for {dmg}{' — CRITICAL' if crit else ''}.")
    if stun:
        log.append("Your ears ring. You are stunned.")
    if hp <= 0:
        return "died"
    if (espd >= FOLLOWUP_RATIO * pspd and rng.random() < FOLLOWUP_CHANCE
            and not effects.has("player", "defend")):
        extra, _, _ = _damage(rng, row["enemy_str"] * FOLLOWUP_DAMAGE, 1.0,
                              player.defense(), espd, pspd, attacker="enemy",
                              defender="player", defender_boss=False, can_crit=False)
        hp -= extra
        db.set_player(hp=max(0, hp))
        log.append(f"It is faster than you — a second blow lands for {extra}.")
        if hp <= 0:
            return "died"
    return None


def _damage(rng, power, mult, defense, a_spd, d_spd, attacker, defender,
            defender_boss, can_crit=True) -> tuple[int, bool, bool]:
    raw = power * mult - defense / 2
    raw *= effects.mult(attacker, "damage_mult")
    raw *= rng.uniform(0.9, 1.1)
    crit = stun = False
    if can_crit:
        chance = min(CRIT_CAP, BASE_CRIT + max(0, a_spd - d_spd) * CRIT_SPD_BONUS)
        if rng.random() < chance:
            crit = True
            raw *= 2
            if not defender_boss and rng.random() < CRIT_STUN_CHANCE:
                stun = True
                effects.add(defender, "stun", 1, rounds_left=1)
    raw *= effects.mult(defender, "defense_mult") * effects.mult(defender, "defend")
    return max(1, round(raw)), crit, stun


def _use_consumable(log, item_key: str) -> None:
    c = player.consumable_index()[item_key]
    player.remove_item(item_key)
    player.heal(hp=c.get("heal", 0), mp=c.get("mana", 0))
    if c.get("resolve"):
        player.change_resolve(c["resolve"])
    if c.get("buff"):
        b = c["buff"]
        effects.add("player", b["kind"], b["value"],
                    rounds_left=b.get("rounds"))  # None = lasts the day
    log.append(f"You use the {c['name']}.")


# ---------------------------------------------------------------- endings

def _finish(outcome: str, log: list[str]) -> dict:
    row = active()
    out = {"outcome": outcome}
    if outcome == "won":
        notes = []
        if row["enemy_xp"]:
            notes += player.gain_xp(row["enemy_xp"])
        if row["enemy_gold"]:
            db.set_player(gold=db.player()["gold"] + row["enemy_gold"])
            notes.append(f"You take {row['enemy_gold']} gold from the corpse.")
        if row["room_id"] is not None:
            notes += dungeon.mark_cleared(row["room_id"])
        if row["npc_id"] is not None:
            notes += _npc_killed(row)
        if row["enemy_key"] in TOWN_HUMANS and row["npc_id"] is None:
            # Nameless, broken, and human — and flee was on the table (§16).
            player.add_conduct(brutality=1)
            notes.append("They were one of the town's broken, not a monster. "
                         "(+1 brutality)")
        if row["enemy_key"] in DEN_CREW:
            town.garrick_heartened(
                2, "Word reached the watch house: someone bloodied the den crews.")
            g = db.game()
            if row["enemy_key"] == "den_keeper" and g["area"] == "town":
                notes += town.loot_den(g["location"])
        out["notes"] = notes
        log.append(f"{row['enemy_name']} is dead.")
    elif outcome == "fled":
        g = db.game()
        if g["area"] == "dungeon" and row["from_location"]:
            db.set_game(location=row["from_location"])
            out["retreated_to"] = int(row["from_location"])
    db.conn().execute("DELETE FROM combat WHERE id=1")
    db.conn().commit()
    effects.clear_combat()
    if outcome == "died":
        from wyt_mcp.engine import days  # local import breaks the cycle
        out["overnight"] = days.advance_loop("died")
    p = db.player()
    out["player_hp"] = f"{p['hp']}/{p['max_hp']}"
    return out


def _npc_killed(row) -> list[str]:
    n = db.npc_by_id(row["npc_id"])
    g = db.game()
    db.update("npcs", n["id"], dead_this_loop=1)
    # The signature mechanic: the victim wakes up remembering (§ Premise).
    town.add_memory(n["key"], "You killed them. They woke up.", "witnessed", -12)
    witnesses = [k for k in town.witnesses_at(n["location"]) if k != n["key"]]
    town.broadcast(witnesses, f"They watched you kill {n['name']}.", -8)
    player.add_conduct(brutality=2)
    player.change_resolve(-8, f"you killed {n['name']}")
    notes = [f"{n['name']} is dead. Until midnight, anyway."]
    if n["shop"]:
        notes += town.shopkeeper_murdered(n)
    if witnesses:
        notes.append(f"Witnessed by: {', '.join(witnesses)}.")
    if n["is_wizard"]:
        notes.append("WIZARD_DEFEATED: hand off to endings.")
    return notes
