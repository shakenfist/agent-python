import json
import mock
import string
import tempfile
import testtools


from shakenfist_agent.commandline import daemon


class DaemonAgentTestCase(testtools.TestCase):
    @mock.patch('time.time', return_value=1686526181.0196502)
    @mock.patch('psutil.boot_time', return_value=1200)
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=('running\n', ''))
    @mock.patch('shakenfist_agent.protocol.Agent.send_packet')
    def test_is_system_running(self, mock_send_packet, mock_execute,
                               mock_boot_time, mock_time):
        with tempfile.NamedTemporaryFile() as tf:
            a = daemon.SFFileAgent(tf.name)
            a.dispatch_packet({'command': 'is-system-running'})

            # The message changes over time because it has the version
            # packed into it.
            out_packet_1 = mock_send_packet.mock_calls[0].args[0]
            out_packet_1['message'] = 'XXX'
            self.assertEqual(
                {
                    'command': 'agent-start',
                    'message': 'XXX',
                    'system_boot_time': 1200,
                    'unique': '1686526181.0196502'
                }, out_packet_1)

            out_packet_2 = mock_send_packet.mock_calls[1].args[0]
            self.assertEqual('is-system-running-response', out_packet_2['command'])
            self.assertEqual(True, out_packet_2['result'])
            self.assertEqual('running', out_packet_2['message'])
            self.assertEqual(1200, out_packet_2['system_boot_time'])

    @mock.patch('time.time', return_value=1686526181.0196502)
    @mock.patch('psutil.boot_time', return_value=1200)
    @mock.patch('shakenfist_agent.protocol.Agent.send_packet')
    def test_gather_facts(self, mock_send_packet, mock_boot_time, mock_time):
        with tempfile.NamedTemporaryFile() as tf:
            a = daemon.SFFileAgent(tf.name)
            a.dispatch_packet({'command': 'gather-facts'})

            # The message changes over time because it has the version
            # packed into it.
            out_packet_1 = mock_send_packet.mock_calls[0].args[0]
            out_packet_1['message'] = 'XXX'
            self.assertEqual(
                {
                    'command': 'agent-start',
                    'message': 'XXX',
                    'system_boot_time': 1200,
                    'unique': '1686526181.0196502'
                }, out_packet_1)

            out_packet_2 = mock_send_packet.mock_calls[1].args[0]
            self.assertEqual('gather-facts-response', out_packet_2['command'])
            self.assertTrue('distribution' in out_packet_2['result'])

    @mock.patch('time.time', return_value=1686526181.0196502)
    @mock.patch('psutil.boot_time', return_value=1200)
    @mock.patch('shakenfist_agent.protocol.Agent.send_packet')
    def test_get_file(self, mock_send_packet, mock_boot_time, mock_time):
        with tempfile.NamedTemporaryFile() as tf:
            with tempfile.NamedTemporaryFile() as tf2:
                with open(tf2.name, 'w') as f:
                    for _ in range(1000):
                        f.write(string.ascii_letters + string.digits + '\n')

                a = daemon.SFFileAgent(tf.name)
                a.dispatch_packet({'command': 'get-file', 'path': tf2.name})

                # The message changes over time because it has the version
                # packed into it.
                out_packet_1 = mock_send_packet.mock_calls[0].args[0]
                out_packet_1['message'] = 'XXX'
                self.assertEqual(
                    {
                        'command': 'agent-start',
                        'message': 'XXX',
                        'system_boot_time': 1200,
                        'unique': '1686526181.0196502'
                    }, out_packet_1)

                out_packet_2 = mock_send_packet.mock_calls[1].args[0]
                self.assertEqual('get-file-response', out_packet_2['command'])
                self.assertEqual(True, out_packet_2['result'])
                self.assertEqual(63000, out_packet_2['stat_result']['size'])

                # 63000 bytes in base64 is 61 packets
                self.assertEqual(4 + 61, len(mock_send_packet.mock_calls))

                for c in mock_send_packet.mock_calls[2:2 + 61]:
                    out_packet_3 = c.args[0]
                    self.assertEqual('get-file-response', out_packet_3['command'])
                    self.assertEqual(True, out_packet_3['result'])
                    self.assertTrue('offset' in out_packet_3)
                    self.assertEqual('base64', out_packet_3['encoding'])
                    self.assertTrue(out_packet_3['chunk'] is not None)

                    # Ensure the packet is JSON serializable
                    json.dumps(out_packet_3)

                out_packet_4 = mock_send_packet.mock_calls[64].args[0]
                self.assertEqual('get-file-response', out_packet_4['command'])
                self.assertEqual(True, out_packet_4['result'])
                self.assertTrue('offset' in out_packet_4)
                self.assertEqual('base64', out_packet_4['encoding'])
                self.assertEqual(None, out_packet_4['chunk'])
