import logging
from unittest import mock

import testtools
from click.testing import CliRunner

from shakenfist_agent import main


class LoggingStateTestCase(testtools.TestCase):
    """Base class which puts the process-wide logging state back.

    configure_logging() reconfigures the root logger for the whole
    process, so every test here has to hand the interpreter back what it
    was given or the next test -- and stestr's own output -- inherits it.
    """

    def setUp(self):
        super().setUp()

        root = logging.root
        module_logger = logging.getLogger(main.__name__)
        saved = [
            (root, list(root.handlers), root.level),
            (module_logger, list(module_logger.handlers), module_logger.level),
        ]
        propagate = module_logger.propagate

        def restore():
            for logger, handlers, level in saved:
                logger.handlers = handlers
                logger.setLevel(level)
            module_logger.propagate = propagate

        self.addCleanup(restore)


class ConfigureLoggingTestCase(LoggingStateTestCase):
    def test_root_gets_a_handler(self):
        logging.root.handlers = []

        main.configure_logging()

        self.assertNotEqual([], logging.root.handlers)

    def test_our_logger_does_not_propagate(self):
        # Without this our records reach both setup_console()'s handler
        # and root's, and are printed twice.
        module_logger = logging.getLogger(main.__name__)
        module_logger.propagate = True

        main.configure_logging()

        self.assertFalse(module_logger.propagate)


class VerboseTestCase(LoggingStateTestCase):
    def _invoke(self, args):
        # Stand in for basicConfig so the root handler is one this test
        # owns, rather than whatever the test runner left behind.
        def install(**kwargs):
            logging.root.handlers = [logging.NullHandler()]
            logging.root.setLevel(kwargs.get('level', logging.INFO))

        with mock.patch.object(logging, 'basicConfig', side_effect=install):
            # --help on the subcommand exits after the group callback has
            # run, which is the part under test, without starting the
            # daemon.
            return CliRunner().invoke(main.cli, args + ['daemon', '--help'])

    def test_verbose_moves_the_root_logger_too(self):
        # Raising LOG alone would leave every other module's DEBUG records
        # filtered out at root.
        result = self._invoke(['--verbose'])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(logging.DEBUG, logging.root.level)
        for handler in logging.root.handlers:
            self.assertEqual(logging.DEBUG, handler.level)
        self.assertEqual(
            logging.DEBUG,
            logging.getLogger('grpc').getEffectiveLevel())

    def test_default_leaves_root_at_info(self):
        result = self._invoke([])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(logging.INFO, logging.root.level)
