import operator
import sys
import os.path

class Constants(object):
    UP = "up"
    NORTHEAST = "ne"
    NORTHWEST = "nw"
    DOWN = "down"
    SOUTHEAST = "se"
    SOUTHWEST = "sw"
    LEFT = "left"
    RIGHT = "right"

    CMD_MOVE = "move"
    CMD_LOOK = "look"
    CMD_FIRE = "fire"

    OBJ_WALL = "wall"
    OBJ_HORIZONTAL_WALL = "h-wall"
    OBJ_VERTICAL_WALL = "v-wall"
    OBJ_CORNER_WALL = "c-wall"
    OBJ_PLAYER = "player"
    OBJ_EMPTY = "empty"
    OBJ_BULLET = "bullet"
    OBJ_EXPLOSION = "boom"
    OBJ_MINE = "mine"
    OBJ_SLIME = "slime"
    OBJ_SLIME_BULLET = "slime-bullet"
    OBJ_LAVA = "lava"

    N1 = 1
    N2 = 2
    N3 = 3
    N4 = 4
    N5 = 5
    N6 = 6
    N7 = 7
    N8 = 8
    N9 = 9
    SMALL_SLIME = "small-slime"
    BIG_SLIME = "big-slime"

    # Check for duplicate values
    assert sorted(vars().values()) == sorted(set(vars().values())) # duplicates

    _constants = list(vars().items())
    _constants.sort(key=operator.itemgetter(0))
    _constants_table = [c[1] for c in _constants]
    del _constants

    # Non-network constants after this point

    BANNER = """Whiteshoe {version}""".format(version='0.0.0')

    CONFIG_LOCATIONS = (os.path.join('~','.config','whiteshoe','config'),
                        os.path.join('~','.whiteshoe','config'),
                       )
    SLIME_COSTS = {
        SMALL_SLIME: 5,
        BIG_SLIME: 10,
    }
    SLIME_SPREAD = {
        SMALL_SLIME: 10,
        BIG_SLIME: 20,
    }
    SLIME_BULLET_SPEED = {
        SMALL_SLIME: 0.10,
        BIG_SLIME: 0.20,
    }

    SLIME_SPREAD_TIME = 0.3
    SLIME_DAMAGE = 5

    LAVA_TIME = 0.5
    LAVA_DAMAGE = 1

    PACKET_SIZE_LIMIT = 600
    DEFAULT_PORT = 25008
    TIMEOUT = 30

    GET_GAMES_LIST = 0
    GAMES_RUNNING = 1
    MAKE_NEW_GAME = 2
    ERROR = 3
    GAME_ACTION = 4
    JOIN_GAME = 5
    VISION_UPDATE = 6
    KEEP_ALIVE = 7
    GAME_STATUS = 8
    DISCONNECT = 9

    DISCONNECT_SHUTDOWN = 1
    DISCONNECT_KICKED = 2
    DISCONNECT_ERROR = 3
    DISCONNECT_TIMEOUT = 4

    ERROR_NOT_IN_GAME = 1

    STATUS_JOINED = 1
    STATUS_LEFT = 2
    STATUS_DEATH = 3
    STATUS_DAMAGED = 4
    STATUS_SPAWN = 5
    STATUS_KILL = 6
    STATUS_GAMEPAUSE = 7
    STATUS_GAMERESUME = 8

    DIRECTIONS = (UP, RIGHT, DOWN, LEFT)

    DIFFS = {
        UP: (0, -1),
        NORTHEAST: (1,-1),
        NORTHWEST: (-1,-1),
        DOWN: (0, 1),
        SOUTHEAST: (1,1),
        SOUTHWEST: (-1,1),
        LEFT: (-1, 0),
        RIGHT: (1, 0)
    }
    ADJACENT = {
        UP: (LEFT, UP, RIGHT, NORTHEAST, NORTHWEST),
        DOWN: (LEFT, DOWN, RIGHT, SOUTHEAST, SOUTHWEST),
        LEFT: (UP, LEFT, DOWN, NORTHWEST, SOUTHWEST),
        RIGHT: (UP, RIGHT, DOWN, NORTHEAST, SOUTHEAST),
    }

    WALLS = (OBJ_WALL, OBJ_HORIZONTAL_WALL, OBJ_VERTICAL_WALL, OBJ_CORNER_WALL)
    HISTORICAL_OBJECTS = WALLS + (OBJ_EMPTY,)
    SOLID_OBJECTS = WALLS + (OBJ_PLAYER,)
    AIRTIGHT_OBJECTS = WALLS
    OPAQUE_OBJECTS = WALLS
    ALWAYS_VISIBLE_OBJECTS = (OBJ_EXPLOSION,OBJ_SLIME)

    VISIBLE_OBJECTS = WALLS + (OBJ_EMPTY,OBJ_PLAYER,OBJ_EXPLOSION,OBJ_BULLET,
                              OBJ_MINE)
    BLOWABLE_UP = WALLS + (OBJ_PLAYER,OBJ_MINE,OBJ_SLIME)
    CAN_STAB = (OBJ_PLAYER,)
    SLIMEABLE = (OBJ_PLAYER, OBJ_MINE)

    ATTRIBUTE_KEYS = ("player_id", "direction", "team", "hp_max", "hp",
                      "max_ammo", "ammo", "owner","size","historical",
                      "name")
    ATTRIBUTE_CONSTANT_KEYS = ("direction","size")

    BULLET_SPEEDS = {
        1: 0.05,
        2: 0.10,
        3: 0.15,
        4: 0.20,
        5: 0.25,
        6: 0.30,
        7: 0.35,
        8: 0.40,
        9: 0.45,
    }
    EXPLOSION_LIFE = 0.5
    KEEPALIVE_TIME = 5
    STAB_DAMAGE = 2

    MINE_DIRECT_PROBABILITY = 0.98
    MINE_SIDE_PROBABILITY = 0.50
    MINE_BACKWARDS_PROBABILITY = 0.02

    @classmethod
    def to_numerical_constant(cls,constant):
        return cls._constants_table.index(constant)

    @classmethod
    def from_numerical_constant(cls,number):
        return cls._constants_table[number]

# This is a Guido approved hack that replaces the module that you're about
# to import with a class instance, so that our vars() doesn't pick up
# all the builtin crap
sys.modules[__name__] = Constants()
