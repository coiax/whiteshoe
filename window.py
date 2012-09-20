from __future__ import print_function

import curses
import random
import time
import itertools
import operator
import argparse
import socket
import select
import sys
import traceback

import packet_pb2

def main():
    curses.wrapper(main2)

def main2(stdscr):
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_BLACK, -1)
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

            time.sleep(0.02)

        except NewScene as ns:
            scene = ns[0]

        except CloseProgram:
            break
        except KeyboardInterrupt:
            break

def display_character(object, history=False):
    display_chr = None
    # Default colour
    colour = curses.color_pair(0)

    obj, attr = object

    display_chr = {
        OBJ_WALL: '#',
        OBJ_PLAYER: '@',
        OBJ_EMPTY: '.'}[obj]

    if obj == OBJ_PLAYER:
        colour = curses.color_pair(attr['colour'])
        direction = attr['direction']

        if direction == RIGHT:
            display_chr = '>'
        elif direction == LEFT:
            display_chr = '<'
        elif direction == UP:
            display_chr = '^'
        elif direction == DOWN:
            display_chr = 'v'

    assert display_chr is not None

    if history:
        # Grey
        colour = curses.color_pair(3)

    return display_chr, colour


class GameScene(object):
    def __init__(self, data):
        self.data = data
        self.network = FakeNetwork()
    def tick(self, stdscr):
        visible, history = self.network.get_visible()

        # TODO currently window viewport is based on the 0,0 topleft
        # corner, and we want to be able to move around
        stdscr.clear()

        for coord, objects in history.items():
            x,y = coord
            for o in objects:
                display_chr, colour = display_character(o,history=True)
            try:
                stdscr.addstr(y,x,display_chr, colour)
            except curses.error:
                pass

        for coord, objects in visible.items():
            x,y = coord
            for o in objects:
                display_chr, colour = display_character(o)
            try:
                stdscr.addstr(y,x,display_chr, colour)
            except curses.error:
                pass

        x,y = self.network.find_me()
        stdscr.move(y,x)

        stdscr.refresh()

    def input(self, stdscr, c):
        #print(c)
        cmds = {
            curses.KEY_DOWN: (CMD_MOVE, DOWN),
            curses.KEY_UP: (CMD_MOVE, UP),
            curses.KEY_LEFT: (CMD_MOVE, LEFT),
            curses.KEY_RIGHT: (CMD_MOVE, RIGHT),
            # vim keys
            ord('j'): (CMD_MOVE, DOWN),
            ord('h'): (CMD_MOVE, LEFT),
            ord('k'): (CMD_MOVE, UP),
            ord('l'): (CMD_MOVE, RIGHT),

            # Looking directions, arrow keys with SHIFT held down
            curses.KEY_SF: (CMD_LOOK, DOWN),
            curses.KEY_SR: (CMD_LOOK, UP),
            curses.KEY_SRIGHT: (CMD_LOOK, RIGHT),
            curses.KEY_SLEFT: (CMD_LOOK, LEFT),

            #
            ord('J'): (CMD_LOOK, DOWN),
            ord('H'): (CMD_LOOK, LEFT),
            ord('K'): (CMD_LOOK, UP),
            ord('L'): (CMD_LOOK, RIGHT),
        }
        if c in cmds:
            cmd = cmds[c]
            self.network.send_command(cmd)
        elif c == ord('f'):
            curses.flash()
        elif c == ord('r'):
            self.network.generate_world(seed=random.random())

class FakeNetwork(object):
    def __init__(self):
        self.generate_world()

    def generate_world(self,seed=0):
        self.world = {}
        r = random.Random(seed)

        for i,j in itertools.product(range(80), range(24)):
            if r.random() < 0.35:
                self.world[i,j] = [(OBJ_WALL, {})]
            else:
                self.world[i,j] = [(OBJ_EMPTY, {})]
        player = (OBJ_PLAYER, {'number':0, 'direction': RIGHT, 'colour':1})
        # DEBUG Give player "seen" for whole world
        player[1]['seen'] = set(self.world)

        self.world[40,10] = [(OBJ_EMPTY, {}), player]

    def update(self):
        # Do network things
        pass
    def _find_player(self, number):
        # Find player location
        location = None

        for coord, objects in self.world.items():
            for obj, attr in objects:
                if obj == OBJ_PLAYER and attr['number'] == number:
                    player = (obj, attr)
                    location = coord
                    break

            if location is not None:
                break

        assert location is not None

        return location, player

    def send_command(self, commands):
        player_number = 0

        command = commands[0]
        args = commands[1:]

        location, player = self._find_player(player_number)

        handlers = {
            CMD_LOOK: self._look,
            CMD_MOVE: self._move
        }

        handlers[command](player, location, args)

    def _look(self, player, location, args):
        direction = args[0]
        player[1]['direction'] = direction

    def _move(self, player, location, args):
        self.world[location].remove(player)

        diff = DIFFS[args[0]]

        new_location = (location[0] + diff[0], location[1] + diff[1])
        moved = False

        if new_location in self.world:
            # If the area is empty
            if OBJ_EMPTY in [oa[0] for oa in self.world[new_location]]:
                self.world[new_location].append(player)
                moved = True

        if not moved:
            # Player can't move to that location, no move
            self.world[location].append(player)

    def find_me(self):
        player_number = 0

        location, player = self._find_player(player_number)
        return location

    def _vision(self,location,player):
        attr = player[1]

        visible = set()
        # The square you are in is always visible as well one square
        # behind you
        visible.add(location)

        main_direction = DIFFS[attr['direction']]
        behind_you = main_direction[0] * -1, main_direction[1] * -1

        visible.add((location[0] + behind_you[0], location[1] + behind_you[1]))

        adjacent = {
            UP: (LEFT, UP, RIGHT, NORTHEAST, NORTHWEST),
            DOWN: (LEFT, DOWN, RIGHT, SOUTHEAST, SOUTHWEST),
            LEFT: (UP, LEFT, DOWN, NORTHWEST, SOUTHWEST),
            RIGHT: (UP, RIGHT, DOWN, NORTHEAST, SOUTHEAST),
        }

        # First, everything in the direction the player is looking
        # straight ahead

        def look_until_wall(start, diff):
            coord = start
            v = set()

            while True:
                coord = coord[0] + diff[0], coord[1] + diff[1]
                if coord not in self.world:
                    break
                v.add(coord)
                objects = self.world[coord]
                if OBJ_WALL in [o[0] for o in objects]:
                    break
            return v

        for direction in adjacent[attr['direction']]:
            visible.update(look_until_wall(location, DIFFS[direction]))

        return visible

    def get_visible(self):
        player_number = 0

        location, player = self._find_player(player_number)
        attr = player[1]

        if 'seen' not in attr:
            attr['seen'] = set()

        seen = attr['seen']

        visible_now = self._vision(location, player)
        seen.update(visible_now)

        visible = {}
        history = {}
        for coord in seen:
            history[coord] = []
            for obj,attr in self.world[coord]:
                if obj in (OBJ_WALL, OBJ_EMPTY):
                    history[coord].append((obj,attr))

        for coord in visible_now:
            visible[coord] = self.world[coord]
        return visible, history

def neighbourhood(coord,n=1):
    coords = []
    x,y = coord
    for i in range(x-n, x+n+1):
        for j in range(y-n, y+n+1):
            coords.append((i,j))
    return coords

def server_main():
    s = Server()
    s.serve()

class Server(object):

    def __init__(self):
        self.handlers = {
            0: self._initial_greeting,
            # 1 games running (s->c)
            2: self._join_or_new_game,
            3: self._error,
            4: self._game_action,
            # 5 reserved
            # 6 vision update (s->c)
            7: self._keep_alive,

        }
        self.port = 25008
        self.games = []

        self.seen_ids = []
        self.id_counters = {}

        self.clients = {}

        self.socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

    def get_id(self,family='packet'):
        if family not in self.id_counters:
            self.id_counters[family] = 0
        new_id = self.id_counters[family]
        self.id_counters[family] = (new_id + 1) % 2**16
        return new_id

    def serve(self):
        self.socket.bind(('',self.port))

        while True:
            for game in self.games:
                game.tick()

            rlist, wlist, xlist = select.select([self.socket],[],[],0.1)
            for rs in rlist:
                data, addr = rs.recvfrom(4096)

                try:
                    packet = packet_pb2.Packet.FromString(data)

                    # disabled while we're debugging
                    #if (addr,packet.packet_id) in self.seen_ids:
                        # Ignore duplicates
                        #    continue

                    self.seen_ids.append((addr,packet.packet_id))

                    while len(self.seen_ids) > 200:
                        del self.seen_ids[0]

                    for payload_type in packet.payload_types:
                        self.handlers[payload_type](packet, addr)

                    if addr not in self.clients:
                        self.clients[addr] = {
                            'player_id': self.get_id('player')
                        }

                    self.clients[addr]['last heard from'] = time.time()


                except Exception as e:
                    traceback.print_exc()

    def _initial_greeting(self, packet, addr):
        reply = packet_pb2.Packet()
        reply.payload_types.append(self.GAMES_RUNNING)

        reply.packet_id = self.get_id()

        for game in self.games:
            game_message = reply.games.add()

            game_message.game_id = game.id
            game_message.name = game.name
            game_message.mode = game.mode
            game_message.max_players = game.max_players
            game_message.current_players = game.current_players

        data = reply.SerializeToString()
        self.socket.sendto(data, addr)

    def _join_or_new_game(self, packet, addr):
        if packet.game_id == -1:
            # creating new game
            max_players = packet.max_players or 20
            map_generator = packet.map_generator or "maze"
            game_name = packet.new_game_name or "Unnamed"
            game_mode = packet.new_game_mode or "ffa"
            game_id = self.get_id('game')

            g = Game(max_players,map_generator,game_name,game_mode,game_id)
            self.games.append(g)

        else:
            # joining existing game
            game_id = packet.game_id
            assert game_id in [g.id for g in self.games]
            g.player_join(self.clients[addr]['player_id'])

    def _error(self, packet, addr):
        # Handle silently.
        pass

    def _game_action(self, packet, addr):
        player_id = self.clients[addr]['player_id']
        game_id = packet.action_game_id

        game = [g for g in self.games if g.id == game_id][0]

        assert game.is_player_in_game(player_id)

    def _keep_alive(self, packet, addr):
        pass

    GAMES_RUNNING = 1

class Game(object):
    def __init__(self,max_players,map_generator,name,mode,id):
        self.max_players = max_players
        self.map_generator = map_generator
        self.name = name
        self.mode = mode
        self.id = id

        self.players = []

    @property
    def current_players(self):
        return 0

    def is_player_in_game(self,player_id):
        return player_id in self.players

    def player_join(self,player_id):
        assert player_id not in self.players
        self.players.append(player_id)

    def player_action(self, action, argument):
        pass

    def tick(self):
        # Do anything that occurs independently of network input
        pass


# Constants
UP = "up"
NORTHEAST = "ne"
NORTHWEST = "nw"
DOWN = "down"
SOUTHEAST = "se"
SOUTHWEST = "sw"
LEFT = "left"
RIGHT = "right"

DIFFS = {
    UP: (0, -1),
    NORTHEAST: (1,-1),
    NORTHWEST: (-1,-1),
    DOWN: (0, 1),
    SOUTHEAST: (1,1),
    SOUTHWEST: (-1,1),
    LEFT: (-1, 0),
    RIGHT: (1, 0)
}

CMD_MOVE = "move"
CMD_LOOK = "look"
CMD_FIRE = "fire"

OBJ_WALL = "wall"
OBJ_PLAYER = "player"
OBJ_EMPTY = "empty"

class NewScene(Exception):
    pass

class CloseProgram(Exception):
    pass

if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('-s','--server',action='store_true')
    args = p.parse_args()
    if args.server:
        server_main()
    else:
        main()
