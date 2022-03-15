# Copyright 2022 Michael Still

import click
import datetime
import logging


from shakenfist_agent.commandline import daemon


class LogFormatter(logging.Formatter):
    def format(self, record):
        level_to_color = {
            logging.DEBUG: 'blue',
            logging.INFO: None,
            logging.WARNING: 'yellow',
            logging.ERROR: 'red'
        }

        timestamp = str(datetime.datetime.now())
        if not record.exc_info:
            colour = level_to_color.get(record.levelno)
            msg = record.getMessage()
            if colour:
                return '%s %s: %s' % (timestamp,
                                      click.style(logging._levelToName[record.levelno],
                                                  level_to_color[record.levelno]),
                                      msg)
            return '%s %s' % (timestamp, msg)
        return logging.Formatter.format(self, record)


class LoggingHandler(logging.Handler):
    level = logging.INFO

    def emit(self, record):
        try:
            # NOTE(mikal): level looks unused, but is used by the python
            # logging handler
            self.level = logging._nameToLevel[record.levelname.upper()]
            click.echo(self.format(record), err=True)
        except Exception:
            self.handleError(record)


LOG = logging.getLogger(__name__)
handler = LoggingHandler()
handler.formatter = LogFormatter()
LOG.handlers = [handler]


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
