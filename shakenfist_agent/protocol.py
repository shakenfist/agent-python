# I cannot take the same approach for the SF Agent as I did for the privexec
# daemon in the Shaken Fist daemon itself -- the virtio-serial layer is a single
# stream of bytes in each direction, not a new connection to a socket per
# request like a unix domain socket. I therefore need to retain at least parts
# of the agent protocol from v1 to delineate between requests. That's ok though
# because I need that for backwards compatibility for a while at least anyway.

import base64
import copy
import fcntl
import json
import os
import random
import socket
import time


MAX_WRITE = 2048


class PacketTooLarge(Exception):
    ...


class Agent(object):
    def __init__(self, logger=None):
        self.buffer = b''
        self.received_any_data = False
        self.last_data = time.time()

        self.output_fileno = None
        self.input_fileno = None

        self._v1_command_map = {
            'ping': self.send_pong,
            'pong': self.noop,
            'json-decode-failure': self.log_error_packet,
            'command-error': self.log_error_packet,
            'unknown-command': self.log_error_packet,
        }

        self.log = logger
        self.poll_tasks = []

    def _read(self):
        d = None
        try:
            d = os.read(self.input_fileno, MAX_WRITE * 2)
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
            while data:
                os.write(self.output_fileno, data[:MAX_WRITE])
                data = data[MAX_WRITE:]
        except BlockingIOError:
            if self.log:
                self.log.info(
                    'Discarded write due to non-blocking IO error, '
                    'no connection?')
            pass

    def set_fd_nonblocking(self, fd):
        oflags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, oflags | os.O_NONBLOCK)

    def add_command(self, name, meth):
        if self.log:
            self.log.debug('Registered command %s' % name)
        self._v1_command_map[name] = meth

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

    # Our packet format is:
    #
    #     *SFvVVV[XXXXXXX]YYYY...
    #
    # Where:
    #   VVV is a three character decimal protcol version number with padding.
    #   XXXXXXX is a eight character decimal length with padding.
    #   YYYY is XXXXXXX bytes of UTF-8 encoded JSON for v1 or encoded
    #     protobuf for v2.
    PREAMBLE_COMMON = b'*SFv00'
    PREAMBLE_v1 = b'*SFv001'
    PREAMBLE_v2 = b'*SFv002'

    def send_v1_packet(self, p):
        data = json.dumps(p).encode()
        self._send_packet(self.PREAMBLE_v1, data)

    def _send_packet(self, preamble, data, body_is_binary=False):
        length = len(data)
        if length > 99999999:
            raise PacketTooLarge(
                'The maximum packet size is 99,999,999 bytes of UTF-8 encoded JSON. '
                f'This packet is {length} bytes.')

        packet = preamble + f'[{length:08}]'.encode() + data
        self._write(packet)

        if self.log:
            if body_is_binary:
                self.log.debug(f'Sent: {packet[:16]} ...binary data...')
            else:
                self.log.debug(f'Sent: {packet}')

    def find_packets(self):
        packet = self.find_packet()
        while packet:
            yield packet
            packet = self.find_packet()

    def find_packet(self):
        d = self._read()
        if d:
            self.buffer += d

        offset = self.buffer.find(self.PREAMBLE_COMMON)
        if offset == -1:
            return None

        # Is the version recognized?
        version = int(self.buffer[offset + 4:offset + 7].decode())
        if version not in [1, 2]:
            return None

        # Do we have the complete length field?
        blen = len(self.buffer)
        len_start = offset + len(self.PREAMBLE_v1) + 1
        len_end = len_start + 8
        if blen < len_end + 1:
            return None

        # Find the length of the body of the packet and make sure we have that
        # much buffered
        plen = int(self.buffer[len_start: len_end])
        if blen < len_end + 1 + plen:
            return None

        # The arguments to the packet parsers are the offset to the start of
        # the packet and its length. This does not include the header we
        # dealt with here.
        out = None
        if version == 1:
            out = self._find_packet_v1(len_end + 1, plen)
        if version == 2:
            out = self._find_packet_v2(len_end + 1, plen)

        # Remove this packet from the buffer
        self.buffer = self.buffer[len_end + 1 + plen:]
        return out

    def _find_packet_v1(self, offset, length):
        # Extract and parse the body of the packet
        packet = self.buffer[offset: offset + length]

        try:
            return json.loads(packet)
        except json.JSONDecodeError:
            if self.log:
                self.log.with_fields({'packet': packet}).error(
                    'Failed to JSON decode packet')
            self.send_v1_packet(
                {
                    'command': 'json-decode-failure',
                    'message': ('failed to JSON decode packet: %s'
                                % packet.decode('utf-8'))
                })
            return None

    def _find_packet_v2(self, offset, length):
        # Not yet implemented
        return None

    def dispatch_packet(self, packet):
        if self.log:
            lp = copy.copy(packet)
            if 'chunk' in lp:
                lp['chunk'] = '...'
            self.log.debug('Processing: %s' % lp)
        command = packet.get('command')

        if command in self._v1_command_map:
            try:
                self._v1_command_map[command](packet)
            except Exception as e:
                if self.log:
                    self.log.with_fields({'error': str(e)}).error(
                        'Command %s raised an error')
                self.send_v1_packet(
                    {
                        'command': 'command-error',
                        'message': 'command %s raised an error: %s' % (command, e)
                    })
        else:
            if self.log:
                self.log.error('Could not find command "%s" in %s'
                               % (command, self._v1_command_map.keys()))
            self.send_v1_packet(
                {
                    'command': 'unknown-command',
                    'message': '%s is an unknown command' % command
                })

    def noop(self, packet):
        return

    def log_error_packet(self, packet):
        if self.log:
            self.log.with_fields(packet).error('Received a packet indicating an error')

    def send_ping(self, unique=None):
        if not unique:
            unique = random.randint(0, 65535)

        self.send_v1_packet({
            'command': 'ping',
            'unique': unique
        })

    def send_pong(self, packet):
        self.send_v1_packet({
            'command': 'pong',
            'unique': packet['unique']
        })

    def _path_is_a_file(self, command, path, unique):
        if not path:
            self.send_v1_packet({
                'command': '%s-response' % command,
                'result': False,
                'message': 'path is not set',
                'unique': unique
            })
            return 'path is not set'

        if not os.path.exists(path):
            self.send_v1_packet({
                'command': '%s-response' % command,
                'result': False,
                'path': path,
                'message': 'path does not exist',
                'unique': unique
            })
            return 'path does not exist'

        if not os.path.isfile(path):
            self.send_v1_packet({
                'command': '%s-response' % command,
                'result': False,
                'path': path,
                'message': 'path is not a file',
                'unique': unique
            })
            return 'path is not a file'

        return None

    def _send_file(self, command, source_path, destination_path, unique):
        st = os.stat(source_path, follow_symlinks=True)
        self.send_v1_packet({
            'command': command,
            'result': True,
            'path': destination_path,
            'stat_result': {
                'mode': st.st_mode,
                'size': st.st_size,
                'uid': st.st_uid,
                'gid': st.st_gid,
                'atime': st.st_atime,
                'mtime': st.st_mtime,
                'ctime': st.st_ctime
            },
            'unique': unique
        })

        offset = 0
        with open(source_path, 'rb') as f:
            d = f.read(1024)
            while d:
                self.send_v1_packet({
                    'command': command,
                    'result': True,
                    'path': destination_path,
                    'offset': offset,
                    'encoding': 'base64',
                    'chunk': base64.b64encode(d).decode('utf-8'),
                    'unique': unique
                })
                offset += len(d)
                d = f.read(1024)

            self.send_v1_packet({
                'command': command,
                'result': True,
                'path': destination_path,
                'offset': offset,
                'encoding': 'base64',
                'chunk': None,
                'unique': unique
            })


class UnixDomainSocketAgent(Agent):
    def __init__(self, path, logger=None):
        super().__init__(logger=logger)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.input_fileno = self.s.fileno()
        self.output_fileno = self.s.fileno()
        self.set_fd_nonblocking(self.input_fileno)


class CharacterDeviceAgent(Agent):
    def __init__(self, path, logger=None):
        super().__init__(logger=logger)
        self.input_fileno = os.open(path, os.O_RDWR)
        self.output_fileno = self.input_fileno
        self.set_fd_nonblocking(self.input_fileno)
