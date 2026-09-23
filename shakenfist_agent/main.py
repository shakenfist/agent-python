# Copyright 2022 Michael Still

import click
import logging

from shakenfist_agent import log as logs

from shakenfist_agent.commandline import daemon


LOG = logs.setup_console(__name__)


def configure_logging():
    """Give the root logger a handler.

    setup_console() raises the root logger's level to INFO, but attaches
    its handler to this module's logger only. Records from every other
    module -- ours and our dependencies' alike -- therefore propagate up
    to a root logger with no handler on it and are dropped. basicConfig()
    gives root a handler. Once root has one, our own records reach both it
    and the handler setup_console() installed and are printed twice, which
    is what turning off propagation prevents. setup_console() already does
    that for every logger it creates; it is repeated here so that the
    entry point states the whole of its own logging setup in one place.

    Called from cli() rather than run at import: this reconfigures logging
    for the whole process, which is sf-agent's business when it is the
    program being run and nobody else's when a test merely imports this
    module. force=True for the same reason: without it basicConfig() is a
    silent no-op if anything imported earlier already gave root a handler,
    leaving root unconfigured while propagation is still turned off below.
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(name)s: %(message)s',
        force=True)
    logging.getLogger(__name__).propagate = False


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx, verbose):
    configure_logging()

    if not ctx.obj:
        ctx.obj = {}
    ctx.obj['LOGGER'] = LOG

    if verbose:
        ctx.obj['VERBOSE'] = True
        # Raising LOG alone would leave every other module's DEBUG records
        # filtered out at the root logger and its handler.
        logging.root.setLevel(logging.DEBUG)
        for handler in logging.root.handlers:
            handler.setLevel(logging.DEBUG)
        LOG.setLevel(logging.DEBUG)
        LOG.debug('Set log level to DEBUG')
    else:
        ctx.obj['VERBOSE'] = False
        LOG.setLevel(logging.INFO)


cli.add_command(daemon.daemon)
