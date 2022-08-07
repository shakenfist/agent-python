# Copyright 2022 Michael Still

import click
from shakenfist_utilities import logs
import logging


from shakenfist_agent.commandline import daemon


LOG = logs.setup_console(__name__)


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx, verbose):
    if not ctx.obj:
        ctx.obj = {}
    ctx.obj['LOGGER'] = LOG

    if verbose:
        ctx.obj['VERBOSE'] = True
        LOG.setLevel(logging.DEBUG)
        LOG.debug('Set log level to DEBUG')
    else:
        ctx.obj['VERBOSE'] = False
        LOG.setLevel(logging.INFO)


cli.add_command(daemon.daemon)
