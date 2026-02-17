"""Lightweight console logging with .with_fields() support."""

import copy
import datetime
import logging


class _ConsoleLogger(logging.Logger):
    """Logger subclass with .with_fields() support."""

    def with_fields(self, fields=None):
        return _ConsoleAdapter(self, fields)

    def with_prefix(self, prefix=None):
        return _ConsoleAdapter(self, None, prefix)


class _ConsoleAdapter(logging.LoggerAdapter):
    """LoggerAdapter that appends extra fields to messages."""

    def __init__(self, logger, extra=None, prefix=None):
        self._logger = logger
        self._extra = {}
        if isinstance(extra, dict):
            self._extra = {
                k.lower(): v for k, v in extra.items()
            }
        self._prefix = prefix
        super().__init__(
            self._logger,
            {
                'extra_fields': self._extra,
                'prefix': self._prefix,
            })

    def with_fields(self, fields=None):
        extra = copy.deepcopy(self._extra)
        if isinstance(fields, dict):
            extra.update({
                k.lower(): v for k, v in fields.items()
            })
        return _ConsoleAdapter(
            self._logger, extra, self._prefix)

    def with_prefix(self, prefix=None):
        if prefix is None:
            return self
        return _ConsoleAdapter(
            self._logger, self._extra, prefix)

    def process(self, msg, kwargs):
        extra_fields = self.extra.get('extra_fields', {})
        for key in extra_fields:
            msg += '\n\t%s: %s' % (key, extra_fields[key])
        return msg, kwargs


class _ConsoleFormatter(logging.Formatter):
    """Formatter with ANSI color-coded log levels."""

    COLORS = {
        logging.DEBUG: '\033[34m',
        logging.INFO: '',
        logging.WARNING: '\033[033m',
        logging.ERROR: '\033[031m',
    }
    RESET = '\033[0m'

    def format(self, record):
        if record.exc_info:
            return super().format(record)
        color = self.COLORS.get(record.levelno, '')
        level = logging.getLevelName(record.levelno)
        timestamp = str(datetime.datetime.now())
        return '%s %s%s%s: %s' % (
            timestamp, color, level,
            self.RESET, record.getMessage())


class _ConsoleHandler(logging.Handler):
    """Handler that prints to stdout."""

    def emit(self, record):
        try:
            print(self.format(record))
        except Exception:
            self.handleError(record)


def setup_console(name):
    """Set up a console logger with .with_fields() support."""
    logging.setLoggerClass(_ConsoleLogger)
    logging.root.setLevel(logging.INFO)
    log = logging.getLogger(name)
    handler = _ConsoleHandler()
    handler.formatter = _ConsoleFormatter()
    log.handlers = [handler]
    return log.with_prefix()
