"""Headless balance harness (§12): run N loops without Claude and watch the
town die. Tuning lives in engine/tuning.py; this is how you check a change
in seconds.

    uv run python -m wyt_mcp.simulate --loops 15
    uv run python -m wyt_mcp.simulate --loops 20 --class mage --runs 5
"""

import argparse
import os
import statistics
import tempfile


def run_once(klass: str, loops: int, quiet: bool = False) -> dict:
    os.environ["WYT_MCP_DB"] = os.path.join(tempfile.mkdtemp(), "sim.db")
    from wyt_mcp import db
    for mod in list(__import__("sys").modules):
        pass  # db module caches a connection; fresh env var + close handles it
    db.close()

    from wyt_mcp.engine import days, economy, town

    days.new_game("Sim", klass)
    marta_base = 60  # iron_sword
    header = (f"{'loop':>4} {'avg':>4} {'min':>4} {'sword':>6} "
              f"{'resolve':>8} {'garrick':>12} {'notes'}")
    if not quiet:
        print(header)
        print("-" * len(header))
    summary = {}
    for _ in range(loops):
        out = days.advance_loop("slept")
        if "error" in out:
            summary["ended"] = out.get("ending")
            break
        g, p = db.game(), db.player()
        sanities = [n["sanity"] for n in db.npcs_all() if not n["is_wizard"]]
        marta = db.npc("marta")
        price = economy.price_for(marta_base, marta)["price"]
        flips = sum(1 for n in db.npcs_all()
                    if n["hostile"] or n["withdrawn"] or not n["will_trade"])
        valve = ("OPEN" if g["garrick_failed"]
                 else f"warn@{g['garrick_warning_loop']}"
                 if g["garrick_warning_loop"] else town.garrick_tier())
        marks = []
        if out.get("ended"):
            marks.append(f"ENDED:{out['ended']['ending']}")
        if not _tavern_ok(db):
            marks.append("tavern-dead")
        if not quiet:
            print(f"{g['loop_count']:>4} {statistics.mean(sanities):>4.0f} "
                  f"{min(sanities):>4} {price:>5}g {p['resolve']:>8} "
                  f"{valve:>12} {' '.join(marks)}")
        summary = {
            "loop": g["loop_count"],
            "avg_sanity": round(statistics.mean(sanities)),
            "min_sanity": min(sanities),
            "iron_sword": price,
            "resolve": p["resolve"],
            "gate_flips": flips,
            "garrick": valve,
        }
        if out.get("ended"):
            summary["ended"] = out["ended"]["ending"]
            break
    db.close()
    return summary


def _tavern_ok(db) -> bool:
    from wyt_mcp.engine import town
    t = db.npc("tobias")
    return t is not None and not t["withdrawn"] and town.tier(t["sanity"]) != "gone"


def main() -> None:
    ap = argparse.ArgumentParser(description="wyt-mcp headless balance run")
    ap.add_argument("--loops", type=int, default=15)
    ap.add_argument("--class", dest="klass", default="warrior",
                    choices=["warrior", "mage", "archer", "ninja"])
    ap.add_argument("--runs", type=int, default=1,
                    help="multiple runs: print only the final-state summary of each")
    args = ap.parse_args()

    if args.runs == 1:
        run_once(args.klass, args.loops)
        return
    finals = [run_once(args.klass, args.loops, quiet=True)
              for _ in range(args.runs)]
    keys = ["loop", "avg_sanity", "min_sanity", "iron_sword", "resolve",
            "gate_flips", "garrick"]
    print("  ".join(f"{k:>10}" for k in keys))
    for f in finals:
        print("  ".join(f"{str(f.get(k, '-')):>10}" for k in keys))


if __name__ == "__main__":
    main()
