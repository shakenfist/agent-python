import base64
import json
import mock
import os
import string
import tempfile
import testtools

from shakenfist_utilities import logs
from shakenfist_utilities import random as sf_random
import symbolicmode

from shakenfist_agent.commandline import daemon
from shakenfist_agent.protos import agent_pb2
from shakenfist_agent.protos import common_pb2


LOG = logs.setup_console(__name__)


class DaemonAgentTestCase(testtools.TestCase):
    @mock.patch('time.time', return_value=1686526181.0196502)
    @mock.patch('psutil.boot_time', return_value=1200)
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=('running\n', ''))
    @mock.patch('shakenfist_agent.protocol.Agent.send_v1_packet')
    def test_is_system_running(self, mock_send_v1_packet, mock_execute,
                               mock_boot_time, mock_time):
        with tempfile.NamedTemporaryFile() as tf:
            a = daemon.SFCharacterDeviceAgent(tf.name)
            a.dispatch_packet({'command': 'is-system-running'})

            # The message changes over time because it has the version
            # packed into it.
            out_packet_1 = mock_send_v1_packet.mock_calls[0].args[0]
            out_packet_1['message'] = 'XXX'
            self.assertEqual(
                {
                    'command': 'agent-start',
                    'message': 'XXX',
                    'system_boot_time': 1200,
                    'unique': '1686526181.0196502'
                }, out_packet_1)

            out_packet_2 = mock_send_v1_packet.mock_calls[1].args[0]
            self.assertEqual('is-system-running-response', out_packet_2['command'])
            self.assertEqual(True, out_packet_2['result'])
            self.assertEqual('running', out_packet_2['message'])
            self.assertEqual(1200, out_packet_2['system_boot_time'])

    @mock.patch('time.time', return_value=1686526181.0196502)
    @mock.patch('psutil.boot_time', return_value=1200)
    @mock.patch('shakenfist_agent.protocol.Agent.send_v1_packet')
    def test_gather_facts(self, mock_send_v1_packet, mock_boot_time, mock_time):
        with tempfile.NamedTemporaryFile() as tf:
            a = daemon.SFCharacterDeviceAgent(tf.name)
            a.dispatch_packet({'command': 'gather-facts'})

            # The message changes over time because it has the version
            # packed into it.
            out_packet_1 = mock_send_v1_packet.mock_calls[0].args[0]
            out_packet_1['message'] = 'XXX'
            self.assertEqual(
                {
                    'command': 'agent-start',
                    'message': 'XXX',
                    'system_boot_time': 1200,
                    'unique': '1686526181.0196502'
                }, out_packet_1)

            out_packet_2 = mock_send_v1_packet.mock_calls[1].args[0]
            self.assertEqual('gather-facts-response', out_packet_2['command'])
            self.assertTrue('distribution' in out_packet_2['result'])

    @mock.patch('time.time', return_value=1686526181.0196502)
    @mock.patch('psutil.boot_time', return_value=1200)
    @mock.patch('shakenfist_agent.protocol.Agent.send_v1_packet')
    def test_get_file(self, mock_send_v1_packet, mock_boot_time, mock_time):
        with tempfile.NamedTemporaryFile() as tf:
            with tempfile.NamedTemporaryFile() as tf2:
                with open(tf2.name, 'w') as f:
                    for _ in range(1000):
                        f.write(string.ascii_letters + string.digits + '\n')

                a = daemon.SFCharacterDeviceAgent(tf.name)
                a.dispatch_packet({'command': 'get-file', 'path': tf2.name})

                # The message changes over time because it has the version
                # packed into it.
                out_packet_1 = mock_send_v1_packet.mock_calls[0].args[0]
                out_packet_1['message'] = 'XXX'
                self.assertEqual(
                    {
                        'command': 'agent-start',
                        'message': 'XXX',
                        'system_boot_time': 1200,
                        'unique': '1686526181.0196502'
                    }, out_packet_1)

                out_packet_2 = mock_send_v1_packet.mock_calls[1].args[0]
                self.assertEqual('get-file-response', out_packet_2['command'])
                self.assertEqual(True, out_packet_2['result'])
                self.assertEqual(63000, out_packet_2['stat_result']['size'])

                # 63000 bytes in base64 is 61 packets
                self.assertEqual(4 + 61, len(mock_send_v1_packet.mock_calls))

                for c in mock_send_v1_packet.mock_calls[2:2 + 61]:
                    out_packet_3 = c.args[0]
                    self.assertEqual('get-file-response', out_packet_3['command'])
                    self.assertEqual(True, out_packet_3['result'])
                    self.assertTrue('offset' in out_packet_3)
                    self.assertEqual('base64', out_packet_3['encoding'])
                    self.assertTrue(out_packet_3['chunk'] is not None)

                    # Ensure the packet is JSON serializable
                    json.dumps(out_packet_3)

                out_packet_4 = mock_send_v1_packet.mock_calls[64].args[0]
                self.assertEqual('get-file-response', out_packet_4['command'])
                self.assertEqual(True, out_packet_4['result'])
                self.assertTrue('offset' in out_packet_4)
                self.assertEqual('base64', out_packet_4['encoding'])
                self.assertEqual(None, out_packet_4['chunk'])


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
        self.assertTrue(env[0].HasField('agent_welcome'))
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
