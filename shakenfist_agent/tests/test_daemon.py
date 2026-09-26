import base64
import mock
import os
import psutil
import tempfile
import testtools

import symbolicmode

from shakenfist_agent import log as logs
from shakenfist_agent.commandline import daemon
from shakenfist_agent.commandline.daemon import random_id
from shakenfist_agent.protos import agent_pb2
from shakenfist_agent.protos import common_pb2


LOG = logs.setup_console(__name__)


class DaemonAgentV2TestCase(testtools.TestCase):
    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_send_garbage(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)
        d.buffered += b'dkjrfgjklsdfhgjukeqradfhjkftghasdfjkghdsfjklhgjkldsfhgj'
        d._attempt_decode()

        # And make sure we did nothing
        self.assertEqual(0, len(mock_send_responses.mock_calls))

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_command_error(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send an ExecuteRequest which cannot be started because its working
        # directory does not exist
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                execute_request=common_pb2.ExecuteRequest(
                    command='whoami',
                    working_directory='/nosuch/directory',
                    io_priority=common_pb2.ExecuteRequest.NORMAL
                )
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertFalse(env[0].HasField('execute_reply'))
        self.assertTrue(env[0].HasField('command_error'))

        error_commands = env[0].command_error.last_envelope.commands
        self.assertEqual(1, len(error_commands))
        self.assertTrue(error_commands[0].HasField('execute_request'))

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_hypervisor_welcome(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a HypervisorWelcome
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                hypervisor_welcome=agent_pb2.HypervisorWelcome(
                    version='0.8'
                )
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(
            env[0].HasField('agent_welcome'),
            f'Request was {msg}\n\n'
            f'Response was {env[0]}')
        self.assertTrue(env[0].agent_welcome.version.startswith('version '))

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_hypervisor_departure(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a HypervisorDeparture
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                hypervisor_departure=agent_pb2.HypervisorDeparture()
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # The agent doesn't say anything to a departing hypervisor
        self.assertEqual(0, len(mock_send_responses.mock_calls))

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_ping(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a PingRequest
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                ping_request=agent_pb2.PingRequest()
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(env[0].HasField('ping_reply'))

    @mock.patch('subprocess.run',
                return_value=mock.Mock(stdout='running'))
    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_is_system_running(self, mock_send_responses, mock_run):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a IsSystemRunningRequest
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                is_system_running_request=agent_pb2.IsSystemRunningRequest()
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_run.mock_calls))
        self.assertEqual(
            'systemctl is-system-running',
            mock_run.call_args_list[0].args[0])

        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(env[0].HasField('is_system_running_reply'))
        self.assertTrue(env[0].is_system_running_reply.result)
        self.assertEqual('running', env[0].is_system_running_reply.message)

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_gather_facts(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a GatherFactsRequest
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                gather_facts_request=agent_pb2.GatherFactsRequest()
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(env[0].HasField('gather_facts_reply'))

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_execute(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send an ExecuteRequest, this really executes the command because
        # mocking Popen is fiddly.
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                execute_request=common_pb2.ExecuteRequest(
                    command='whoami',
                    io_priority=common_pb2.ExecuteRequest.NORMAL
                )
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(env[0].HasField('execute_reply'))
        self.assertNotEqual(0, len(env[0].execute_reply.stdout))

    def _execute(self, mock_send_responses, command):
        d = daemon.VSockAgentJob(LOG, None)

        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                execute_request=common_pb2.ExecuteRequest(
                    command=command,
                    io_priority=common_pb2.ExecuteRequest.NORMAL
                )
            )
        )

        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]
        self.assertEqual(1, len(env), f'Unexpected length: {env}')
        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(env[0].HasField('execute_reply'))
        return env[0].execute_reply

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_execute_missing_command(self, mock_send_responses):
        # A missing executable is reported by the shell as a normal reply with
        # exit code 127, not as a command error.
        reply = self._execute(mock_send_responses, '/bin/nosuch')
        self.assertEqual(127, reply.exit_code)
        self.assertNotEqual(0, len(reply.stderr))

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_execute_environment_prefix(self, mock_send_responses):
        reply = self._execute(mock_send_responses, 'SF_TEST_VALUE=banana printenv SF_TEST_VALUE')
        self.assertEqual(0, reply.exit_code)
        self.assertEqual('banana\n', reply.stdout)

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_execute_shell_builtin(self, mock_send_responses):
        reply = self._execute(mock_send_responses, 'cd / && pwd')
        self.assertEqual(0, reply.exit_code)
        self.assertEqual('/\n', reply.stdout)

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_execute_environment_without_path(self, mock_send_responses):
        # environment_variables replaces the whole environment, so there is no
        # PATH. The ionice wrapper must still be found. LOW priority forces the
        # wrapper unless the test itself already runs at that priority.
        if not hasattr(psutil.Process(), 'ionice'):
            self.skipTest('ionice is not supported on this platform')

        d = daemon.VSockAgentJob(LOG, None)
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=random_id(),
                execute_request=common_pb2.ExecuteRequest(
                    command='echo $SF_TEST_VALUE',
                    environment_variables=[
                        common_pb2.EnvironmentVariable(name='SF_TEST_VALUE', value='banana')
                    ],
                    io_priority=common_pb2.ExecuteRequest.LOW
                )
            )
        )
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        env = mock_send_responses.call_args_list[0].args[0]
        self.assertTrue(env[0].HasField('execute_reply'), env[0])
        self.assertEqual(0, env[0].execute_reply.exit_code, env[0].execute_reply.stderr)
        self.assertEqual('banana\n', env[0].execute_reply.stdout)

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    @mock.patch('psutil.Process')
    @mock.patch('subprocess.Popen')
    def _mocked_execute(self, ionice, network_namespace, io_priority,
                        mock_popen, mock_process, mock_send_responses):
        # Run an execute request with Popen mocked out and return the command
        # line Popen was given, or the command error if there was one.
        if ionice is None:
            mock_process.return_value.ionice.side_effect = AttributeError
        else:
            mock_process.return_value.ionice.return_value = ionice
        mock_popen.return_value.communicate.return_value = (b'', b'')
        mock_popen.return_value.returncode = 0

        d = daemon.VSockAgentJob(LOG, None)
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=random_id(),
                execute_request=common_pb2.ExecuteRequest(
                    command="FOO='a b' cmd | other",
                    network_namespace=network_namespace,
                    io_priority=io_priority
                )
            )
        )
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        env = mock_send_responses.call_args_list[0].args[0]
        if env[0].HasField('command_error'):
            return None, env[0].command_error.error

        self.assertTrue(env[0].HasField('execute_reply'))
        self.assertTrue(mock_popen.call_args.kwargs['shell'])
        return mock_popen.call_args.args[0], None

    def test_execute_wrapped_command(self):
        # Wrappers must apply to a quoted inner shell, not to the first word
        # of the command line.
        command, _ = self._mocked_execute((0, 0), 'ns1', common_pb2.ExecuteRequest.HIGH)
        self.assertEqual(
            "ip netns exec ns1 ionice -c 2 -n 0 /bin/sh -c 'FOO='\"'\"'a b'\"'\"' cmd | other'",
            command)

    def test_execute_network_namespace_only(self):
        command, _ = self._mocked_execute((2, 4), 'ns1', common_pb2.ExecuteRequest.NORMAL)
        self.assertEqual(
            "ip netns exec ns1 /bin/sh -c 'FOO='\"'\"'a b'\"'\"' cmd | other'",
            command)

    def test_execute_network_namespace_is_quoted(self):
        command, _ = self._mocked_execute((2, 4), 'ns1; touch /tmp/x', common_pb2.ExecuteRequest.NORMAL)
        self.assertTrue(command.startswith("ip netns exec 'ns1; touch /tmp/x' /bin/sh -c "), command)

    def test_execute_unwrapped_command_is_verbatim(self):
        command, _ = self._mocked_execute((2, 4), '', common_pb2.ExecuteRequest.NORMAL)
        self.assertEqual("FOO='a b' cmd | other", command)

    def test_execute_ionice_unsupported(self):
        # Without ionice support NORMAL runs unwrapped, anything else is an
        # error.
        command, _ = self._mocked_execute(None, '', common_pb2.ExecuteRequest.NORMAL)
        self.assertEqual("FOO='a b' cmd | other", command)

        command, error = self._mocked_execute(None, '', common_pb2.ExecuteRequest.HIGH)
        self.assertIsNone(command)
        self.assertEqual('Changing IO priority is not supported on this platform', error)

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_put_file(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a PutFileRequest, and then a series of FileChunks
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                put_file_request=agent_pb2.PutFileRequest(
                    path=f'/tmp/put-file-test-{os.getpid()}',
                    mode=symbolicmode.symbolic_to_numeric_permissions(
                        'ugo+rw'),
                    length=9,
                    first_chunk=agent_pb2.FileChunk(
                        offset=0,
                        encoding=agent_pb2.FileChunk.BASE64,
                        payload=base64.b64encode('aaa'.encode())
                    )
                )
            )
        )
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                file_chunk=agent_pb2.FileChunk(
                    offset=3,
                    encoding=agent_pb2.FileChunk.BASE64,
                    payload=base64.b64encode('bbb'.encode())
                )
            )
        )
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                file_chunk=agent_pb2.FileChunk(
                    offset=6,
                    encoding=agent_pb2.FileChunk.BASE64,
                    payload=base64.b64encode('ccc'.encode())
                )
            )
        )
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                file_chunk=agent_pb2.FileChunk(
                    offset=9,
                    encoding=agent_pb2.FileChunk.BASE64,
                    payload=None
                )
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(4, len(mock_send_responses.mock_calls))

        for i in range(4):
            env = mock_send_responses.call_args_list[i].args[0]
            self.assertEqual(
                1, len(env), f'Unexpected length for reply {i}: {env}')
            self.assertEqual(
                cmd_id, env[0].command_id,
                f'Incorrect command id for reply {i}: {env}')
            self.assertTrue(
                env[0].HasField('file_chunk_reply'),
                f'Incorrect message type for reply {i}: {env}')

    @mock.patch('symbolicmode.chmod')
    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_chmod(self, mock_send_responses, mock_chmod):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a ChmodRequest
        cmd_id = random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                chmod_request=agent_pb2.ChmodRequest(
                    path='/a/random/path',
                    mode=symbolicmode.symbolic_to_numeric_permissions('ugo+r')
                )
            )
        )

        # Let the daemon process that
        d.buffered += msg.SerializeToString()
        d._attempt_decode()

        # And make sure we replied correctly
        self.assertEqual(1, len(mock_chmod.mock_calls))

        self.assertEqual(1, len(mock_send_responses.mock_calls))
        env = mock_send_responses.call_args_list[0].args[0]

        self.assertEqual(1, len(env), f'Unexpected length: {env}')

        self.assertEqual(cmd_id, env[0].command_id)
        self.assertTrue(env[0].HasField('chmod_reply'))
        self.assertEqual('/a/random/path', env[0].chmod_reply.path)

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_get_file(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, 'tempfile')
            with open(tmp, 'w') as f:
                for _ in range(1024):
                    f.write('?' * 1024)

            # Send a GetFileRequest
            cmd_id = random_id()
            msg = agent_pb2.HypervisorToAgent()
            msg.commands.append(
                agent_pb2.HypervisorToAgentCommand(
                    command_id=cmd_id,
                    get_file_request=agent_pb2.GetFileRequest(
                        path=tmp
                    )
                )
            )

            # Let the daemon process that
            d.buffered += msg.SerializeToString()
            d._attempt_decode()

            # And make sure we replied correctly
            self.assertEqual(13, len(mock_send_responses.mock_calls))

            env = mock_send_responses.call_args_list[0].args[0]
            self.assertEqual(1, len(env), f'Unexpected length: {env}')
            self.assertTrue(env[0].HasField('stat_result'))

            for i in range(10):
                env = mock_send_responses.call_args_list[1 + i].args[0]
                self.assertEqual(1, len(env), f'Unexpected length: {env}')
                self.assertTrue(env[0].HasField('file_chunk'))
                self.assertEqual(i * 102400, env[0].file_chunk.offset)
                self.assertEqual(
                    agent_pb2.FileChunk.BASE64, env[0].file_chunk.encoding)
                self.assertNotEqual('', env[0].file_chunk.payload)

                # Ack the FileChunk
                cmd_id = random_id()
                msg = agent_pb2.HypervisorToAgent()
                msg.commands.append(
                    agent_pb2.HypervisorToAgentCommand(
                        command_id=cmd_id,
                        file_chunk_reply=agent_pb2.FileChunkReply(
                            path=tmp,
                            offset=env[0].file_chunk.offset
                        )
                    )
                )

            env = mock_send_responses.call_args_list[12].args[0]
            self.assertEqual(1, len(env), f'Unexpected length: {env}')
            self.assertTrue(env[0].HasField('file_chunk'))
            self.assertNotEqual(0, env[0].file_chunk.offset)
            self.assertEqual(
                agent_pb2.FileChunk.BASE64, env[0].file_chunk.encoding)
            self.assertEqual('', env[0].file_chunk.payload)
