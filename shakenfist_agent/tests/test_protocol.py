import json
import mock
import testtools


from shakenfist_agent import protocol


class ProtocolV1TestCase(testtools.TestCase):
    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_yield_queued_packets(self, mock_read):
        a = protocol.Agent()

        # Fill the buffer with canned packets
        packets = [
            {'command': 'pong', 'unique': 42},
            {'command': 'is-system-running-response', 'result': False,
             'message': 'starting'},
            {'command': 'pong', 'unique': 43},
            {'command': 'is-system-running-response', 'result': False,
             'message': 'starting'},
            {'command': 'pong', 'unique': 44},
            {'command': 'is-system-running-response', 'result': False,
             'message': 'running'}
        ]

        for packet in packets:
            j = json.dumps(packet)
            d = f'{a.PREAMBLE_v1.decode()}[{len(j):08}]{j}'
            a.buffer += d.encode('utf-8')
        a.buffer += '*SFv0'.encode('utf-8')

        # Ask for some packets back
        returned = list(a.find_packets())
        self.assertEqual(6, len(returned))
        self.assertEqual(packets[1], returned[1])
        self.assertEqual('*SFv0'.encode('utf-8'), a.buffer)

    @mock.patch('shakenfist_agent.protocol.Agent._read',
                return_value='AAA'.encode('utf-8'))
    def test_read_appends(self, mock_read):
        a = protocol.Agent()

        a.find_packet()
        self.assertEqual(3, len(a.buffer))

        a.find_packet()
        self.assertEqual(6, len(a.buffer))

    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_incomplete_packet_header(self, mock_read):
        a = protocol.Agent()
        a.buffer = a.PREAMBLE_v1
        self.assertEqual(None, a.find_packet())

    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_incomplete_packet_body(self, mock_read):
        a = protocol.Agent()
        p = f'{a.PREAMBLE_v1.decode()}[00000042]sdfhfg'
        a.buffer = p.encode('utf-8')
        self.assertEqual(None, a.find_packet())

    @mock.patch('shakenfist_agent.protocol.Agent._write')
    def test_send_ping(self, mock_write):
        a = protocol.Agent()
        a.send_ping(unique=4242)
        mock_write.assert_called_with(
            b'*SFv001[00000035]{"command": "ping", "unique": 4242}')

    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_null_body(self, mock_read):
        a = protocol.Agent()
        p = f'{a.PREAMBLE_v1.decode()}[00000004]null'
        a.buffer = p.encode('utf-8')
        self.assertEqual(None, a.find_packet())

    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_small_body(self, mock_read):
        a = protocol.Agent()
        p = f'{a.PREAMBLE_v1.decode()}[00000001]1'
        a.buffer = p.encode('utf-8')
        self.assertEqual(1, a.find_packet())

    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_large_body(self, mock_read):
        b = 'm' * 1024
        a = protocol.Agent()
        p = f'{a.PREAMBLE_v1.decode()}[00001026]"{b}"'
        a.buffer = p.encode('utf-8')
        self.assertEqual(b, a.find_packet())

    @mock.patch('shakenfist_agent.protocol.Agent.send_v1_packet')
    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_json_decode_fails(self, mock_read, mock_send_v1_packet):
        b = '{"notjson"}'
        a = protocol.Agent()
        p = f'{a.PREAMBLE_v1.decode()}[{len(b):08}]"{b}"'
        a.buffer = p.encode('utf-8')
        a.find_packet()
        self.assertEqual(
            [mock.call({
                'command': 'json-decode-failure',
                'message': 'failed to JSON decode packet: "{"notjson"'
            })], mock_send_v1_packet.mock_calls)


# class ProtocolV2TestCase(testtools.TestCase):
#     @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
#     def test_small_body(self, mock_read):
#         a = protocol.Agent()
#         p = '%s[00000001]1' % a.PREAMBLE_v2
#         a.buffer = p.encode('utf-8')
#         self.assertEqual(1, a.find_packet())


class ProtocolBadVersionTestCase(testtools.TestCase):
    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_small_body(self, mock_read):
        a = protocol.Agent()
        p = '*SFv009[00000001]1'
        a.buffer = p.encode('utf-8')
        self.assertEqual(None, a.find_packet())


class ProtocolRegressionsTestCase(testtools.TestCase):
    _real_problem_packet_001 = (
        b'*SFv001[00000120]{"command": "agent-start", '
        b'"message": "version 0.4.0", "system_boot_time": 1739876468.0, '
        b'"unique": "1739876519.0933423"}'
    )

    @mock.patch('shakenfist_agent.protocol.Agent._write', return_value=None)
    def test_send_real_packet(self, mock_write):
        a = protocol.Agent()
        a.send_v1_packet(
            {
                "command": "agent-start",
                "message": "version 0.4.0",
                "system_boot_time": 1739876468.0,
                "unique": "1739876519.0933423"
            }
        )

        mock_write.assert_called_with(self._real_problem_packet_001)

    @mock.patch('shakenfist_agent.protocol.Agent.send_v1_packet')
    @mock.patch('shakenfist_agent.protocol.Agent._read', return_value=None)
    def test_parse_real_packet(self, mock_read, mock_send_v1_packet):
        a = protocol.Agent()
        a.buffer = self._real_problem_packet_001
        parsed = a.find_packet()
        self.assertNotEqual(None, parsed)
        self.assertEqual('agent-start', parsed['command'])
