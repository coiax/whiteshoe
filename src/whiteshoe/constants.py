import operator
import sys

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

    N1 = 1
    N2 = 2
    N3 = 3
    N4 = 4
    N5 = 5
    N6 = 6
    N7 = 7
    N8 = 8
    N9 = 9

    _constants = vars().items()
    _constants.sort(key=operator.itemgetter(0))
    _constants_table = [c[1] for c in _constants]
    del _constants

    # Non-network constants after this point
    PACKET_SIZE_LIMIT = 600
    DEFAULT_PORT = 25008

    GET_GAMES_LIST = 0
    GAMES_RUNNING = 1
    MAKE_NEW_GAME = 2
    ERROR = 3
    GAME_ACTION = 4
    JOIN_GAME = 5
    VISION_UPDATE = 6
    KEEP_ALIVE = 7
    GAME_STATUS = 8

    STATUS_JOINED = 1
    STATUS_LEFT = 2

    DIRECTIONS = (UP, DOWN, LEFT, RIGHT)

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
    OPAQUE_OBJECTS = WALLS
    ALWAYS_VISIBLE_OBJECTS = (OBJ_EXPLOSION,)

    VISIBLE_OBJECTS = WALLS + (OBJ_EMPTY,OBJ_PLAYER,OBJ_EXPLOSION,OBJ_BULLET)
    BLOWABLE_UP = WALLS + (OBJ_PLAYER,)
    CAN_STAB = (OBJ_PLAYER,)

    ATTRIBUTE_KEYS = ("number", "direction", "team", "hp_max", "hp",
                      "max_ammo", "ammo", "owner","size","historical",
                      "name")
    ATTRIBUTE_CONSTANT_KEYS = ("direction",)

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
