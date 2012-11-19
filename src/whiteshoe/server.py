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
import logging

import constants
import packet_pb2
from utility import (neighbourhood, get_id, bytes_to_human, dict_difference,
                     cardinal_neighbourhood)
import utility
import maps
import vision

logger = logging.getLogger(__name__)

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

        self.udp_socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.tcp_socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)

        self.network_id_bidict = {}
        self.clients = {}

        self.client_sockets = []

        self.timeout = constants.TIMEOUT
        self.tcp_backlog = 5

        self.stats = {'packets_sent':0,
                      'packets_recieved':0,
                      'bytes_sent':0,
                      'bytes_recieved':0}


    def serve(self):
        self.udp_socket.bind(('',self.port))
        self.tcp_socket.bind(('',self.port))
        self.tcp_socket.listen(self.tcp_backlog)

        while True:
            try:
                for game in self.games:
                    packets = game.tick()
                    self._send_packets(packets)

                for network_id in list(self.clients):
                    last_heard = self.clients[network_id]['last_heard']
                    last_sent = self.clients[network_id]['last_sent']

                    if last_heard.elapsed_seconds > self.timeout:
                        reason = constants.DISCONNECT_TIMEOUT
                        self._disconnect_client(network_id, reason)

                    elif last_sent.elapsed_seconds > constants.KEEPALIVE_TIME:
                        p = packet_pb2.Packet()
                        p.packet_id = get_id('packet')
                        p.payload_types.append(constants.KEEP_ALIVE)
                        p.timestamp = int(time.time())

                        self._send_packets([(network_id, p)])
                        # Sending the packets resets the last_sent
                        # stopwatch

                rlist = [self.udp_socket, self.tcp_socket]
                rlist.extend(self.client_sockets)

                rlist, wlist, xlist = select.select(rlist,(),(),0.05)

                if self.display_stats:
                    display_stats(self.stats)


                for rs in rlist:
                    if rs == self.udp_socket:
                        data, addr = rs.recvfrom(4096)

                        key = ('UDP', addr)

                        if key not in self.network_id_bidict:
                            nid = get_id('network')
                            self.network_id_bidict[key] = nid
                            self.network_id_bidict[nid] = key

                        network_id = self.network_id_bidict[key]

                        if network_id not in self.clients:
                            self.clients[network_id] = {
                                'last_heard': utility.Stopwatch(start=True),
                                'last_sent': utility.Stopwatch(start=True),
                            }

                        self.clients[network_id]['last_heard'].restart()


                        packet = packet_pb2.Packet.FromString(data)
                        self.stats['packets_recieved'] += 1
                        self.stats['bytes_recieved'] += len(data)

                        for payload_type in packet.payload_types:
                            self.handlers[payload_type](packet, network_id)

                    elif rs == self.tcp_socket:
                        conn, address = self.tcp_socket.accept()
                        key = ('TCP', conn)

                        self.network_id_bidict[key] = nid = get_id('network')
                        self.network_id_bidict[nid] = key

                        self.clients[nid] = {
                            'last_heard': utility.Stopwatch(start=True),
                            'last_sent': utility.Stopwatch(start=True),
                            'buffer': '',
                        }

                        self.client_sockets.append(conn)
                    else:
                        # Client socket.
                        key = ('TCP', rs)

                        network_id = self.network_id_bidict[key]
                        client = self.clients[network_id]

                        disconnect = False

                        try:
                            data = rs.recv(4096)
                        except socket.error as e:
                            disconnect = True
                            logger.error(e)

                        if not data:
                            disconnect = True

                        if not disconnect:
                            client['last_heard'].restart()

                            stream = client['buffer']
                            stream += data

                            chunks, remaining = utility.stream_unwrap(stream)
                            client['buffer'] = remaining

                            for chunk in chunks:
                                packet = packet_pb2.Packet.FromString(chunk)
                                for payload_type in packet.payload_types:
                                    handler = self.handlers[payload_type]
                                    handler(packet, network_id)

                                self.stats['packets_recieved'] += 1
                                self.stats['bytes_recieved'] += len(chunk)

                        else:
                            # Recieving the empty string means a disconnect
                            self._disconnect_client(network_id)


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
        for network_id, packet in packets:
            data = packet.SerializeToString()
            type_, other = self.network_id_bidict[network_id]

            if type_ == 'TCP':
                conn = other
                conn.sendall(utility.stream_wrap(data))
            elif type_ == 'UDP':
                addr = other
                self.udp_socket.sendto(data, addr)

            self.clients[network_id]['last_sent'].restart()
            self.stats['packets_sent'] += 1
            self.stats['bytes_sent'] += len(data)

    def _get_games_list(self, packet, network_id):
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

        self._send_packets([(network_id, reply)])

    def _make_new_game(self, packet, network_id):
        # creating new game
        max_players = packet.max_players or None
        map_generator = packet.map_generator or None
        game_name = packet.new_game_name or None
        game_mode = packet.new_game_mode or None
        game_id = get_id('game')

        g = Game(max_players,map_generator,game_name,game_mode,game_id)
        if packet.join_new_game:
            packets = g.player_join(network_id)
            self._send_packets(packets)

        self.games.append(g)

    def _join_game(self, packet, network_id):
        # joining existing game
        if packet.autojoin:
            game = random.choice(self.games)
        else:
            game_id = packet.join_game_id
            game = [g for g in self.games if g.id == game_id][0]

        name = packet.player_name or None
        team = packet.player_team or None

        packets = game.player_join(network_id, name=name, team=team)
        self._send_packets(packets)

    def _error(self, packet, network_id):
        # Handle silently.
        pass

    def _game_action(self, packet, network_id):
        player_id = network_id
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

    def _keep_alive(self, packet, network_id):
        pass

    def _disconnect_packet(self, packet, network_id):
        self._disconnect_client(network_id, packet.disconnect_code or None)

    def _disconnect_client(self, network_id, reason=None):
        player_id = network_id
        for game in self.games:
            if game.is_player_in_game(player_id):
                packets = game.player_leave(player_id)
                self._send_packets(packets)

        # If there's a reason, send a packet about it.
        # Some types of disconnection, such as a TCP socket closing,
        # shouldn't provide a reason, so we don't try to send a packet
        # to a closed connection.
        if reason is not None:
            p = packet_pb2.Packet()
            p.packet_id = get_id('packet')
            p.payload_types.append(constants.DISCONNECT)
            p.disconnect_code = reason
            self._send_packets(((network_id, p),))

        type_, other = self.network_id_bidict[network_id]

        if type_ == 'TCP':
            conn = other

            conn.close()
            self.client_sockets.remove(conn)
        elif type_ == 'UDP':
            addr = other

        del self.clients[network_id]
        del self.network_id_bidict[network_id]
        del self.network_id_bidict[(type_, other)]

def display_stats(stats):
    fmt = "\rNumber Sent: {0}, Number Recieved: {1}, Sent: {2}, Recieved: {3}"
    s = fmt.format(stats['packets_sent'],
                   stats['packets_recieved'],
                   bytes_to_human(stats['bytes_sent']),
                   bytes_to_human(stats['bytes_recieved']))

    sys.stderr.write(s)
    sys.stderr.flush()

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
    MAP_GENERATORS = maps.generators
    VISION_FUNCTIONS = vision.functions

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
        self._tick_lava(time_diff_s)

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
                attr['_damaged'] = []

                slime = (constants.OBJ_SLIME, attr)
                self.world[slime_coord].append(slime)
                self._mark_dirty_cell(slime_coord)
        # end for

        for coord, slime in self.find_objs(constants.OBJ_SLIME):
            for obj in self.world[coord]:
                if obj == slime:
                    continue
                elif obj[0] in constants.SLIMEABLE:
                    if obj in slime[1]['_damaged']:
                        continue
                    else:
                        slime[1]['_damaged'].append(obj)

                    self._damage_object(coord, obj, constants.SLIME_DAMAGE)
                    self._mark_dirty_cell(coord)

            attr = slime[1]
            if '_death_time' in attr:
                attr['_death_time'] -= time_passed
                if attr['_death_time'] < 0:
                    self.world[coord].remove(slime)
                    self._mark_dirty_cell(coord)
                    continue

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

                if spreads_remaining == 0:
                    attr['_death_time'] = constants.SLIME_SPREAD_TIME
                    break

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
                    new_attr['_damaged'] = slime[1]['_damaged']

                    slime = (constants.OBJ_SLIME, new_attr)
                    self.world[spread_coord].append(slime)

    def _tick_lava(self, time_passed):
        for coord, lava in self.find_objs(constants.OBJ_LAVA):
            # Lava damages people in a pool on regular intervals
            # Getting syncronised lava damaging is difficult, so we'll just
            # do it every LAVA_TIME seconds
            attr = lava[1]

            if '_time_passed' not in attr:
                attr['_time_passed'] = 0

            attr['_time_passed'] += time_passed

            times = attr['_time_passed'] // constants.LAVA_TIME

            if times:
                attr['_time_passed'] %= constants.LAVA_TIME
                for other in self.world[coord]:
                    if other == lava:
                        continue
                    else:
                        damage = constants.LAVA_DAMAGE
                        for i in range(times):
                            self._damage_object(coord, other, damage)

            if attr['_spreading']:
                pass #LAVA SPREADS, EVERYONE DIES

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

            if object[0] == constants.OBJ_MINE:
                # Mines explode when they're destroyed
                self._make_explosion(coord, object[1]['size'])

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
