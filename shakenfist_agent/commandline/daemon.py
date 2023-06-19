import base64
import click
import distro
from linux_utils.fstab import find_mounted_filesystems
import multiprocessing
import os
from oslo_concurrency import processutils
from pbr.version import VersionInfo
import psutil
import select
import shutil
import signal
import symbolicmode
import sys
import time

from shakenfist_agent import protocol


SIDE_CHANNEL_PATH = '/dev/virtio-ports/sf-agent'


@click.group(help='Daemon commands')
def daemon():
    pass


class SFFileAgent(protocol.FileAgent):
    def __init__(self, path, logger=None):
        super(SFFileAgent, self).__init__(path, logger=logger)

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

        self.send_packet({
            'command': 'agent-start',
            'message': 'version %s' % VersionInfo('shakenfist_agent').version_string(),
            'system_boot_time': psutil.boot_time(),
            'unique': str(time.time())
        })

        if self.log:
            self.log.debug('Setup complete')

        self.incomplete_file_puts = {}

    def close(self):
        self.send_packet({
            'command': 'agent-stop',
            'system_boot_time': psutil.boot_time(),
            'unique': str(time.time())
        })
        super(SFFileAgent, self).close()

    def is_system_running(self, packet):
        out, _ = processutils.execute(
            'systemctl is-system-running', shell=True, check_exit_code=False)
        out = out.rstrip()
        self.send_packet({
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

        self.send_packet({
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
            self.send_packet({
                'command': 'put-file-response',
                'path': packet['path'],
                'unique': packet['unique']
            })
            return

        d = base64.b64decode(packet['chunk'])
        self.incomplete_file_puts[path]['flo'].write(d)

    def chmod(self, packet):
        mode = packet['mode']
        try:
            int(mode)
        except ValueError:
            mode = symbolicmode.symbolic_to_numeric_permissions(mode)

        os.chmod(packet['path'], mode)
        self.send_packet({
            'command': 'chmod-response',
            'path': packet['path'],
            'unique': packet.get('unique', str(time.time()))
        })

    def chown(self, packet):
        shutil.chown(packet.get('path'), user=packet.get('user'), group=packet.get('group'))
        self.send_packet({
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
                self.send_packet({
                    'command': 'watch-file-response',
                    'result': True,
                    'path': self.watched_files[fd]['path'],
                    'chunk': None
                })
                del self.watched_files[fd]

        for fd in readable:
            if fd in self.watched_files:
                try:
                    self.send_packet({
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
            self.send_packet({
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
                self.send_packet({
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
                self.send_packet({
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

        self.send_packet({
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
    if sig == signal.SIGTERM:
        print('Caught SIGTERM, gracefully exiting')
        if CHANNEL:
            CHANNEL.close()
        sys.exit()


@daemon.command(name='run', help='Run the sf-agent daemon')
@click.pass_context
def daemon_run(ctx):
    global CHANNEL

    signal.signal(signal.SIGTERM, exit_gracefully)

    if not os.path.exists(SIDE_CHANNEL_PATH):
        click.echo('Side channel missing, will periodically check.')

        while not os.path.exists(SIDE_CHANNEL_PATH):
            time.sleep(60)

    CHANNEL = SFFileAgent(SIDE_CHANNEL_PATH, logger=ctx.obj['LOGGER'])
    CHANNEL.send_ping()

    while True:
        for packet in CHANNEL.find_packets():
            CHANNEL.dispatch_packet(packet)
        CHANNEL.watch_files()
        CHANNEL.reap_processes()


daemon.add_command(daemon_run)
