import base64
import click
import distro
import fcntl
import json
from linux_utils.fstab import find_mounted_filesystems
import os
from oslo_concurrency import processutils
import psutil
import shutil
import signal
import socket
import struct
import subprocess
import symbolicmode
import time
import threading

from google.protobuf.message import DecodeError
import setproctitle
from shakenfist_utilities import random as sf_random

from shakenfist_agent.protos import agent_pb2
from shakenfist_agent.protos import common_pb2


VSOCK_PORT = 1025
MAX_CHUNK_SIZE = 102400
EXIT = threading.Event()


# Mid-range best effort, equivalent to not specifying a value
IO_PRIORITIES = {
    common_pb2.ExecuteRequest.NORMAL: (2, 4),
    common_pb2.ExecuteRequest.LOW: (2, 7),
    common_pb2.ExecuteRequest.HIGH: (2, 0)
}


class NoSuchCommand(Exception):
    ...


class IOClassException(Exception):
    ...


@click.group(help='Daemon commands')
def daemon():
    pass


class AgentJob:
    def __init__(self, logger):
        self.log = logger


class _ChunkConsumer:
    def __init__(self, command_id, path, mode):
        self.command_id = command_id
        self.path = path
        self.mode = mode

        self.offset = 0
        self.file = open(path, 'wb')

    def write(self, d):
        self.file.write(d)

    def close(self):
        self.file.close()
        if self.mode:
            symbolicmode.chmod(self.path, self.mode)


VERSION_CACHE = None


class VSockAgentJob(AgentJob):
    def __init__(self, logger, conn):
        super().__init__(logger)
        self.conn = conn

        self.buffered = bytearray()
        self.consumer = None

    def _send_responses(self, responses):
        out = agent_pb2.AgentToHypervisor()
        for cmd in responses:
            out.commands.append(cmd)
        self.conn.sendall(out.SerializeToString())

    def _handle_hypervisor_welcome(self, request):
        global VERSION_CACHE
        self.log.debug('...hypervisor welcome')

        if not VERSION_CACHE:
            try:
                from shakenfist_agent import _version
                agent_version = VERSION_CACHE = _version.version
            except ImportError as e:
                agent_version = VERSION_CACHE = 'unreleased development version'
        else:
            agent_version = VERSION_CACHE

        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=request.command_id,
                    agent_welcome=agent_pb2.AgentWelcome(
                        version=f'version {agent_version}',
                        boot_time=psutil.boot_time()
                    )
                )
            ]
        )

    def _handle_ping(self, request):
        self.log.debug('...ping')
        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=request.command_id,
                    ping_reply=agent_pb2.PingReply()
                )
            ]
        )

    def _handle_is_system_running(self, request):
        self.log.debug('...is system running')
        out, _ = processutils.execute(
            'systemctl is-system-running', shell=True,
            check_exit_code=False)
        out = out.rstrip()

        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=request.command_id,
                    is_system_running_reply=agent_pb2.IsSystemRunningReply(
                        result=out == 'running',
                        message=out,
                        boot_time=psutil.boot_time()
                    )
                )
            ]
        )

    def _handle_gather_facts(self, request):
        self.log.debug('...gather facts')
        gather_facts_reply = agent_pb2.GatherFactsReply()

        di = distro.info()
        for key in di:
            f = gather_facts_reply.distro_facts.add()
            f.name = key
            f.value = json.dumps(di[key])

        # We should allow this agent to at least run on MacOS
        if di['id'] != 'darwin':
            for entry in find_mounted_filesystems():
                mp = gather_facts_reply.mount_points.add()
                mp.device = entry.device
                mp.mount_point = entry.mount_point
                mp.vfs_type = entry.vfs_type

        for kind, path in [
                ('rsa', '/etc/ssh/ssh_host_rsa_key.pub'),
                ('ecdsa',  '/etc/ssh/ssh_host_ecdsa_key.pub'),
                ('ed25519', '/etc/ssh/ssh_host_ed25519_key.pub')
        ]:
            if os.path.exists(path):
                with open(path) as f:
                    hk = gather_facts_reply.ssh_host_keys.add()
                    hk.name = kind
                    hk.value = f.read()

        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=request.command_id,
                    gather_facts_reply=gather_facts_reply
                )
            ]
        )

    def _handle_execute(self, request):
        self.log.debug('...execute')
        execute_request = request.execute_request
        command = execute_request.command
        if not shutil.which(command.split(' ')[0]):
            raise NoSuchCommand(f'No such command: {command}')

        if execute_request.network_namespace != '':
            command = f'ip netns exec {execute_request.network_namespace} {command}'

        env_variables = {}
        for env_var in execute_request.environment_variables:
            env_variables[env_var.name] = env_var.value
        if not env_variables:
            env_variables = None

        # MacOS appears to not support this ionice code. I like unit tests
        # working on MacOS however.
        supports_ionice = True
        try:
            ioclass, iovalue = list(psutil.Process().ionice())
        except AttributeError:
            supports_ionice = False

        if supports_ionice:
            current_iopriority = (int(ioclass), int(iovalue))
            requested_iopriority = IO_PRIORITIES.get(
                execute_request.io_priority, IO_PRIORITIES[common_pb2.ExecuteRequest.NORMAL])

            if current_iopriority != requested_iopriority:
                command = (f'ionice -c {requested_iopriority[0]} '
                           f'-n {requested_iopriority[1]} {command}')
        elif execute_request.io_priority != common_pb2.ExecuteRequest.NORMAL:
            raise IOClassException(
                'Changing IO priority is not supported on this platform')

        working_directory = None
        if execute_request.working_directory != '':
            working_directory = execute_request.working_directory

        start_time = time.time()
        pipe = subprocess.PIPE
        obj = subprocess.Popen(
            command, stdin=pipe, stdout=pipe, stderr=pipe, close_fds=True,
            shell=True, cwd=working_directory, env=env_variables)

        stdout, stderr = obj.communicate(None, timeout=None)
        obj.stdin.close()

        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=request.command_id,
                    execute_reply=common_pb2.ExecuteReply(
                        stdout=stdout,
                        stderr=stderr,
                        exit_code=obj.returncode,
                        request_id=execute_request.request_id,
                        execution_id=execute_request.execution_id,
                        execution_seconds=round(time.time() - start_time, 2)
                    )
                )
            ]
        )

    def _handle_put_file(self, request):
        self.log.debug('...put file')
        put_file_request = request.put_file_request
        self.consumer = _ChunkConsumer(
            request.command_id, put_file_request.path, put_file_request.mode)
        self._handle_file_chunk(put_file_request.first_chunk)

    def _handle_file_chunk(self, chunk):
        self.log.debug('...file chunk')
        command_id = self.consumer.command_id
        path = self.consumer.path

        if len(chunk.payload) == 0:
            # End of file
            self.consumer.close()
            self.consumer = None

        else:
            if chunk.encoding == agent_pb2.FileChunk.BASE64:
                d = base64.b64decode(chunk.payload)
                self.consumer.write(d)
            else:
                self._send_responses(
                    [
                        agent_pb2.AgentToHypervisorCommand(
                            command_id=command_id,
                            command_error=agent_pb2.CommandError(
                                error='unknown payload encoding')
                        )
                    ]
                )
                return

        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=command_id,
                    file_chunk_reply=agent_pb2.FileChunkReply(
                        path=path,
                        offset=chunk.offset
                    )
                )
            ]
        )

    def _handle_chmod(self, request):
        self.log.debug('...chmod')
        chmod_request = request.chmod_request
        symbolicmode.chmod(chmod_request.path, chmod_request.mode)
        self._send_responses(
            [
                agent_pb2.AgentToHypervisorCommand(
                    command_id=request.command_id,
                    chmod_reply=agent_pb2.ChmodReply(
                        path=chmod_request.path
                    )
                )
            ]
        )

    def _handle_get_file(self, request):
        self.log.debug('...get file')
        get_request = request.get_file_request
        if not os.path.exists(get_request.path):
            fake_envelope = agent_pb2.AgentToHypervisor()
            fake_envelope.commands.append(
                agent_pb2.HypervisorToAgentCommand(
                    command_id=f'{request.command_id}-partial',
                    get_file_request=request
                )
            )
            self._send_responses(
                [
                    agent_pb2.AgentToHypervisorCommand(
                        command_id=request.command_id,
                        command_error=agent_pb2.CommandError(
                            error='file not found',
                            last_envelope=fake_envelope
                        )
                    )
                ]
            )
            return

        with open(get_request.path, 'rb') as f:
            st = os.stat(get_request.path)
            self._send_responses(
                [
                    agent_pb2.AgentToHypervisorCommand(
                        command_id=request.command_id,
                        stat_result=agent_pb2.StatResult(
                            mode=st.st_mode,
                            size=st.st_size,
                            uid=st.st_uid,
                            gid=st.st_gid,
                            atime=st.st_atime,
                            mtime=st.st_mtime,
                            ctime=st.st_ctime,
                        )
                    )
                ]
            )

            offset = 0
            while d := f.read(MAX_CHUNK_SIZE):
                self._send_responses(
                    [
                        agent_pb2.AgentToHypervisorCommand(
                            command_id=request.command_id,
                            file_chunk=agent_pb2.FileChunk(
                                offset=offset,
                                encoding=agent_pb2.FileChunk.BASE64,
                                payload=base64.b64encode(d)
                            )
                        )
                    ]
                )
                offset += len(d)

            self._send_responses(
                [
                    agent_pb2.AgentToHypervisorCommand(
                        command_id=request.command_id,
                        file_chunk=agent_pb2.FileChunk(
                            offset=offset,
                            encoding=agent_pb2.FileChunk.BASE64,
                            payload=None
                        )
                    )
                ]
            )

    def _attempt_decode(self):
        envelope = None
        try:
            envelope = agent_pb2.HypervisorToAgent()
            try:
                consumed = envelope.ParseFromString(self.buffered)
            except DecodeError:
                consumed = 0

            if consumed == 0:
                return
            self.buffered = self.buffered[consumed:]

            for request in envelope.commands:
                if request.HasField('hypervisor_welcome'):
                    self._handle_hypervisor_welcome(request)

                elif request.HasField('ping_request'):
                    self._handle_ping(request)

                elif request.HasField('is_system_running_request'):
                    self._handle_is_system_running(request)

                elif request.HasField('gather_facts_request'):
                    self._handle_gather_facts(request)

                elif request.HasField('execute_request'):
                    self._handle_execute(request)

                elif request.HasField('put_file_request'):
                    self._handle_put_file(request)

                elif request.HasField('file_chunk'):
                    self._handle_file_chunk(request.file_chunk)

                elif request.HasField('chmod_request'):
                    self._handle_chmod(request)

                elif request.HasField('hypervisor_departure'):
                    self.log.debug('...hypervisor departure')
                    return

                elif request.HasField('get_file_request'):
                    self._handle_get_file(request)

                elif request.HasField('file_chunk_reply'):
                    self.log.debug('...file chunk reply')

                else:
                    self.log.debug('...unknown command')
                    self._send_responses(
                        [
                            agent_pb2.AgentToHypervisorCommand(
                                command_id=request.command_id,
                                unknown_command=agent_pb2.UnknownCommand(
                                    last_envelope=envelope
                                )
                            )
                        ]
                    )

        except BrokenPipeError as e:
            self.log.warning(f'...broken pipe: {e}')

        except Exception as e:
            self.log.warning(f'...command error: {e}')

            import traceback
            traceback.print_exc()

            self._send_responses(
                [
                    agent_pb2.AgentToHypervisorCommand(
                        command_id=request.command_id,
                        command_error=agent_pb2.CommandError(
                            last_envelope=envelope,
                            error=str(e)
                        )
                    )
                ]
            )

    def run(self):
        while True:
            input = self.conn.recv(102400)
            if not input:
                break

            self.buffered += input
            self._attempt_decode()

        self.conn.close()


CHANNEL = None


def exit_gracefully(sig, _frame):
    if sig == signal.SIGTERM:
        click.echo('Received SIGTERM')
        EXIT.set()


@daemon.command(name='run', help='Run the sf-agent daemon')
@click.pass_context
def daemon_run(ctx):
    signal.signal(signal.SIGTERM, exit_gracefully)
    setproctitle.setproctitle('sf-agent')

    # Start listening for v2 connections on the vsock.
    s = None
    if os.path.exists('/dev/vsock'):
        # Lookup our CID. This is a 32 bit unsigned int returned from an ioctl
        # against /dev/vsock. As best as I can tell the empty string argument
        # at the end is because that is used as a buffer to return the result
        # in. Yes really.
        with open('/dev/vsock', 'rb') as f:
            r = fcntl.ioctl(f, socket.IOCTL_VM_SOCKETS_GET_LOCAL_CID, '    ')
            cid = struct.unpack('I', r)[0]
        click.echo(f'Our v2 vsock CID is {cid}.')

        s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        s.bind((cid, VSOCK_PORT))
        s.listen()
        s.settimeout(0.2)
        click.echo('Listening for incoming v2 requests')

    workers = {}
    while not EXIT.is_set():
        conn = None
        if s:
            try:
                conn, (remote_cid, remote_port) = s.accept()
                click.echo(f'Connection from CID {remote_cid} with remote port '
                           f'{remote_port}')
            except socket.timeout:
                conn = None
        else:
            time.sleep(0.2)

        if conn:
            thread_name = sf_random.random_id()
            log = ctx.obj['LOGGER'].with_fields({
                'remote_cid': remote_cid,
                'remote_port': remote_port,
                'thread_name': thread_name
            })

            worker_object = VSockAgentJob(log, conn)
            worker_thread = threading.Thread(
                target=worker_object.run, daemon=True, name=thread_name)
            workers[thread_name] = {
                'object': worker_object,
                'thread': worker_thread
            }
            worker_thread.start()

        remaining_workers = {}
        for thread_name in workers:
            if workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = workers[thread_name]
            else:
                workers[thread_name]['thread'].join(0.2)
        workers = remaining_workers

    click.echo('Stopping')

    while workers:
        click.echo(f'There are {len(workers)} remaining workers')

        remaining_workers = {}
        for thread_name in workers:
            if workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = workers[thread_name]
                click.echo(f'Thread is still executing {thread_name}')
            else:
                click.echo(f'Reaping thread: {thread_name}')
                workers[thread_name]['thread'].join(0.2)

        workers = remaining_workers
        if workers:
            time.sleep(5)

    click.echo(f'There are {len(workers)} remaining workers')
    click.echo('Stopped')

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    click.echo('Terminating ourselves')
    raise SystemExit(0)


daemon.add_command(daemon_run)
