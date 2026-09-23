import io
import logging
from unittest import mock

import testtools
from click.testing import CliRunner

from shakenfist_agent import main


# A logger standing in for "any module other than main", named under this
# test module so that registering it with the logging manager -- which
# cannot be undone -- seeds nothing a real dependency would use.
OTHER = 'shakenfist_agent.tests.test_main.other'


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
        other_logger = logging.getLogger(OTHER)
        saved = [
            (root, list(root.handlers), root.level),
            (module_logger, list(module_logger.handlers), module_logger.level),
            (other_logger, list(other_logger.handlers), other_logger.level),
        ]
        propagate = module_logger.propagate

        def restore():
            for logger, handlers, level in saved:
                logger.handlers = handlers
                logger.setLevel(level)
            module_logger.propagate = propagate

        self.addCleanup(restore)

        # basicConfig(force=True) closes whatever handlers root has, so
        # hand it none of the runner's.
        root.handlers = []


class ConfigureLoggingTestCase(LoggingStateTestCase):
    def _configure(self):
        """Configure logging, capturing root's output and our own.

        Root's handler is the StreamHandler basicConfig() installs; our own
        logger's is the one setup_console() installed, which print()s.
        """
        main.configure_logging()
        root_stream = io.StringIO()
        for handler in logging.root.handlers:
            handler.setStream(root_stream)
        stdout = self._capture_stdout()
        return root_stream, stdout

    def _capture_stdout(self):
        patcher = mock.patch('sys.stdout', new_callable=io.StringIO)
        stdout = patcher.start()
        self.addCleanup(patcher.stop)
        return stdout

    def test_other_modules_records_are_emitted(self):
        # The defect being fixed: records from any module but main reached
        # a root logger with no handler, and were dropped.
        root_stream, stdout = self._configure()

        logging.getLogger(OTHER).info('from another module')

        self.assertEqual(1, root_stream.getvalue().count('from another module'))
        self.assertEqual(0, stdout.getvalue().count('from another module'))

    def test_our_records_are_emitted_once(self):
        # With root now holding a handler, main's records would reach both
        # it and setup_console()'s handler unless propagation is off.
        root_stream, stdout = self._configure()

        main.LOG.info('from main')

        self.assertEqual(1, stdout.getvalue().count('from main'))
        self.assertEqual(0, root_stream.getvalue().count('from main'))

    def test_existing_root_handler_is_replaced(self):
        # basicConfig() without force is a no-op once root has a handler,
        # which would leave both the handler and the level unapplied.
        stale = logging.NullHandler()
        logging.root.handlers = [stale]
        logging.root.setLevel(logging.WARNING)

        root_stream, _ = self._configure()
        logging.getLogger(OTHER).info('after a stale handler')

        self.assertNotIn(stale, logging.root.handlers)
        self.assertEqual(logging.INFO, logging.root.level)
        self.assertIn('after a stale handler', root_stream.getvalue())


class VerboseTestCase(LoggingStateTestCase):
    def _invoke(self, args):
        # Stand in for basicConfig so the root handler is one this test
        # owns, rather than a StreamHandler on the runner's stderr.
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
        self.assertTrue(logging.getLogger(OTHER).isEnabledFor(logging.DEBUG))
        for handler in logging.root.handlers:
            self.assertEqual(logging.DEBUG, handler.level)

    def test_cli_configures_logging(self):
        # configure_logging() is only worth having if running sf-agent
        # calls it.
        logging.getLogger(main.__name__).propagate = True

        result = self._invoke([])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotEqual([], logging.root.handlers)
        self.assertFalse(logging.getLogger(main.__name__).propagate)

    def test_default_leaves_other_modules_at_info(self):
        result = self._invoke([])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertFalse(logging.getLogger(OTHER).isEnabledFor(logging.DEBUG))
        self.assertTrue(logging.getLogger(OTHER).isEnabledFor(logging.INFO))
