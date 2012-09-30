from __future__ import print_function

import curses
import argparse
import socket
import select
import datetime
import time
import readline
import code
import random
import logging

import constants
import packet_pb2
from utility import get_id, grouper

logger = logging.getLogger(__name__)

def client_main(args=None):
    p = argparse.ArgumentParser()
    p.add_argument('-c','--connect',default="::1")
    ns = p.parse_args(args)

    #logging.basicConfig(filename='client.log',level=logging.DEBUG)

    curses.wrapper(main2, ns)

def main2(stdscr, ns):
    curses.curs_set(0) # not visible
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_BLACK, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)
    curses.init_pair(6, curses.COLOR_CYAN, -1)
    curses.init_pair(7, curses.COLOR_WHITE, -1)
    curses.init_pair(8, curses.COLOR_BLUE, -1)
    stdscr.nodelay(1)

    def print(x):
        stdscr.addstr("{0}".format(x), curses.color_pair(0))
        stdscr.refresh()

    autojoin_addr = (ns.connect,None)

    data = {}
    data['network'] = ClientNetwork(autojoin=autojoin_addr,automake=True)
    data['hallu'] = False

    scene = GameScene(data)
    while True:
        try:
            # Calling .tick will make the client network update,
            # which will use select to check the socket, meaning we sleep
            # for a small amount of time
            # So no 100% CPU usage
            scene.tick(stdscr)

            # non-blocking
            c = stdscr.getch()
            if c != curses.ERR:
                # 27 is the <ESC> key
                if c == 27:
                    raise CloseProgram
                else:
                    scene.input(stdscr, c)

        except NewScene as ns:
            scene = ns[0]

        except CloseProgram:
            break
        except KeyboardInterrupt:
            break

class ConsoleScene(object):
    def __init__(self, data):
        self.data = data
        self.network = self.data['network']

    def tick(self, stdscr):
        # We're not actually going to use the main framework at all
        curses.nocbreak()
        stdscr.keypad(0)
        curses.echo()
        curses.endwin()

        our_locals = {
            'update': self.network.update,
            'network': self.network,
            'data': self.data
        }

        code.interact("Whiteshoe Python Console",raw_input,our_locals)

        curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(1)

        raise NewScene(GameScene(self.data))

    def input(self, stdscr, c):
        pass


class GameScene(object):
    def __init__(self, data):
        self.data = data
        self.network = data['network']

        self.had_first_tick = False
    def first_tick(self, stdscr):
        # This is where all the initialisation stuff that can only happen
        # with the curses screen can happen.
        #self.viewport = curses.newwin()
        max_y, max_x = stdscr.getmaxyx()
        self.size = max_x, max_y

        self.infobar_type = 'horizontal'
        assert self.infobar_type in ('vertical', 'horizontal')

        if self.infobar_type == 'vertical':
            viewport, infobar = self._vertical_infobar(stdscr)
        elif self.infobar_type == 'horizontal':
            viewport, infobar = self._horizontal_infobar(stdscr)

        self.viewport = stdscr.subwin(*viewport)
        self.infobar = stdscr.subwin(*infobar)

        # Debug
        #self.viewport.bkgd("v")
        #self.sidebar.bkgd("s")
        self.infobar.border()
        self.viewport.border()

        #stdscr.border()

    def _vertical_infobar(self, stdscr):
        max_y, max_x = stdscr.getmaxyx()
        INFOBAR_WIDTH = 20

        viewport_topleft = (0,0) # y,x
        viewport_linescols = (max_y , max_x - INFOBAR_WIDTH)
        viewport = viewport_linescols + viewport_topleft

        infobar_topleft = (0,max_x - INFOBAR_WIDTH)
        infobar_linescols = (max_y, INFOBAR_WIDTH)
        infobar = infobar_linescols + infobar_topleft

        return viewport, infobar

    def _horizontal_infobar(self, stdscr):
        max_y, max_x = stdscr.getmaxyx()
        INFOBAR_HEIGHT = 2

        viewport_topleft = (0,0)
        viewport_linescols = (max_y - INFOBAR_HEIGHT, max_x)

        viewport = viewport_linescols + viewport_topleft

        infobar_topleft = (max_y - INFOBAR_HEIGHT, 0)
        infobar_linescols = (INFOBAR_HEIGHT, max_x)

        infobar = infobar_linescols + infobar_topleft

        return viewport, infobar

    def tick(self, stdscr):
        if not self.had_first_tick:
            self.first_tick(stdscr)
            self.had_first_tick = True

        self.network.update()

        # TODO currently window viewport is based on the 0,0 topleft
        # corner, and we want to be able to move around
        self.viewport.clear()

        try:
            my_coord, player = self.network.find_me()
        except PlayerNotFound:
            # Do not draw the viewport
            pass
        else:
            self.draw_viewport(self.data.get('topleft',(0,0)))
            pass

    def draw_viewport(self, topleft):
        visible = self.network.get_visible()
        my_coord, player = self.network.find_me()

        max_y, max_x = self.viewport.getmaxyx()

        drawing = set()
        for i in range(topleft[0], topleft[0] + max_x):
            for j in range(topleft[1], topleft[1] + max_y):
                drawing.add((i,j))

        for coord, objects in visible.items():
            if coord not in drawing:
                continue

            x,y = (coord[0] - topleft[0], coord[1] - topleft[1])
            if objects:
                for o in objects:
                    display_chr, colour = self.display_character(o)
                try:
                    self.viewport.addstr(y,x,display_chr, colour)
                except curses.error:
                    pass
            else:
                # A bold purple ? mark indicates a coordinate that is
                # in the known_world, but has no objects, meaning it has
                # been explicitly cleared by the network.
                #
                # This is an artifact that may or may not be present
                # as stuff changes.
                purple = curses.color_pair(5) | curses.A_BOLD
                self.viewport.addstr(y,x,"?",purple)
                pass

        if my_coord in drawing:
            x,y = (my_coord[0] - topleft[0], my_coord[1] - topleft[1])
            self.viewport.move(y,x)
        else:
            self.viewport.move(0,0)
        self.viewport.refresh()

    def display_character(self, object, history=False):
        display_chr = None
        # Default colour
        colour = curses.color_pair(0)

        obj, attr = object

        display_chr = {
            constants.OBJ_WALL: '#',
            constants.OBJ_PLAYER: '@',
            constants.OBJ_EMPTY: '.',
            constants.OBJ_HORIZONTAL_WALL: '-',
            constants.OBJ_VERTICAL_WALL: '|',
            constants.OBJ_CORNER_WALL: '+',
            constants.OBJ_BULLET: ':',
            constants.OBJ_EXPLOSION: '*'}.get(obj,'?')

        if obj == constants.OBJ_PLAYER:
            direction = attr['direction']
            if attr['number'] == self.network.player_id:
                colour = curses.color_pair(1) | curses.A_REVERSE
            else:
                colour = curses.color_pair(2)

            if direction == constants.RIGHT:
                display_chr = '>'
            elif direction == constants.LEFT:
                display_chr = '<'
            elif direction == constants.UP:
                display_chr = '^'
            elif direction == constants.DOWN:
                display_chr = 'v'
        elif obj == constants.OBJ_BULLET:
            owner = attr['owner']
            if owner == self.network.player_id:
                colour = curses.color_pair(1)
            else:
                colour = curses.color_pair(2)
        elif obj == constants.OBJ_EXPLOSION:
            colour = curses.color_pair(4)

        assert display_chr is not None

        if attr.get('historical', False):
            # Grey
            colour = curses.color_pair(3)

        if self.data.get('hallu', False):
            colour = curses.color_pair(random.randint(1,8))

        return display_chr, colour

    def input(self, stdscr, c):
        #print(c)
        cmds = {
            curses.KEY_DOWN: (constants.CMD_MOVE, constants.DOWN),
            curses.KEY_UP: (constants.CMD_MOVE, constants.UP),
            curses.KEY_LEFT: (constants.CMD_MOVE, constants.LEFT),
            curses.KEY_RIGHT: (constants.CMD_MOVE, constants.RIGHT),
            # vim keys
            ord('j'): (constants.CMD_MOVE, constants.DOWN),
            ord('h'): (constants.CMD_MOVE, constants.LEFT),
            ord('k'): (constants.CMD_MOVE, constants.UP),
            ord('l'): (constants.CMD_MOVE, constants.RIGHT),

            # wasd
            ord('w'): (constants.CMD_MOVE, constants.UP),
            ord('a'): (constants.CMD_MOVE, constants.LEFT),
            ord('s'): (constants.CMD_MOVE, constants.DOWN),
            ord('d'): (constants.CMD_MOVE, constants.RIGHT),

            # Looking directions, arrow keys with SHIFT held down
            curses.KEY_SF: (constants.CMD_LOOK, constants.DOWN),
            curses.KEY_SR: (constants.CMD_LOOK, constants.UP),
            curses.KEY_SRIGHT: (constants.CMD_LOOK, constants.RIGHT),
            curses.KEY_SLEFT: (constants.CMD_LOOK, constants.LEFT),

            #
            ord('J'): (constants.CMD_LOOK, constants.DOWN),
            ord('H'): (constants.CMD_LOOK, constants.LEFT),
            ord('K'): (constants.CMD_LOOK, constants.UP),
            ord('L'): (constants.CMD_LOOK, constants.RIGHT),

            # wasd looking
            ord('W'): (constants.CMD_LOOK, constants.UP),
            ord('A'): (constants.CMD_LOOK, constants.LEFT),
            ord('S'): (constants.CMD_LOOK, constants.DOWN),
            ord('D'): (constants.CMD_LOOK, constants.RIGHT),

            ord('f'): (constants.CMD_FIRE, constants.N1),
            ord('F'): (constants.CMD_FIRE, constants.N2),

            ord('1'): (constants.CMD_FIRE, constants.N1),
            ord('2'): (constants.CMD_FIRE, constants.N2),
            ord('3'): (constants.CMD_FIRE, constants.N3),
            ord('4'): (constants.CMD_FIRE, constants.N4),
            ord('5'): (constants.CMD_FIRE, constants.N5),
            ord('6'): (constants.CMD_FIRE, constants.N6),
            ord('7'): (constants.CMD_FIRE, constants.N7),
            ord('8'): (constants.CMD_FIRE, constants.N8),
            ord('9'): (constants.CMD_FIRE, constants.N9),
        }
        if c == ord('c'):
            raise NewScene(ConsoleScene(self.data))
        elif c in cmds:
            cmd = cmds[c]
            self.network.send_command(cmd[0], cmd[1])

class ClientNetwork(object):
    def __init__(self,autojoin=None,automake=False):
        self.handlers = {
            # c->s get games list
            constants.GAMES_RUNNING: self._games_running,
            constants.ERROR: self._error,
            constants.VISION_UPDATE: self._vision_update,
            constants.KEEP_ALIVE: self._keep_alive,
            constants.GAME_STATUS: self._game_status,
        }

        address_type = socket.AF_INET6

        if autojoin is not None:
            ip,port = autojoin
            if '.' in ip:
                address_type = socket.AF_INET
            elif ':' in ip:
                address_type = socket.AF_INET6

        self.socket = socket.socket(address_type,socket.SOCK_DGRAM)
        self.known_world = {}

        if autojoin is not None:
            if port is None:
                port = constants.DEFAULT_PORT
            self._server_addr = (ip, port)

            p = packet_pb2.Packet()
            p.packet_id = get_id('packet')
            p.payload_types.append(constants.JOIN_GAME)
            p.autojoin = True

            self._send_packets([p], self._server_addr)

        self.game_id = None
        self.player_id = None
        self.vision = None

        self.keepalive_timer = 0
        self.last_tick = None


    def update(self):
        # Do network things
        self._ticklet()
        if self.last_tick is None:
            self.last_tick = datetime.datetime.now()
        else:
            diff = (datetime.datetime.now() - self.last_tick)
            self.last_tick = datetime.datetime.now()

            self.keepalive_timer += diff.seconds + (diff.microseconds*10.0**-6)

            if self.keepalive_timer > constants.KEEPALIVE_TIME:
                self.keepalive_timer -= constants.KEEPALIVE_TIME

                self._send_keepalive()
    def _ticklet(self):
        rlist, wlist, xlist = select.select([self.socket],[],[],0.1)
        for rs in rlist:
            data, addr = rs.recvfrom(4096)
            try:
                packet = packet_pb2.Packet.FromString(data)

                for payload_type in packet.payload_types:
                    self.handlers[payload_type](packet, addr)

                self.last_heard = datetime.datetime.now()

            except Exception as e:
                #traceback.print_exc()
                # Can't print exceptions when the tty is up
                raise

    def _send_keepalive(self):
        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(constants.KEEP_ALIVE)
        p.timestamp = int(time.time())

        self._send_packets([p], self._server_addr)

    def _send_packets(self, packets, addr):
        for packet in packets:
            self.socket.sendto(packet.SerializeToString(), addr)
        # Don't need to keepalive if we're sending other packets
        self.keepalive_timer = 0

    def send_command(self,cmd,arg):
        cmd_num = constants.to_numerical_constant(cmd)
        arg_num = constants.to_numerical_constant(arg)

        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(constants.GAME_ACTION)
        # Ah, we need to note the game_id that we're participating in
        p.action_game_id = self.game_id
        p.action = cmd_num
        p.argument = arg_num

        self._send_packets([p],self._server_addr)

    def find_me(self):
        if self.player_id is not None:
            for coord, objects in self.known_world.items():
                for object in objects:
                    if (object[0] == constants.OBJ_PLAYER and
                        object[1]['number'] == self.player_id):

                        return coord, object

        # If no player is found
        raise PlayerNotFound

    def get_visible(self):
        return self.known_world

    # packet handlers
    def _games_running(self, packet, addr):
        pass
    def _error(self, packet, addr):
        pass
    def _vision_update(self, packet, addr):
        """
        repeated sint32 objects = 600 [packed=true];
        // objects consists of 4-tuples: x,y,obj_type,attr_id
        // attr_id is either -1 for no attributes, or an index of an attribute
        message Attribute {
            optional int32 number = 1;
            optional int32 direction = 2;
            optional int32 team = 3;
            optional int32 hp_max = 4;
            optional int32 hp = 5;
            optional int32 max_ammo = 6;
            optional int32 ammo = 7;
        }
        repeated Attribute attributes = 601;
        """
        # unpack attributes first
        unpacked_attributes = []

        for attribute in packet.attributes:
            unpacked = {}
            for key in constants.ATTRIBUTE_KEYS:
                if attribute.HasField(key):
                    value = getattr(attribute, key)
                    if key in constants.ATTRIBUTE_CONSTANT_KEYS:
                        value = constants.from_numerical_constant(value)
                    unpacked[key] = value

            unpacked_attributes.append(unpacked)

        cleared = set()

        for x,y,obj_type,attr_id in grouper(4, packet.objects):
            assert None not in (x,y,obj_type,attr_id)

            if attr_id == -1:
                attr = {}
            else:
                attr = unpacked_attributes[attr_id].copy()

            if (x,y) not in cleared:
                self.known_world[x,y] = []
                cleared.add((x,y))

            if obj_type == -1:
                # An obj_type of -1 merely clears the (x,y) cell
                continue

            obj_type = constants.from_numerical_constant(obj_type)

            self.known_world[x,y].append((obj_type, attr))

    def _keep_alive(self, packet, addr):
        pass

    def _game_status(self, packet, addr):
        if packet.status == constants.STATUS_JOINED:
            self.game_id = packet.status_game_id
            self.player_id = packet.your_player_id
            self.vision = packet.game_vision
        elif packet.status == constants.STATUS_LEFT:
            self.game_id = None

class ClientException(Exception):
    pass

class NewScene(ClientException):
    pass

class CloseProgram(ClientException):
    pass

class PlayerNotFound(ClientException):
    pass

if __name__=='__main__':
    client_main()
