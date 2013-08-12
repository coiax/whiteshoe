from __future__ import print_function

import itertools
import math
import collections
import datetime
import logging
logger = logging.getLogger(__name__)
import random
import re
import struct
import marshal
import operator
import threading
try:
    import cPickle as pickle
except ImportError as __e:
    __fmt = "Failure to import cPickle, using slower pickle module: {}"
    logger.warning(__fmt.format(__e))

    import pickle

import bsdiff4

logger = logging.getLogger(__name__)

class IDCounter(object):
    def __init__(self):
        self._start = 0
        self._lock = threading.Lock()
        self._counters = {}
        self._released = {}

    def get_id(self, family):
        with self._lock:
            try:
                if self._released[family]:
                    new_id = self._released[family].pop()
                else:
                    new_id = self._counters[family]
                    self._counters[family] = new_id + 1
                return new_id
            except KeyError:
                self._released[family] = []
                self._counters[family] = self._start + 1
                return self._start

    def release_id(self, family, id):
        with self._lock:
            assert id < self._counters[family]
            try:
                assert id not in self._released[family]
                self._released[family].append(id)
            except KeyError:
                self._released[family] = [id]

_id_counter = IDCounter()

get_id = _id_counter.get_id
release_id = _id_counter.release_id

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

def border(coords):
    border_coords = []
    for coord in coords:
        border_coords.extend(neighbourhood(coord))
    border_coords = set(border_coords) - set(coords)
    return list(border_coords)

def cardinal_neighbourhood(x_y):
    x, y = x_y
    return [
        (x, y + 1),
        (x, y - 1),
        (x + 1, y),
        (x - 1, y),
    ]

def wall_direction(wall_coords, chosen_wall):
    # returns either a horizontal -
    # or vertical |
    # indicator.

    x,y = chosen_wall
    if (x - 1, y) in wall_coords or (x + 1, y) in wall_coords:
        return '-'
    else:
        return '|'

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

#TYPE = "MARSHAL"
TYPE = "PICKLE"

if TYPE == "PICKLE":
    def quick_pickle(obj):
        return pickle.dumps(obj, pickle.HIGHEST_PROTOCOL)

    def quick_unpickle(binstr):
        return pickle.loads(binstr)
elif TYPE == "MARSHAL":
    def quick_pickle(obj):
        return marshal.dumps(obj)
    def quick_unpickle(binstr):
        return marshal.loads(binstr)

class DifflingAuthor(object):
    def __init__(self, aggressive=False):
        self._items = {}
        self._pickled = {}
        self._changed = set()
        self.aggressive = aggressive

    def __setitem__(self, key, value):
        if key not in self._items:
            self._pickled[key] = quick_pickle(None)

        self._items[key] = value

        self.hint(key)

    def hint(self, key):
        # Hint that the value associated with the key has changed.
        self._changed.add(key)

    def get_changes(self):
        changes = []
        # Aggressive is pickle and check every time get_changes is called.
        # Probably only good for debugging.
        if not self.aggressive:
            changed = list(self._changed)
        else:
            changed = list(self._items)

        self._changed.clear()

        for key in changed:
            old_state = self._pickled[key]
            current_state = quick_pickle(self._items[key])

            if old_state == current_state:
                # Misleading hint. Maybe whine about it. But do nothing more.
                # but only whine if we're not aggressive.
                continue

            # Otherwise, since we're calculating the changes, set the
            # current state.
            self._pickled[key] = current_state
            diff_bytes = bsdiff4.diff(old_state, current_state)
            changes.append((key, diff_bytes))

        return changes

class DifflingReader(collections.Mapping):
    def __init__(self):
        self._items = {}
        self._pickled = {}

    def __getitem__(self, key):
        return self._items[key]
    def __len__(self):
        return len(self._items)
    def __contains__(self, key):
        return  key in self._items
    def __iter__(self):
        return iter(self._items)
    def copy(self):
        return self._items.copy()

    def feed(self, key, diff):
        if key not in self._items:
            self._pickled[key] = quick_pickle(None)

        old_state = self._pickled[key]
        new_state = bsdiff4.patch(old_state, diff)

        self._pickled[key] = new_state
        self._items[key] = quick_unpickle(new_state)

    def _feed_list(self, L):
        for key, diff in L:
            self.feed(key, diff)

def test_diffling():
    da = DifflingAuthor()
    dr = DifflingReader()

    da["key"] = test1 = "The patching stuff appears to be working."

    dr._feed_list(da.get_changes())
    assert dr["key"] == test1

    da["key"] = test2 = "The patching stuff continues to work. Probably."

    dr._feed_list(da.get_changes())
    assert dr["key"] == test2

def dict_check(obj):
    singleton = object()
    value = object()
    try:
        obj[singleton] = value
        obj[singleton]
        del obj[singleton]
    except TypeError:
        return False
    else:
        return True

class nesty(object):
    def __init__(self, _prefix=(), _dict=None):
        if _dict is None:
            _dict = {}

        self._dict = _dict
        self._prefix = _prefix
        self._keys = set()

    def __repr__(self):
        name = self.__class__.__name__
        prefix = self._prefix
        keys = self._keys

        fmt = "<{name} prefix={prefix} keys={keys}>"

        return fmt.format(name=name,prefix=prefix,keys=keys)

    def __getitem__(self, key):
        return self._dict[self._prefix + (key,)]
    def __setitem__(self, key, value):
        if dict_check(value):
            if type(value) != dict:
                raise TypeError("Non vanilla dicts cannot use a nesty.")
            replacement = self.__class__(_prefix=self._prefix + (key,),
                                         _dict=self._dict)
            for inner_key in value:
                replacement[inner_key] = value[inner_key]

            self._dict[self._prefix + (key,)] = replacement
        else:
            self._dict[self._prefix + (key,)] = value
        self._keys.add(key)

    def __delitem__(self, key):
        key_prefix = self._prefix + (key,)
        key_prefix_length = len(key_prefix)

        value = self._dict.pop(key_prefix)
        self._keys.remove(key)

        for key in list(self._dict):
            if key[:key_prefix_length] == key_prefix:
                del self._dict[key]


    def keys(self):
        return self._keys

    def empty(self):
        for key in self._keys:
            del self[key]

    def __contains__(self, key):
        return key in self._keys
    def __len__(self):
        return len(self._keys)
    def items(self):
        for key in self._keys:
            yield (key, self._dict[self._prefix + (key,)])
    def values(self):
        for key in self._keys:
            yield self._dict[self._prefix + (key,)]
    def get(self, key, default=None):
        if key in self:
            return self[key]
        else:
            return default

def test_nesty():
    n = nesty()
    n["layer1"] = "The bottom."
    n["depth"] = {}
    n["depth"]["layer2"] = "Hello there!"
    del n["depth"]["layer2"]

    assert n["depth"].get("layer2") is None
    n["depth"]["doomed"] = 40
    del n["depth"]
    assert len(n._dict) == 1

    n = nesty()
    n["one"] = {"two": {"three": "value"}}
    assert n["one"]["two"]["three"] == 'value'

def perimeter(coords):
    sorted_coords = list(sorted(coords,key=operator.itemgetter(1,0)))
    topleft = sorted_coords[0]
    bottomright = sorted_coords[-1]

    # Then the perimeter is all coordinates that share an X or a Y with
    # either the topleft or the bottomright.
    x1, y1 = topleft
    x2, y2 = bottomright

    perimeter = []
    for coord in sorted_coords:
        if coord[0] in (x1,x2) or coord[1] in (y1,y2):
            perimeter.append(coord)

    return perimeter

def coordinate_check(potentional_coord):
    # Screens for a 3 length iterable containing only integers.
    try:
        x,y,z = key
        if not (type(x) == type(y) == type(z) == int):
            raise ValueError
    except ValueError, TypeError:
        # Non coordinate key, it's probably the level settings
        return False
    else:
        return True

def corners(coords):
    # Assume it's some sort of 2D rectangular structure.
    sorted_coords = sorted(coords,key=operator.itemgetter(1,0))

    # (x1, y1)---(x2, y1)
    #    |          |
    # (x1, y2)---(x2, y2)

    x1, y1 = topleft = sorted_coords[0]
    x2, y2 = bottomright = sorted_coords[-1]

    topright = x2, y1
    bottomleft = x1, y2

    return set([topleft, topright, bottomleft, bottomright])

    # Then the topright should 

# FIXME It does occur to me that this utility module is becoming VERY large.

def get_entity_state(entity_data, entity_states, entity):
    entity_id, entity_type = entity

    entity_datum = entity_data.get(entity_type, {})
    entity_state = entity_states.get(entity_id, {})
    entity_flag_set = entity_datum.get('entity_flag_set', {})

    actual_state = {}
    # So the datum is the base state, like
    # grid bug is purple. grid bug is an 'x' symbol.
    actual_state.update(entity_datum)

    # Then the entity_state is the specific stuff to that entity,
    # like, this entity is 'slowed', or this entity has been permamantly
    # afflicted with a case of the gribblies.
    actual_state.update(entity_state)

    # Then calculate flags, to work out if any entity_flag_set stuff triggers.
    flags = combine_flags(entity_datum.get('flags', ()),
                          entity_state.get('flags', ()))

    for flag in tuple(flags):
        if flag in entity_flag_set:
            actual_state.update(entity_flag_set[flag])

            flags = combine_flags(flags, entity_flag_set[flag].get('flags',()))

    actual_state['flags'] = flags
    return actual_state

def combine_flags(A, B):
    A = set(A)
    B = set(B)
    result = set()

    for flag in A:
        # Skip not-flags. They don't persist and nullify.
        if flag[0] != '!':
            result.add(flag)

    for flag in B:
        # If there's a '!flag' in B, then the result WILL NOT have 'flag'.
        if flag[0] == '!':
            result.discard(flag[1:])
        else:
            result.add(flag)

    return tuple(result)


def generate_path(x_length, y_length, start,
                  forbidden=(), whitelist=None, order='xy'):

    assert len(start) == 2
    # The below is always true, but will complain if forbidden cannot
    # __contains__ things.
    assert (None in forbidden) or (None not in forbidden)
    assert whitelist is None or list(whitelist)
    assert order in ('xy','yx')

    path = []
    path.append(start)

    current = start

    for letter in order:
        if letter == 'x':
            negative_x_length = x_length < 0
            for i in range(abs(x_length)):
                cx, cy = current

                nx = cx + (-1 if negative_x_length else 1)
                ny = cy

                current = nx, ny
                path.append(current)

        elif letter == 'y':
            negative_y_length = y_length < 0
            for i in range(abs(y_length)):
                cx, cy = current

                nx = cx
                ny = cy + (-1 if negative_y_length else 1)

                current = nx, ny
                path.append(current)

        if (set(path) & set(forbidden)):
            return False
        if whitelist is not None and set(path) - set(whitelist):
            return False
    return path

def try_many_paths(X, Y, start_coord, whitelist=None, forbidden=(),
                   our_random=random):
    r = our_random

    path = set()
    x_list = list(range(-X, X))
    r.shuffle(x_list)
    y_list = list(range(-Y, Y))
    r.shuffle(y_list)

    random_order = ['xy','yx']
    r.shuffle(random_order)

    for x in x_list:
        for y in y_list:
            for order in random_order:
                out = generate_path(x,y,start_coord,
                                    forbidden=forbidden, whitelist=whitelist)
                if out:
                    yield out
