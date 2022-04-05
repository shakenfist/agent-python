import click
import distro
import linux_utils
import os
from oslo_concurrency import processutils
import sys

from shakenfist_agent import protocol


SIDE_CHANNEL_PATH = '/dev/virtio-ports/sf-agent'


@click.group(help='Daemon commands')
def daemon():
    pass


class SFFileAgent(protocol.FileAgent):
    def __init__(self, path, logger=None):
        super(SFFileAgent, self).__init__(path, logger=logger)

        self.add_command('is-system-running', self.is_system_running)
        self.log.debug('Setup complete')

    def is_system_running(self, _packet):
        out, _ = processutils.execute(
            'systemctl is-system-running', shell=True, check_exit_code=False)
        out = out.rstrip()
        self.send_packet({
            'command': 'is-system-running-response',
            'result': out == 'running',
            'message': out
        })

    def gather_facts(self, _packet):
        facts = {
            'distribution': distro.info(),
            'mounts': [],
            'ssh-host-keys': {}
        }

        for entry in linux_utils.fstab.find_mounted_filesystems():
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
            'command': 'gater-facts-response',
            'result': facts
        })


@daemon.command(name='run', help='Run the sf-agent daemon')
@click.pass_context
def daemon_run(ctx):
    if not os.path.exists(SIDE_CHANNEL_PATH):
        click.echo('Side channel missing')
        sys.exit(1)
    channel = SFFileAgent(SIDE_CHANNEL_PATH, logger=ctx.obj['LOGGER'])
    channel.send_ping()

    while True:
        for packet in channel.find_packets():
            try:
                channel.dispatch_packet(packet)
            except protocol.UnknownCommand as e:
                print(e)


daemon.add_command(daemon_run)
