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
import operator
import json

try:
    import cPickle as pickle
except ImportError:
    import pickle

import constants
import packet_pb2
from utility import get_id, grouper
import utility

logger = logging.getLogger(__name__)

def client_main(args=None):
    curses.setupterm("xterm-color256")
    curses.tigetnum("colors")

    p = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)

    p.add_argument('-c','--connect',default="::1",dest='ipaddr')
    p.add_argument('-n','--name')
    p.add_argument('-t','--team',type=int)
    p.add_argument('--socket-type',default='tcp')
    p.add_argument('-o',dest='option_strings',action='append',default=[])

    ns = p.parse_args(args)

    options = collections.OrderedDict()
    for option_str in ns.option_strings:
        if '=' in option_str:
            parts = option_str.split('=')
            assert len(parts) == 2
            options[part[0]] = part[1]
        else:
            options[option_str] = None

    del ns.option_strings
    ns.options = options

    #logging.basicConfig(filename='client.log',level=logging.DEBUG)
    network = MultiHackClientNetwork()
    network.connect((ns.ipaddr, None), socket_type=ns.socket_type)

    #network.join_game((ns.connect, None), autojoin=True)
    #scene = GameScene(ns, network)
    scene = SetupScene(ns, network)

    while True:
        try:
            with scene:
                # interact enters its own loop, and then raises NewScene
                # or CloseProgram to change the program state
                scene.interact()
        except NewScene as s:
            scene = s[0]
        except (CloseProgram, KeyboardInterrupt):
            break
        except Exception:
            # Be polite, if we're crashing, tell the server
            network.shutdown(constants.DISCONNECT_ERROR)
            raise

class Scene(object):
    def __init__(self, namespace, network):
        self.namespace = namespace
        self.network = network

    def interact(self):
        pass

    def cleanup(self):
        pass

    def __enter__(self):
        pass
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False # always propogate exceptions.


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

class SetupScene(Scene):
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

class ConsoleScene(Scene):
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

            grouped_scores = list(utility.grouper(2, scores))
            grouped_scores.sort(key=operator.itemgetter(1),reversed=True)

            for player_id, score in grouped_scores:
                name = self.network.players[player_id]

                stdscr.addstr(y, 0, "{} : {}".format(name, score))
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


class GameScene(Scene):
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

    def draw_viewport(self):
        location = self.network.store['player_location']
        world, x, y, z = location

        map_data = self.network.store['universe'][world]

        for coord, entities in map_data.items():
            x, y = coord

            # There might be multiple entities in a single coordinate
            # So we need to determine which has the highest priority.
            # Generally, a monster/player is more important than an item
            # An item is more important than a dungeon feature (fountain etc.)
            # A dungeon feature is more important than empty floor, etc.

            # If there's only one entity though, then we don't have to worry
            # about precedence.

            # First, the debug case, a coordinate with empty entities.
            # This is clearly a bug, a coordinate has to contain something.
            if not entities:


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

        # Attempt to determine our score.
        scores_json = self.network.keyvalues.get(constants.KEYVALUE_SCORES)
        scores = json.loads(scores_json) if scores_json else {}

        # Cooerce string json keys to numeric keys
        for key in list(scores):
            scores[int(key)] = scores[key]

        fmta = {
            'name' : attr.get('name', 'Unnamed'),
            'hp': attr.get('hp', '?'),
            'hp_max': attr.get('hp_max', '?'),
            'ammo': attr.get('ammo','?'),
            'player_id': attr.get('player_id','?'),
            'topname': '?',
            'topscore': '?',
            'yourscore': scores.get(attr.get('player_id','?'), '?'),
            'rank': 'Ordinary',
        }

        fmt1 = "{name} the {rank}"
        if 'ShowPlayerID' in self.namespace.options: #TODO add to flags docs
            fmt1 += '  player_id:{player_id}'


        # TODO draw hp in green/yellow/red depending on health
        fmt2 = "HP:{hp}({hp_max})  Ammo:{ammo}  Score:{yourscore}"

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

class _MultiHackClientNetworkPacketHandler(object):
    def _store_packet(self, packet):
        key = pickle.loads(packet.key)
        value = pickle.loads(packet.value)

        with self._lock:
            self._store[key] = value

    def _event_packet(self, packet):
        event = pickle.loads(packet.event)
        with self._lock:
            self._events.append(event)

    def _keep_alive_packet(self, packet):
        pass
    def _disconnect_packet(self, packet):
        raise ServerDisconnect

class MultiHackClientNetwork(_MultiHackClientNetworkPacketHandler):
    def __init__(self):
        self.handlers = {
            constants.Payload.store: self._store_packet,
            constants.Payload.event: self._event_packet,
            constants.KEEP_ALIVE: self._keep_alive_packet,
            constants.DISCONNECT: self._disconnect_packet,
        }

        self.socket = None
        self.socket_type = None

        self._events = []
        self._store = {}
        self._remote_store = {}

        self.keepalive_timer = utility.Stopwatch()
        self.lastheard_timer = utility.Stopwatch()

        self._buffer = ''

        self._lock = threading.RLock()
        self._thread = None
        self._thread_running = False

    def remote_set(self, key, value):
        if key in self._remote_store and self._remote_store[key] == value:
            # Do nothing.
            return
        else:
            p = packet_pb2.Packet()
            p.payload_type = constants.Payload.store
            p.key = pickle.dumps(key, -1)
            p.value = pickle.dumps(value, -1)
            self._send_packets(p)

    def remote_event(self, event):
        p = packet_pb2.Packet()
        p.payload_type = constants.Payload.event
        p.event = pickle.dumps(event, -1)
        self._send_packets(p)

    def start_thread(self):
        assert self._thread is None
        assert not self._thread_running
        assert self.socket is not None

        self._thread = threading.Thread(target=self._thread_loop)
        self._thread_running = True
        self._thread.start()

    def stop_thread(self):
        assert self._thread is not None
        assert self._thread_running

        with self._lock:
            self._thread_running = False

        self._thread.join()
        self._thread = None

    def _thread_loop(self):
        while True:
            with self._lock:
                if not self._thread_running:
                    break
                self.update()

    def connect(self, addr, socket_type='tcp'):
        assert socket_type in ('tcp','udp')
        self.socket_type = socket_type

        ip,port = addr

        if port is None:
            port = constants.DEFAULT_PORT

        if '.' in ip:
            family = socket.AF_INET
        elif ':' in ip:
            family = socket.AF_INET6
        else:
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

    def _send_keepalive(self):
        p = packet_pb2.Packet()
        p.payload_type = constants.KEEP_ALIVE
        p.timestamp = int(time.time())

        self._send_packets(p)

    def join_game(self, autojoin=False, game_id=None,
                  player_name=None, player_team=None):

        assert self.socket is not None
        assert autojoin or game_id is not None

        p = packet_pb2.Packet()
        p.payload_type = constants.JOIN_GAME
        if autojoin:
            p.autojoin = True
        else:
            p.autojoin = False
            p.join_game_id = game_id
        if player_name is not None:
            p.player_name = player_name
        if player_team is not None:
            p.player_team = player_team

        self._send_packets(p)

    def update(self):
        with self._lock:
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
            p.payload_type = constants.DISCONNECT
            p.disconnect_code = reason

            self._send_packets(p)

    @property
    def events(self):
        with self._lock:
            out = self._events
            self._events = []
        return out

    def get_events(self):
        return self.events

    @property
    def store(self):
        with self._lock:
            return self._store.copy()

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

                    self.handlers[packet.payload_type](packet)

            except Exception as e:
                #traceback.print_exc()
                # Can't print exceptions when the tty is up
                raise

    def _send_packets(self, *args):
        assert self.socket is not None
        packets = []
        for arg in args:
            try:
                iter(arg)
            except TypeError:
                packets.append(arg)
            else:
                packets.extend(arg)

        for packet in packets:
            if self.socket_type == 'udp':
                p.packet_id = get_id('packet')

            data = packet.SerializeToString()
            if self.socket_type == 'tcp':
                self.socket.sendall(utility.stream_wrap(data))
            elif self.socket_type == 'udp':
                self.socket.sendto(data, self._server_addr)

        self.keepalive_timer.restart()


class ClientNetwork(object):
    def __init__(self,socket_type='tcp'):
        self.handlers = {
            # c->s get games list
            constants.GAMES_LIST: self._games_running,
            # c->s make new game
            constants.ERROR: self._error,
            # c->s action
            constants.VISION_UPDATE: self._vision_update,
            constants.KEEP_ALIVE: self._keep_alive,
            constants.GAME_STATUS: self._game_status,
            constants.DISCONNECT: self._disconnect,
            constants.KEYVALUE: self._keyvalue,
        }

        self.socket = None
        assert socket_type in ('tcp','udp')
        self.socket_type = socket_type

        self.known_world = {}

        self.game_id = None
        self.player_id = None
        self.vision = None

        self.players = {}

        self.events = []

        self.keepalive_timer = utility.Stopwatch()
        self.lastheard_timer = utility.Stopwatch()

        self._cached_player = None
        self._buffer = ''

        self.keyvalues = {}

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
        p.payload_type = constants.JOIN_GAME
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
            p.payload_type = constants.DISCONNECT
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

                    self.handlers[packet.payload_type](packet)

            except Exception as e:
                #traceback.print_exc()
                # Can't print exceptions when the tty is up
                raise

    def _send_keepalive(self):
        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_type = constants.KEEP_ALIVE
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
        p.payload_type = constants.GAME_ACTION
        # Ah, we need to note the game_id that we're participating in
        p.game_id = self.game_id
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
            self.game_id = packet.game_id
            self.player_id = packet.your_player_id
            self.vision = packet.game_vision
            event = (status, self.game_id, self.player_id, self.vision)

        elif status == constants.STATUS_JOINED:
            event = (status, packet.game_id, packet.player_id,
                     packet.joined_player_name)
            self.players[packet.player_id] = packet.joined_player_name

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

    def _keyvalue(self, packet):
        for key, value in utility.grouper(2, packet.keyvalues):
            self.keyvalues[key] = value

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
