import time
import socket
import select
import datetime
import logging

logger = logging.getLogger(__name__)

import constants
import packet_pb2
import client
from utility import get_id

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

        self.last_thought = None

    def go(self):
        self.network = client.ClientNetwork()
        self.network.join(self.addr, self.game_id)

        while True:
            self.network.update()
            self.think()

    def think(self):
        self.last_thought = datetime.datetime.now()

if __name__=='__main__':
    cmd_main()
