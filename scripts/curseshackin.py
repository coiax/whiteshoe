from __future__ import print_function

import curses
import os

os.environ["TERM"] = "xterm-256color"

curses.setupterm(os.environ["TERM"])

num_colours = curses.tigetnum("colors")
information = []

def info(msg):
    information.append(msg)

info("Number of colours (according to .tigetnum()): {}".format(num_colours))

window = curses.initscr()
curses.start_color()

can_change_colour = curses.can_change_color()
info("Can change colours? {}".format(can_change_colour))

curses.use_default_colors()

max_colours = curses.COLORS
info("Max colours (according to curses.COLORS): {}".format(max_colours))

max_colour_pairs = curses.COLOR_PAIRS
info("Max colour pairs: {}".format(max_colour_pairs))

curses.endwin()

for factoid in information:
    print(factoid)
