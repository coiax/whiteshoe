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
import threading
import collections

import constants
import packet_pb2
from utility import get_id, grouper
import utility

logger = logging.getLogger(__name__)

def client_main(args=None):
    p = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)

    p.add_argument('-c','--connect',default="::1",dest='ipaddr')
    p.add_argument('-n','--name')
    p.add_argument('-t','--team',type=int)
    p.add_argument('--socket-type',default='tcp')
    p.add_argument('-o',dest='option_strings',action='append',default=())

    ns = p.parse_args(args)

    options = collections.OrderedDict()
    for option_str in ns.option_strings:
        if '=' in option:
            parts = option.split('=')
            assert len(parts) == 2
            options[part[0]] = part[1]
        else:
            options[option_str] = None

    del ns.option_strings
    ns.options = options

    #logging.basicConfig(filename='client.log',level=logging.DEBUG)
    network = ClientNetwork(ns.socket_type)
    network.connect((ns.ipaddr, None))

    #network.join_game((ns.connect, None), autojoin=True)
    #scene = GameScene(ns, network)
    scene = SetupScene(ns, network)

    while True:
        try:
            try:
                # interact enters its own loop, and then raises NewScene
                # or CloseProgram to change the program state
                scene.interact()
            except Exception:
                # scene.cleanup() is called before the exception rises
                scene.cleanup()
                raise
        except NewScene as s:
            scene = s[0]
        except (CloseProgram, KeyboardInterrupt):
            scene.shutdown()
            network.shutdown()
            break
        except Exception:
            scene.shutdown()
            # Be polite, if we're crashing, tell the server
            network.shutdown(constants.DISCONNECT_ERROR)
            raise

def curses_setup(stdscr):
    curses.curs_set(2) # block cursor
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

class SetupScene(object):
    def __init__(self, namespace, network):
        self.namespace = namespace
        self.network = network

        self.network_lock = threading.RLock()

    def interact(self):
        self._start_thread()
        # This keeps calling network.update(), although it means
        # we need to aquire the network_lock to do any network things
        # print(constants.BANNER)

        if 'name' not in self.namespace:
            self.namespace.name = raw_input('Codename> ')
        if 'team' not in self.namespace:
            team_str = raw_input('Team (0 for no team)> ')
            team = 0

            if team_str and team_str != '0':
                try:
                    team = int(team_str)
                except ValueError:
                    print("Invalid team, assuming no team.")

            self.namespace.team = team

        name = self.namespace.name
        team = self.namespace.team

        scene = GameScene(self.namespace, self.network)
        self.network.join_game(autojoin=True,player_name=name,player_team=team)
        raise NewScene(scene)

    def cleanup(self):
        self._stop_thread()

    def shutdown(self):
        pass

    def _start_thread(self):
        self.thread_running = True

        self.network_thread = threading.Thread(name='NetworkThread',
                                               target=self._thread_run)
        self.network_thread.daemon = True

        self.network_thread.start()

    def _stop_thread(self):
        with self.network_lock:
            self.thread_running = False

        self.network_thread.join(timeout=0.4)

        if self.network_thread.is_alive():
            # Give up, the thread is marked daemon, and should die with
            # the program
            pass

    def _thread_run(self):
        # I am sorry for my use of threads in Python, I am a horrible person.
        while True:
            # Allows the main thread to obtain this lock
            time.sleep(0.01)
            with self.network_lock:
                if not self.thread_running:
                    break
                self.network.update()

class ConsoleScene(object):
    def __init__(self, namespace, network):
        self.network = network
        self.namespace = namespace

    def interact(self):
        our_locals = {
            'update': self.network.update,
            'network': self.network,
            'data': self.namespace.local_data,
            'namespace': self.namespace,
        }

        code.interact("Whiteshoe Python Console",raw_input,our_locals)

        gamescene = GameScene(self.namespace, self.network)

        raise NewScene(gamescene)

    def cleanup(self):
        pass
    def shutdown(self):
        pass

class ScoreScene(object):
    def __init__(self, namespace, network):
        self.namespace = namespace
        self.network = network

    def interact(self):
        curses.wrapper(self._real_interact)

    def _real_interact(self, stdscr):
        curses_setup(stdscr)

        # FIXME since scores comes from namespace, it doesn't update if
        # network gets new information. This should be fixed, probably
        # by scores being stored in the network object?

        if 'scores' in self.namespace:
            stdscr.clear()

            scores = self.namespace.scores

            y = 0
            stdscr.addstr(y, 0, "Scores")
            y += 1

            for player_id, score in utility.grouper(2, scores):
                stdscr.addstr(y, 0, "{} : {}".format(player_id, score))
                y += 1

            while True:
                self.network.update()

                # non-blocking
                c = stdscr.getch()
                if c != curses.ERR:
                    # Any key quits ScoreScene
                    break

        scene = GameScene(self.namespace, self.network)
        raise NewScene(scene)

    def cleanup(self):
        pass

    def shutdown(self):
        pass


class GameScene(object):
    def __init__(self, namespace, network):
        self.namespace = namespace
        self.network = network

        self.data = self.namespace.local_data = {}

    def interact(self):
        curses.wrapper(self._real_interact)

    def _real_interact(self, stdscr):
        curses_setup(stdscr)

        self.first_tick(stdscr)

        while True:
            # Calling .tick will make the client network update,
            # which will use select to check the socket, meaning we sleep
            # for a small amount of time
            # So no 100% CPU usage
            self.tick(stdscr)

            # non-blocking
            c = stdscr.getch()
            if c != curses.ERR:
                # 27 is the <ESC> key
                if c == 27:
                    raise CloseProgram
                else:
                    self.input(stdscr, c)


    def cleanup(self):
        pass
    def shutdown(self):
        pass

    def first_tick(self, stdscr):
        # This is where all the initialisation stuff that can only happen
        # with the curses screen can happen.
        #self.viewport = curses.newwin()
        max_y, max_x = stdscr.getmaxyx()
        self.size = max_x, max_y

        if 'topleft' not in self.data:
            self.data['topleft'] = [0,0]

        self.infobar_type = 'bottom'
        assert self.infobar_type in ('bottom', 'rightside')

        if self.infobar_type == 'rightside':
            viewport, infobar = self._rightside_infobar(stdscr)
        elif self.infobar_type == 'bottom':
            viewport, infobar = self._bottom_infobar(stdscr)

        self.viewport = stdscr.subwin(*viewport)
        self.infobar = stdscr.subwin(*infobar)

        # Debug
        #self.viewport.bkgd("v")
        #self.sidebar.bkgd("s")
        #self.infobar.border()
        #self.viewport.border()

        #stdscr.border()

    def _rightside_infobar(self, stdscr):
        max_y, max_x = stdscr.getmaxyx()
        INFOBAR_WIDTH = 20

        viewport_topleft = (0,0) # y,x
        viewport_linescols = (max_y , max_x - INFOBAR_WIDTH)
        viewport = viewport_linescols + viewport_topleft

        infobar_topleft = (0,max_x - INFOBAR_WIDTH)
        infobar_linescols = (max_y, INFOBAR_WIDTH)
        infobar = infobar_linescols + infobar_topleft

        return viewport, infobar

    def _bottom_infobar(self, stdscr):
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
        self.network.update()

        for event in self.network.get_events():
            if event[0] in {constants.STATUS_DAMAGED, constants.STATUS_DEATH}:
                curses.flash()
            if event[0] == constants.STATUS_SCORES:
                scores = event[1]
                self.namespace.scores = scores


        self.viewport.clear()

        self.draw_infobar()

        try:
            my_coord, player = self.network.find_me()

        except PlayerNotFound:
            # Do not draw the viewport
            pass
        else:
            self.draw_viewport(self.data['topleft'])
            curses.doupdate()

    def draw_viewport(self, topleft):
        visible = self.network.get_visible()
        my_coord, player = self.network.find_me()

        max_y, max_x = self.viewport.getmaxyx()

        bottomright = [topleft[0] + max_x, topleft[1] + max_y]

        drawing = set()
        for i in range(topleft[0], bottomright[0]):
            for j in range(topleft[1], bottomright[1]):
                drawing.add((i,j))

        # drawing is the set of in-game coordinates that we are going
        # to draw
        while not all(topleft[i] <= my_coord[i] < bottomright[i]
                      for i in (0,1)):
            if bottomright[0] <= my_coord[0]:
                topleft[0] += 1
            if bottomright[1] <= my_coord[1]:
                topleft[1] += 1
            if topleft[0] > my_coord[0]:
                topleft[0] -= 1
            if topleft[1] > my_coord[1]:
                topleft[1] -= 1

            # Recalculate bottomright
            bottomright = [topleft[0] + max_x, topleft[1] + max_y]

        if my_coord in drawing:
            screen_x,screen_y = (my_coord[0] - topleft[0],
                                 my_coord[1] - topleft[1])
            curses.curs_set(2) # block cursor
        else:
            screen_x, screen_y = (0,0)
            curses.curs_set(0) # hide cursor


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
            elif self.data.get('empty-?'):
                # A bold purple ? mark indicates a coordinate that is
                # in the known_world, but has no objects, meaning it has
                # been explicitly cleared by the network.
                #
                # This is an artifact that may or may not be present
                # as stuff changes.
                purple = curses.color_pair(5) | curses.A_BOLD
                self.viewport.addstr(y,x,"?",purple)
                pass

        # Cursor on player
        self.viewport.move(screen_y,screen_x)
        self.viewport.noutrefresh()

    def draw_infobar(self):
        assert self.infobar_type in ('rightside', 'bottom')

        if self.infobar_type == 'bottom':
            self._draw_bottom_infobar()
        elif self.infobar_type == 'rightside':
            self._draw_rightside_infobar()

    def _draw_rightside_infobar(self):
        visible = self.network.get_visible()
        my_coord, player = self.network.find_me()
        self.infobox.border()

    def _draw_bottom_infobar(self):
        self.infobar.clear()
        visible = self.network.get_visible()
        try:
            my_coord, player = self.network.find_me()

            attr = player[1]
        except PlayerNotFound:
            attr = {}

        fmta = {
            'name' : attr.get('name', 'Unnamed'),
            'hp': attr.get('hp', '?'),
            'hp_max': attr.get('hp_max', '?'),
            'ammo': attr.get('ammo','?'),
            'scores': self.namespace.scores
        }

        fmt1 = "{name}"
        # TODO draw hp in green/yellow/red depending on health
        fmt2 = "HP: {hp}({hp_max}) Ammo: {ammo} Scores: {scores!r}"
        line1 = fmt1.format(**fmta)
        line2 = fmt2.format(**fmta)

        self.infobar.addstr(0,0,line1)
        self.infobar.addstr(1,0,line2)

        self.infobar.noutrefresh()

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
            constants.OBJ_EXPLOSION: '*',
            constants.OBJ_MINE: ';',
            constants.OBJ_SLIME_BULLET: '$',
            constants.OBJ_SLIME: '$',
            constants.OBJ_LAVA: '~'}.get(obj,'?')

        if obj == constants.OBJ_PLAYER:
            direction = attr['direction']
            # The colour of a player generally is either
            # enemy, ally, or self
            me = self.network.find_me()[1]

            if attr['player_id'] == self.network.player_id:
                # Colour green for self
                colour = curses.color_pair(1)# | curses.A_REVERSE
            elif attr['team'] == me[1]['team']:
                colour = curses.color_pair(4)
                # Yellow for ally
            else:
                # Red for neither ally nor self ie. enemy
                colour = curses.color_pair(2)

            if direction == constants.RIGHT:
                display_chr = '>'
            elif direction == constants.LEFT:
                display_chr = '<'
            elif direction == constants.UP:
                display_chr = '^'
            elif direction == constants.DOWN:
                display_chr = 'v'
        elif obj == constants.OBJ_BULLET or obj == constants.OBJ_SLIME_BULLET:
            owner = attr['owner']
            if owner == self.network.player_id:
                colour = curses.color_pair(1)
            else:
                colour = curses.color_pair(2)
        elif obj == constants.OBJ_EXPLOSION:
            colour = curses.color_pair(4)
        elif obj == constants.OBJ_MINE:
            colour = curses.color_pair(4) # yellow
            if attr['size'] == 1:
                display_chr = ';'
            elif attr['size'] == 2:
                display_chr = 'g'
        elif obj == constants.OBJ_SLIME:
            colour = curses.color_pair(1) | curses.A_BOLD
        elif obj == constants.OBJ_LAVA:
            colour = curses.color_pair(4) | curses.A_BOLD

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

            ord('o'): (constants.CMD_FIRE, constants.SMALL_SLIME),
            ord('O'): (constants.CMD_FIRE, constants.BIG_SLIME),
        }
        if c == ord('c'):
            consolescene = ConsoleScene(self.namespace, self.network)
            raise NewScene(consolescene)
        elif c == 9:
            # <TAB> character
            raise NewScene(ScoreScene(self.namespace, self.network))

        elif c in cmds:
            cmd = cmds[c]
            try:
                self.network.send_command(cmd[0], cmd[1])
            except NotInGame:
                # TODO Put notification on message buffer
                pass

class ClientNetwork(object):
    def __init__(self,socket_type='tcp'):
        self.handlers = {
            # c->s get games list
            constants.GAMES_RUNNING: self._games_running,
            # c->s make new game
            constants.ERROR: self._error,
            # c->s action
            constants.VISION_UPDATE: self._vision_update,
            constants.KEEP_ALIVE: self._keep_alive,
            constants.GAME_STATUS: self._game_status,
            constants.DISCONNECT: self._disconnect,
        }

        self.socket = None
        assert socket_type in ('tcp','udp')
        self.socket_type = socket_type

        self.known_world = {}

        self.game_id = None
        self.player_id = None
        self.vision = None

        self.events = []

        self.keepalive_timer = utility.Stopwatch()
        self.lastheard_timer = utility.Stopwatch()

        self._cached_player = None
        self._buffer = ''

    def connect(self, addr):
        family = socket.AF_INET6

        ip,port = addr

        if port is None:
            port = constants.DEFAULT_PORT

        if '.' in ip:
            family = socket.AF_INET
        elif ':' in ip:
            family = socket.AF_INET6

        if self.socket_type == 'tcp':
            type_ = socket.SOCK_STREAM
        elif self.socket_type == 'udp':
            type_ = socket.SOCK_DGRAM

        self.socket = socket.socket(family, type_)
        self._server_addr = (ip, port)

        if self.socket_type == 'tcp':
            self.socket.connect((ip, port))

        self._send_keepalive()
        self.lastheard_timer.start()

    def join_game(self, autojoin=False, game_id=None,
                  player_name=None, player_team=None):

        assert self.socket is not None
        assert autojoin or game_id is not None

        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(constants.JOIN_GAME)
        if autojoin:
            p.autojoin = True
        else:
            p.autojoin = False
            p.join_game_id = game_id
        if player_name is not None:
            p.player_name = player_name
        if player_team is not None:
            p.player_team = player_team

        self._send_packets([p])

    def update(self):
        # Do network things
        if self.socket is not None:
            self._cached_player = None
            self._ticklet()


            if self.keepalive_timer.elapsed_seconds > constants.KEEPALIVE_TIME:
                self._send_keepalive()

            if self.lastheard_timer.elapsed_seconds > 30:
                # Later, we'll flag the server as being disconnected,
                # but for now, raise the exception FIXME
                raise ServerDisconnect

    def shutdown(self, reason=constants.DISCONNECT_SHUTDOWN):
        if self.socket is not None:
            p = packet_pb2.Packet()
            p.packet_id = get_id('packet')
            p.payload_types.append(constants.DISCONNECT)
            p.disconnect_code = reason

            self._send_packets((p,))

    def _ticklet(self):
        rlist, wlist, xlist = select.select((self.socket,),(),(),0.1)
        for rs in rlist:
            if self.socket_type == 'udp':
                data, addr = rs.recvfrom(4096)
                chunks = (data,)
            elif self.socket_type == 'tcp':
                data = rs.recv(4096)
                if not data:
                    raise ServerDisconnect

                self._buffer += data
                chunks, buffer = utility.stream_unwrap(self._buffer)
                self._buffer = buffer

            self.lastheard_timer.restart()
            try:
                for chunk in chunks:
                    packet = packet_pb2.Packet.FromString(chunk)

                    for payload_type in packet.payload_types:
                        self.handlers[payload_type](packet)

            except Exception as e:
                #traceback.print_exc()
                # Can't print exceptions when the tty is up
                raise

    def _send_keepalive(self):
        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(constants.KEEP_ALIVE)
        p.timestamp = int(time.time())

        self._send_packets([p])

    def _send_packets(self, packets):
        if self.socket is not None:
            for packet in packets:
                data = packet.SerializeToString()
                if self.socket_type == 'tcp':
                    self.socket.sendall(utility.stream_wrap(data))
                elif self.socket_type == 'udp':
                    self.socket.sendto(data, self._server_addr)

            self.keepalive_timer.restart()

    def send_command(self,cmd,arg):
        assert self.socket is not None

        if self.game_id is None:
            raise NotInGame
        cmd_num = constants.to_numerical_constant(cmd)
        arg_num = constants.to_numerical_constant(arg)

        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(constants.GAME_ACTION)
        # Ah, we need to note the game_id that we're participating in
        p.action_game_id = self.game_id
        p.action = cmd_num
        p.argument = arg_num

        self._send_packets([p])

    def find_me(self):
        assert self.socket is not None

        if self.player_id is not None:
            if self._cached_player is not None:
                return self._cached_player
            else:
                for coord, objects in self.known_world.items():
                    for object in objects:
                        if (object[0] == constants.OBJ_PLAYER and
                            object[1]['player_id'] == self.player_id):

                            self._cached_player = (coord, object)

                            return coord, object

        # If no player is found
        raise PlayerNotFound

    def get_visible(self):
        return self.known_world

    def get_events(self):
        e = self.events
        self.events = []
        return e

    # packet handlers
    def _games_running(self, packet):
        pass
    def _error(self, packet):
        pass
    def _vision_update(self, packet):
        """
        repeated sint32 objects = 600 [packed=true];
        // objects consists of 4-tuples: x,y,obj_type,attr_id
        // attr_id is either -1 for no attributes, or an index of an attribute
        message Attribute {
            optional int32 player_id = 1;
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

        if packet.clear_all:
            self.known_world.clear()


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

    def _keep_alive(self, packet):
        pass

    def _game_status(self, packet):
        status = packet.status

        event = None

        if status == constants.STATUS_GAMEINFO:
            self.game_id = packet.status_game_id
            self.player_id = packet.your_player_id
            self.vision = packet.game_vision
            event = (status, self.game_id, self.player_id, self.vision)

        elif status == constants.STATUS_JOINED:
            event = (status, self.player_id, packet.joined_player_name)

        elif status == constants.STATUS_LEFT:
            if packet.player_id == self.player_id:
                # You just left the game
                self.game_id = None

        elif status == constants.STATUS_SPAWN:
            pass

        elif status in {constants.STATUS_DEATH, constants.STATUS_DAMAGED}:
            responsible = packet.responsible_id
            if responsible == -1:
                responsible = None

            damage_type = packet.damage_type

            event = (status, responsible, damage_type)
            if status == constants.STATUS_DEATH:
                self.known_world.clear()

        elif status == constants.STATUS_KILL:
            event = (status, packet.victim_id)
        elif status == constants.STATUS_GAMEPAUSE:
            unpause_time = packet.unpause_time
            if unpause_time:
                unpause_time = datetime.datetime(*unpause_time)

            else:
                unpause_time = None


            event = (status, unpause_time, packet.countdown)
        elif status == constants.STATUS_GAMERESUME:
            pass

        elif status == constants.STATUS_SCORES:
            event = (status, packet.scores)
        elif status == constants.STATUS_GLOBALMESSAGE:
            event = (status, packet.message_from, packet.message_body)

        if event is None:
            event = (status,)

        self.events.append(event)

    def _disconnect(self, packet):
        # Like STATUS_LEFT, but we remove all information about the server

        self.known_world = {}

        self.game_id = None
        self.player_id = None
        self.vision = None

        # FIXME later we won't raise this, maybe possibly change scenes?
        raise ServerDisconnect


class ClientException(Exception):
    pass

class NewScene(ClientException):
    pass

class CloseProgram(ClientException):
    pass

class PlayerNotFound(ClientException):
    pass

class ServerDisconnect(ClientException):
    pass

class NotInGame(ClientException):
    pass

if __name__=='__main__':
    client_main()
