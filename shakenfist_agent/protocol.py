import fcntl
import json
import os
import random
import socket
import sys
import time


class UnknownCommand(Exception):
    pass


class JSONDecodeFailure(Exception):
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
        self.poll_tasks = []

    def _read(self):
        d = None
        try:
            d = os.read(self.input_fileno, 102400)
            self.received_any_data = True
        except BlockingIOError:
            time.sleep(0.200)

        if d:
            self.last_data = time.time()
            if self.log:
                self.log.debug('Read: %s' % d)
        return d

    def _write(self, data):
        try:
            os.write(self.output_fileno, data)
        except BlockingIOError:
            if self.log:
                self.log.info(
                    'Discarded write due to non-blocking IO error, no connection?')
            pass

    def set_fd_nonblocking(self, fd):
        oflags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, oflags | os.O_NONBLOCK)

    def add_command(self, name, meth):
        if self.log:
            self.log.debug('Registered command %s' % name)
        self._command_map[name] = meth

    def poll(self):
        if time.time() - self.last_data > 5:
            pts = self.poll_tasks
            if not pts:
                pts = [self.send_ping]

            for pt in pts:
                if self.log:
                    self.log.debug(
                        'Sending %s poll due to idle connection' % pt)
                pt()
            self.last_data = time.time()

    def close(self):
        if self.log:
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
        if self.log:
            self.log.debug('Sent: %s' % packet)

    def find_packets(self):
        packet = self.find_packet()
        while packet:
            yield packet
            packet = self.find_packet()

    def find_packet(self):
        d = self._read()
        if d:
            self.buffer += d

        buffer_as_string = self.buffer.decode('utf-8')
        offset = buffer_as_string.find(self.PREAMBLE)
        if offset == -1:
            return None

        # Do we have any length characters?
        blen = len(self.buffer)
        if blen < offset + 11:
            return None

        # Find the end of the length field
        len_end = offset + 9
        while buffer_as_string[len_end].isnumeric():
            len_end += 1

        # Find the length of the body of the packet
        plen = int(buffer_as_string[offset + 9: len_end])
        if blen < len_end + 1 + plen:
            return None

        # Extract and parse the body of the packet
        packet = self.buffer[len_end + 1: len_end + 1 + plen]
        self.buffer = self.buffer[len_end + 1 + plen:]
        try:
            return json.loads(packet.decode('utf-8'))
        except json.JSONDecodeError:
            raise JSONDecodeFailure('Failed to JSON decode packet: %s'
                                    % packet.decode('utf-8'))

    def dispatch_packet(self, packet):
        if self.log:
            self.log.debug('Processing: %s' % packet)
        command = packet.get('command')
        if command in self._command_map:
            self._command_map[command](packet)
        else:
            if self.log:
                self.log.debug('Could not find command "%s" in %s'
                               % (command, self._command_map.keys()))
            raise UnknownCommand('Command %s is unknown' % command)

    def noop(self, packet):
        return

    def send_ping(self, unique=None):
        if not unique:
            unique = random.randint(0, 65535)

        self.send_packet({
            'command': 'ping',
            'unique': unique
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
