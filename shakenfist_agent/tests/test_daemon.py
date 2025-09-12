import base64
import mock
import os
import tempfile
import testtools

from shakenfist_utilities import logs
from shakenfist_utilities import random as sf_random
import symbolicmode

from shakenfist_agent.commandline import daemon
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

        # Send an invalid ExecuteRequest
        cmd_id = sf_random.random_id()
        msg = agent_pb2.HypervisorToAgent()
        msg.commands.append(
            agent_pb2.HypervisorToAgentCommand(
                command_id=cmd_id,
                execute_request=common_pb2.ExecuteRequest(
                    command='/bin/nosuch',
                    io_priority=common_pb2.ExecuteRequest.HIGH
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
        cmd_id = sf_random.random_id()
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
        cmd_id = sf_random.random_id()
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
        cmd_id = sf_random.random_id()
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

    @mock.patch('oslo_concurrency.processutils.execute', return_value=('running', ''))
    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_is_system_running(self, mock_send_responses, mock_execute):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a IsSystemRunningRequest
        cmd_id = sf_random.random_id()
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
        self.assertEqual(1, len(mock_execute.mock_calls))
        self.assertEqual(
            'systemctl is-system-running',
            mock_execute.call_args_list[0].args[0])

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
        cmd_id = sf_random.random_id()
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
        cmd_id = sf_random.random_id()
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

    @mock.patch('shakenfist_agent.commandline.daemon.VSockAgentJob._send_responses')
    def test_put_file(self, mock_send_responses):
        d = daemon.VSockAgentJob(LOG, None)

        # Send a PutFileRequest, and then a series of FileChunks
        cmd_id = sf_random.random_id()
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
        cmd_id = sf_random.random_id()
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
            cmd_id = sf_random.random_id()
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
                cmd_id = sf_random.random_id()
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
