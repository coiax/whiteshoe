import itertools
import math

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
