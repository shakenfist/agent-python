import click
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
        self.send_packet({
            'command': 'is-system-running-response',
            'result': out.rstrip() == 'running'
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
        packet = channel.find_packet()
        if packet:
            try:
                channel.dispatch_packet(packet)
            except protocol.UnknownCommand as e:
                print(e)


daemon.add_command(daemon_run)
