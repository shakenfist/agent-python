import json
import os
import random
import socket
import sys
import time


class Agent(object):
    def __init__(self):
        self.buffer = b''
        self.last_data = time.time()

    def _read(self):
        return os.read(self.input_fileno, 102400)

    def _write(self, data):
        os.write(self.output_fileno, data)

    def poll(self):
        if time.time() - self.last_data > 5:
            self.send_ping()
            self.last_data = time.time()

    def close(self):
        os.close(self.input_fileno)
        os.close(self.output_fileno)

    # Our packet format is: *SFv001*XXXX*YYYY
    # Where XXXX is a four character decimal length with zero padding (i.e. 0100)
    # and YYYY is XXXX bytes of UTF-8 encoded JSON
    PREAMBLE = '*SFv001*'

    @staticmethod
    def _encode_packet(p):
        j = json.dumps(p)
        return len(j), j

    def _send_packet(self, packet):
        self._write(packet.encode('utf-8'))

    def find_packet(self):
        self.buffer += self._read()

        offset = self.buffer.decode('utf-8').find(self.PREAMBLE)
        if offset == -1:
            return None

        blen = len(self.buffer)
        if blen < offset + 13:
            return None

        plen = int(self.buffer[offset + 8: offset + 12])
        if blen < offset + 13 + plen:
            return None

        packet = self.buffer[offset + 13: offset + 13 + plen]
        self.buffer = self.buffer[offset + 13 + plen:]
        return json.loads(packet.decode('utf-8'))

    def send_ping(self):
        l, j = self._encode_packet({
            'command': 'ping',
            'unique': random.randint(0, 65535)
        })
        self._send_packet('%s[%d]%s' % (self.PREAMBLE, l, j))


class SocketAgent(Agent):
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.input_fileno = self.s.fileno()
        self.output_fileno = self.s.fileno()


class StdInOutAgent(Agent):
    def __init__(self):
        self.input_fileno = sys.stdin.fileno()
        self.output_fileno = sys.stdout.fileno()
