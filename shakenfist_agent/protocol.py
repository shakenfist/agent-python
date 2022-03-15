import fcntl
import json
import os
import random
import socket
import sys
import time


class UnknownCommand(Exception):
    pass


class Agent(object):
    def __init__(self, logger=None):
        self.buffer = b''
        self.received_any_data = False
        self.last_data = time.time()

        self._command_map = {
            'ping': self.send_pong,
            'pong': self.noop
        }

        self.log = logger
        self.poll_tasks = [self.send_ping]

    def _read(self):
        d = None
        try:
            d = os.read(self.input_fileno, 102400)
            self.received_any_data = True
        except BlockingIOError:
            pass

        if d:
            self.last_data = time.time()
            self.log.debug('Read: %s' % d)
        return d

    def _write(self, data):
        try:
            os.write(self.output_fileno, data)
        except BlockingIOError:
            self.log.info('Discarded write due to non-blocking IO error, no connection?')
            pass

    def set_fd_nonblocking(self, fd):
        oflags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, oflags | os.O_NONBLOCK)

    def add_command(self, name, meth):
        self.log.debug('Registered command %s' % name)
        self._command_map[name] = meth

    def poll(self):
        if not self.received_any_data:
            return

        if time.time() - self.last_data > 5:
            self.log.debug('Sending ping packet due to idle connection')
            for pt in self.poll_tasks:
                pt()
            self.last_data = time.time()

    def close(self):
        self.log.debug('Cleaning up connection for graceful close.')
        os.close(self.input_fileno)
        os.close(self.output_fileno)

    # Our packet format is: *SFv001*XXXX*YYYY
    # Where XXXX is a four character decimal length with zero padding (i.e. 0100)
    # and YYYY is XXXX bytes of UTF-8 encoded JSON
    PREAMBLE = '*SFv001*'

    def send_packet(self, p):
        j = json.dumps(p)
        packet = '%s[%d]%s' % (self.PREAMBLE, len(j), j)
        self._write(packet.encode('utf-8'))
        self.log.debug('Sent: %s' % packet)

    def find_packet(self):
        d = self._read()
        if d:
            self.buffer += d

        offset = self.buffer.decode('utf-8').find(self.PREAMBLE)
        if offset == -1:
            return None

        blen = len(self.buffer)
        if blen < offset + 13:
            return None

        plen = int(self.buffer[offset + 9: offset + 11])
        if blen < offset + 13 + plen:
            return None

        packet = self.buffer[offset + 12: offset + 12 + plen]
        self.buffer = self.buffer[offset + 13 + plen:]
        return json.loads(packet.decode('utf-8'))

    def dispatch_packet(self, packet):
        self.log.debug('Processing: %s' % packet)
        command = packet.get('command')
        if command in self._command_map:
            self._command_map[command](packet)
        else:
            self.log.debug('Could not find command "%s" in %s'
                           %(command, self._command_map.keys()))
            raise UnknownCommand('Command %s is unknown' % command)

    def noop(self, packet):
        return

    def send_ping(self):
        self.send_packet({
            'command': 'ping',
            'unique': random.randint(0, 65535)
        })

    def send_pong(self, packet):
        self.send_packet({
            'command': 'pong',
            'unique': packet['unique']
        })


class SocketAgent(Agent):
    def __init__(self, path, logger=None):
        super(SocketAgent, self).__init__(logger=logger)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.input_fileno = self.s.fileno()
        self.output_fileno = self.s.fileno()
        self.set_fd_nonblocking(self.input_fileno)


class FileAgent(Agent):
    def __init__(self, path, logger=None):
        super(FileAgent, self).__init__(logger=logger)
        self.input_fileno = os.open(path, os.O_RDWR)
        self.output_fileno = self.input_fileno
        self.set_fd_nonblocking(self.input_fileno)


class StdInOutAgent(Agent):
    def __init__(self, logger=None):
        super(StdInOutAgent, self).__init__(logger=logger)
        self.input_fileno = sys.stdin.fileno()
        self.output_fileno = sys.stdout.fileno()
        self.set_fd_nonblocking(self.input_fileno)
