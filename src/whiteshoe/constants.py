import operator
import sys
import os.path

from flufl.enum import Enum, IntEnum
from utility import get_id

# Call once to "consume" zero.
get_id('enum')


class Direction(Enum):
    up = get_id('enum')
    down = get_id('enum')

    left = get_id('enum')
    right = get_id('enum')

    north = get_id('enum')
    northeast = get_id('enum')
    east = get_id('enum')
    southeast = get_id('enum')
    south = get_id('enum')
    southwest = get_id('enum')
    west = get_id('enum')
    northwest = get_id('enum')

class Event(Enum):
    action = get_id('enum')

class Action(Enum):
    move = get_id('enum')
    look = get_id('enum')
    fire = get_id('enum')


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

DAMAGETYPE_UNKNOWN = 1
DAMAGETYPE_STAB = 2
DAMAGETYPE_EXPLOSION = 3
DAMAGETYPE_SLIME = 4
DAMAGETYPE_LAVA = 5

GET_GAMES_LIST = -1
GAMES_LIST = -2
MAKE_NEW_GAME = -3
ERROR = -4
JOIN_GAME = -5
KEEP_ALIVE = -6
DISCONNECT = -7

GAME_ACTION = 1
VISION_UPDATE = 2
GAME_STATUS = 3
GAME_MESSAGE = 4
KEYVALUE = 5

class Payload(IntEnum):
    event = 11
    picklediff = 12

DISCONNECT_SHUTDOWN = 1
DISCONNECT_KICKED = 2
DISCONNECT_ERROR = 3
DISCONNECT_TIMEOUT = 4

ERROR_NOT_IN_GAME = 1

STATUS_GAMEINFO = 1
STATUS_JOINED = 2
STATUS_LEFT = 3
STATUS_DEATH = 4
STATUS_DAMAGED = 5
STATUS_SPAWN = 6
STATUS_KILL = 7
STATUS_GAMEPAUSE = 8
STATUS_GAMERESUME = 9

KEYVALUE_SCORES = "scores"
KEYVALUE_ENHANCED_SCORES = "scores+"

#DIRECTIONS = (UP, RIGHT, DOWN, LEFT)
MULTIHACK_DIRECTIONS = (
    Direction.up,
    Direction.down,
    Direction.north,
    Direction.northeast,
    Direction.east,
    Direction.southeast,
    Direction.south,
    Direction.southwest,
    Direction.west,
    Direction.northwest
)

DIFFS = {
    Direction.north: (0, -1),
    Direction.northeast: (1,-1),
    Direction.east: (1, 0),
    Direction.southeast: (1,1),
    Direction.south: (0, 1),
    Direction.southwest: (-1,1),
    Direction.west: (-1,0),
    Direction.northwest: (-1,-1),
}

WALLS = (OBJ_WALL, OBJ_HORIZONTAL_WALL, OBJ_VERTICAL_WALL, OBJ_CORNER_WALL)
HISTORICAL_OBJECTS = WALLS + (OBJ_EMPTY,)
SOLID_OBJECTS = WALLS + (OBJ_PLAYER,)
AIRTIGHT_OBJECTS = WALLS
OPAQUE_OBJECTS = WALLS
ALWAYS_VISIBLE_OBJECTS = (OBJ_EXPLOSION,OBJ_SLIME)
TEMPORARY_OBJECTS = (OBJ_EXPLOSION, OBJ_SLIME)

VISIBLE_OBJECTS = WALLS + (OBJ_EMPTY,OBJ_PLAYER,OBJ_EXPLOSION,OBJ_BULLET,
                          OBJ_MINE)
BLOWABLE_UP = WALLS + (OBJ_PLAYER,OBJ_MINE,OBJ_SLIME)
CAN_STAB = (OBJ_PLAYER,)
SLIMEABLE = (OBJ_PLAYER, OBJ_MINE)

DISPLAY_CHAR = {
    OBJ_PLAYER: '@',
    OBJ_WALL: '#',
    OBJ_HORIZONTAL_WALL: '-',
    OBJ_VERTICAL_WALL: '|',
    OBJ_CORNER_WALL: '+',
    OBJ_BULLET: ':',
    OBJ_SLIME: '$',
    OBJ_SLIME_BULLET: '$',
    OBJ_LAVA: '~',
    OBJ_EMPTY: '.',
}

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

ORIGIN_UNKNOWN = -1
ORIGIN_ENVIRONMENT = -2

MINE_DIRECT_PROBABILITY = 0.98
MINE_SIDE_PROBABILITY = 0.50
MINE_BACKWARDS_PROBABILITY = 0.02

def to_numerical_constant(constant):
    return _constants_table.index(constant)

def from_numerical_constant(number):
    return _constants_table[number]

