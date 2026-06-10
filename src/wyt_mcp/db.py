"""SQLite persistence: connection, schema, seeding, save lifecycle.

The save lives in the platformdirs user data dir (never the package dir —
uvx installs are ephemeral). Override with the WYT_MCP_DB env var (used by
simulate.py to run against a throwaway file).
"""

import json
import os
import sqlite3
import time
from importlib import resources
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "wyt-mcp"

_conn: sqlite3.Connection | None = None


def db_path() -> Path:
    override = os.environ.get("WYT_MCP_DB")
    if override:
        p = Path(override)
    else:
        p = Path(user_data_dir(APP_NAME)) / "save.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(db_path())
        _conn.row_factory = sqlite3.Row
    return _conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def load_data(name: str):
    """Load a JSON file from package data (data/<name>.json)."""
    src = resources.files("wyt_mcp.data").joinpath(f"{name}.json")
    return json.loads(src.read_text(encoding="utf-8"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS game (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    loop_count INTEGER NOT NULL DEFAULT 1,
    area TEXT NOT NULL DEFAULT 'town',          -- 'town' | 'dungeon'
    location TEXT NOT NULL DEFAULT 'gate',      -- town location key, or room id as text
    dungeon_seed INTEGER NOT NULL,
    max_floor_cleared INTEGER NOT NULL DEFAULT 0,
    intro_done INTEGER NOT NULL DEFAULT 0,
    has_artifact INTEGER NOT NULL DEFAULT 0,
    wizard_revealed INTEGER NOT NULL DEFAULT 0,
    ended INTEGER NOT NULL DEFAULT 0,
    ending_key TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    class TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    xp INTEGER NOT NULL DEFAULT 0,
    hp INTEGER NOT NULL, max_hp INTEGER NOT NULL,
    mp INTEGER NOT NULL, max_mp INTEGER NOT NULL,
    gold INTEGER NOT NULL,
    str INTEGER NOT NULL, def INTEGER NOT NULL,
    spd INTEGER NOT NULL, mag INTEGER NOT NULL,
    stat_points INTEGER NOT NULL DEFAULT 0,    -- banked on level-up, player-assigned
    resolve INTEGER NOT NULL DEFAULT 100,
    brutality INTEGER NOT NULL DEFAULT 0,
    despair INTEGER NOT NULL DEFAULT 0,
    tavern_rested_loop INTEGER NOT NULL DEFAULT 0,
    park_rested_loop INTEGER NOT NULL DEFAULT 0
);

-- §16: generated ONCE per save in new_game; loop resets never touch these.
CREATE TABLE IF NOT EXISTS town_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,              -- shop|tavern|chapel|landmark|den|park|alley|gate...
    shop_tag TEXT,                   -- smith|apothecary|magic|general (shops only)
    visited INTEGER NOT NULL DEFAULT 0,  -- town fog-of-war; pre-set for the local class
    paired_den TEXT,                 -- general stores: key of the den that robs them
    den_buff TEXT,                   -- dens: premium buff the gatekeeper drank tonight
    risk_kind TEXT                   -- NULL | 'flat' (dens) | 'scaling' (alleys/park)
);

CREATE TABLE IF NOT EXISTS town_edges (
    from_key TEXT NOT NULL,
    to_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shop_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_key TEXT NOT NULL,      -- a shop, or a den holding stolen goods
    item_key TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    premium INTEGER NOT NULL DEFAULT 0   -- what robberies move store -> den
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL,
    equipped INTEGER NOT NULL DEFAULT 0,
    qty INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS npcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    location TEXT NOT NULL,
    baseline_personality TEXT NOT NULL,
    base_decay INTEGER NOT NULL DEFAULT 2,
    traits TEXT NOT NULL DEFAULT '',            -- comma-separated
    shop INTEGER NOT NULL DEFAULT 0,
    combat_tier TEXT NOT NULL DEFAULT 'weak',
    is_wizard INTEGER NOT NULL DEFAULT 0,
    sanity INTEGER NOT NULL DEFAULT 100,
    disposition INTEGER NOT NULL DEFAULT 0,
    dead_this_loop INTEGER NOT NULL DEFAULT 0,
    missing_this_loop INTEGER NOT NULL DEFAULT 0,
    withdrawn INTEGER NOT NULL DEFAULT 0,
    hostile INTEGER NOT NULL DEFAULT 0,
    will_trade INTEGER NOT NULL DEFAULT 1,
    accepts_gold INTEGER NOT NULL DEFAULT 1,
    gate_reason TEXT
);

CREATE TABLE IF NOT EXISTS npc_statuses (
    npc_id INTEGER NOT NULL,
    status_key TEXT NOT NULL,
    applied_loop INTEGER NOT NULL,
    UNIQUE (npc_id, status_key)
);

CREATE TABLE IF NOT EXISTS npc_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL,
    loop_count INTEGER NOT NULL,
    event_text TEXT NOT NULL,
    source TEXT NOT NULL,                        -- 'witnessed' | 'rumor'
    weight INTEGER NOT NULL                      -- signed: moves disposition
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor INTEGER NOT NULL,
    room_type TEXT NOT NULL,                     -- enemy|trap|treasure|empty|boss|artifact
    name TEXT NOT NULL,
    cleared INTEGER NOT NULL DEFAULT 0,
    visited INTEGER NOT NULL DEFAULT 0,
    enemy_key TEXT
);

CREATE TABLE IF NOT EXISTS room_edges (
    from_room INTEGER NOT NULL,
    to_room INTEGER NOT NULL,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_count INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    tone TEXT                                    -- 'cruel' | 'despairing' | 'kind' | NULL
);

CREATE TABLE IF NOT EXISTS combat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enemy_key TEXT,
    enemy_name TEXT NOT NULL,
    enemy_hp INTEGER NOT NULL, enemy_max_hp INTEGER NOT NULL,
    enemy_str INTEGER NOT NULL, enemy_def INTEGER NOT NULL, enemy_spd INTEGER NOT NULL,
    enemy_xp INTEGER NOT NULL DEFAULT 0, enemy_gold INTEGER NOT NULL DEFAULT 0,
    boss INTEGER NOT NULL DEFAULT 0,             -- bosses: no auto mode, stun-immune
    npc_id INTEGER,                              -- set when fighting a townsperson
    room_id INTEGER,                             -- set when fighting in a dungeon room
    from_location TEXT,                          -- where a successful flee retreats to
    round INTEGER NOT NULL DEFAULT 0,
    forced_rounds INTEGER NOT NULL DEFAULT 0     -- 1 = auto mode refused
);

CREATE TABLE IF NOT EXISTS effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,            -- 'player' | 'enemy'
    kind TEXT NOT NULL,              -- damage_mult|defense_mult|speed_add|stun|defend
    value REAL NOT NULL DEFAULT 1,
    rounds_left INTEGER              -- NULL = lasts until the loop reset
);
"""

TABLES = [
    "game", "player", "inventory", "npcs", "npc_statuses",
    "npc_memories", "rooms", "room_edges", "event_log", "combat", "effects",
    "town_locations", "town_edges", "shop_stock",
]


def wipe() -> None:
    c = conn()
    for t in TABLES:
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.executescript(SCHEMA)
    c.commit()


def has_save() -> bool:
    c = conn()
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='game'"
    ).fetchone()
    if row is None:
        return False
    return c.execute("SELECT 1 FROM game WHERE id=1").fetchone() is not None


def game() -> sqlite3.Row:
    return conn().execute("SELECT * FROM game WHERE id=1").fetchone()


def player() -> sqlite3.Row:
    return conn().execute("SELECT * FROM player WHERE id=1").fetchone()


def npc(key: str) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM npcs WHERE key=?", (key,)).fetchone()


def npc_by_id(npc_id: int) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM npcs WHERE id=?", (npc_id,)).fetchone()


def npcs_all() -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM npcs ORDER BY id").fetchall()


def update(table: str, row_id: int = 1, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(
        f"UPDATE {table} SET {cols} WHERE id=?", [*fields.values(), row_id]
    )
    conn().commit()


def set_game(**fields) -> None:
    update("game", 1, **fields)


def set_player(**fields) -> None:
    update("player", 1, **fields)


def log_event(text: str, tone: str | None = None) -> None:
    c = conn()
    g = game()
    seq = c.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM event_log WHERE loop_count=?",
        (g["loop_count"],),
    ).fetchone()[0]
    c.execute(
        "INSERT INTO event_log (loop_count, seq, text, tone) VALUES (?,?,?,?)",
        (g["loop_count"], seq, text, tone),
    )
    c.commit()


def events_for_loop(loop_count: int) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM event_log WHERE loop_count=? ORDER BY seq", (loop_count,)
    ).fetchall()


def create_save(name: str, klass: str, class_template: dict, seed: int) -> None:
    """Fresh save: schema, game row, player row, starting gear, NPC roster."""
    wipe()
    c = conn()
    c.execute(
        "INSERT INTO game (id, loop_count, area, location, dungeon_seed, created_at) "
        "VALUES (1, 1, 'town', 'gate', ?, ?)",
        (seed, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    t = class_template
    c.execute(
        "INSERT INTO player (id, name, class, hp, max_hp, mp, max_mp, gold, "
        "str, def, spd, mag) VALUES (1,?,?,?,?,?,?,?,?,?,?,?)",
        (name, klass, t["hp"], t["hp"], t["mp"], t["mp"], t["gold"],
         t["str"], t["def"], t["spd"], t["mag"]),
    )
    for item_key in t["start_items"]:
        equipped = 1 if item_key in t.get("start_equipped", []) else 0
        c.execute(
            "INSERT INTO inventory (item_key, equipped, qty) VALUES (?,?,1)",
            (item_key, equipped),
        )
    for n in load_data("npcs"):
        c.execute(
            "INSERT INTO npcs (key, name, role, location, baseline_personality, "
            "base_decay, traits, shop, combat_tier, is_wizard, disposition) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                n["key"], n["name"], n["role"], n["location"], n["personality"],
                n["base_decay"], ",".join(n.get("traits", [])),
                1 if n.get("shop") else 0, n.get("combat_tier", "weak"),
                1 if n.get("is_wizard") else 0,
                n.get("dispositions", {}).get(klass, 0),
            ),
        )
    c.commit()
