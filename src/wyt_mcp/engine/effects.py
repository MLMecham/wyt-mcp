"""Buffs, debuffs and stuns: one table drives combat math and day-long blessings.

Scoping by rounds_left:
  NULL  -> lasts the day (a blessing, a draught); cleared by the loop reset.
  N     -> combat-scoped; ticks down each round, gone at zero.
Enemy-targeted rows never outlive the fight.
"""

from wyt_mcp import db


def add(target: str, kind: str, value: float = 1.0,
        rounds_left: int | None = None) -> None:
    db.conn().execute(
        "INSERT INTO effects (target, kind, value, rounds_left) VALUES (?,?,?,?)",
        (target, kind, value, rounds_left),
    )
    db.conn().commit()


def mult(target: str, kind: str) -> float:
    """Product of all active multipliers of one kind (1.0 when none)."""
    out = 1.0
    for r in db.conn().execute(
        "SELECT value FROM effects WHERE target=? AND kind=?", (target, kind)
    ):
        out *= r["value"]
    return out


def add_sum(target: str, kind: str) -> int:
    row = db.conn().execute(
        "SELECT COALESCE(SUM(value), 0) FROM effects WHERE target=? AND kind=?",
        (target, kind),
    ).fetchone()
    return int(row[0])


def has(target: str, kind: str) -> bool:
    return db.conn().execute(
        "SELECT 1 FROM effects WHERE target=? AND kind=? LIMIT 1", (target, kind)
    ).fetchone() is not None


def consume_stun(target: str) -> bool:
    """If stunned, spend one stun and report True (the turn is skipped)."""
    c = db.conn()
    row = c.execute(
        "SELECT id FROM effects WHERE target=? AND kind='stun' LIMIT 1", (target,)
    ).fetchone()
    if row is None:
        return False
    c.execute("DELETE FROM effects WHERE id=?", (row["id"],))
    c.commit()
    return True


def tick_round() -> None:
    c = db.conn()
    c.execute("UPDATE effects SET rounds_left = rounds_left - 1 "
              "WHERE rounds_left IS NOT NULL")
    c.execute("DELETE FROM effects WHERE rounds_left IS NOT NULL AND rounds_left <= 0")
    c.commit()


def clear_combat() -> None:
    """Combat is over: enemy effects and round-scoped player effects die."""
    c = db.conn()
    c.execute("DELETE FROM effects WHERE target='enemy'")
    c.execute("DELETE FROM effects WHERE target='player' AND rounds_left IS NOT NULL")
    c.commit()


def clear_all() -> None:
    """Loop reset: the night takes the blessings too."""
    db.conn().execute("DELETE FROM effects")
    db.conn().commit()


def active(target: str) -> list[dict]:
    return [
        {"kind": r["kind"], "value": r["value"], "rounds_left": r["rounds_left"]}
        for r in db.conn().execute(
            "SELECT kind, value, rounds_left FROM effects WHERE target=?", (target,)
        )
    ]
