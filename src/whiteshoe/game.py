import random
import collections
import json
import logging
try:
    import cPickle as pickle
except ImportError:
    import pickle

logger = logging.getLogger(__name__)
import abc

import utility
import constants
import packet_pb2 as wire
import maps
import vision

modes = {}

def gamemode(cls):
    modes[cls.mode] = cls
    return cls

class Game(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def __init__(self,max_players=20,map_generator='purerandom',
                 name='Untitled',id=None,vision='basic',options=None):
        pass

    @abc.abstractmethod
    def handle(self, packet, player_id):
        return []

    @abc.abstractmethod
    def player_join(self,player_id,name=None,team=None):
        return []

    @abc.abstractmethod
    def player_leave(self, player_id):
        return []

    @abc.abstractmethod
    def tick(self):
        return []

class _MultiHackEventHandler(object):
    def _do_move(self, player_id, flags=()):
        player_state = self.players[player_id]
        player_flags = player_state['flags']

        if 'lost' in player_flags:
            # A lost player cannot move, as he has no entity.
            return

        direction = set(flags) & set(constants.MULTIHACK_DIRECTIONS)
        assert len(direction) == 1
        direction = direction.pop()
        # TODO special case UP and DOWN

        current_location = player_state['location']

        # This is not minecraft, x and y are as usual, z is depth.
        # World is generally one of the dungeon levels
        # GENERALLY, z is 0. But say you're at the bottom of a moat,
        # or climbed up a tree? You're still on the same level, but you
        # have a different z.
        world_name, x, y, z = current_location

        world = self.universe[world_name]

        current_location_entities = world[x,y,z]
        player_entity_id = player_state['entity_id']
        for entity in current_location_entities:
            entity_id, entity_type = entity
            if entity_id == player_entity_id:
                player_entity = entity
                break
        else:
            # We didn't find the player. SOMETHING HAS GONE HORRIBLY
            # WRONG AAAAAAAAA
            player_flags.add("lost")
            # This is most of the time, indicating a bug, but occasionally
            # the sign of weird debugging.
            return

        diff = constants.DIFFS[direction]

        new_x, new_y = x + diff[0], y + diff[1]
        new_z = z


        move_happening = True
        try:
            new_location_entities = world[new_x, new_y, new_z]
        except KeyError:
            # This is a bug, all maps should be bordered by unpassable,
            # undiggable walls or something to that effect.
            msg = "{} attempted to move to non existent coord {}"
            logger.warning(msg.format(player_id, (new_x, new_y, new_z)))
            move_happening = False

            new_location_entities = ()

        if 'god' not in player_flags:
            for entity in new_location_entities:
                entity_id, entity_type = entity
                type_data = self.entity_data.get(entity_type)
                if type_data is None:
                    # Assume no flags as some sensible default.
                    # TODO whine in the server log about the unknown entity.
                    flags = ()
                else:
                    flags = type_data.get('flags',())
                if "walkable" not in flags:
                    # Special case walking into walls and closed doors.
                    #print("bump")
                    move_happening = False
                    break #TODO implement bumping into things
        else:
            # So if the player has the god flag, he moves through all things
            # regardless
            move_happening = True

        if move_happening:
            current_location_entities.remove(player_entity)
            new_location_entities.append(player_entity)
            # TODO obviously, we'll now check to see if you fall into the moat
            # or the lava.
            location = (world_name, new_x, new_y, new_z)

            player_state['remote_store']['player_location'] = location
            player_state['location'] = location

            #print(player_state['location'])

    def _do_command(self, player_id, commandstr):
        player_state = self.players[player_id]

        parts = commandstr.split()
        if parts:
            command = parts[0]
            args = parts[1:]
        else:
            command = 'noop' #TODO probably change to HELP by default.
            args = ()

        # TODO Probably should initalise these before hand, or at least assert
        # that these functions exist
        handlers = {
            'status': self._command_status,
            'noop': lambda *args: None,
            'fail': self._command_fail,
            'savestate': self._command_savestate,
            'loadstate': self._command_loadstate,
            '#player': self._command_hashplayer,
            '#remove': self._command_hashremove,
            '#spawn': self._command_hashspawn,
            '#teleport': self._command_hashteleport,
            '#look': self._command_hashlook,
            '#playerflag': self._command_hashplayerflag,
        }

        # TODO complain at the client for a bad command
        if command not in handlers:
            self._message_player(player_id, "Bad command: {}".format(command))
        else:
            handler = handlers[command]
            try:
                handler(player_id, *args)
            except Exception as e:
                msg = "Command error: {}".format(repr(e))
                self._message_player(player_id, msg)

class _MultiHackCommandHandler(object):
    def _command_fail(self, player_id, *args):
        # This command does nothing except fail.
        raise Exception("Fail command can only fail.")
    def _command_status(self, player_id):
        num_levels = len(self.universe)
        num_entities = 0
        for level in self.universe:
            for coord, entities in self.universe[level].items():
                num_entities += len(entities)

        fmt = "{} levels, with {} entities."

        self._message_player(player_id, fmt.format(num_levels, num_entities))
    def _command_savestate(self, player_id):
        state = self.save_state()
        with open('game.state', 'wb') as f:
            pickle.dump(state, f)
    def _command_loadstate(self, player_id):
        with open('game.state','rb') as f:
            state = pickle.load(f)
        self.load_state(state)

    def _command_hashplayer(self, commanding_player_id):
        for player_id in sorted(self.players):
            name = self.players[player_id]['name']
            location = self.players[player_id]['location']
            flags = ','.join(self.players[player_id]['flags'])
            fmt = "{name} (ID {player_id}) [{flags}] at {location}"
            msg = fmt.format(**vars())
            self._message_player(commanding_player_id, msg)

    def _command_hashremove(self, player_id, *args):
        assert args #Must have at least one entity id to remove
        doomed_ids = []
        for arg in args:
            doomed_ids.append(int(arg))

        found = False
        for world_name, world in self.universe.items():
            for coord, entities in world.items():
                for entity in entities:
                    entity_id, entity_type = entity
                    if entity_id in doomed_ids:
                        entities.remove(entity)
                        doomed_ids.remove(entity_id)
                        if not entities:
                            del world[coord]
                        id = entity_id
                        loc = (world_name,) + coord
                        fmt = "Entity #{id} \"{entity_type}\" {loc} removed."
                        msg = fmt.format(**vars())
                        self._message_player(player_id, msg)

        for doomed_id in doomed_ids:
            msg = "Entity #{} not found.".format(doomed_id)
            self._message_player(player_id, msg)

    def _command_hashspawn(self, player_id, entity_type):
        # Spawn from where the player is.
        world, x, y, z = loc = self.players[player_id]['location']
        assert world in self.universe
        if (x,y,z) not in self.universe[world]:
            self.universe[world][x,y,z] = []

        entity_list = self.universe[world][x,y,z]

        new_id = utility.get_id('entity')

        entity_list.append((new_id, entity_type))

        fmt = "Entity #{new_id} \"{entity_type}\" spawned at {loc}"
        self._message_player(player_id, fmt.format(**vars()))

    def _command_hashteleport(self, player_id, world, x, y, z):
        player_state = self.players[player_id]

        x, y, z = int(x), int(y), int(z)

        assert world in self.universe
        assert (x,y,z) in self.universe[world]

        old_entity_location = player_state['location']
        old_world, old_x, old_y, old_z = old_entity_location

        player_entity_id = player_state['entity_id']

        entities = self.universe[old_world][old_x,old_y,old_z]

        for entity in entities:
            entity_id, entity_type = entity
            if entity_id == player_entity_id:
                entities.remove(entity)
                self.universe[world][x,y,z].append(entity)
                player_state['location'] = loc = (world,x,y,z)
                fmt = "Moved player entity #{entity_id} to {loc}"
                msg = fmt.format(**vars())
                break
        else:
            # No entity found, so we'll just MAKE A NEW ONE.
            # Remember that the hash commands are debug. Normally, having
            # no known entity (unexpectedly) is grounds for panicking
            new_id = utility.get_id('entity')
            new_entity = (new_id, "human")
            self.universe[world][x,y,z].append(new_entity)
            player_state['location'] = loc = (world, x, y, z)
            player_state['entity_id'] = new_id
            player_state['flags'].discard('lost')
            fmt = "No entity found, new player entity #{new_id} at {loc}"
            msg = fmt.format(**vars())

        player_state['remote_store']['player_location'] = loc
        self._message_player(player_id, msg)

    def _command_hashlook(self, player_id, world=None,
                          x_str=None, y_str=None, z_str=None):

        if (world, x_str, y_str, z_str) == (None, None, None, None):
            loc = self.players[player_id]['location']
        else:
            loc = world, int(x_str), int(y_str), int(z_str)
        world, x, y, z = loc

        assert world in self.universe
        assert (x,y,z) in self.universe[world]

        self._message_player(player_id, "Entities at {}:".format(loc))
        for entity in self.universe[world][x,y,z]:
            entity_id, entity_type = entity
            msg = " - #{entity_id} \"{entity_type}\"".format(**vars())
            self._message_player(player_id, msg)

    def _command_hashplayerflag(self, player_id, flag, state='toggle'):

        player_state = self.players[player_id]
        player_flags = player_state['flags']

        if state.lower() in ('toggle',):
            action = 'toggle'
        elif state.lower() in ('on','yes','true','set'):
            action = 'set'
        elif state.lower() in ('off','no','false','clear'):
            action = 'clear'
        else:
            action = 'unknown'

        if action == 'toggle':
            if flag in player_flags:
                player_flags.remove(flag)
            else:
                player_flags.add(flag)

        elif action == 'set':
            player_flags.add(flag)
        elif action == 'clear':
            player_flags.discard(flag)
        fmt = "{name} ID {id} {action} {flag}: [{player_flags_str}]"

        name = player_state['name']
        id = player_id
        player_flags_str = ','.join(player_flags)

        self._message_player(player_id, fmt.format(**vars()))

class _MultiHackUtility(object):
    def _message_player(self, player_id, message):
        event = ("message", message)
        self.players[player_id]['event_queue'].append(event)

@gamemode
class MultiHack(_MultiHackEventHandler, _MultiHackCommandHandler,
                _MultiHackUtility, Game):
    mode = 'multihack'
    def __init__(self,name="Untitled",id=None,**kwargs):
        #def __init__(self,max_players=20,map_generator='purerandom',
        #         name='Untitled',id=None,vision='basic',options=None):
            #
        self.name = name
        self.id = id if id is not None else utility.get_id('game')

        self.entity_data = {
            'human': {'symbol': '@', 'colour': 'white'},
            'wall': {'symbol': '#', 'colour': 'white'},
            'floor': {'symbol': '.', 'colour': 'white', 'flags':('walkable',)},
            'pool': {'symbol': '}', 'colour': 'blue'},
            'lava': {'symbol': '}', 'colour': 'red'},
            'gridbug': {'symbol': 'x', 'colour':'purple','flags':('monster',)},
            'throne': {'symbol': '\\','colour':'yellow',
                       'flags':('walkable','bold')},
            'tree': {'symbol':'#','colour':'green'},
            'stair': {'symbol':'X','colour':'white',
                      'entity_flag_set':{'up': {'symbol':'<', 'flags':()},
                                         'down': {'symbol':'>', 'flags':()}},
                      'flags': ('invalid',)
                     }
        }

        self.players = {}
        self.universe = {}
        self.entity_state = {}

        # Quickly spin up a shitty world substitute.
        world = {}

        self.random = random.Random(0)

        self._empties = [] # not permament, just used for the hacky current
                           # setup

        for x in range(80):
            for y in range(24):
                world[x,y,0] = entities = []
                entity_id = utility.get_id('entity')
                if self.random.random() < 0.35:
                    entity = (entity_id, 'wall')
                else:
                    entity = (entity_id, 'floor')

                    self._empties.append((x,y))
                entities.append(entity)

        self.random.shuffle(self._empties)

        self.universe['level1'] = world

    def save_state(self):
        state = {'universe': self.universe, 'random': self.random.getstate(),
                 'players': self.players}
        return state

    def load_state(self, state):
        self.universe = state['universe']
        self.random.setstate(state['random'])
        self.players = state['players']

    def player_join(self, player_id, name=None, team=None):
        player_id = utility.get_id('player')

        self.players[player_id] = player_state = {}

        # The client store is where THEY give us information.
        player_state['client_store'] = utility.DifflingReader()

        # The remote information is what we're sending to them.
        # TODO aggressive is, well, aggressive and should only be enabled
        # while we're debugging.

        player_state['remote_store'] = utility.DifflingAuthor(aggressive=True)
        player_state['event_queue'] = []
        player_state['name'] = "Bob Newbie"
        player_state['flags'] = set()

        entity_x, entity_y = self._empties.pop()

        player_state['location'] = ('level1', entity_x, entity_y, 0)
        player_state['entity_id'] = entity_id = utility.get_id('entity')

        entity = (entity_id, 'human')

        self.universe['level1'][entity_x, entity_y, 0].append(entity)

        # TODO later, we'll give them incomplete information. Right now
        # GET SOMETHING WORKING
        remote_store = player_state['remote_store']

        remote_store['known_universe'] = self.universe
        remote_store['player_location'] = player_state['location']
        remote_store['entity_data'] = self.entity_data
        remote_store['entity_state'] = self.entity_state
        remote_store['player_name'] = player_state['name']

        return self._crappy_all_store()

    def _crappy_store(self, player_id):
        # Returns a number of key/value packets for storage. This is really
        # lame and is definitely a hack so FIXME
        packets = []
        remote_store = self.players[player_id]['remote_store']
        for key, diff in remote_store.get_changes():
            p = wire.Packet()
            p.payload_type = constants.Payload.picklediff
            p.key = utility.quick_pickle(key)
            p.diff = diff

            packets.append((player_id,p))

        has_events = False

        for event in self.players[player_id]['event_queue']:
            p = wire.Packet()
            p.payload_type = constants.Payload.event
            p.event = utility.quick_pickle(event)
            packets.append((player_id, p))

            has_events = True

        if has_events:
            self.players[player_id]['event_queue'] = []

        return packets

    def _crappy_all_store(self):
        packets = []
        for player_id in self.players:
            packets.extend(self._crappy_store(player_id))
        return packets


    def player_leave(self, player_id):
        assert player_id in self.players

        player_state = self.players[player_id]
        location = player_state['location']
        player_entity_id = player_state['entity_id']

        world = self.universe[location[0]]
        x,y,z = location[1], location[2], location[3]

        for entity in world[x,y,z]:
            entity_id, entity_type = entity
            if entity_id == player_entity_id:
                world[x,y,z].remove(entity)
                break

        del self.players[player_id]

        return self._crappy_all_store()

    def handle(self, packet, player_id):
        assert player_id in self.players

        player_state = self.players[player_id]

        if packet.payload_type == constants.Payload.picklediff:
            key = pickle.loads(packet.key)
            diff = packet.diff

            player_state['client_store'].feed(key, diff)

        elif packet.payload_type == constants.Payload.event:
            event = pickle.loads(packet.event)

            handlers = {
                "move": self._do_move,
                "command": self._do_command
            }

            #TODO handle unknown commands. Probably by kicking the client,
            # tbh.
            handler = handlers[event[0]]
            handler(player_id, *event[1:])

        return self._crappy_all_store()

    tick = None

class GameException(Exception):
    pass

if __name__=='__main__':
    mh = MultiHack()
    mh.player_join("bob")
    world = mh.universe['level1']
