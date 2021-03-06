import itertools
import math
import collections
import datetime
import logging
import random
import re
import struct

import constants

logger = logging.getLogger(__name__)

class IDCounter(object):
    def __init__(self):
        self._counters = {}

    def get_id(self, family):
        if family not in self._counters:
            self._counters[family] = 0

        new_id = self._counters[family]
        self._counters[family] = (new_id + 1) % 2**31
        return new_id

get_id = IDCounter().get_id

class Stopwatch(object):
    def __init__(self, start=False):
        self.running = False

        self.start_time = None

        if start:
            self.start()
    @property
    def elapsed_time(self):
        if self.start_time is None:
            return None
        else:
            return datetime.datetime.now() - self.start_time

    @property
    def elapsed_seconds(self):
        if self.start_time is None:
            return None
        else:
            return self.elapsed_time.total_seconds()

    def start(self):
        assert not self.running
        self.restart()

    def restart(self):
        elapsed_time = self.elapsed_time
        self.running = True
        self.start_time = datetime.datetime.now()
        return elapsed_time

    def stop(self):
        self.running = False

        elapsed_time = self.elapsed_time

        self.start_time = None

        return elapsed_time

class RecurringTimer(object):
    def __init__(self, period):
        assert period > 0
        self.period = period
        self.last_time = None

        self.accumulated = datetime.timedelta()

    def start(self):
        self.last_time = datetime.datetime.now()

    def check(self):
        """Returns the number of periods that have passed since the last call
        to start() or check()."""
        if self.last_time is None:
            # Auto start
            self.start()
            return 0

        now = datetime.datetime.now()
        if now < self.last_time:
            # We've gone backwards in time. For some reason.
            logger.warning("RecurringTimer: time's gone backwards.")
            return 0

        delta = now - self.last_time

        self.accumulated += delta

        seconds = self.accumulated.total_seconds()
        amount = int(seconds // self.period)
        if amount != 0:
            remainder = seconds % self.period
            self.accumulated = datetime.timedelta(0, remainder)

            assert remainder < self.period

        self.last_time = now
        return amount

class bidict(collections.MutableMapping):
    def __init__(self, dict_=None):
        self._a_to_b = {}
        self._b_to_a = {}
        self._items = set()

        if dict_ is not None:
            for key, value in dict_.items():
                self[key] = value

    def __eq__(self, other):
        return self.items() == other.items()
    def __repr__(self):
        return "bidict({0})".format(repr(dict(self.items())))
    def __getitem__(self, key):
        if key not in self._items:
            raise KeyError(key)
        if key in self._a_to_b:
            return self._a_to_b[key]
        else:
            return self._b_to_a[key]
    def __setitem__(self, key, value):
        if key in self:
            del self[key]
        if value in self:
            del self[value]

        self._items.add(key)
        self._items.add(value)

        self._a_to_b[key] = value
        self._b_to_a[value] = key

    def __delitem__(self, key):
        if key not in self._items:
            raise KeyError(key)
        self._items.remove(key)
        if key in self._a_to_b:
            del self._a_to_b[key]
        if key in self._b_to_a:
            del self._b_to_a[key]

    def __len__(self):
        return len(self._items)
    def __iter__(self):
        return iter(self._items)
    def __contains__(self, key):
        return key in self._items

def grouper(n, iterable, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return itertools.izip_longest(fillvalue=fillvalue, *args)

def neighbourhood(coord,n=1):
    coords = []
    x,y = coord
    for i in range(x-n, x+n+1):
        for j in range(y-n, y+n+1):
            coords.append((i,j))
    return coords

def cardinal_neighbourhood(x_y):
    x, y = x_y
    return [
        (x, y + 1),
        (x, y - 1),
        (x + 1, y),
        (x - 1, y),
    ]

def bytes_to_human(bytes):
    # 0 for bytes, 1 for k, 2 for meg, 3 for gig
    # Thanks to Toby/TheNerd for this function
    assert bytes >= 0
    if bytes == 0:
        return "0B"

    value = int(math.log(bytes,2) // 10)
    scaled = bytes / float(2**(10*value))
    suffixes = ["B", "KiB", "MiB", "GiB", "TiB", "FUCK YOU"]
    if value == 0:
        fmt = "{0:.0f}{1}"
    else:
        fmt = "{0:.2f}{1}"
    return fmt.format(scaled, suffixes[value])

def dict_difference(old, new):
    changed = set()
    # So all keys that are not present/are present with the old
    # and the new, are changed.
    changed.update(set(old).symmetric_difference(new))

    shared = set(old) & set(new)

    for key in shared:
        if old[key] != new[key]:
            changed.add(key)

    return changed

class CellularAutomaton(collections.MutableMapping):
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self._grid = [False] * self.width * self.height

    def _cell_ref(self, x, y):
        return self.width*y + x

    def cells(self):
        for y in xrange(self.height):
            for x in xrange(self.width):
                yield x, y

    def __getitem__(self, x_y):
        x, y = x_y
        return self._grid[self._cell_ref(x, y)]

    def __setitem__(self, x_y, value):
        x, y = x_y
        self._grid[self._cell_ref(x, y)] = value

    def __delitem__(self, x_y):
        self[x_y] = False

    def __len__(self):
        return len(self._grid)

    def __contains__(self, x_y):
        return x_y in self.cells()

    def __iter__(self):
        return iter(self.cells())

    def seed(self, density = 0.5, rng = random):
        for coord in self.cells():
            self[coord] = rng.random() < density

    def in_bounds(self, coord):
        x, y = coord
        return 0 <= x < self.width and 0 <= y < self.height

    def apply(self, rules, boundary = False):
        live = False

        birth_rule, survive_rule = [[int(n) for n in x] for x in rules.split('/')]

        will_birth = set()
        will_die = set()

        for coord in self.cells():
            alive = self[coord]

            neighbours = neighbourhood(coord, n=1)
            neighbours.remove(coord)
            # Remember that True has a numeric value of 1
            number = sum(self[n] if self.in_bounds(n) else boundary for n in neighbours)
            if not alive and number in birth_rule:
                will_birth.add(coord)
                live = True

            elif alive and number not in survive_rule:
                will_die.add(coord)
                live = True

        for coord in will_birth:
            self[coord] = True

        for coord in will_die:
            self[coord] = False

        return live

    def converge(self, rules, max_ticks = 300, boundary = False):
        live = True
        ticks = 0
        while live and ticks < max_ticks:
            ticks += 1
            live = self.apply(rules, boundary)

def ca_world_to_world(ca_world,inverse=False):
    world = {}
    for coord in ca_world:
        is_wall = bool(ca_world[coord])
        if inverse:
            is_wall = not is_wall

        if is_wall:
            world[coord] = [(constants.OBJ_WALL, {})]
        else:
            world[coord] = [(constants.OBJ_EMPTY, {})]

    return world

_stream_fmt = '>L'

def stream_wrap(data):
    # Take binary data, and prepend a four byte integer size
    # and return the new data with the size prepended
    size = len(data)

    size_bytes = struct.pack(_stream_fmt, size)

    return size_bytes + data

def stream_unwrap(data):
    # Given a stream of binary data, prepended with four bytes integer
    # sizes, return a list of binary datas, and unconsumed data

    unpacked = []

    minimum_size = struct.calcsize(_stream_fmt)

    while True:
        if len(data) < minimum_size:
            break
        next_chunk_size = struct.unpack(_stream_fmt, data[:minimum_size])[0]

        if len(data) < minimum_size + next_chunk_size:
            break
        else:
            # Chop the leading size integer off
            data = data[minimum_size:]
            unpacked.append(data[:next_chunk_size])

            data = data[next_chunk_size:]

    return unpacked, data
