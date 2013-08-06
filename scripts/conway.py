from __future__ import print_function

import curses
import os
import random
import time

os.environ["TERM"] = "xterm-256color"

def neighbours(x,y):
    return [
        (x + 1, y + 1), (x + 0, y + 1), (x - 1, y + 1),
        (x + 1, y + 0),                 (x - 1, y + 0),
        (x + 1, y - 1), (x + 0, y - 1), (x - 1, y - 1)
    ]

def main(window):
    curses.start_color()
    curses.use_default_colors()
    for a in range(curses.COLORS):
        try:
            curses.init_pair(a, a, -1)
            pass
        except (curses.error, OverflowError):
            continue

    B = [3,6]
    S = [2,3]
    max_y, max_x = window.getmaxyx()
    world = {}
    for x in range(max_x):
        for y in range(max_y):
            world[x,y] = 1 if random.random() < 0.35 else 0
    try:
        while True:
            time.sleep(0.1)
            for x,y in world:
                value = world[x,y]
                chr = "O" if value else " "
                attr = curses.color_pair(value % curses.COLORS)
                attr |= curses.A_BOLD
                try:
                    window.addstr(y,x,chr,attr)
                except curses.error:
                    pass
            window.refresh()

            borned = []
            survived = []
            died = []
            for x,y in world:
                alive_neighbours = 0
                for neighbour in neighbours(x,y):
                    if world.get(neighbour, False):
                        alive_neighbours += 1
                if world[x,y]:
                    # Already alive.
                    if alive_neighbours in S:
                        survived.append((x,y))
                    else:
                        died.append((x,y))
                else:
                    if alive_neighbours in B:
                        borned.append((x,y))
            for born in borned:
                world[born] = 1
            for survivor in survived:
                world[survivor] += 1
            for doomed in died:
                world[doomed] = 0

    except KeyboardInterrupt:
        return

if __name__=='__main__':
    curses.wrapper(main)
