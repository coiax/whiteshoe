from __future__ import print_function

import curses
import random
import time
import itertools

def main():
    curses.wrapper(main2)

def main2(stdscr):
    curses.use_default_colors()
    stdscr.nodelay(1)

    def print(x):
        stdscr.addstr("{0}".format(x), curses.color_pair(0))
        stdscr.refresh()

    data = {}
    scene = GameScene(data)
    while True:
        try:
            scene.tick(stdscr)

            # non-blocking
            c = stdscr.getch()
            if c != curses.ERR:
                if c == 27:
                    raise CloseProgram
                else:
                    scene.input(stdscr, c)

            time.sleep(0.01)

        except NewScene as ns:
            scene = ns[0]

        except CloseProgram:
            break
        except KeyboardInterrupt:
            break

class GameScene(object):
    def __init__(self, data):
        self.data = data
        self.network = FakeNetwork()
    def tick(self, stdscr):
        visible = self.network.get_visible()
        # TODO currently window viewport is based on the 0,0 topleft
        # corner, and we want to be able to move around
        player_coord = None

        for coord, objects in visible.items():
            x,y = coord
            display_chr = None
            for obj, attr in objects:
                display_chr = {
                    OBJ_WALL: '#',
                    OBJ_PLAYER: '@',
                    OBJ_EMPTY: ' '}[obj]

                if obj == OBJ_PLAYER:
                    player_coord = coord

            assert display_chr is not None
            try:
                stdscr.addstr(y,x,display_chr, curses.color_pair(0))
            except curses.error:
                pass

        stdscr.move(player_coord[1], player_coord[0])
        stdscr.refresh()

    def input(self, stdscr, c):
        cmds = {
            curses.KEY_DOWN: CMD_DOWN,
            curses.KEY_UP: CMD_UP,
            curses.KEY_LEFT: CMD_LEFT,
            curses.KEY_RIGHT: CMD_RIGHT,
            ord('j'): CMD_DOWN,
            ord('h'): CMD_LEFT,
            ord('k'): CMD_UP,
            ord('l'): CMD_RIGHT,
        }
        if c in cmds:
            cmd = cmds[c]
            self.network.send_command(cmd)

class FakeNetwork(object):
    def __init__(self):
        self.world = {}
        r = random.Random(0)

        for i,j in itertools.product(range(80), range(24)):
            if r.random() < 0.35:
                self.world[i,j] = [(OBJ_WALL, {})]
            else:
                self.world[i,j] = [(OBJ_EMPTY, {})]

        self.world[0,0] = [(OBJ_EMPTY, {}), (OBJ_PLAYER, {'number':0})]

    def update(self):
        # Do network things
        pass
    def send_command(self, command, *args):
        player_number = 0

        # Find player location
        location = None

        for coord, objects in self.world.items():
            for obj, attr in objects:
                if obj == OBJ_PLAYER and attr['number'] == player_number:
                    player = (obj, attr)
                    location = coord
                    break

            if location is not None:
                break

        assert location is not None

        self.world[location].remove(player)

        diffs = {
            CMD_UP: (0, -1),
            CMD_DOWN: (0, 1),
            CMD_LEFT: (-1, 0),
            CMD_RIGHT: (1, 0)
        }

        diff = diffs[command]

        new_location = (location[0] + diff[0], location[1] + diff[1])

        if new_location in self.world:
            self.world[new_location].append(player)
        else:
            # Player can't move to that location, no move
            self.world[location].append(player)

    def get_visible(self):
        return self.world


# Constants
CMD_UP = "up"
CMD_DOWN = "down"
CMD_LEFT = "left"
CMD_RIGHT = "right"

OBJ_WALL = "wall"
OBJ_PLAYER = "player"
OBJ_EMPTY = "empty"

class NewScene(Exception):
    pass

class CloseProgram(Exception):
    pass

if __name__=='__main__':
    main()
