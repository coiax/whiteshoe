import itertools

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
