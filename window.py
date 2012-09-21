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
    data['network'] = FakeNetwork()
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

def display_character(object, history=False):
    display_chr = None
    # Default colour
    colour = curses.color_pair(0)

    obj, attr = object

    display_chr = {
        Constants.OBJ_WALL: '#',
        Constants.OBJ_PLAYER: '@',
        Constants.OBJ_EMPTY: '.'}[obj]

    if obj == Constants.OBJ_PLAYER:
        colour = curses.color_pair(1)
        direction = attr['direction']

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
        return (0,0)

    def get_visible(self):
        visible = self.known_world
        history = {}
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

        for x,y,obj_type,attr_id in grouper(4, packet.objects):
            assert None not in (x,y,obj_type,attr_id)
            obj_type = Constants.from_numerical_constant(obj_type)
            if attr_id == -1:
                attr = {}
            else:
                attr = unpacked_attributes[attr_id].copy()

            if (x,y) not in self.known_world:
                self.known_world[x,y] = []

            self.known_world[x,y].append((obj_type, attr))

    def _keep_alive(self, packet, addr):
        pass

    def _game_status(self, packet, addr):
        if packet.status == Constants.STATUS_JOINED:
            self.game_id = packet.status_game_id
        elif packet.status == Constants.STATUS_LEFT:
            self.game_id = None


def grouper(n, iterable, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return itertools.izip_longest(fillvalue=fillvalue, *args)

class FakeNetwork(object):
    def __init__(self):
        self.generate_world()

    def generate_world(self,seed=0):
        self.world = purerandom_map(seed=seed)
        player = (Constants.OBJ_PLAYER,
                  {'number':0, 'direction': Constants.RIGHT, 'colour':1})
        # DEBUG Give player "seen" for whole world
        player[1]['seen'] = set(self.world)

        self.world[40,10] = [(Constants.OBJ_EMPTY, {}), player]

    def update(self):
        # Do network things
        pass
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

    def send_command(self, cmd, arg):
        player_number = 0

        command = cmd
        args = arg

        location, player = self._find_player(player_number)

        handlers = {
            Constants.CMD_LOOK: self._look,
            Constants.CMD_MOVE: self._move
        }

        handlers[command](player, location, args)

    def _look(self, player, location, args):
        direction = args[0]
        player[1]['direction'] = direction

    def _move(self, player, location, args):
        self.world[location].remove(player)

        diff = Constants.DIFFS[args[0]]

        new_location = (location[0] + diff[0], location[1] + diff[1])
        moved = False

        if new_location in self.world:
            # If the area is empty
            if Constants.OBJ_EMPTY in [oa[0] for oa in self.world[new_location]]:
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

        main_direction = Constants.DIFFS[attr['direction']]
        behind_you = main_direction[0] * -1, main_direction[1] * -1

        visible.add((location[0] + behind_you[0], location[1] + behind_you[1]))


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
                if Constants.OBJ_WALL in [o[0] for o in objects]:
                    break
            return v

        for direction in Constants.ADJACENT[attr['direction']]:
            visible.update(look_until_wall(location,
                                           Constants.DIFFS[direction]))

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
                if obj in (Constants.OBJ_WALL, Constants.OBJ_EMPTY):
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
        g = Game(20,"empty","Default","ffa",get_id('game'))
        self.games.append(g)

        self.seen_ids = []

        self.clients = {}

        self.socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)


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


                    self.clients[addr]['last heard from'] = time.time()

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
        max_players = packet.max_players or 20
        map_generator = packet.map_generator or "purerandom"
        game_name = packet.new_game_name or "Unnamed"
        game_mode = packet.new_game_mode or "ffa"
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
    }
    def __init__(self,max_players,map_generator,name,mode,id):
        self.max_players = max_players
        self.world = self.MAP_GENERATORS[map_generator]()
        self.name = name
        self.mode = mode
        self.id = id

        self.players = []
        self._player_coords = {}

    @property
    def current_players(self):
        return 0

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

        locations = self._determine_can_see(spawn_coord)

        return [(player_id, join_packet)] + self._send_player_vision(player_id, locations)

    def _determine_can_see(self, coord):
        return neighbourhood(coord,n=3)

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
        moved = False

        if new_location in self.world:
            # If the area is empty
            if Constants.OBJ_EMPTY in [oa[0] for oa in self.world[new_location]]:
                self.world[new_location].append(player)
                moved = True

        if not moved:
            # Player can't move to that location, no move
            self.world[location].append(player)
            return []
        else:
            player_id = player[1]['number']
            dirty_packets = self._mark_dirty([old_location,new_location])


            new_can_see = self._determine_can_see(new_location)
            new_vision_packets = self._send_player_vision(player_id,
                                                          new_can_see)
            return dirty_packets + new_vision_packets


    def _mark_dirty(self, coordinates):
        # Vision is assumed to be reciprical, so if a player is in
        # sight of a dirty coordinate, then he needs to be updated
        packets = []
        for player_id in self.players:
            location, playerobj = self._find_player(player_id)
            player_vision = self._determine_can_see(location)

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
    OBJ_PLAYER = "player"
    OBJ_EMPTY = "empty"
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
        main()
