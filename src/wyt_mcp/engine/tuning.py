"""Every pacing knob in one file (§18). Tuned light — the game should run
many loops. When the simulation says the town dies too fast, change it here.
"""

# --- resolve (the player's own sanity) ------------------------------------
RESOLVE_DEATH = -8          # dying; supersedes a failed run — you didn't retreat
RESOLVE_RETREAT = -4        # entered the dungeon, cleared nothing new, slept
RESOLVE_KILL_NPC = -8       # killing a named townsperson
RESOLVE_FLOOR_CLEAR = +4    # clearing a floor's gatekeeper
TAVERN_REST = +6            # an evening with Tobias; once per loop
PARK_REST = +2              # an hour on the green; once per loop, early game only
PARK_SAFE_SANITY = 55       # town average below this and the green stops helping
IDLE_DESPAIR = 1            # truly idle day: no dungeon, no fight, nothing logged

# --- town sanity ----------------------------------------------------------
DECAY_LOOP_DIVISOR = 3      # nightly decay = base + roll(0, loop // this)
TOWN_EVENT_HIT = 3          # town-wide overnight event, per NPC
NPC_EVENT_HIT = 6           # targeted overnight event
NPC_EVENT_HIT_BIG = 12      # fire, public breakdown

# --- the crime valve (§16) -------------------------------------------------
GARRICK_SUPPORT = 2         # talking to him, once per loop
GARRICK_WARNING_SUPPORT = 6 # same act on the warning day — he needed someone
GARRICK_DEN_WIN = 2         # per den-crew fight won: the watch isn't alone
GARRICK_MURDER_WEIGHT = -10 # a shopkeeper murdered in his town

# --- rapport (manner of speech) ----------------------------------------------
RAPPORT_FRIEND = 40         # disposition at/above: -1 nightly decay, and a
                            # withdrawn NPC still opens the door for the player
RAPPORT_GUARD = 20          # above this, hostile flips on collapse are halved
FRIEND_PRICE_DISPOSITION = 30   # at/above: mates' rates at their own counter
FRIEND_PRICE_RATE = 0.9

# --- risk tiles -------------------------------------------------------------
FLAT_RISK = 0.15            # dens: always was rough, stays rough
SCALING_CAP = 0.35          # alleys/park ceiling
SCALING_PER_LOOP = 0.025    # alleys/park growth once past the grace loops
SCALING_GRACE_LOOPS = 3

# --- economy ----------------------------------------------------------------
GOLD_ROT_PER_LOOP = 0.15    # price multiplier growth per loop
REWARD_GOLD_BASE = 12       # npc_reward cap = base + per_loop x loop
REWARD_GOLD_PER_LOOP = 3    # (loop 1 = 15g: Sela's number for the errand)
SELL_RATE = 0.5             # matching shop pays half an item's current price
WRONG_SHOP_RATE = 0.25      # the wrong counter pays a quarter
FENCE_MARKUP = 2.5          # buying your town's goods back from a den

# --- dungeon & leveling ------------------------------------------------------
DUNGEON_SCALE_PER_LOOP = 0.08   # enemy stat growth per loop
XP_PER_LEVEL = 25               # xp_needed = this x level

# --- overnight events ---------------------------------------------------------
EVENTS_MAX = 2              # at most this many per night

# --- endings -------------------------------------------------------------------
DAWN_RESOLVE_MIN = 40       # below this, killing Malgor crowns a Successor
DAWN_BRUTALITY_MAX = 10     # at or above this, same
TOWN_BROKEN_AVG = 15        # town average sanity at/below this = fully broken
