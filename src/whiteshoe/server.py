from __future__ import print_function

import datetime
import select
import socket
import random
import itertools
import traceback
import sys

import constants
import packet_pb2
from utility import neighbourhood, get_id, bytes_to_human

def server_main(args=None):
    # Ignore arguments for now
    s = Server()
    s.serve()

class Server(object):

    def __init__(self):
        self.handlers = {
            constants.GET_GAMES_LIST: self._get_games_list,
            # games running (s->c)
            constants.MAKE_NEW_GAME: self._make_new_game,
            constants.ERROR: self._error,
            constants.GAME_ACTION: self._game_action,
            constants.JOIN_GAME: self._join_game,
            # vision update (s->c)
            constants.KEEP_ALIVE: self._keep_alive,

        }
        self.port = constants.DEFAULT_PORT

        self.games = []

        # Debug starting game
        g = Game(vision='all')
        self.games.append(g)

        self.seen_ids = []

        self.clients = {}

        self.socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

        self.timeout = 30

        self.stats = {'packets_sent':0,
                      'packets_recieved':0,
                      'bytes_sent':0,
                      'bytes_recieved':0}


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


            rlist, wlist, xlist = select.select([self.socket],[],[],0.005)

            display_stats(self.stats)


            for rs in rlist:
                data, addr = rs.recvfrom(4096)

                try:
                    packet = packet_pb2.Packet.FromString(data)
                    self.stats['packets_recieved'] += 1
                    self.stats['bytes_recieved'] += len(data)

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
                    self.stats['packets_sent'] += 1
                    self.stats['bytes_sent'] += packet.ByteSize()
                    sent = True
                    break

            assert sent

    def _get_games_list(self, packet, addr):
        reply = packet_pb2.Packet()
        reply.payload_types.append(constants.GAMES_RUNNING)

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

def display_stats(stats):
    fmt = "\rNumber Sent: {0}, Number Recieved: {1}, Sent: {2}, Recieved: {3}"
    s = fmt.format(stats['packets_sent'],
                   stats['packets_recieved'],
                   bytes_to_human(stats['bytes_sent']),
                   bytes_to_human(stats['bytes_recieved']))

    sys.stderr.write(s)
    sys.stderr.flush()


def purerandom_map(X=80,Y=24,seed=0):
    world = {}
    r = random.Random(seed)

    for i,j in itertools.product(range(X), range(Y)):
        if r.random() < 0.35:
            world[i,j] = [(constants.OBJ_WALL, {})]
        else:
            world[i,j] = [(constants.OBJ_EMPTY, {})]

    return world

def empty_map(X=80,Y=24,seed=None):
    world = {}

    for i,j in itertools.product(range(X), range(Y)):
        world[i,j] = [(constants.OBJ_EMPTY, {})]
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
            obj = [(constants.OBJ_WALL, {})]
        else:
            obj = [(constants.OBJ_EMPTY, {})]

        world[coord] = obj

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

def vision_square(world, start_coord, direction):
    visible = neighbourhood(start_coord, n=3)
    return _visible_world(world, visible)

def vision_cone(world, coord, direction):
    visible = set()
    # The square you are in is always visible as well one square
    # behind you
    visible.add(coord)

    main_direction = constants.DIFFS[direction]
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
                if o[0] in constants.OPAQUE_OBJECTS:
                    running = False
        return v

    for direction in constants.ADJACENT[direction]:
        visible.update(look_until_wall(coord,
                                       constants.DIFFS[direction]))

    return _visible_world(world, visible)

def vision_all(world, coord, direction):
    visible_coords = set(world)
    return _visible_world(world, visible_coords)

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
        'purerandom': purerandom_map,
        'empty': empty_map,
        'ca_maze':ca_maze_map
    }
    VISION_FUNCTIONS = {
        'square': vision_square,
        'cone': vision_cone,
        'all': vision_all,
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
        for obj_type in constants.SOLID_OBJECTS:
            suitable -= set(self.find_obj_locations(obj_type))

        spawn_coord = random.choice(list(suitable))
        direction = random.choice(constants.DIRECTIONS)

        start_max_hp = 10

        player = (constants.OBJ_PLAYER,
                  {'number':player_id,
                   'direction': direction,
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
                if obj in constants.HISTORICAL_OBJECTS:
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
                                if o[0] in constants.HISTORICAL_OBJECTS]

            historical_visibles = [o for o in visible_objects
                                   if o[0] in constants.HISTORICAL_OBJECTS]

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
                if obj in constants.HISTORICAL_OBJECTS:
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
        vision_func = self.VISION_FUNCTIONS[self.vision]

        visible_world = vision_func(self.world, coord, direction)

        return visible_world

    def _send_player_vision(self,player_id, coords):
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

        for coord in coords:
            if current_packet.ByteSize() > constants.PACKET_SIZE_LIMIT:
                packets.append(current_packet)
                current_packet = gen_packet()

            if coord not in self.world:
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

        #TODO For each packet, make sure that identical Attributes are
        # compressed down to one, and the corresponding attr_ids are changed.

        return [(player_id, packet) for packet in packets]

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
                if obj == constants.OBJ_PLAYER and attr['number'] == number:
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

        packets = handlers[cmd](player, location, arg)

        return packets

    def _look(self, player, location, arg):
        player[1]['direction'] = arg
        return self._mark_dirty([location])

    def _move(self, player, location, arg):
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

        diff = constants.DIFFS[direction]
        bullet_location = location

        attr = {'owner': player_id, 'direction':direction, 'size':arg}
        bullet = (constants.OBJ_BULLET, attr)

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
        bullets = self.find_objs(constants.OBJ_BULLET)
        for coord,object in bullets:
            attr = object[1]
            size = attr['size']
            speed = constants.BULLET_SPEEDS[size]

            if '_time_remaining' not in attr:
                attr['_time_remaining'] = speed

            attr['_time_remaining'] -= time_passed
            # TODO, currently a bullet can move only 1 square per tick
            if attr['_time_remaining'] < 0:
                attr['_time_remaining'] += speed


                self.world[coord].remove(object)
                dirty_coords.add(coord)


                loc_diff = constants.DIFFS[object[1]['direction']]

                new_coord = (coord[0] + loc_diff[0], coord[1] + loc_diff[1])

                if new_coord in self.world:
                    exploded = False
                    for other in list(self.world[new_coord]):
                        if other[0] in constants.SOLID_OBJECTS:
                            # Boom, bullet explodes.
                            for ex_coord in neighbourhood(new_coord,n=size-1):
                                if ex_coord not in self.world:
                                    continue

                                explosion = (constants.OBJ_EXPLOSION,
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
        explosions = self.find_objs(constants.OBJ_EXPLOSION)
        for coord,bullet in explosions:
            attr = bullet[1]
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

                    hp = object[1].get('hp', 0)
                    hp -= attr['_damage']

                    if hp <= 0:
                        if object[0] != constants.OBJ_PLAYER:
                            self.world[coord].remove(object)
                        else:
                            player_id = object[1]['number']
                            self._kill_player(player_id)

                        dirty_coords.add(coord)

                        non_explosions = [o for o in self.world[coord]
                                          if o[0] != constants.OBJ_EXPLOSION]

                        if not non_explosions:
                            empty = (constants.OBJ_EMPTY, {})
                            self.world[coord].insert(0,empty)
                    else:
                        object[1]['hp'] = hp


            attr['_time_left'] -= time_passed
            if attr['_time_left'] < 0:
                self.world[coord].remove(bullet)
                dirty_coords.add(coord)

class ServerException(Exception):
    pass

class PlayerNotFound(ServerException):
    pass

if __name__=='__main__':
    server_main()