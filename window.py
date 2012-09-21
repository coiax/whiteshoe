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

import packet_pb2

# mainmethods
def server_main():
    s = Server()
    s.serve()

def client_main():
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
    data['network'] = ClientNetwork(autojoin=('::1',None),automake=True)

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



class GameScene(object):
    def __init__(self, data):
        self.data = data
        self.network = data['network']
    def tick(self, stdscr):
        self.network.update()
        visible, history = self.network.get_visible()

        # TODO currently window viewport is based on the 0,0 topleft
        # corner, and we want to be able to move around
        stdscr.clear()

        my_coord, player = self.network.find_me()

        for coord, objects in history.items():
            x,y = coord
            for o in objects:
                display_chr, colour = self.display_character(o,history=True)
            try:
                stdscr.addstr(y,x,display_chr, colour)
            except curses.error:
                pass

        for coord, objects in visible.items():
            x,y = coord
            for o in objects:
                display_chr, colour = self.display_character(o)
            try:
                stdscr.addstr(y,x,display_chr, colour)
            except curses.error:
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
            Constants.OBJ_CORNER_WALL: '+'}[obj]

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

        assert display_chr is not None

        if history:
            # Grey
            colour = curses.color_pair(3)

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
        }
        if c in cmds:
            cmd = cmds[c]
            self.network.send_command(cmd[0], cmd[1])
        elif c == ord('f'):
            curses.flash()
        elif c == ord('r'):
            self.network.generate_world(seed=random.random())

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

        self.socket = socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
        self.known_world = {}

        if autojoin is not None:
            ip,port = autojoin
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


    def update(self):
        # Do network things
        self._ticklet()
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

    def _send_packets(self, packets, addr):
        for packet in packets:
            self.socket.sendto(packet.SerializeToString(), addr)

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
        player_location, player = self.find_me()

        if self.vision is not None:
            direction = player[1].get('direction',Constants.RIGHT)
            can_see = Constants.VISION[self.vision](self.known_world,
                                                    player_location,direction)
        else:
            can_see = list(self.known_world)

        visible = {}
        history = collections.defaultdict(list)

        for coord, objects in self.known_world.items():
            if coord in can_see:
                visible[coord] = objects
            else:
                for obj in objects:
                    if obj[0] in Constants.HISTORICAL_OBJECTS:
                        history[coord].append(obj)

        return visible, history

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
        attribute_keys = ["number", "direction", "team", "hp_max", "hp",
                          "max_ammo", "ammo"]
        constants_keys = ["direction"]

        for attribute in packet.attributes:
            unpacked = {}
            for key in attribute_keys:
                if attribute.HasField(key):
                    value = getattr(attribute, key)
                    if key in constants_keys:
                        value = Constants.from_numerical_constant(value)
                    unpacked[key] = value

            unpacked_attributes.append(unpacked)

        cleared = set()

        for x,y,obj_type,attr_id in grouper(4, packet.objects):
            assert None not in (x,y,obj_type,attr_id)
            obj_type = Constants.from_numerical_constant(obj_type)
            if attr_id == -1:
                attr = {}
            else:
                attr = unpacked_attributes[attr_id].copy()

            if (x,y) not in cleared:
                self.known_world[x,y] = []
                cleared.add((x,y))

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
                game.tick()

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


            rlist, wlist, xlist = select.select([self.socket],[],[],0.1)
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


def vision_basic(world, coord, direction):
    return neighbourhood(coord,n=3)

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

        while True:
            coord = coord[0] + diff[0], coord[1] + diff[1]
            if coord not in world:
                break
            v.add(coord)
            objects = world[coord]
            for o in objects:
                if o[0] in Constants.SOLID_OBJECTS:
                    break
        return v

    for direction in Constants.ADJACENT[direction]:
        visible.update(look_until_wall(coord,
                                       Constants.DIFFS[direction]))

    return visible

def network_pack_object(coord, object):
    x,y = coord
    obj_type, obj_attr = object
    obj_type = Constants.to_numerical_constant(obj_type)

    if obj_attr == {}:
        attribute = None
    else:
        attribute = packet_pb2.Packet.Attribute()
        keys = ["number", "direction", "team", "hp_max", "hp", "max_ammo",
                "ammo"]
        for key in keys:
            if key in obj_attr:
                value = obj_attr[key]
                if type(value) == str:
                    value = Constants.to_numerical_constant(value)
                setattr(attribute, key, value)

    return x,y,obj_type,attribute

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

    @property
    def current_players(self):
        return len(self.players)

    def is_player_in_game(self,player_id):
        return player_id in self.players

    def player_join(self,player_id):
        assert player_id not in self.players
        self.players.append(player_id)

        # Find location for player to spawn
        empty_locations = self.find_obj_locations(Constants.OBJ_EMPTY)
        player_locations = self.find_obj_locations(Constants.OBJ_PLAYER)

        suitable = set(empty_locations) - set(player_locations)
        spawn_coord = random.choice(list(suitable))

        player = (Constants.OBJ_PLAYER,
                  {'number':player_id,
                   'direction': Constants.RIGHT,
                   'team':player_id})

        self.world[spawn_coord].append(player)

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
        locations = self._determine_can_see(spawn_coord, direction)

        return [(player_id, join_packet)] + self._send_player_vision(player_id, locations)

    def player_leave(self, player_id):
        assert player_id in self.players
        location, player = self._find_player(player_id)

        self.world[location].remove(player)
        self.players.remove(player_id)

        # Later, we might make them blow up
        return self._mark_dirty([location])

    def _determine_can_see(self, coord, direction):
        return Constants.VISION[self.vision](self.world, coord, direction)

    def _send_player_vision(self,player_id, locations):
        location, player = self._find_player(player_id)

        packet = packet_pb2.Packet()
        packet.packet_id = get_id('packet')
        packet.payload_types.append(Constants.VISION_UPDATE)
        packet.vision_game_id = self.id

        # Now to pack the objects
        attributes = []

        for coord in locations:
            if coord not in self.world:
                continue

            for object in self.world[coord]:
                x,y,obj_type,attribute = network_pack_object(coord,object)
                if attribute is None:
                    attr_id = -1
                else:
                    attributes.append(attribute)
                    attr_id = len(attributes) - 1

                packet.objects.extend([x,y,obj_type,attr_id])

        packet.attributes.extend(attributes)

        return [(player_id, packet)]

    def find_obj_locations(self, obj_type):
        locations = []
        for coord, objects in self.world.items():
            for obj, attr in objects:
                if obj == obj_type:
                    locations.append(coord)
                    break

        return locations

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

        assert location is not None

        return location, player

    def player_action(self, player_id, action, argument):
        assert player_id in self.players
        # Translate into internal constants
        cmd = Constants.from_numerical_constant(action)
        arg = Constants.from_numerical_constant(argument)

        location, player = self._find_player(player_id)

        handlers = {
            Constants.CMD_LOOK: self._look,
            Constants.CMD_MOVE: self._move
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

            new_can_see = self._determine_can_see(new_location, direction)
            new_vision_packets = self._send_player_vision(player_id,
                                                          new_can_see)
            return dirty_packets + new_vision_packets


    def _mark_dirty(self, coordinates):
        # Vision is assumed to be reciprical, so if a player is in
        # sight of a dirty coordinate, then he needs to be updated
        packets = []
        for player_id in self.players:
            location, playerobj = self._find_player(player_id)
            direction = playerobj[1]['direction']
            player_vision = self._determine_can_see(location, direction)

            dirty_locations = set(player_vision) & set(coordinates)

            if dirty_locations:
                packets.extend(self._send_player_vision(player_id,
                                                        dirty_locations))

        return packets

    def tick(self):
        # Do anything that occurs independently of network input
        pass

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

    WALLS = (OBJ_WALL, OBJ_HORIZONTAL_WALL, OBJ_VERTICAL_WALL, OBJ_CORNER_WALL)
    HISTORICAL_OBJECTS = WALLS + (OBJ_EMPTY,)
    SOLID_OBJECTS = WALLS + (OBJ_PLAYER,)

    VISION = {
        'basic': vision_basic,
        'cone': vision_cone,
    }
    @classmethod
    def to_numerical_constant(cls,constant):
        constants = list(vars(cls).values())
        constants.sort()
        return constants.index(constant)
    @classmethod
    def from_numerical_constant(cls,number):
        constants = list(vars(cls).values())
        constants.sort()
        return constants[number]


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
        client_main()
