import base64
import click
import distro
from linux_utils.fstab import find_mounted_filesystems
import os
from oslo_concurrency import processutils
from pbr.version import VersionInfo
import psutil
import select
import signal
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

        self.watched_files = []

        self.add_command('is-system-running', self.is_system_running)
        self.add_command('gather-facts', self.gather_facts)
        self.add_command('get-file', self.get_file)
        self.add_command('watch-file', self.watch_file)

        self.send_packet({
            'command': 'agent-start',
            'message': 'version %s' % VersionInfo('shakenfist_agent').version_string(),
            'system_boot_time': psutil.boot_time()
        })

        if self.log:
            self.log.debug('Setup complete')

    def close(self):
        self.send_packet({
            'command': 'agent-stop',
            'system_boot_time': psutil.boot_time()
        })
        super(SFFileAgent, self).close()

    def is_system_running(self, _packet):
        out, _ = processutils.execute(
            'systemctl is-system-running', shell=True, check_exit_code=False)
        out = out.rstrip()
        self.send_packet({
            'command': 'is-system-running-response',
            'result': out == 'running',
            'message': out,
            'system_boot_time': psutil.boot_time()
        })

    def gather_facts(self, _packet):
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
            'result': facts
        })

    def _path_is_a_file(self, command, path):
        if not path:
            self.send_packet({
                'command': '%s-response' % command,
                'result': False,
                'message': 'path is not set'
            })
            return False

        if not os.path.exists(path):
            self.send_packet({
                'command': '%s-response' % command,
                'result': False,
                'path': path,
                'message': 'path does not exist'
            })
            return False

        if not os.path.isfile(path):
            self.send_packet({
                'command': '%s-response' % command,
                'result': False,
                'path': path,
                'message': 'path is not a file'
            })
            return False

        return True

    def get_file(self, packet):
        path = packet.get('path')
        if not self._path_is_a_file('get-file', path):
            return

        st = os.stat(path, follow_symlinks=True)
        self.send_packet({
            'command': 'get-file-response',
            'result': True,
            'path': path,
            'stat_result': {
                'mode': st.st_mode,
                'size': st.st_size,
                'uid': st.st_uid,
                'gid': st.st_gid,
                'atime': st.st_atime,
                'mtime': st.st_mtime,
                'ctime': st.st_ctime
            }
        })

        offset = 0
        with open(path, 'rb') as f:
            d = f.read(1024)
            while d:
                self.send_packet({
                    'command': 'get-file-response',
                    'result': True,
                    'path': path,
                    'offset': offset,
                    'encoding': 'base64',
                    'chunk': base64.b64encode(d).decode('utf-8')
                })
                offset += len(d)
                d = f.read(1024)

            self.send_packet({
                'command': 'get-file-response',
                'result': True,
                'path': path,
                'offset': offset,
                'encoding': 'base64',
                'chunk': None
            })

    def watch_file(self, packet):
        path = packet.get('path')
        if not self._path_is_a_file('watch-file', path):
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


daemon.add_command(daemon_run)
