import time
import socket
import select
import datetime
import logging

logger = logging.getLogger(__name__)

import constants
import packet_pb2
import client
import utility

def cmd_main():
    # parser here
    main()

def main(addr=('127.0.0.1',constants.DEFAULT_PORT), game_id=0):
    b = Bot(addr, game_id)
    b.go()

class Bot(object):
    def __init__(self, addr, game_id):
        self.addr = addr
        self.game_id = game_id

        self.period = 0.2

        self.thought_timer = utility.RecurringTimer(self.period)

    def go(self):
        self.network = client.ClientNetwork()
        self.network.join(self.addr, self.game_id)

        while True:
            self.network.update()
            times = self.thought_timer.check()
            if times:
                self.think()

    def think(self):
        try:
            coord, player = self.network.find_me()
        except client.PlayerNotFound:
            pass
        else:
            obj, attr = player
            visible = self.network.get_visible()
            cmd = None

            # Look ahead.
            direction = attr['direction']

            diff = constants.DIFFS[direction]

            ahead = coord[0] + diff[0], coord[1] + diff[1]

            if ahead in visible:
                if any(object[0] in constants.SOLID_OBJECTS
                       for object in visible[ahead]):

                    cmd = self._rotate(direction)

                else:
                    # Move forward
                    cmd = (constants.CMD_MOVE, direction)
            else:
                cmd = self._rotate(direction)

            if cmd is not None:
                self.network.send_command(*cmd)

    def _rotate(self, direction):
        # Rotate
        index = constants.DIRECTIONS.index(direction)
        index += 1
        index %= len(constants.DIRECTIONS)

        # That's what makes you beautiful etc.
        new_direction = constants.DIRECTIONS[index]
        # Wait no, that's one_direction
        return (constants.CMD_LOOK, new_direction)

if __name__=='__main__':
    cmd_main()
