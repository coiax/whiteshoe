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
import multiprocessing
import os

import constants
import packet_pb2
from utility import get_id, grouper
import utility

logger = logging.getLogger(__name__)

def client_main(args=None):
    # TODO Check whether the terminal supports majorcolour, minorcolour
    # or nocolour.
    # Also ask at some point about colourblindness
    # Probably also need a unicode check as well.
    os.environ["TERM"] = "xterm-256color"

    p = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)

    p.add_argument('-c','--connect',default="::1",dest='ipaddr')
    p.add_argument('--socket-type',default='tcp')
    p.add_argument('--log-file',default='client.log')
    p.add_argument('-o',dest='option_strings',action='append',default=[])

    ns = p.parse_args(args)

    logging.basicConfig(filename=ns.log_file, level=logging.DEBUG)
    logger.info("Whiteshoe Client v0.0.1")

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

    scene = GameScene(ns, network)

    network.join_game(autojoin=True)

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

COLOUR_PAIRS = {}

def curses_setup(stdscr):
    curses.curs_set(2) # block cursor
    curses.use_default_colors()
    for i in range(1,257):
        curses.init_pair(i, i - 1, -1)
        COLOUR_PAIRS[i] = curses.color_pair(i)
    stdscr.nodelay(1)

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

def cursify(character, colour, flags=()):
    # Then turn that collection of settings into actual curses stuff.
    # TODO this probably needs processing and rounding to transform
    # into majorcolor, minorcolor, colorblind, and monocolor mode.
    if colour == "purple":
        colour_pair = 6
    elif colour == "green":
        colour_pair = 3
    elif colour == "white":
        colour_pair = 16
    elif colour == "blue":
        colour_pair = 5
    elif colour == "red":
        colour_pair = 2
    elif colour == "yellow":
        colour_pair = 12
    else:
        colour_pair = 1 # TODO complain

    attr = COLOUR_PAIRS[colour_pair]
    if "bold" in flags:
        attr |= curses.A_BOLD
    if "underline" in flags:
        attr |= curses.A_UNDERLINE
    if "standout" in flags:
        attr |= curses.A_STANDOUT

    return character, attr


class GameScene(Scene):
    def __init__(self, namespace, network):
        self.namespace = namespace
        self.network = network

        self._command_mode = False
        self._curses_feedback_mode = False

        self._messages = []
        self._message_timer = utility.Stopwatch()

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

        # or, put more simply, this is where a significant amount of code
        # is spent saving two lower rows to put information.

        max_y, max_x = stdscr.getmaxyx()
        self.size = max_x, max_y

        INFOBAR_HEIGHT = 2

        viewport_topleft = (0,0)
        viewport_linescols = (max_y - INFOBAR_HEIGHT, max_x)

        viewport = viewport_linescols + viewport_topleft

        infobar_topleft = (max_y - INFOBAR_HEIGHT, 0)
        infobar_linescols = (INFOBAR_HEIGHT, max_x)

        infobar = infobar_linescols + infobar_topleft

        self.viewport = stdscr.subwin(*viewport)
        self.infobar = stdscr.subwin(*infobar)

    def tick(self, stdscr):
        self.network.update()

        if 'player_location' in self.network.store:
            self.draw_viewport()

        self._draw_bottom_infobar()

        if self._command_mode:
            typed = self._command
            chr, attr = cursify("# " + typed, "green")
            self.viewport.addstr(0,0, chr, attr)

        for event in self.network.events:
            if event[0] == "message":
                self._messages.append(event[1])

        if self._messages and not self._message_timer.running:
            self._message_timer.start()

        if self._messages and self._message_timer.elapsed_seconds > 10:
            self._messages.pop(0)
            if self._messages:
                self._message_timer.restart()
            else:
                self._message_timer.stop()


        for i, message in enumerate(self._messages):
            chr, attr = cursify(message, "purple", ('bold','standout'))
            try:
                self.viewport.addstr(i + 1,0, chr, attr)
            except curses.error:
                # probably too many messages, that's probably why
                # TODO get a better message system than this simple one.
                pass


        # Move cursor to your player.
        if 'player_location' in self.network.store:
            world, x, y, z = self.network.store['player_location']
            # TODO obviously with a level bigger than window size
            # we can't just rip the coordinates straight out.
            curses.curs_set(2)
            try:
                self.viewport.move(y,x)
            except curses.error:
                # TODO out of bounds cursor location probably. Need to
                # notice and catch and stuff.
                # Hide cursor.
                curses.curs_set(0)

        # Call nout refresh on all windows.
        self.infobar.noutrefresh()
        self.viewport.noutrefresh()


        # Then draw all changes.
        curses.doupdate()

    def draw_viewport(self):
        location = self.network.store['player_location']
        player_world, player_x, player_y, player_z = location

        known_universe = self.network.store.get('known_universe')

        if known_universe is None:
            # TODO Might complain about this later. Right now, wait a bit.
            return

        map_data = self.network.store['known_universe'][player_world]

        # This may increase CPU usage, but ensures that the display is
        # correct.
        self.viewport.erase()

        for coord, entities in map_data.items():
            x, y, z = coord
            # Only draw coordinates that the player shares a z coord with.
            if z != player_z:
                continue
            self.draw_tile(coord, entities)

    def draw_tile(self, coord, entities):
        x, y, z = coord

        # There might be multiple entities in a single coordinate
        # So we need to determine which has the highest priority.
        # Generally, a monster/player is more important than an item
        # An item is more important than a dungeon feature (fountain etc.)
        # A dungeon feature is more important than empty floor, etc.

        # If there's only one entity though, then we don't have to worry
        # about precedence.

        # First, the debug case, a coordinate with empty entities.
        # This is clearly a bug, a coordinate has to contain something.

        # Default settings. A green '?' indicates an unknown character.

        character = '?'
        colour = "green"
        flags = ()

        if not entities:
            character = '?'
            flags = ('bold',)
            colour = "purple"
        elif entities:
            # TODO for now, we'll draw the last one in the list.
            entity = entities[-1]

            entity_data = self.network.store['entity_data']
            entity_states = self.network.store['entity_state']

            entity_state = utility.get_entity_state(entity_data, entity_states,
                                                    entity)


            if 'symbol' in entity_state:
                character = entity_state['symbol']
            if 'colour' in entity_state:
                colour = entity_state['colour']
            if 'flags' in entity_state:
                flags = entity_state['flags']

        character, attr = cursify(character, colour, flags)
        try:
            self.viewport.addstr(y,x, character, attr)
        except curses.error:
            # Ignore all the errors.
            pass

    def _draw_bottom_infobar(self):
        self.infobar.erase()

        remote_store = self.network.store

        player_name = remote_store.get('player_name','<NAME?>')
        player_location = remote_store.get('player_location','????')

        world, x, y, z = player_location

        fmt1 = '{player_name} the Debugling'
        fmt2 = 'DL:{world}  Loc:{x},{y},{z}'

        line1 = fmt1.format(player_name=player_name)
        line2 = fmt2.format(world=world,x=x,y=y,z=z)

        self.infobar.addstr(0,0,line1)
        self.infobar.addstr(1,0,line2)

    def input(self, stdscr, c):
        cmds = {
            #curses.KEY_DOWN: ("move", [constants.Direction.down]),
            #curses.KEY_UP: ("move", [constants.Direction.up]),
            #curses.KEY_LEFT: ("move", [constants.Direction.left]),
            #curses.KEY_RIGHT: ("move", [constants.Direction.right]),
            # vim keys
            ord('j'): ("move", (constants.Direction.south,)),
            ord('h'): ("move", (constants.Direction.west,)),
            ord('k'): ("move", (constants.Direction.north,)),
            ord('l'): ("move", (constants.Direction.east,)),

            ord('y'): ("move", (constants.Direction.northwest,)),
            ord('u'): ("move", (constants.Direction.northeast,)),
            ord('b'): ("move", (constants.Direction.southwest,)),
            ord('n'): ("move", (constants.Direction.southeast,)),


            # wasd
            #ord('w'): ("move", [constants.Direction.up]),
            #ord('a'): ("move", [constants.Direction.left]),
            #ord('s'): ("move", [constants.Direction.down]),
            #ord('d'): ("move", [constants.Direction.right]),

            # Looking directions, arrow keys with SHIFT held down
            #curses.KEY_SF: (constants.CMD_LOOK, constants.DOWN),
            #curses.KEY_SR: (constants.CMD_LOOK, constants.UP),
            #curses.KEY_SRIGHT: (constants.CMD_LOOK, constants.RIGHT),
            #curses.KEY_SLEFT: (constants.CMD_LOOK, constants.LEFT),

            #
            #ord('J'): (constants.CMD_LOOK, constants.DOWN),
            #ord('H'): (constants.CMD_LOOK, constants.LEFT),
            #ord('K'): (constants.CMD_LOOK, constants.UP),
            #ord('L'): (constants.CMD_LOOK, constants.RIGHT),

            # wasd looking
            #ord('W'): (constants.CMD_LOOK, constants.UP),
            #ord('A'): (constants.CMD_LOOK, constants.LEFT),
            #ord('S'): (constants.CMD_LOOK, constants.DOWN),
            #ord('D'): (constants.CMD_LOOK, constants.RIGHT),

            #ord('f'): (constants.CMD_FIRE, constants.N1),
            #ord('F'): (constants.CMD_FIRE, constants.N2),

            #ord('1'): (constants.CMD_FIRE, constants.N1),
            #ord('2'): (constants.CMD_FIRE, constants.N2),
            #ord('3'): (constants.CMD_FIRE, constants.N3),
            #ord('4'): (constants.CMD_FIRE, constants.N4),
            #ord('5'): (constants.CMD_FIRE, constants.N5),
            #ord('6'): (constants.CMD_FIRE, constants.N6),
            #ord('7'): (constants.CMD_FIRE, constants.N7),
            #ord('8'): (constants.CMD_FIRE, constants.N8),
            #ord('9'): (constants.CMD_FIRE, constants.N9),

            #ord('o'): (constants.CMD_FIRE, constants.SMALL_SLIME),
            #ord('O'): (constants.CMD_FIRE, constants.BIG_SLIME),
        }
        if self._command_mode:
            if 0 <= c <= 255:
                character = chr(c)
                if character == "\n":
                    event = ('command', self._command)
                    self.network.remote_event(event)
                    self._command_mode = False
                else:
                    self._command += character

            elif c == 263:
                # BACKSPACE
                self._command = self._command[:-1]

        elif self._curses_feedback_mode:
            self._messages.append("Pressed: {}".format(c))
            self._curses_feedback_mode = False

        elif c == ord('c'):
            consolescene = ConsoleScene(self.namespace, self.network)
            raise NewScene(consolescene)

        elif c == ord('#'):
            # Now entering command mode.
            self._command_mode = True
            self._command = ''

        elif c == ord('?'):
            # Curses feedback mode. The next key pressed will print it's
            # keycode to the message log.
            self._curses_feedback_mode = True

        elif c in cmds:
            self.network.remote_event(cmds[c])

class _MultiHackClientNetworkPacketHandler(object):
    def _event_packet(self, packet):
        event = utility.quick_unpickle(packet.event)
        with self._lock:
            self._events.append(event)

    def _keep_alive_packet(self, packet):
        pass
    def _disconnect_packet(self, packet):
        raise ServerDisconnect
    def _picklediff_packet(self, packet):
        key = utility.quick_unpickle(packet.key)
        self._store.feed(key, packet.diff)

class MultiHackClientNetwork(_MultiHackClientNetworkPacketHandler):
    def __init__(self):
        self.handlers = {
            constants.Payload.event: self._event_packet,
            constants.Payload.picklediff: self._picklediff_packet,
            constants.KEEP_ALIVE: self._keep_alive_packet,
            constants.DISCONNECT: self._disconnect_packet,
        }

        self.socket = None
        self.socket_type = None

        self._events = []

        # This is what the server is sending us.
        self._store = utility.DifflingReader()

        # This is what we're storing remotely, at the server.
        self._remote_store = utility.DifflingAuthor(aggressive=True)

        self.keepalive_timer = utility.Stopwatch()
        self.lastheard_timer = utility.Stopwatch()

        self._buffer = ''

        self._lock = threading.RLock()
        self._thread = None
        self._thread_running = False

    def remote_set(self, key, value):
        self._remote_store[key] = value

        for key, diff in self._remote_store.get_changes():
            p = packet_pb2.Packet()
            p.payload_type = constants.Payload.picklediff
            p.key = utility.quick_pickle(key)
            p.diff = diff
            self._send_packets(p)

    def remote_event(self, event):
        p = packet_pb2.Packet()
        p.payload_type = constants.Payload.event
        p.event = utility.quick_pickle(event)
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
            try:
                self.socket.close()
            except socket.error:
                # Who cares, we're shutting down.
                # TODO just check that we're not breaking stuff.
                pass

    @property
    def events(self):
        if self._thread_running:
            with self._lock:
                out = self._events
                self._events = []
        else:
            out = self._events
            self._events = []
        return out

    def get_events(self):
        return self.events

    @property
    def store(self):
        if self._thread_running:
            with self._lock:
                return self._store.copy()
        else:
            return self._store

    def _ticklet(self):
        rlist, wlist, xlist = select.select((self.socket,),(),(),0.01)
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

class ClientException(Exception):
    pass

class NewScene(ClientException):
    pass

class CloseProgram(ClientException):
    pass

class ServerDisconnect(ClientException):
    pass

if __name__=='__main__':
    client_main()
