import base64
import click
from collections import defaultdict
import distro
from linux_utils.fstab import find_mounted_filesystems
import os
from oslo_concurrency import processutils
from pbr.version import VersionInfo
import psutil
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

        self.add_command('is-system-running', self.is_system_running)
        self.add_command('gather-facts', self.gather_facts)
        self.add_command('fetch-file', self.fetch_file)
        self.send_packet({
            'command': 'agent-start',
            'message': 'version %s' % VersionInfo('shakenfist_agent').version_string(),
            'system_boot_time': psutil.boot_time()
        })
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

    def fetch_file(self, packet):
        path = packet.get('path')
        if not path:
            self.send_packet({
                'command': 'fetch-file-response',
                'result': False,
                'message': 'path is not set'
            })
            return

        if not os.path.exists(path):
            self.send_packet({
                'command': 'fetch-file-response',
                'result': False,
                'path': path,
                'message': 'path does not exist'
            })
            return

        if not os.is_file(path, follow_symlinks=True):
            self.send_packet({
                'command': 'fetch-file-response',
                'result': False,
                'path': path,
                'message': 'path is not a file'
            })
            return

        st = os.stat(path, follow_symlinks=True)
        self.send_packet({
            'command': 'fetch-file-response',
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
                    'command': 'fetch-file-response',
                    'result': True,
                    'path': path,
                    'offset': offset,
                    'encoding': 'base64',
                    'chunk': base64.b64encode(d).encode('utf-8')
                })
                offset += len(d)
                d = f.read(1024)

            self.send_packet({
                'command': 'fetch-file-response',
                'result': True,
                'path': path,
                'offset': offset,
                'encoding': 'base64',
                'chunk': None
            })


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
        processed = defaultdict(int)

        for packet in CHANNEL.find_packets():
            command = packet.get('command', 'none')
            processed[command] += 1
            # if processed[command] > 1 and command in ['ping', 'is-system-running']:
            #     continue

            try:
                CHANNEL.dispatch_packet(packet)
            except protocol.UnknownCommand as e:
                print(e)


daemon.add_command(daemon_run)
