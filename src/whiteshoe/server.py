from __future__ import print_function

import datetime
import select
import socket
import random
import itertools
import traceback
import sys
import copy
import argparse
import fractions
import operator
import time

import constants
import packet_pb2
from utility import (neighbourhood, get_id, bytes_to_human, dict_difference,
                     cardinal_neighbourhood)
import utility

def server_main(args=None):
    # Ignore arguments for now
    p = argparse.ArgumentParser()
    p.add_argument('-v','--vision',default='cone')
    p.add_argument('-m','--map',default='depth_first')
    p.add_argument('-q','--quiet',action='store_true',default=False)
    p.add_argument('-d','--debug',action='store_true')
    ns = p.parse_args(args)
    s = Server(vars(ns))
    s.serve()

class Server(object):

    def __init__(self,options):
        self.handlers = {
            constants.GET_GAMES_LIST: self._get_games_list,
            # games running (s->c)
            constants.MAKE_NEW_GAME: self._make_new_game,
            constants.ERROR: self._error,
            constants.GAME_ACTION: self._game_action,
            constants.JOIN_GAME: self._join_game,
            # vision update (s->c)
            constants.KEEP_ALIVE: self._keep_alive,
            constants.DISCONNECT: self._disconnect_packet,

        }
        self.port = constants.DEFAULT_PORT

        self.games = []

        # Debug starting game
        g = Game(vision=options.get('vision','cone'),
                 map_generator=options.get('map','purerandom'))
        self.games.append(g)

        self.display_stats = not options['quiet']
        self.debug = options['debug']

        self.seen_ids = []

        self.clients = {}

        self.socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

        self.timeout = constants.TIMEOUT

        self.stats = {'packets_sent':0,
                      'packets_recieved':0,
                      'bytes_sent':0,
                      'bytes_recieved':0}


    def serve(self):
        self.socket.bind(('',self.port))

        while True:
            try:
                for game in self.games:
                    packets = game.tick()
                    self._send_packets(packets)

                for addr in list(self.clients):
                    last_heard = self.clients[addr]['last_heard']
                    last_sent = self.clients[addr]['last_sent']
                    player_id = self.clients[addr]['player_id']

                    if last_heard.elapsed_seconds > self.timeout:
                        reason = constants.DISCONNECT_TIMEOUT
                        self._disconnect_client(addr, reason)

                    elif last_sent.elapsed_seconds > constants.KEEPALIVE_TIME:
                        p = packet_pb2.Packet()
                        p.packet_id = get_id('packet')
                        p.payload_types.append(constants.KEEP_ALIVE)
                        p.timestamp = int(time.time())

                        self._send_packets(((player_id, p),))


                rlist, wlist, xlist = select.select((self.socket,),(),(),0.05)

                if self.display_stats:
                    display_stats(self.stats)


                for rs in rlist:
                    data, addr = rs.recvfrom(4096)

                    packet = packet_pb2.Packet.FromString(data)
                    self.stats['packets_recieved'] += 1
                    self.stats['bytes_recieved'] += len(data)

                    #print("Recv: {}".format(packet))

                    if addr not in self.clients:
                        self.clients[addr] = {
                            'player_id': get_id('player'),
                            'last_heard': utility.Stopwatch(start=True),
                            'last_sent': utility.Stopwatch(start=True)
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

                    # The handler could have "disconnected" them
                    if addr in self.clients:
                        self.clients[addr]['last_heard'].restart()


            except KeyboardInterrupt:
                if self.display_stats:
                    # Print an extra newline, because of the live statistics
                    print()
                # TODO Notify all connected clients of server shutdown
                break

            except Exception as e:
                if self.debug:
                    # If we're debugging, then the server can crash
                    raise
                else:
                    traceback.print_exc()

    def _send_packets(self, packets):
        for packet_player_id, packet in packets:
            # Determine addr
            sent = False
            for addr, addr_dict in self.clients.items():
                if packet_player_id == addr_dict['player_id']:
                    #print("Sent: {}".format(packet))
                    self.socket.sendto(packet.SerializeToString(), addr)
                    self.stats['packets_sent'] += 1
                    self.stats['bytes_sent'] += packet.ByteSize()
                    addr_dict['last_sent'].restart()
                    sent = True
                    break

            assert sent

    def _get_games_list(self, packet, addr):
        reply = packet_pb2.Packet()
        reply.payload_types.append(constants.GAMES_RUNNING)

        reply.packet_id = get_id('packet')

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
        game_mode = packet.new_game_mode or None
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

        name = packet.player_name or None
        team = packet.player_team or None

        packets = game.player_join(self.clients[addr]['player_id'],
                                  name=name, team=team)
        self._send_packets(packets)

    def _error(self, packet, addr):
        # Handle silently.
        pass

    def _game_action(self, packet, addr):
        player_id = self.clients[addr]['player_id']
        game_id = packet.action_game_id

        game = [g for g in self.games if g.id == game_id][0]

        if not game.is_player_in_game(player_id):
            p = packet_pb2.Packet()
            p.packet_id = get_id('packet')
            p.payload_types.append(constants.ERROR)
            p.error_type = constants.ERROR_NOT_IN_GAME
            self._send_packets(((player_id, p),))
        else:
            action = packet.action
            arguments = packet.argument

            packets = game.player_action(player_id, action, arguments)
            self._send_packets(packets)

    def _keep_alive(self, packet, addr):
        pass

    def _disconnect_packet(self, packet, addr):
        self._disconnect_client(addr, packet.disconnect_code or None)

    def _disconnect_client(self, addr, reason=None):
        last_heard = self.clients[addr]['last_heard']
        last_sent = self.clients[addr]['last_sent']
        player_id = self.clients[addr]['player_id']

        for game in self.games:
            if game.is_player_in_game(player_id):
                packets = game.player_leave(player_id)
                self._send_packets(packets)

        del self.clients[addr]


def display_stats(stats):
    fmt = "\rNumber Sent: {0}, Number Recieved: {1}, Sent: {2}, Recieved: {3}"
    s = fmt.format(stats['packets_sent'],
                   stats['packets_recieved'],
                   bytes_to_human(stats['bytes_sent']),
                   bytes_to_human(stats['bytes_recieved']))

    sys.stderr.write(s)
    sys.stderr.flush()

def map_purerandom(X=80,Y=24,seed=0):
    world = {}
    r = random.Random(seed)

    for i,j in itertools.product(range(X), range(Y)):
        if r.random() < 0.35:
            world[i,j] = [(constants.OBJ_WALL, {})]
        else:
            world[i,j] = [(constants.OBJ_EMPTY, {})]

    return world

def map_empty(X=80,Y=24,seed=None):
    world = {}

    for i,j in itertools.product(range(X), range(Y)):
        world[i,j] = [(constants.OBJ_EMPTY, {})]
    return world

def map_ca_maze(X=80,Y=24,seed=1):
    ca_world = utility.CellularAutomaton(X, Y)
    r = random.Random(seed)
    ca_world.seed(0.35,rng=r)
    ca_world.converge('3/12345')

    return utility.ca_world_to_world(ca_world)

def map_ca_caves(X=80,Y=24,seed=1):
    ca_world = utility.CellularAutomaton(X, Y)
    r = random.Random(seed)
    ca_world.seed(0.5, rng=r)
    ca_world.converge('678/345678', boundary = True)

    # Now the maze CA tends to generate isolated islands
    return utility.ca_world_to_world(ca_world)

def map_depth_first(X=80, Y=24, seed=0):
    r = random.Random(seed)

    # The division by 2 will be important later
    cells = list(itertools.product(range(X//2), range(Y//2)))

    initial_cell = r.choice(cells)
    current_cell = initial_cell

    visited = set()
    visited.add(current_cell)

    removed_walls = set()

    stack = []

    while set(cells) - visited:
        neighbours = set(cardinal_neighbourhood(current_cell))
        neighbours &= set(cells)

        # If the current cell has any neighbours which have not been visited
        if neighbours - visited:
            # Choose random one of the unvisited neighbours
            neighbour = r.choice(list(neighbours - visited))
            # Push the current cell to the stack
            stack.append(current_cell)
            # Remove the wall between the current cell and the chosen cell
            removed_walls.add((neighbour, current_cell))
            # Make the chosen cell the current cell and mark it as visited
            current_cell = neighbour
            visited.add(current_cell)
        elif stack:
            current_cell = stack.pop()
        else:
            current_cell = r.choice(cells)
            visited.add(current_cell)

    # Now we have a number of eliminated walls
    world = {}

    for x,y in itertools.product(range(X), range(Y)):
        world[x,y] = [(constants.OBJ_WALL, {})]

    for point_a, point_b in removed_walls:
        real_a = (point_a[0] * 2, point_a[1] * 2)
        real_b = (point_b[0] * 2, point_b[1] * 2)

        # The removed wall is the shared neighbourhood between them
        shared = set(cardinal_neighbourhood(real_a))
        shared &= set(neighbourhood(real_b))

        assert len(shared) == 1

        world[shared.pop()] = [(constants.OBJ_EMPTY, {})]
        world[real_a] = [(constants.OBJ_EMPTY, {})]
        world[real_b] = [(constants.OBJ_EMPTY, {})]

    return world

def pretty_walls(world):
    for coord, objects in world.items():
        if objects[0][0] == constants.OBJ_EMPTY:
            continue

        vertical = False
        horizontal = False

        for neighbour in ((coord[0], coord[1] - 1), (coord[0], coord[1] + 1)):
            if neighbour not in world:
                continue
            if world[neighbour][0][0] != constants.OBJ_EMPTY:
                vertical = True
                break

        for neighbour in ((coord[0] - 1, coord[1]), (coord[0] + 1, coord[1])):
            if neighbour not in world:
                continue
            if world[neighbour][0][0] != constants.OBJ_EMPTY:
                horizontal = True
                break

        if not vertical and not horizontal:
            # Do nothing
            continue
        elif vertical and not horizontal:
            del objects[0]
            objects.append((constants.OBJ_VERTICAL_WALL, {}))
        elif not vertical and horizontal:
            del objects[0]
            objects.append((constants.OBJ_HORIZONTAL_WALL, {}))
        elif vertical and horizontal:
            del objects[0]
            objects.append((constants.OBJ_CORNER_WALL, {}))

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
            if obj in constants.ALWAYS_VISIBLE_OBJECTS:
                if coord not in visible_world:
                    visible_world[coord] = []
                visible_world[coord].append((obj,dict(attr)))
    return visible_world

def vision_square(world, start_coord, direction=None):
    visible = neighbourhood(start_coord, n=3)
    return visible

def vision_cone(world, coord, direction=None):
    visible = set()
    # The square you are in is always visible as well one square
    # behind you
    visible.add(coord)

    main_direction = constants.DIFFS[direction]
    behind_you_direction = main_direction[0] * -1, main_direction[1] * -1

    behind_you = (coord[0] + behind_you_direction[0],
                  coord[1] + behind_you_direction[1])

    if behind_you in world:
        visible.add(behind_you)


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
                if o[0] in constants.OPAQUE_OBJECTS:
                    running = False
        return v

    for direction in constants.ADJACENT[direction]:
        visible.update(look_until_wall(coord,
                                       constants.DIFFS[direction]))

    return visible

def vision_all(world, coord, direction=None):
    visible_coords = set(world)
    return visible_coords

def vision_rays(world, coord, direction=None):
    class Transform2D(object):
        def __init__(self):
            self.m00 = 1
            self.m01 = 0
            self.m10 = 0
            self.m11 = 1
            self.tX = 0
            self.tY = 0

        @classmethod
        def translate(cls, x, y):
            instance = cls()
            instance.tX = x
            instance.tY = y
            return instance

        @classmethod
        def linear(cls, m00, m01, m10, m11):
            instance = cls()
            instance.m00 = m00
            instance.m01 = m01
            instance.m10 = m10
            instance.m11 = m11
            return instance

        def apply(self, x, y):
            return x*self.m00 + y*self.m01 + self.tX, x*self.m10 + y*self.m11 + self.tY

        def __mul__(self, x):
            new_instance = self.__class__()
            new_instance.m00 = self.m00*x.m00 + self.m01*x.m10
            new_instance.m01 = self.m00*x.m01 + self.m01*x.m11
            new_instance.m10 = self.m10*x.m00 + self.m11*x.m10
            new_instance.m11 = self.m10*x.m01 + self.m11*x.m11
            new_instance.tX = self.tX + x.tX*self.m00 + x.tY*self.m01
            new_instance.tY = self.tY + x.tX*self.m10 + x.tY*self.m11
            return new_instance

        def inverse(self):
            determinant = self.m00*self.m11 - self.m01*self.m10
            new_instance = self.__class__()
            new_instance.m00 = self.m11/determinant
            new_instance.m01 = -self.m01/determinant
            new_instance.m10 = -self.m10/determinant
            new_instance.m11 = self.m00/determinant
            new_instance.tX = (self.tY*self.m01 - self.tX*self.m11)/determinant
            new_instance.tY = -(self.tY*self.m00 - self.tX*self.m10)/determinant
            return new_instance

        def __str__(self):
            return '[[{0} {1} {4}] [{2} {3} {5}]]'.format(*(self.m00, self.m01, self.m10, self.m11, self.tX, self.tY))

    def bresenham_line(a, b):
        yield a
        x0, y0 = a
        x1, y1 = b
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx - dy

        while (x0, y0) != (x1, y1):
            e2 = 2*error
            if e2 > -dy:
                error -= dy
                x0 += sx
            if e2 < dx:
                error += dx
                y0 += sy
            yield x0, y0

    # Optimisation: track whether blocks are impeded
    impedances = {}
    def is_impeded(point):
        previous_impedance = impedances.get(point)
        if previous_impedance is not None:
            return previous_impedance
        x, y = point
        impeded = any(obj[0] in constants.OPAQUE_OBJECTS
                          for obj
                          in world.get(camera_to_world.apply(x, y), ()))
        impedances[point] = impeded
        return impeded

    def neighbours(point):
        x, y = point
        yield x + 1, y
        yield x - 1, y
        yield x, y + 1
        yield x, y - 1

    def right_points_by_distance(max_radius):
        yield 0, 0
        half_radius = max_radius // 2
        for n in xrange(1, max_radius + 1):
            # Handle the on-axis cases
            yield 0, n
            yield n, 0
            yield 0, -n
            for i in xrange(1, half_radius):
                yield i, n
                yield n, i
                yield n, -i
                yield i, -n
            # Handle the corners
            yield half_radius, n
            yield half_radius, -n

    # Parameters
    MAX_RADIUS = 15
    Y_RADIUS_SCALE = 3
    APPROXIMATION_ACCURACY = 3
    # End of parameters
    MAX_RADIUS_SQUARED = MAX_RADIUS*MAX_RADIUS
    coord_matrix = Transform2D.translate(-coord[0], -coord[1])
    rotate_matrix = {constants.RIGHT: Transform2D.linear(1, 0, 0, 1),
                     constants.LEFT:  Transform2D.linear(-1, 0, 0, -1),
                     constants.UP:    Transform2D.linear(0, -1, 1, 0),
                     constants.DOWN:  Transform2D.linear(0, 1, -1, 0)}[direction]
    world_to_camera = rotate_matrix * coord_matrix
    camera_to_world = world_to_camera.inverse()
    outputs = [camera_to_world.apply(x, y) for x in (0, -1) for y in (-1, 0, 1)]
    # Scale x and y so it 'looks right' - compensating for characters being taller than wide
    x_scale = Y_RADIUS_SCALE if direction in (constants.UP, constants.DOWN) else 1
    y_scale = Y_RADIUS_SCALE if direction in (constants.LEFT, constants.RIGHT) else 1
    # Optimisation: keep track of blocked directions
    blocked_directions = {}
    potential_corners = []
    for x, y in right_points_by_distance(MAX_RADIUS):
        # Early exit: discard any points outside the maximum radius
        radius_squared = x_scale*x*x + y_scale*y*y
        if radius_squared > MAX_RADIUS_SQUARED:
            continue
        # Peripheral vision limits
        if x == 0 and abs(y) > MAX_RADIUS*(1.0/3.0):
            continue
        if x == 1 and abs(y) > MAX_RADIUS*(2.0/3.0):
            continue
        direction_fraction = fractions.Fraction(x, y).limit_denominator(APPROXIMATION_ACCURACY) if y != 0 else None
        block_distance_squared = blocked_directions.get(direction_fraction, float('inf'))
        if radius_squared > block_distance_squared:
            continue
        # Use bresenham
        is_visible = True
        for point in bresenham_line((0, 0), (x, y)):
            if point in ((0, 0), (x, y)):
                continue
            if is_impeded(point):
                is_visible = False
                break
        if is_visible:
            outputs.append(camera_to_world.apply(x, y))
        # Handle the - ah-hah - corner case
        elif is_impeded((x, y)) and y:
            # Determine if this is a corner
            if len([point for point in neighbours((x, y))
                              if is_impeded(point)]) in (1, 2, 3):
                # At least 2 wall nearby, this is a corner or edge
                potential_corners.append((x, y))
        else:
            blocked_directions[direction_fraction] = radius_squared

    # Add potential corners
    for x, y in potential_corners:
        if len([point for point in neighbours((x, y))
                          if camera_to_world.apply(*point) in outputs]) >= 2:
            outputs.append(camera_to_world.apply(x, y))
    return set(outputs) & set(world.iterkeys())

def vision_projectx(world, coord, direction):

    return visible_coords

def network_pack_object(coord, object):
    x,y = coord
    obj_type, obj_attr = object
    obj_type = constants.to_numerical_constant(obj_type)

    if obj_attr == {}:
        attribute = None
    else:
        attribute = packet_pb2.Packet.Attribute()
        for key in constants.ATTRIBUTE_KEYS:
            if key in obj_attr:
                value = obj_attr[key]
                if key in constants.ATTRIBUTE_CONSTANT_KEYS:
                    value = constants.to_numerical_constant(value)
                setattr(attribute, key, value)

    return x,y,obj_type,attribute

def pack_attribute(obj_attr):
    attribute = packet_pb2.Packet.Attribute()
    for key in constants.ATTRIBUTE_KEYS:
        if key in obj_attr:
            value = obj_attr[key]
            if key in constants.ATTRIBUTE_CONSTANT_KEYS:
                value = constants.to_numerical_constant(value)
            setattr(attribute, key, value)
    return attribute

class Game(object):
    MAP_GENERATORS = {
        'purerandom': map_purerandom,
        'empty': map_empty,
        'ca_maze': map_ca_maze,
        'ca_caves': map_ca_caves,
        'depth_first': map_depth_first,
    }
    VISION_FUNCTIONS = {
        'square': vision_square,
        'cone': vision_cone,
        'all': vision_all,
        'ray': vision_rays,
    }
    def __init__(self,max_players=20,map_generator='purerandom',
                 name='Untitled',mode='ffa',id=None,vision='basic'):

        # Same as random module for now, later we can change it
        self.random = random.Random(0)

        self.max_players = max_players

        generator = self.MAP_GENERATORS[map_generator]
        self.world = generator(seed=self.random.random())

        #self.world = pretty_walls(self.world)

        print("World ({0}) generated.".format(map_generator))
        self.name = name
        self.mode = mode

        if id is None:
            id = get_id('game')

        self.id = id

        self.vision = vision

        self.players = []
        self.known_worlds = {}
        self.events = []

        self.tick_stopwatch = utility.Stopwatch()

        # Dirty stuff
        self._dirty_coords = set()
        self._dirty_players = set()

    def get_vision(self):
        return self._vision

    def set_vision(self, value):
        # This *will* throw an exception if value is not present,
        # later FIXME we can put a proper exception, but this will do for now.
        self.VISION_FUNCTIONS[value]
        self._vision = value

    vision = property(get_vision, set_vision)

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
        for obj_type in constants.SOLID_OBJECTS:
            suitable -= set(self.find_obj_locations(obj_type))

        spawn_coord = self.random.choice(list(suitable))
        suitable.remove(spawn_coord)
        self._mark_dirty_cell(spawn_coord)
        self._mark_dirty_player(player_id)
        direction = self.random.choice(constants.DIRECTIONS)

        start_max_hp = 10
        start_ammo = 10

        player = (constants.OBJ_PLAYER,
                  {'player_id':player_id,
                   'direction': direction,
                   'team':player_id,
                   'hp':start_max_hp,
                   'hp_max': start_max_hp,
                   'ammo' : start_ammo})

        self.world[spawn_coord].append(player)

        # And now, some mines
        for mine_size in (1,2):
            # symbols are ; and g
            mine_coord = self.random.choice(list(suitable))
            suitable.remove(mine_coord)

            mine = (constants.OBJ_MINE, {'size': mine_size})

            self.world[mine_coord].append(mine)
            self._mark_dirty_cell(mine_coord)

        # And increase all ammo for all players by 5
        # including the new player
        for coord, player in self.find_objs(constants.OBJ_PLAYER):
            player[1]['ammo'] += 5
            self._mark_dirty_cell(coord)

        return spawn_coord, player

    def _remove_player(self, player_id):
        location, player = self._find_player(player_id)

        self.world[location].remove(player)

        self._mark_dirty_cell(location)

        return location, player

    def _kill_player(self, player_id):
        old_location, old_player = self._remove_player(player_id)

        self.known_worlds[player_id] = {}

        event_type = constants.STATUS_DEATH
            # TODO later put the damage type, and MAYBE the person
            # responsible
        responsible_id = -1
        damage_type = 0
        event = (player_id, event_type, responsible_id, damage_type)
        self.events.append(event)

        new_location, player = self._spawn_player(player_id)

        old_attr = old_player[1]
        new_attr = player[1]

        for attribute in ('name','team'):
            if attribute in old_attr:
                new_attr[attribute] = old_attr[attribute]

    def player_join(self,player_id,name=None,team=None):
        assert player_id not in self.players

        self.players.append(player_id)
        self.known_worlds[player_id] = {}

        location, player = self._spawn_player(player_id)

        attr = player[1]
        if name is not None:
            attr['name'] = name
        if team is not None:
            attr['team'] = team


        join_packet = packet_pb2.Packet()
        join_packet.packet_id = get_id('packet')
        join_packet.payload_types.append(constants.GAME_STATUS)

        join_packet.status_game_id = self.id
        join_packet.status = constants.STATUS_JOINED
        join_packet.your_player_id = player_id
        join_packet.game_name = self.name
        join_packet.game_mode = self.mode
        join_packet.game_max_players = self.max_players
        join_packet.game_current_players = self.current_players
        join_packet.game_vision = self.vision

        direction = player[1]['direction']

        packets = [(player_id, join_packet)]

        packets.extend(self._flush_dirty())

        return packets

    def _update_known_world(self, player_id, visible, dirty):
        known_world = self.known_worlds[player_id]

        intersection_coords = visible & dirty

        av_coords = self.find_obj_locations(*constants.ALWAYS_VISIBLE_OBJECTS)

        changed = set()

        # Intersection coords are in direct vision.
        # so we don't historify them, we merely replace the contents
        # of them

        for coord in set(known_world) - visible:
            assert coord in self.world

            to_remove = []

            for obj,attr in known_world[coord]:
                if obj in constants.HISTORICAL_OBJECTS:
                    marked_historical = attr.get('historical', False)

                    if not marked_historical:
                        attr['historical'] = True
                        changed.add(coord)
                else:
                    to_remove.append((obj,attr))
                    changed.add(coord)

            for doomed in to_remove:
                known_world[coord].remove(doomed)

        for coord in intersection_coords:
            assert coord in self.world

            if known_world.get(coord) == self.world[coord]:
                # No change.
                continue
            else:
                known_world[coord] = new_contents = []
                for obj,attr in self.world[coord]:
                    new_contents.append((obj, attr.copy()))
                changed.add(coord)

        for coord in dirty | set(av_coords):
            for obj,attr in self.world[coord]:
                if obj in constants.ALWAYS_VISIBLE_OBJECTS:
                    if coord not in known_world:
                        known_world[coord] = []
                    known_world[coord].append((obj,dict(attr)))
                    changed.add(coord)

        assert changed <= set(self.world)

        return changed


    def player_leave(self, player_id):
        assert player_id in self.players

        try:
            location, player = self._find_player(player_id)
        except PlayerNotFound:
            pass
        else:
            self._remove_player(player_id)

        del self.known_worlds[player_id]
        self.players.remove(player_id)

        return self._flush_dirty()

    def _determine_can_see(self, coord, direction):
        vision_func = self.VISION_FUNCTIONS[self.vision]

        coords = vision_func(self.world, coord, direction)

        #visible_world = _visible_world(self.world, coords)
        assert coords <= set(self.world)

        return coords

    def _send_player_vision(self,player_id, coords, all=False):
        #location, player = self._find_player(player_id)

        known_world = self.known_worlds[player_id]

        packets = []

        def gen_packet():
            packet = packet_pb2.Packet()
            packet.packet_id = get_id('packet')
            packet.payload_types.append(constants.VISION_UPDATE)
            packet.vision_game_id = self.id
            return packet

        current_packet = gen_packet()
        if all:
            current_packet.clear_all = True

        for coord in coords:
            if current_packet.ByteSize() > constants.PACKET_SIZE_LIMIT:
                packets.append(current_packet)
                current_packet = gen_packet()

            if coord not in self.world:
                continue
            if coord not in known_world:
                continue

            if known_world[coord] == []:
                x,y = coord
                obj_type = -1
                attr_id = -1
                current_packet.objects.extend([x,y,obj_type,attr_id])

            else:
                for object in known_world[coord]:
                    x,y = coord
                    obj_type, obj_attr = object
                    obj_type = constants.to_numerical_constant(obj_type)
                    if obj_attr == {}:
                        attr_id = -1
                    else:
                        packed = pack_attribute(obj_attr)
                        attr_id = len(current_packet.attributes)
                        current_packet.attributes.extend([packed])

                    current_packet.objects.extend([x,y,obj_type,attr_id])

        packets.append(current_packet)

        #TODO For each packet, make sure that identical Attributes are
        # compressed down to one, and the corresponding attr_ids are changed.

        out = [(player_id, packet) for packet in packets]
        return out

    def find_objs(self, *obj_types):
        locations = []
        for coord, objects in self.world.items():
            for obj, attr in objects:
                if obj in obj_types:
                    pair = (coord, (obj,attr))
                    locations.append(pair)

        return locations

    def find_obj_locations(self, *obj_types):
        locations = self.find_objs(*obj_types)
        return [pair[0] for pair in locations]

    def _find_player(self, player_id):
        # Find player location
        location = None

        for coord, object in self.find_objs(constants.OBJ_PLAYER):
            if object[1]['player_id'] == player_id:
                location = coord
                player = object
                break

        if location is None:
            raise PlayerNotFound

        return location, player

    def _player_death(self, player_id):
        location, player = self._find_player(player_id)

    def player_action(self, player_id, action, argument):
        assert player_id in self.players
        # Translate into internal constants
        cmd = constants.from_numerical_constant(action)
        arg = constants.from_numerical_constant(argument)

        try:
            location, player = self._find_player(player_id)
        except PlayerNotFound:
            location = None
            player = None

        handlers = {
            constants.CMD_LOOK: self._look,
            constants.CMD_MOVE: self._move,
            constants.CMD_FIRE: self._fire,
        }

        handlers[cmd](player, location, arg)

        packets = []
        packets.extend(self._event_check())
        packets.extend(self._flush_dirty())

        return packets

    def _look(self, player, location, arg):
        player[1]['direction'] = arg
        player_id = player[1]['player_id']
        self._mark_dirty_cell(location)
        self._mark_dirty_player(player_id)

    def _move(self, player, location, arg):
        # We'll return this list later
        self.world[location].remove(player)

        diff = constants.DIFFS[arg]

        old_location = location
        new_location = (location[0] + diff[0], location[1] + diff[1])
        can_move = True

        if new_location not in self.world:
            can_move = False
        else:
            for obj,attr in self.world[new_location]:
                # If the area is empty
                if obj in constants.SOLID_OBJECTS:
                    can_move = False
                    break


        if not can_move:
            # Player can't move to that location, no move
            self.world[location].append(player)

            if new_location in self.world:
                # Special case stabbing things.
                for object in self.world[new_location]:
                    if object[0] in constants.CAN_STAB:
                        self._damage_object(new_location, object,
                                            constants.STAB_DAMAGE)
                        self._mark_dirty_cell(new_location)

        else:
            player_id = player[1]['player_id']
            direction = player[1]['direction']

            self.world[new_location].append(player)

            for cell in (old_location, new_location):
                self._mark_dirty_cell(cell)
            self._mark_dirty_player(player_id)

            self._move_into(player, old_location, new_location)

    def _fire(self, player, location, arg):
        direction = player[1]['direction']
        player_id = player[1]['player_id']

        if arg in (constants.SMALL_SLIME, constants.BIG_SLIME):
            ammo_cost = constants.SLIME_COSTS[arg]

            if player[1]['ammo'] >= ammo_cost:
                player[1]['ammo'] -= ammo_cost
            else:
                # Nothing fires
                return []

            attr = {'owner': player_id, 'direction': direction, 'size': arg}
            bullet = (constants.OBJ_SLIME_BULLET, attr)

        else:
            power = arg

            attr = player[1]

            ammo = attr['ammo']
            ammo_cost = power**2
            # Find how much ammo the player can spend
            while ammo_cost > ammo:
                power -= 1

                if power == 0:
                    # Nothing happens, not enough ammo
                    return []
                ammo_cost = power**2

            attr['ammo'] -= ammo_cost

            attr = {'owner': player_id, 'direction':direction, 'size':power}
            bullet = (constants.OBJ_BULLET, attr)

        diff = constants.DIFFS[direction]
        bullet_location = location

        self.world[bullet_location].append(bullet)

        self._mark_dirty_cell(bullet_location)

    def _move_into(self, player, old_location, new_location):
        diff = (new_location[0] - old_location[0],
                new_location[1] - old_location[1])

        direction = None
        if diff in constants.DIFFS.values():
            for direction, other_diff in constants.DIFFS.items():
                if other_diff == diff:
                    break

        for object in self.world[new_location]:
            obj, attr = object

            if object == player:
                continue
            elif obj == constants.OBJ_MINE:
                # If the direction of the movement is the same as the
                # look direction of the player, then the mine will most likely
                # not explode.
                player_diff = constants.DIFFS[player[1]['direction']]

                if direction == player[1]['direction']:
                    chance = constants.MINE_DIRECT_PROBABILITY

                # Backwards is the biggest chance.
                elif (diff[0] * -1, diff[1] * -1) == player_diff:
                    chance = constants.MINE_BACKWARDS_PROBABILITY

                # Side on is more chance of explosion.
                else:
                    chance = constants.MINE_SIDE_PROBABILITY

                size = attr['size']
                self.world[new_location].remove(object)

                # The chance is chance of NO EXPLOSION
                if chance > self.random.random():
                    # Disarm, the mine.
                    if size == 1:
                        ammo_increase = 1
                    elif size == 2:
                        ammo_increase = 5
                    else:
                        ammo_increase = 0

                    player[1]['ammo'] += ammo_increase
                else:
                    self._make_explosion(new_location, size)

    def _mark_dirty_cell(self, coord):
        self._dirty_coords.add(coord)

    def _mark_dirty_player(self, player_id):
        self._dirty_players.add(player_id)

    def _flush_dirty(self):
        packets = []

        # Return a number of packet tuples, in the form
        # (player_id, packet) generally vision packets, informing the player
        # of what has changed.
        for player_id in self.players:
            try:
                location, playerobj = self._find_player(player_id)
            except PlayerNotFound:
                # If player isn't present in the map, then we don't have
                # to worry about vision for them
                continue

            direction = playerobj[1]['direction']

            visible = self._determine_can_see(location, direction)
            # So we have the list of coordinates that are in direct vision

            dirty = self._dirty_coords

            changed = ()

            if player_id in self._dirty_players:
                # yes, for now, if a player is marked dirty, then we
                # just send his whole known world
                changed = self._update_known_world(player_id, visible, visible)

            else:
                changed = self._update_known_world(player_id, visible, dirty)

            if changed:
                p = self._send_player_vision(player_id, changed)
                packets.extend(p)

        self._dirty_players.clear()
        self._dirty_coords.clear()

        return packets

    def _event_check(self):
        packets = []
        p = None

        while self.events:
            event = self.events.pop()
            player_id = event[0]

            if p is None:
                p = packet_pb2.Packet()
                p.packet_id = get_id('packet')
                p.payload_types.append(constants.GAME_STATUS)
                p.status_game_id = self.id

            if event[1] in {constants.STATUS_DAMAGED, constants.STATUS_DEATH}:
                p.status = event[1]

                p.responsible_id = event[2]
                p.damage_type = event[3]

                packets.append((player_id, p))
                p = None

        return packets

    def tick(self):
        # Do anything that occurs independently of network input
        # like bullets moving
        if not self.tick_stopwatch.running:
            # Can't do anything on a tick until we know how much time
            # has passed
            self.tick_stopwatch.start()
            return ()

        elapsed = self.tick_stopwatch.restart()
        time_diff_s = elapsed.total_seconds()

        self._tick_bullets(time_diff_s)
        self._tick_explosions(time_diff_s)
        self._tick_slimes(time_diff_s)

        packets = []
        packets.extend(self._event_check())
        packets.extend(self._flush_dirty())
        return packets

    def _tick_bullets(self, time_passed):
        # Pair of (coord, object)
        bullets = self.find_objs(constants.OBJ_BULLET)
        for coord,object in bullets:
            attr = object[1]
            size = attr['size']
            speed = constants.BULLET_SPEEDS[size]

            if '_time_remaining' not in attr:
                attr['_time_remaining'] = speed

            attr['_time_remaining'] -= time_passed

            exploded = False
            while attr['_time_remaining'] < 0 and not exploded:
                attr['_time_remaining'] += speed


                self.world[coord].remove(object)
                self._mark_dirty_cell(coord)

                loc_diff = constants.DIFFS[object[1]['direction']]

                new_coord = (coord[0] + loc_diff[0], coord[1] + loc_diff[1])

                if new_coord not in self.world:
                    # Bullet just disappears.
                    break

                any_solid = any(o[0] in constants.SOLID_OBJECTS
                                for o in self.world[new_coord])

                if any_solid:
                    self._make_explosion(new_coord, size)
                    exploded = True

                if exploded:
                    break
                else:
                    # Bullet keeps moving
                    self.world[new_coord].append(object)
                    self._mark_dirty_cell(new_coord)
                    coord = new_coord
                    # Then the while loop may continue

    def _make_explosion(self, coord, size):
        for ex_coord in neighbourhood(coord,n=size-1):
            if ex_coord not in self.world:
                continue

            explosion = (constants.OBJ_EXPLOSION,
                         {'_damage':size**2})

            self.world[ex_coord].append(explosion)
            self._mark_dirty_cell(ex_coord)


    def _tick_explosions(self, time_passed):
        explosions = self.find_objs(constants.OBJ_EXPLOSION)
        for coord,explosion in explosions:
            attr = explosion[1]
            if '_time_left' not in attr:
                attr['_time_left'] = constants.EXPLOSION_LIFE
            if '_damaged' not in attr:
                attr['_damaged'] = []

            for object in list(self.world[coord]):
                if object[0] in constants.BLOWABLE_UP:
                    if object in attr['_damaged']:
                        continue
                    else:
                        attr['_damaged'].append(object)

                    self._damage_object(coord, object, attr['_damage'])
                    self._mark_dirty_cell(coord)

                    non_explosions = [o for o in self.world[coord]
                                      if o[0] != constants.OBJ_EXPLOSION]

                    if not non_explosions:
                        # If we've destroyed everything else,
                        # insert a new EMPTY into the world
                        empty = (constants.OBJ_EMPTY, {})
                        self.world[coord].insert(0,empty)

            attr['_time_left'] -= time_passed
            if attr['_time_left'] < 0:
                self.world[coord].remove(explosion)
                self._mark_dirty_cell(coord)

    def _tick_slimes(self, time_passed):
        slime_bullets = self.find_objs(constants.OBJ_SLIME_BULLET)

        for coord, bullet in slime_bullets:
            # Horrible reuse of explosion bullet code here,
            # TODO will need to see if we can combine it into some sort
            # of function
            attr = bullet[1]
            size = attr['size']
            speed = constants.SLIME_BULLET_SPEED[size]

            if '_time_remaining' not in attr:
                attr['_time_remaining'] = speed

            attr['_time_remaining'] -= time_passed

            explode = False
            while attr['_time_remaining'] < 0 and not explode:
                attr['_time_remaining'] += speed


                self.world[coord].remove(bullet)
                old_coord = coord
                self._mark_dirty_cell(coord)

                loc_diff = constants.DIFFS[bullet[1]['direction']]

                new_coord = (coord[0] + loc_diff[0], coord[1] + loc_diff[1])

                if new_coord not in self.world:
                    # Bullet just disappears.
                    break

                any_solid = any(o[0] in constants.SOLID_OBJECTS
                                for o in self.world[new_coord])

                if any_solid:
                    explode = True

                if explode:
                    # The bullet has been removed by this point
                    break
                else:
                    # Bullet keeps moving
                    self.world[new_coord].append(bullet)
                    self._mark_dirty_cell(new_coord)
                    old_coord = coord
                    coord = new_coord
                    assert old_coord != new_coord
                    # Then the while loop may continue
            if explode:
                slime_coord = old_coord
                # A new slime is born
                attr = {}
                attr['owner'] = bullet[1]['owner']
                attr['size'] = bullet[1]['size']
                attr['_spread_to'] = set([slime_coord])

                slime = (constants.OBJ_SLIME, attr)
                self.world[slime_coord].append(slime)
                self._mark_dirty_cell(slime_coord)
        # end for

        for coord, slime in self.find_objs(constants.OBJ_SLIME):
            attr = slime[1]
            #TODO kill slime after a bit
            if '_spread_time' not in attr:
                attr['_spread_time'] = constants.SLIME_SPREAD_TIME

            attr['_spread_time'] -= time_passed
            while attr['_spread_time'] < 0:
                attr['_spread_time'] += constants.SLIME_SPREAD_TIME
                neighbourhood = utility.cardinal_neighbourhood(coord)
                possible_locations = set(neighbourhood) - attr['_spread_to']
                possible_locations &= set(self.world)

                slime_spread = constants.SLIME_SPREAD[attr['size']]
                spreads_remaining = slime_spread - len(attr['_spread_to'])

                assert spreads_remaining >= 0

                # Slime can only spread to non-solid locations
                for location in list(possible_locations):
                    if any(obj[0] in constants.AIRTIGHT_OBJECTS
                           for obj in self.world[location]):
                        possible_locations.remove(location)

                if possible_locations and spreads_remaining:
                    spread_coord = self.random.choice(list(possible_locations))
                    attr['_spread_to'].add(spread_coord)
                    self._mark_dirty_cell(spread_coord)

                    new_attr = {}
                    new_attr['owner'] = slime[1]['owner']
                    new_attr['size'] = slime[1]['size']
                    new_attr['_spread_to'] = slime[1]['_spread_to']

                    slime = (constants.OBJ_SLIME, new_attr)
                    self.world[spread_coord].append(slime)


    def _damage_object(self, coord, object, amount):
        is_player = object[0] == constants.OBJ_PLAYER

        hp = object[1].get('hp', 0)
        hp -= amount

        object[1]['hp'] = hp

        if hp <= 0:
            if is_player:
                player_id = object[1]['player_id']
                self._kill_player(player_id)
            else:
                self.world[coord].remove(object)
                self._mark_dirty_cell(coord)
        elif is_player:
            player_id = object[1]['player_id']
            event_type = constants.STATUS_DAMAGED
            # TODO later put the damage type, and MAYBE the person
            # responsible
            responsible_id = -1
            damage_type = 0
            event = (player_id, event_type, responsible_id, damage_type)
            self.events.append(event)


class ServerException(Exception):
    pass

class PlayerNotFound(ServerException):
    pass

if __name__=='__main__':
    server_main()
