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
import collections

import constants
import packet_pb2
from utility import (neighbourhood, get_id, bytes_to_human, dict_difference,
                     cardinal_neighbourhood)
import utility
import maps
import vision
import game

logger = logging.getLogger(__name__)

def server_main(args=None):
    # Ignore arguments for now
    p = argparse.ArgumentParser()
    p.add_argument('-v','--vision',default='cone')
    p.add_argument('-m','--map',default='depth_first')
    p.add_argument('-M','--mode',default='ffa')
    p.add_argument('-q','--quiet',action='store_true',default=False)
    p.add_argument('-d','--debug',action='store_true')
    p.add_argument('-o',dest='options',action='append')
    ns = p.parse_args(args)

    options = collections.OrderedDict()
    for option_string in ns.options:
        if '=' not in option_string:
            options[option_string] = None
        else:
            parts = option_string.split('=')
            assert len(parts) == 2

            option[parts[0]] = parts[1]

    s = Server(ns, options)
    s.serve()

class Server(object):
    def __init__(self,ns, options):
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
        game_cls = game.modes[ns.mode]

        g = game_cls(vision=ns.vision, map_generator=ns.map, options=options)
        self.games.append(g)

        self.display_stats = not ns.quiet
        self.debug = ns.debug

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

        self.options = options


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
                    # TODO Notify all connected clients of server crash
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

        if player_id not in game.players:
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
            if player_id in game.players:
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

class ServerException(Exception):
    pass

if __name__=='__main__':
    server_main()
