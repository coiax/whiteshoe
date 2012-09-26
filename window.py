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
import collections
import datetime
import code
import readline

import packet_pb2

# mainmethods
def server_main(args):
    # Ignore arguments for now
    s = Server()
    s.serve()

def client_main(args):
    curses.wrapper(main2, args)

def main2(stdscr, arguments):
    p = argparse.ArgumentParser()
    p.add_argument('-c','--connect',default="::1")

    ns = p.parse_args(arguments)

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
    def tick(self, stdscr):
        self.network.update()
        visible = self.network.get_visible()

        # TODO currently window viewport is based on the 0,0 topleft
        # corner, and we want to be able to move around
        stdscr.clear()

        my_coord, player = self.network.find_me()

        for coord, objects in visible.items():
            x,y = coord
            if objects:
                for o in objects:
                    display_chr, colour = self.display_character(o)
                try:
                    stdscr.addstr(y,x,display_chr, colour)
                except curses.error:
                    pass
            else:
                # A bold purple ? mark indicates a coordinate that is
                # in the known_world, but has no objects, meaning it has
                # been explicitly cleared by the network.
                #
                # This is an artifact that may or may not be present
                # as stuff changes.
                #stdscr.addstr(y,x,"?",curses.color_pair(5) | curses.A_BOLD)
                pass

        x,y = my_coord
        stdscr.move(y,x)

        stdscr.refresh()

    def display_character(self, object, history=False):
        display_chr = None
        # Default colour
        colour = curses.color_pair(0)

        obj, attr = object

        display_chr = {
            Constants.OBJ_WALL: '#',
            Constants.OBJ_PLAYER: '@',
            Constants.OBJ_EMPTY: '.',
            Constants.OBJ_HORIZONTAL_WALL: '-',
            Constants.OBJ_VERTICAL_WALL: '|',
            Constants.OBJ_CORNER_WALL: '+',
            Constants.OBJ_BULLET: ':',
            Constants.OBJ_EXPLOSION: '*'}.get(obj,'?')

        if obj == Constants.OBJ_PLAYER:
            direction = attr['direction']
            if attr['number'] == self.network.player_id:
                colour = curses.color_pair(1)
            else:
                colour = curses.color_pair(2)

            if direction == Constants.RIGHT:
                display_chr = '>'
            elif direction == Constants.LEFT:
                display_chr = '<'
            elif direction == Constants.UP:
                display_chr = '^'
            elif direction == Constants.DOWN:
                display_chr = 'v'
        elif obj == Constants.OBJ_BULLET:
            owner = attr['owner']
            if owner == self.network.player_id:
                colour = curses.color_pair(1)
            else:
                colour = curses.color_pair(2)
        elif obj == Constants.OBJ_EXPLOSION:
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
            curses.KEY_DOWN: (Constants.CMD_MOVE, Constants.DOWN),
            curses.KEY_UP: (Constants.CMD_MOVE, Constants.UP),
            curses.KEY_LEFT: (Constants.CMD_MOVE, Constants.LEFT),
            curses.KEY_RIGHT: (Constants.CMD_MOVE, Constants.RIGHT),
            # vim keys
            ord('j'): (Constants.CMD_MOVE, Constants.DOWN),
            ord('h'): (Constants.CMD_MOVE, Constants.LEFT),
            ord('k'): (Constants.CMD_MOVE, Constants.UP),
            ord('l'): (Constants.CMD_MOVE, Constants.RIGHT),

            # wasd
            ord('w'): (Constants.CMD_MOVE, Constants.UP),
            ord('a'): (Constants.CMD_MOVE, Constants.LEFT),
            ord('s'): (Constants.CMD_MOVE, Constants.DOWN),
            ord('d'): (Constants.CMD_MOVE, Constants.RIGHT),

            # Looking directions, arrow keys with SHIFT held down
            curses.KEY_SF: (Constants.CMD_LOOK, Constants.DOWN),
            curses.KEY_SR: (Constants.CMD_LOOK, Constants.UP),
            curses.KEY_SRIGHT: (Constants.CMD_LOOK, Constants.RIGHT),
            curses.KEY_SLEFT: (Constants.CMD_LOOK, Constants.LEFT),

            #
            ord('J'): (Constants.CMD_LOOK, Constants.DOWN),
            ord('H'): (Constants.CMD_LOOK, Constants.LEFT),
            ord('K'): (Constants.CMD_LOOK, Constants.UP),
            ord('L'): (Constants.CMD_LOOK, Constants.RIGHT),

            # wasd looking
            ord('W'): (Constants.CMD_LOOK, Constants.UP),
            ord('A'): (Constants.CMD_LOOK, Constants.LEFT),
            ord('S'): (Constants.CMD_LOOK, Constants.DOWN),
            ord('D'): (Constants.CMD_LOOK, Constants.RIGHT),

            ord('f'): (Constants.CMD_FIRE, Constants.N1),
            ord('F'): (Constants.CMD_FIRE, Constants.N2),
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
            Constants.GAMES_RUNNING: self._games_running,
            Constants.ERROR: self._error,
            Constants.VISION_UPDATE: self._vision_update,
            Constants.KEEP_ALIVE: self._keep_alive,
            Constants.GAME_STATUS: self._game_status,
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
                port = Constants.DEFAULT_PORT
            self._server_addr = (ip, port)

            p = packet_pb2.Packet()
            p.packet_id = get_id('packet')
            p.payload_types.append(Constants.JOIN_GAME)
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

            if self.keepalive_timer > Constants.KEEPALIVE_TIME:
                self.keepalive_timer -= Constants.KEEPALIVE_TIME

                self._send_keepalive()
    def _ticklet(self):
        rlist, wlist, xlist = select.select([self.socket],[],[],0.1)
        for rs in rlist:
            data, addr = rs.recvfrom(4096)
            try:
                packet = packet_pb2.Packet.FromString(data)

                for payload_type in packet.payload_types:
                    self.handlers[payload_type](packet, addr)

                self.last_heard = time.time()

            except Exception as e:
                #traceback.print_exc()
                # Can't print exceptions when the tty is up
                pass

    def _send_keepalive(self):
        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(Constants.KEEP_ALIVE)
        p.timestamp = int(time.time())

        self._send_packets([p], self._server_addr)

    def _send_packets(self, packets, addr):
        for packet in packets:
            self.socket.sendto(packet.SerializeToString(), addr)
        # Don't need to keepalive if we're sending other packets
        self.keepalive_timer = 0

    def send_command(self,cmd,arg):
        cmd_num = Constants.to_numerical_constant(cmd)
        arg_num = Constants.to_numerical_constant(arg)

        p = packet_pb2.Packet()
        p.packet_id = get_id('packet')
        p.payload_types.append(Constants.GAME_ACTION)
        # Ah, we need to note the game_id that we're participating in
        p.action_game_id = self.game_id
        p.action = cmd_num
        p.argument = arg_num

        self._send_packets([p],self._server_addr)

    def find_me(self):
        if self.player_id is not None:
            for coord, objects in self.known_world.items():
                for object in objects:
                    if (object[0] == Constants.OBJ_PLAYER and
                        object[1]['number'] == self.player_id):

                        return coord, object

        # Failsafe
        return (0,0), [Constants.OBJ_PLAYER, {}]

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
            for key in Constants.ATTRIBUTE_KEYS:
                if attribute.HasField(key):
                    value = getattr(attribute, key)
                    if key in Constants.ATTRIBUTE_CONSTANT_KEYS:
                        value = Constants.from_numerical_constant(value)
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

            obj_type = Constants.from_numerical_constant(obj_type)

            self.known_world[x,y].append((obj_type, attr))

    def _keep_alive(self, packet, addr):
        pass

    def _game_status(self, packet, addr):
        if packet.status == Constants.STATUS_JOINED:
            self.game_id = packet.status_game_id
            self.player_id = packet.your_player_id
            self.vision = packet.game_vision
        elif packet.status == Constants.STATUS_LEFT:
            self.game_id = None


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


def get_id(family='packet',_id_counters={}):
    if family not in _id_counters:
        _id_counters[family] = 0
    new_id = _id_counters[family]
    _id_counters[family] = (new_id + 1) % 2**16
    return new_id

class Server(object):

    def __init__(self):
        self.handlers = {
            Constants.GET_GAMES_LIST: self._get_games_list,
            # games running (s->c)
            Constants.MAKE_NEW_GAME: self._make_new_game,
            Constants.ERROR: self._error,
            Constants.GAME_ACTION: self._game_action,
            Constants.JOIN_GAME: self._join_game,
            # vision update (s->c)
            Constants.KEEP_ALIVE: self._keep_alive,

        }
        self.port = Constants.DEFAULT_PORT

        self.games = []

        # Debug starting game
        g = Game(vision='cone')
        self.games.append(g)

        self.seen_ids = []

        self.clients = {}

        self.socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

        self.timeout = 30


    def serve(self):
        self.socket.bind(('',self.port))

        while True:
            for game in self.games:
                packets = game.tick()
                self._send_packets(packets)

            for addr in list(self.clients):
                last_heard = self.clients[addr].get('last_heard')
                if last_heard is None:
                    continue

                seconds = (datetime.datetime.now() - last_heard).seconds
                if seconds > self.timeout:
                    player_id = self.clients[addr]['player_id']
                    for game in self.games:
                        if game.is_player_in_game(player_id):
                            packets = game.player_leave(player_id)
                            self._send_packets(packets)

                    del self.clients[addr]


            rlist, wlist, xlist = select.select([self.socket],[],[],0.05)
            for rs in rlist:
                data, addr = rs.recvfrom(4096)

                try:
                    packet = packet_pb2.Packet.FromString(data)
                    #print("Recv: {}".format(packet))

                    if addr not in self.clients:
                        self.clients[addr] = {
                            'player_id': get_id('player')
                        }

                    # disabled while we're debugging
                    #if (addr,packet.packet_id) in self.seen_ids:
                        # Ignore duplicates
                        #    continue

                    self.seen_ids.append((addr,packet.packet_id))

                    while len(self.seen_ids) > 200:
                        del self.seen_ids[0]

                    for payload_type in packet.payload_types:
                        self.handlers[payload_type](packet, addr)


                    self.clients[addr]['last_heard'] = datetime.datetime.now()

                except Exception as e:
                    traceback.print_exc()

    def _send_packets(self, packets):
        for packet_player_id, packet in packets:
            # Determine addr
            sent = False
            for addr, addr_dict in self.clients.items():
                if packet_player_id == addr_dict['player_id']:
                    #print("Sent: {}".format(packet))
                    self.socket.sendto(packet.SerializeToString(), addr)
                    sent = True
                    break

            assert sent

    def _get_games_list(self, packet, addr):
        reply = packet_pb2.Packet()
        reply.payload_types.append(Constants.GAMES_RUNNING)

        reply.packet_id = get_id()

        for game in self.games:
            game_message = reply.games.add()

            game_message.game_id = game.id
            game_message.name = game.name
            game_message.mode = game.mode
            game_message.max_players = game.max_players
            game_message.current_players = game.current_players

        self._send_packets([reply], addr)

    def _make_new_game(self, packet, addr):
        # creating new game
        max_players = packet.max_players or None
        map_generator = packet.map_generator or None
        game_name = packet.new_game_name or None
        game_mode = packet.new_game_mode or none
        game_id = get_id('game')

        g = Game(max_players,map_generator,game_name,game_mode,game_id)
        if packet.join_new_game:
            packets = g.player_join(self.clients[addr]['player_id'])
            self._send_packets(packets)

        self.games.append(g)

    def _join_game(self, packet, addr):
        # joining existing game
        if packet.autojoin:
            game = random.choice(self.games)
        else:
            game_id = packet.join_game_id
            game = [g for g in self.games if g.id == game_id][0]

        packets = game.player_join(self.clients[addr]['player_id'])
        self._send_packets(packets)

    def _error(self, packet, addr):
        # Handle silently.
        pass

    def _game_action(self, packet, addr):
        player_id = self.clients[addr]['player_id']
        game_id = packet.action_game_id

        game = [g for g in self.games if g.id == game_id][0]

        assert game.is_player_in_game(player_id)

        packets = game.player_action(player_id, packet.action, packet.argument)
        self._send_packets(packets)

    def _keep_alive(self, packet, addr):
        pass

def purerandom_map(X=80,Y=24,seed=0):
    world = {}
    r = random.Random(seed)

    for i,j in itertools.product(range(X), range(Y)):
        if r.random() < 0.35:
            world[i,j] = [(Constants.OBJ_WALL, {})]
        else:
            world[i,j] = [(Constants.OBJ_EMPTY, {})]

    return world

def empty_map(X=80,Y=24,seed=None):
    world = {}

    for i,j in itertools.product(range(X), range(Y)):
        world[i,j] = [(Constants.OBJ_EMPTY, {})]
    return world

def ca_maze_map(X=80,Y=24,seed=1):
    r = random.Random(seed)
    ca_world = {}

    starting_density = 0.35
    max_ticks = 300
    B = [3]
    S = [1,2,3,4,5]

    # A cellular automata with rules B3/S12345 generates a maze based on
    # starting conditions
    for i,j in itertools.product(range(X),range(Y)):
        ca_world[i,j] = r.random() < starting_density

    ticks = 0
    live = True
    while live and ticks < max_ticks:
        live = False
        ticks += 1

        will_birth = set()
        will_die = set()

        for coord in ca_world:
            alive = ca_world[coord]

            neighbours = neighbourhood(coord,n=1)
            neighbours.remove(coord)
            # Remember that True has a numeric value of 1
            number = sum(ca_world[n] for n in neighbours if n in ca_world)
            if not alive and number in B:
                will_birth.add(coord)
                live = True

            elif alive and number not in S:
                will_die.add(coord)
                live = True

        for coord in will_birth:
            ca_world[coord] = True

        for coord in will_die:
            ca_world[coord] = False

    # Now the maze CA tends to generate isolated islands

    world = {}
    for coord in ca_world:
        if ca_world[coord]:
            obj = [(Constants.OBJ_WALL, {})]
        else:
            obj = [(Constants.OBJ_EMPTY, {})]

        world[coord] = obj

    return world

def pretty_walls(world):
    for coord, objects in world.items():
        if objects[0][0] == Constants.OBJ_EMPTY:
            continue

        vertical = False
        horizontal = False

        for neighbour in ((coord[0], coord[1] - 1), (coord[0], coord[1] + 1)):
            if neighbour not in world:
                continue
            if world[neighbour][0][0] != Constants.OBJ_EMPTY:
                vertical = True
                break

        for neighbour in ((coord[0] - 1, coord[1]), (coord[0] + 1, coord[1])):
            if neighbour not in world:
                continue
            if world[neighbour][0][0] != Constants.OBJ_EMPTY:
                horizontal = True
                break

        if not vertical and not horizontal:
            # Do nothing
            continue
        elif vertical and not horizontal:
            del objects[0]
            objects.append((Constants.OBJ_VERTICAL_WALL, {}))
        elif not vertical and horizontal:
            del objects[0]
            objects.append((Constants.OBJ_HORIZONTAL_WALL, {}))
        elif vertical and horizontal:
            del objects[0]
            objects.append((Constants.OBJ_CORNER_WALL, {}))

    return world

def _visible_world(world, visible):
    visible_world = {}

    for coord in visible:
        if coord not in world:
            continue

        visible_objects = []
        for obj,attr in world[coord]:
            visible_objects.append((obj,dict(attr)))

        visible_world[coord] = visible_objects

    for coord, objects in world.items():
        for obj,attr in objects:
            if obj in Constants.ALWAYS_VISIBLE_OBJECTS:
                if coord not in visible_world:
                    visible_world[coord] = []
                visible_world[coord].append((obj,dict(attr)))
    return visible_world

def vision_basic(world, start_coord, direction):
    visible = neighbourhood(start_coord, n=3)
    return _visible_world(world, visible)

def vision_cone(world, coord, direction):
    visible = set()
    # The square you are in is always visible as well one square
    # behind you
    visible.add(coord)

    main_direction = Constants.DIFFS[direction]
    behind_you = main_direction[0] * -1, main_direction[1] * -1

    visible.add((coord[0] + behind_you[0], coord[1] + behind_you[1]))


    # First, everything in the direction the player is looking
    # straight ahead

    def look_until_wall(start, diff):
        coord = start
        v = set()

        running = True
        while running:
            coord = coord[0] + diff[0], coord[1] + diff[1]
            if coord not in world:
                break
            v.add(coord)
            objects = world[coord]
            for o in objects:
                if o[0] in Constants.OPAQUE_OBJECTS:
                    running = False
        return v

    for direction in Constants.ADJACENT[direction]:
        visible.update(look_until_wall(coord,
                                       Constants.DIFFS[direction]))

    return _visible_world(world, visible)

def network_pack_object(coord, object):
    x,y = coord
    obj_type, obj_attr = object
    obj_type = Constants.to_numerical_constant(obj_type)

    if obj_attr == {}:
        attribute = None
    else:
        attribute = packet_pb2.Packet.Attribute()
        for key in Constants.ATTRIBUTE_KEYS:
            if key in obj_attr:
                value = obj_attr[key]
                if key in Constants.ATTRIBUTE_CONSTANT_KEYS:
                    value = Constants.to_numerical_constant(value)
                setattr(attribute, key, value)

    return x,y,obj_type,attribute

def pack_attribute(obj_attr):
    attribute = packet_pb2.Packet.Attribute()
    for key in Constants.ATTRIBUTE_KEYS:
        if key in obj_attr:
            value = obj_attr[key]
            if key in Constants.ATTRIBUTE_CONSTANT_KEYS:
                value = Constants.to_numerical_constant(value)
            setattr(attribute, key, value)
    return attribute

class Game(object):
    MAP_GENERATORS = {
        'purerandom': purerandom_map,
        'empty': empty_map,
        'ca_maze':ca_maze_map
    }
    def __init__(self,max_players=20,map_generator='purerandom',
                 name='Untitled',mode='ffa',id=None,vision='basic'):

        self.max_players = max_players
        self.world = self.MAP_GENERATORS[map_generator]()
        print("World ({0}) generated.".format(map_generator))
        self.name = name
        self.mode = mode

        if id is None:
            id = get_id('game')

        self.id = id

        self.vision = vision

        self.players = []
        self.known_worlds = {}

        self.last_tick = None

    @property
    def current_players(self):
        return len(self.players)

    def is_player_in_game(self,player_id):
        return player_id in self.players

    def _spawn_player(self, player_id):
        try:
            location, player_obj = self._find_player(player_id)
        except PlayerNotFound:
            # this is what we expect
            pass
        else:
            # If the player is alive, then he gonna be removed
            self._remove_player(player_id)

        # Find location for player to spawn
        suitable = set(self.world)
        for obj_type in Constants.SOLID_OBJECTS:
            suitable -= set(self.find_obj_locations(obj_type))

        spawn_coord = random.choice(list(suitable))

        start_max_hp = 10

        player = (Constants.OBJ_PLAYER,
                  {'number':player_id,
                   'direction': Constants.RIGHT,
                   'team':player_id,
                   'hp':start_max_hp,
                   'hp_max': start_max_hp})

        self.world[spawn_coord].append(player)

        dirty = [spawn_coord]
        return dirty

    def _remove_player(self, player_id):
        location, player = self._find_player(player_id)
        self.world[location].remove(player)

        return self._mark_dirty([location])

    def _kill_player(self, player_id):
        self._remove_player(player_id)
        self.known_worlds[player_id] = {}
        self._spawn_player(player_id)

    def player_join(self,player_id):
        assert player_id not in self.players
        self.players.append(player_id)
        self.known_worlds[player_id] = {}

        dirty = self._spawn_player(player_id)
        location, player = self._find_player(player_id)


        join_packet = packet_pb2.Packet()
        join_packet.packet_id = get_id('packet')
        join_packet.payload_types.append(Constants.GAME_STATUS)

        join_packet.status_game_id = self.id
        join_packet.status = Constants.STATUS_JOINED
        join_packet.your_player_id = player_id
        join_packet.game_name = self.name
        join_packet.game_mode = self.mode
        join_packet.game_max_players = self.max_players
        join_packet.game_current_players = self.current_players
        join_packet.game_vision = self.vision

        direction = player[1]['direction']
        visible_world = self._determine_can_see(location, direction)

        changed_coords = self._update_known_world(player_id, visible_world)

        packets = [(player_id, join_packet)]
        packets.extend(self._send_player_vision(player_id, changed_coords))
        packets.extend(self._mark_dirty(dirty))

        return packets

    def _update_known_world(self, player_id, visible_world):
        known_world = self.known_worlds[player_id]
        changed_coords = set()

        # Decay the things we have in vision
        for coord in set(visible_world):
            if coord not in known_world:
                continue
            for obj,attr in list(known_world[coord]):
                if obj in Constants.HISTORICAL_OBJECTS:
                    if attr.get('historical',False) != True:
                        changed_coords.add(coord)
                    attr['historical'] = True
                else:
                    known_world[coord].remove((obj,attr))
                    changed_coords.add(coord)

        for coord, visible_objects in visible_world.items():
            known_objects = known_world.get(coord,[])

            # We will compare these two later
            start_state = list(known_objects)
            new_state = list(known_objects)

            historical_known = [o for o in known_objects
                                if o[0] in Constants.HISTORICAL_OBJECTS]

            historical_visibles = [o for o in visible_objects
                                   if o[0] in Constants.HISTORICAL_OBJECTS]

            if historical_visibles:
                for known in historical_known:
                    new_state.remove(known)

            for object in visible_objects:
                new_state.append(object)

            if start_state != new_state:
                changed_coords.add(coord)
                known_world[coord] = new_state

        # Now, historical decay
        coords = set(known_world) - set(visible_world)
        for coord in coords:
            for obj,attr in list(known_world[coord]):
                if obj in Constants.HISTORICAL_OBJECTS:
                    if attr.get('historical',False) != True:
                        changed_coords.add(coord)
                    attr['historical'] = True
                else:
                    known_world[coord].remove((obj,attr))
                    changed_coords.add(coord)

        return changed_coords

    def player_leave(self, player_id):
        assert player_id in self.players

        try:
            location, player = self._find_player(player_id)
        except PlayerNotFound:
            packets = []
        else:
            packets = self._remove_player(player_id)

        del self.known_worlds[player_id]
        self.players.remove(player_id)

        return packets

    def _determine_can_see(self, coord, direction):
        vision_func = Constants.VISION[self.vision]

        visible_world = vision_func(self.world, coord, direction)

        return visible_world

    def _send_player_vision(self,player_id, coords):
        #location, player = self._find_player(player_id)

        packet = packet_pb2.Packet()
        packet.packet_id = get_id('packet')
        packet.payload_types.append(Constants.VISION_UPDATE)
        packet.vision_game_id = self.id

        known_world = self.known_worlds[player_id]

        # Now to pack the objects
        attributes = []

        for coord in coords:
            if coord not in self.world:
                continue

            if known_world[coord] == []:
                x,y = coord
                obj_type = -1
                attr_id = -1
                packet.objects.extend([x,y,obj_type,attr_id])

            else:
                for object in known_world[coord]:
                    x,y = coord
                    obj_type, obj_attr = object
                    obj_type = Constants.to_numerical_constant(obj_type)
                    if obj_attr == {}:
                        attr_id = -1
                    else:
                        attr_id = None
                        for attr, packed_attr in attributes:
                            if attr == obj_attr:
                                attr_id = attributes.index((attr, packed_attr))

                        if attr_id is None:
                            # No existing attribute dict exists
                            packed = pack_attribute(obj_attr)
                            attributes.append((obj_attr, packed))
                            attr_id = len(attributes) - 1

                    packet.objects.extend([x,y,obj_type,attr_id])

        for obj_attr, packed in attributes:
            packet.attributes.extend([packed])

        return [(player_id, packet)]

    def find_objs(self, obj_type):
        locations = []
        for coord, objects in self.world.items():
            for obj, attr in objects:
                if obj == obj_type:
                    pair = (coord, (obj,attr))
                    locations.append(pair)

        return locations

    def find_obj_locations(self, obj_type):
        locations = self.find_objs(obj_type)
        return [pair[0] for pair in locations]

    def _find_player(self, number):
        # Find player location
        location = None

        for coord, objects in self.world.items():
            for obj, attr in objects:
                if obj == Constants.OBJ_PLAYER and attr['number'] == number:
                    player = (obj, attr)
                    location = coord
                    break

            if location is not None:
                break

        if location is None:
            raise PlayerNotFound

        return location, player

    def _player_death(self, player_id):
        location, player = self._find_player(player_id)

    def player_action(self, player_id, action, argument):
        assert player_id in self.players
        # Translate into internal constants
        cmd = Constants.from_numerical_constant(action)
        arg = Constants.from_numerical_constant(argument)

        try:
            location, player = self._find_player(player_id)
        except PlayerNotFound:
            location = None
            player = None

        handlers = {
            Constants.CMD_LOOK: self._look,
            Constants.CMD_MOVE: self._move,
            Constants.CMD_FIRE: self._fire,
        }

        packets = handlers[cmd](player, location, arg)

        return packets

    def _look(self, player, location, arg):
        player[1]['direction'] = arg
        return self._mark_dirty([location])

    def _move(self, player, location, arg):
        self.world[location].remove(player)

        diff = Constants.DIFFS[arg]

        old_location = location
        new_location = (location[0] + diff[0], location[1] + diff[1])
        can_move = True

        if new_location not in self.world:
            can_move = False
        else:
            for obj,attr in self.world[new_location]:
                # If the area is empty
                if obj in Constants.SOLID_OBJECTS:
                    can_move = False
                    break


        if not can_move:
            # Player can't move to that location, no move
            self.world[location].append(player)
            return []
        else:
            self.world[new_location].append(player)
            player_id = player[1]['number']
            dirty_packets = self._mark_dirty([old_location,new_location])
            direction = player[1]['direction']

            visible_world = self._determine_can_see(new_location, direction)
            new_vision_packets = self._send_player_vision(player_id,
                                                          visible_world)
            return dirty_packets + new_vision_packets

    def _fire(self, player, location, arg):
        direction = player[1]['direction']
        player_id = player[1]['number']

        diff = Constants.DIFFS[direction]
        bullet_location = location

        attr = {'owner': player_id, 'direction':direction, 'size':arg}
        bullet = (Constants.OBJ_BULLET, attr)

        self.world[bullet_location].append(bullet)

        return self._mark_dirty([bullet_location])


    def _mark_dirty(self, coordinates):
        packets = []
        for player_id in self.players:
            try:
                location, playerobj = self._find_player(player_id)
            except PlayerNotFound:
                # If player isn't present in the map, then we don't have
                # to worry about vision for them
                continue

            direction = playerobj[1]['direction']

            visible_world = self._determine_can_see(location, direction)

            #dirty_locations = set(visible_world) & set(coordinates)
            #if dirty_locations:
            # This dirty locations stuff should be replaced with a
            # method checking the difference between the old known world
            # and the new known world, and then updating the player
            # about that

            # To provide good (rather than correct) behaviour, the server
            # will now just scream vision packets at clients

            changed_coords = self._update_known_world(player_id,
                                                      visible_world)
            packets.extend(self._send_player_vision(player_id,
                                                    changed_coords))

        return packets

    def tick(self):
        # Do anything that occurs independently of network input
        # like bullets moving
        old_time = self.last_tick
        self.last_tick = now = datetime.datetime.now()

        if old_time is None:
            # Can't do anything on a tick until we know how much time
            # has passed
            return ()

        time_diff = now - old_time
        time_diff_s = time_diff.seconds + (time_diff.microseconds * (10.0**-6))

        dirty_coords = set()

        self._tick_bullets(time_diff_s, dirty_coords)
        self._tick_explosions(time_diff_s, dirty_coords)

        if dirty_coords:
            return self._mark_dirty(dirty_coords)
        else:
            return ()

    def _tick_bullets(self, time_passed, dirty_coords):
        # Pair of (coord, object)
        bullets = self.find_objs(Constants.OBJ_BULLET)
        for coord,object in bullets:
            attr = object[1]
            size = attr['size']
            speed = Constants.BULLET_SPEEDS[size]

            if '_time_remaining' not in attr:
                attr['_time_remaining'] = speed

            attr['_time_remaining'] -= time_passed
            # TODO, currently a bullet can move only 1 square per tick
            if attr['_time_remaining'] < 0:
                attr['_time_remaining'] += speed


                self.world[coord].remove(object)
                dirty_coords.add(coord)


                loc_diff = Constants.DIFFS[object[1]['direction']]

                new_coord = (coord[0] + loc_diff[0], coord[1] + loc_diff[1])

                if new_coord in self.world:
                    exploded = False
                    for other in list(self.world[new_coord]):
                        if other[0] in Constants.SOLID_OBJECTS:
                            # Boom, bullet explodes.
                            for ex_coord in neighbourhood(new_coord,n=size-1):
                                if ex_coord not in self.world:
                                    continue

                                explosion = (Constants.OBJ_EXPLOSION,
                                             {'_damage':size**2})

                                self.world[ex_coord].append(explosion)
                                dirty_coords.add(ex_coord)
                                exploded = True

                        if exploded:
                            break

                    if not exploded:
                        # Bullet keeps moving
                        self.world[new_coord].append(object)
                        dirty_coords.add(new_coord)

    def _tick_explosions(self, time_passed, dirty_coords):
        explosions = self.find_objs(Constants.OBJ_EXPLOSION)
        for coord,bullet in explosions:
            attr = bullet[1]
            if '_time_left' not in attr:
                attr['_time_left'] = Constants.EXPLOSION_LIFE
            if '_damaged' not in attr:
                attr['_damaged'] = []

            for object in list(self.world[coord]):
                if object[0] in Constants.BLOWABLE_UP:
                    if object in attr['_damaged']:
                        continue
                    else:
                        attr['_damaged'].append(object)

                    hp = object[1].get('hp', 0)
                    hp -= attr['_damage']

                    if hp <= 0:
                        if object[0] != Constants.OBJ_PLAYER:
                            self.world[coord].remove(object)
                        else:
                            player_id = object[1]['number']
                            self._kill_player(player_id)

                        dirty_coords.add(coord)

                        non_explosions = [o for o in self.world[coord]
                                          if o[0] != Constants.OBJ_EXPLOSION]

                        if not non_explosions:
                            empty = (Constants.OBJ_EMPTY, {})
                            self.world[coord].insert(0,empty)
                    else:
                        object[1]['hp'] = hp


            attr['_time_left'] -= time_passed
            if attr['_time_left'] < 0:
                self.world[coord].remove(bullet)
                dirty_coords.add(coord)

class Constants:
    # Constants
    DEFAULT_PORT = 25008

    GET_GAMES_LIST = 0
    GAMES_RUNNING = 1
    MAKE_NEW_GAME = 2
    ERROR = 3
    GAME_ACTION = 4
    JOIN_GAME = 5
    VISION_UPDATE = 6
    KEEP_ALIVE = 7
    GAME_STATUS = 8


    STATUS_JOINED = 1
    STATUS_LEFT = 2

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
    ADJACENT = {
        UP: (LEFT, UP, RIGHT, NORTHEAST, NORTHWEST),
        DOWN: (LEFT, DOWN, RIGHT, SOUTHEAST, SOUTHWEST),
        LEFT: (UP, LEFT, DOWN, NORTHWEST, SOUTHWEST),
        RIGHT: (UP, RIGHT, DOWN, NORTHEAST, SOUTHEAST),
    }

    CMD_MOVE = "move"
    CMD_LOOK = "look"
    CMD_FIRE = "fire"

    OBJ_WALL = "wall"
    OBJ_HORIZONTAL_WALL = "h-wall"
    OBJ_VERTICAL_WALL = "v-wall"
    OBJ_CORNER_WALL = "c-wall"
    OBJ_PLAYER = "player"
    OBJ_EMPTY = "empty"
    OBJ_BULLET = "bullet"
    OBJ_EXPLOSION = "boom"

    WALLS = (OBJ_WALL, OBJ_HORIZONTAL_WALL, OBJ_VERTICAL_WALL, OBJ_CORNER_WALL)
    HISTORICAL_OBJECTS = WALLS + (OBJ_EMPTY,)
    SOLID_OBJECTS = WALLS + (OBJ_PLAYER,)
    OPAQUE_OBJECTS = WALLS
    ALWAYS_VISIBLE_OBJECTS = (OBJ_EXPLOSION,)

    VISIBLE_OBJECTS = WALLS + (OBJ_EMPTY,OBJ_PLAYER,OBJ_EXPLOSION,OBJ_BULLET)
    BLOWABLE_UP = WALLS + (OBJ_PLAYER,)
    CAN_STAB = (OBJ_PLAYER,)

    VISION = {
        'basic': vision_basic,
        'cone': vision_cone,
    }
    ATTRIBUTE_KEYS = ("number", "direction", "team", "hp_max", "hp",
                      "max_ammo", "ammo", "owner","size","historical")
    ATTRIBUTE_CONSTANT_KEYS = ("direction",)

    N1 = 1
    N2 = 2
    N3 = 3
    N4 = 4
    N5 = 5
    N6 = 6
    N7 = 7
    N8 = 8
    N9 = 9

    BULLET_SPEEDS = {
        1: 0.05,
        2: 0.10,
        3: 0.15,
        4: 0.20,
        5: 0.25,
        6: 0.30,
        7: 0.35,
        8: 0.40,
        9: 0.45,
    }
    EXPLOSION_LIFE = 0.5
    KEEPALIVE_TIME = 5
    @classmethod
    def to_numerical_constant(cls,constant):
        constants = vars(cls).items()
        constants.sort(key=operator.itemgetter(0))
        constants = [c[1] for c in constants]
        return constants.index(constant)
    @classmethod
    def from_numerical_constant(cls,number):
        constants = vars(cls).items()
        constants.sort(key=operator.itemgetter(0))
        constants = [c[1] for c in constants]
        return constants[number]


class NewScene(Exception):
    pass

class CloseProgram(Exception):
    pass

class PlayerNotFound(Exception):
    pass

if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('-s','--server',action='store_true')
    namespace, remaining_args  = p.parse_known_args()
    if namespace.server:
        server_main(remaining_args)
    else:
        client_main(remaining_args)
