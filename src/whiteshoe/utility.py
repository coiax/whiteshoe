import itertools
import math
import collections

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

def bresenhams_line(p1, p2):
    points = []

    # Pseudocode straight from wikipedia
    x0, y0 = p1
    x1, y1 = p2

    deltax = x1 - x0
    assert deltax != 0 # Line is not vertical
    deltay = y1 - y0

    error = 0.0
    deltaerr = abs(float(deltay) / float(deltax))
    y = y0

    for x in range(x0, x1 + 1):
        points.append((x,y))
        error += deltaerr
        if error >= 0.5:
            y += 1
            error -= 1.0

    return points

