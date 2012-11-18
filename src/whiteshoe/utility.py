import itertools
import math
import collections
import datetime
import logging

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

def grouper(n, iterable, fillvalue=None, izip_longest_=itertools.izip_longest):
    "Collect data into fixed-length chunks or blocks"
    # grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return izip_longest_(fillvalue=fillvalue, *args)

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
