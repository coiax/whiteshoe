from packet_pb2 import Packet
import socket

s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
addr = ('::1',25008)

p = Packet()
