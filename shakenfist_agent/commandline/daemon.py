import click
import os
import sys

from shakenfist_agent import protocol


SIDE_CHANNEL_PATH = '/dev/virtio-ports/sf-agent'


@click.group(help='Daemon commands')
def daemon():
    pass


@daemon.command(name='run', help='Run the sf-agent daemon')
@click.option('--side-channel/--stdin', default=True)
@click.pass_context
def daemon_run(ctx, side_channel):
    channel = None
    if side_channel:
        if not os.path.exists(SIDE_CHANNEL_PATH):
            click.echo('Side channel missing')
            sys.exit(1)
        channel = protocol.SocketAgent(SIDE_CHANNEL_PATH)
    else:
        channel = protocol.StdInOutAgent()


daemon.add_command(daemon_run)
