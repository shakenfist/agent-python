import base64
import click
import distro
import fcntl
from linux_utils.fstab import find_mounted_filesystems
import multiprocessing
import os
from oslo_concurrency import processutils
from pbr.version import VersionInfo
import psutil
import select
import shutil
import signal
import socket
import struct
import symbolicmode
import time
import threading

from shakenfist_utilities import logs
from shakenfist_utilities import random as sf_random

from shakenfist_agent import protocol


SIDE_CHANNEL_PATH = '/dev/virtio-ports/sf-agent'
VSOCK_PORT = 1025
EXIT = threading.Event()
LOG = logs.setup_console(__name__)


@click.group(help='Daemon commands')
def daemon():
    pass


class AgentJob:
    def __init__(self, logger):
        self.logger = logger


class SerialAgentJob(AgentJob):
    def run(self):
        if not os.path.exists(SIDE_CHANNEL_PATH):
            click.echo('Side channel missing, will periodically check.')

            while not os.path.exists(SIDE_CHANNEL_PATH):
                time.sleep(60)

        CHANNEL = SFCharacterDeviceAgent(SIDE_CHANNEL_PATH, logger=self.logger)
        CHANNEL.send_ping()

        while True:
            for packet in CHANNEL.find_packets():
                CHANNEL.dispatch_packet(packet)
            CHANNEL.watch_files()
            CHANNEL.reap_processes()


class VSockAgentJob(AgentJob):
    def __init__(self, logger, conn):
        super().__init__(logger)
        self.conn = conn

    def run(self):
        while True:
            buf = self.conn.recv(1024)
            click.echo(f' in: {buf}')
            if not buf:
                click.echo('Nothing received, exiting')
                break

            click.echo(f'out: {buf}')
            self.conn.sendall(buf)

        self.conn.close()


class SFCharacterDeviceAgent(protocol.CharacterDeviceAgent):
    def __init__(self, path, logger=None):
        super(SFCharacterDeviceAgent, self).__init__(path, logger=logger)

        self.watched_files = {}
        self.executing_commands = []

        self.add_command('is-system-running', self.is_system_running)
        self.add_command('gather-facts', self.gather_facts)
        self.add_command('put-file', self.put_file)
        self.add_command('chmod', self.chmod)
        self.add_command('chown', self.chown)
        self.add_command('get-file', self.get_file)
        self.add_command('watch-file', self.watch_file)
        self.add_command('execute', self.execute)

        self.send_v1_packet({
            'command': 'agent-start',
            'message': 'version %s' % VersionInfo('shakenfist_agent').version_string(),
            'system_boot_time': psutil.boot_time(),
            'unique': str(time.time())
        })

        if self.log:
            self.log.debug('Setup complete')

        self.incomplete_file_puts = {}

    def close(self):
        self.send_v1_packet({
            'command': 'agent-stop',
            'system_boot_time': psutil.boot_time(),
            'unique': str(time.time())
        })
        super(SFCharacterDeviceAgent, self).close()

    def is_system_running(self, packet):
        out, _ = processutils.execute(
            'systemctl is-system-running', shell=True, check_exit_code=False)
        out = out.rstrip()
        self.send_v1_packet({
            'command': 'is-system-running-response',
            'result': out == 'running',
            'message': out,
            'system_boot_time': psutil.boot_time(),
            'unique': packet.get('unique', str(time.time()))
        })

    def gather_facts(self, packet):
        facts = {
            'distribution': distro.info(),
            'mounts': [],
            'ssh-host-keys': {}
        }

        # We should allow this agent to at least run on MacOS
        if facts['distribution']['id'] != 'darwin':
            for entry in find_mounted_filesystems():
                facts['mounts'].append({
                    'device': entry.device,
                    'mount_point': entry.mount_point,
                    'vfs_type': entry.vfs_type
                })

        for kind, path in [('rsa', '/etc/ssh/ssh_host_rsa_key.pub'),
                           ('ecdsa',  '/etc/ssh/ssh_host_ecdsa_key.pub'),
                           ('ed25519', '/etc/ssh/ssh_host_ed25519_key.pub')]:
            if os.path.exists(path):
                with open(path) as f:
                    facts['ssh-host-keys'][kind] = f.read()

        self.send_v1_packet({
            'command': 'gather-facts-response',
            'result': facts,
            'unique': packet.get('unique', str(time.time()))
        })

    def put_file(self, packet):
        path = packet['path']
        if path not in self.incomplete_file_puts:
            self.incomplete_file_puts[path] = {}
            self.incomplete_file_puts[path]['flo'] = open(path, 'wb')

        if 'stat_result' in packet:
            self.incomplete_file_puts[path].update(packet['stat_result'])
            return

        if packet['chunk'] is None:
            self.incomplete_file_puts[path]['flo'].close()
            del self.incomplete_file_puts[path]
            self.log.with_fields(packet).info('File put complete')
            self.send_v1_packet({
                'command': 'put-file-response',
                'path': packet['path'],
                'unique': packet['unique']
            })
            return

        d = base64.b64decode(packet['chunk'])
        self.incomplete_file_puts[path]['flo'].write(d)

    def chmod(self, packet):
        symbolicmode.chmod(packet['path'], packet['mode'])
        self.send_v1_packet({
            'command': 'chmod-response',
            'path': packet['path'],
            'unique': packet.get('unique', str(time.time()))
        })

    def chown(self, packet):
        shutil.chown(packet.get('path'), user=packet.get('user'), group=packet.get('group'))
        self.send_v1_packet({
            'command': 'chown-response',
            'path': packet['path'],
            'unique': packet.get('unique', str(time.time()))
        })

    def get_file(self, packet):
        unique = packet.get('unique', str(time.time()))
        path = packet.get('path')
        error = self._path_is_a_file('get-file', path, unique)
        if error:
            return
        self._send_file('get-file-response', path, path, unique)

    def watch_file(self, packet):
        unique = packet.get('unique', str(time.time()))
        path = packet.get('path')
        if not self._path_is_a_file('watch-file', path, unique):
            return

        flo = open(path, 'rb')
        self.set_fd_nonblocking(flo.fileno())

        self.watched_files[flo.fileno()] = {
            'path': path,
            'flo': flo
        }

    def watch_files(self):
        readable = []
        for f in self.watched_files:
            readable.append(f['flo'])
        readable, _, exceptional = select.select(readable, [], readable, 0)

        for fd in exceptional:
            if fd in self.watched_files:
                self.send_v1_packet({
                    'command': 'watch-file-response',
                    'result': True,
                    'path': self.watched_files[fd]['path'],
                    'chunk': None
                })
                del self.watched_files[fd]

        for fd in readable:
            if fd in self.watched_files:
                try:
                    self.send_v1_packet({
                        'command': 'watch-file-response',
                        'result': True,
                        'path': self.watched_files[fd]['path'],
                        'chunk': base64.base64encode(
                            self.watched_files[fd]['flo'].read(1024)).decode('utf-8')
                    })
                except BlockingIOError:
                    pass

    def execute(self, packet):
        unique = packet.get('unique', str(time.time()))
        if 'command-line' not in packet:
            self.send_v1_packet({
                'command': 'execute-response',
                'result': False,
                'message': 'command-line is not set',
                'unique': unique
            })
            return

        if packet.get('block-for-result', True):
            try:
                out, err = processutils.execute(
                    packet['command-line'], shell=True, check_exit_code=True)
                self.send_v1_packet({
                    'command': 'execute-response',
                    'command-line': packet['command-line'],
                    'result': True,
                    'stdout': out,
                    'stderr': err,
                    'return-code': 0,
                    'unique': unique
                })
                return

            except processutils.ProcessExecutionError as e:
                self.send_v1_packet({
                    'command': 'execute-response',
                    'command-line': packet['command-line'],
                    'result': False,
                    'stdout': e.stdout,
                    'stderr': e.stderr,
                    'return-code': e.exit_code,
                    'unique': unique
                })
                return

        def _execute(cmd):
            processutils.execute(cmd, shell=True, check_exit_code=False)

        p = multiprocessing.Process(
            target=_execute, args=(packet['command-line'],))
        p.start()
        self.executing_commands.append(p)

        self.send_v1_packet({
            'command': 'execute-response',
            'command-line': packet['command-line'],
            'pid': p.pid,
            'unique': unique
        })

    def reap_processes(self):
        for p in self.executing_commands:
            if not p.is_alive():
                p.join(1)
            self.executing_commands.remove(p)


CHANNEL = None


def exit_gracefully(sig, _frame):
    global EXIT
    if sig == signal.SIGTERM:
        click.echo('Received SIGTERM')
        EXIT.set()


@daemon.command(name='run', help='Run the sf-agent daemon')
@click.pass_context
def daemon_run(ctx):
    global CHANNEL
    global EXIT

    signal.signal(signal.SIGTERM, exit_gracefully)

    # Start the v1 thread
    v1 = SerialAgentJob(ctx.obj['LOGGER'])
    v1_thread = threading.Thread(target=v1.run, daemon=True, name='v1')
    v1_thread.start()

    # Start listening for v2 connections on the vsock.

    # Lookup our CID. This is a 32 bit unsigned int returned from an ioctl
    # against /dev/vsock. As best as I can tell the empty string argument
    # at the end is because that is used as a buffer to return the result
    # in. Yes really.
    with open('/dev/vsock', 'rb') as f:
        r = fcntl.ioctl(f, socket.IOCTL_VM_SOCKETS_GET_LOCAL_CID, '    ')
        cid = struct.unpack('I', r)[0]
    click.echo(f'Our v2 vsock CID is {cid}.')

    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.bind((cid, VSOCK_PORT))
    s.listen()
    s.settimeout(0.2)
    click.echo('Listening for incoming v2 requests')

    workers = {}
    while not EXIT.is_set():
        try:
            conn, (remote_cid, remote_port) = s.accept()
            click.echo(f'Connection from {remote_cid} on with remote port '
                       f'{remote_port}')
        except socket.timeout:
            conn = None

        if conn:
            thread_name = sf_random.random_id()
            log = LOG.with_fields({
                'remote_cid': remote_cid,
                'remote_port': remote_port,
                'thread_name': thread_name
            })

            worker_object = VSockAgentJob(log, conn)
            worker_thread = threading.Thread(
                target=worker_object.run, daemon=True, name=thread_name)
            workers[thread_name] = {
                'object': worker_object,
                'thread': worker_thread
            }
            worker_thread.start()

        remaining_workers = {}
        for thread_name in workers:
            if workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = workers[thread_name]
            else:
                workers[thread_name]['thread'].join(0.2)
        workers = remaining_workers

    click.echo('Stopping')

    while workers:
        click.echo(f'There are {len(workers)} remaining workers')

        remaining_workers = {}
        for thread_name in workers:
            if workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = workers[thread_name]
                click.echo(f'Thread is still executing {thread_name}')
            else:
                click.echo(f'Reaping thread: {thread_name}')
                workers[thread_name]['thread'].join(0.2)

        workers = remaining_workers
        if workers:
            time.sleep(5)

    click.echo(f'There are {len(workers)} remaining workers')
    click.echo('Stopped')

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    click.echo('Terminating ourselves')
    raise SystemExit(0)


daemon.add_command(daemon_run)
