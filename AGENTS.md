# Agents Guide

This document provides guidance for AI agents working on the
agent-python codebase.

## Project Overview

agent-python is the in-guest side channel agent for
[Shaken Fist](https://github.com/shakenfist/shakenfist) virtual
machines. It runs inside guest VMs and communicates with the
hypervisor over a vsock connection using protobuf-serialized
messages. The agent handles commands such as executing processes,
transferring files, gathering system facts, and checking service
status.

## Key Patterns

### Adding a New Command

1. Define request and reply messages in `shakenfist_agent/protos/agent.proto`
2. Add the request to the `HypervisorToAgentCommand.request` oneof
3. Add the reply to the `AgentToHypervisorCommand.reply` oneof
4. Regenerate the Python stubs (copy from the main shakenfist repo
   using `shakenfist_agent/protos/_copy_stubs.sh`)
5. Add a `_handle_<command>` method to `VSockAgentJob` in
   `shakenfist_agent/commandline/daemon.py`
6. Add a `HasField` check in `_attempt_decode()` to dispatch to
   your handler
7. Add a test in `shakenfist_agent/tests/test_daemon.py`

Handler template:

```python
def _handle_my_command(self, request):
    self.log.debug('...my command')
    my_request = request.my_request
    # ... do work ...
    self._send_responses(
        [
            agent_pb2.AgentToHypervisorCommand(
                command_id=request.command_id,
                my_reply=agent_pb2.MyReply(
                    # ... fields ...
                )
            )
        ]
    )
```

### Protobuf Stubs

The `.proto` files and generated Python stubs live in
`shakenfist_agent/protos/`. These are copied from the main
shakenfist repository with import paths rewritten. Do not edit
the generated `*_pb2.py` or `*_pb2_grpc.py` files directly.

### File Transfer Protocol

File transfers use a chunked protocol. `PutFileRequest` includes
the first chunk, then subsequent `FileChunk` messages follow.
An empty payload signals end-of-file. See `_handle_put_file()`
and `_handle_file_chunk()` for the receive side, and
`_handle_get_file()` for the send side.

## Build System

The project uses `pyproject.toml` with `setuptools` and
`setuptools_scm` for building and versioning. Versions are
derived from git tags. Dependencies are declared in
`pyproject.toml` under `[project.dependencies]` and
`[project.optional-dependencies.test]`.

## Testing

- **Unit tests**: Located in `shakenfist_agent/tests/`. Run with
  `tox -epy3`.
- **Linting**: Run with `tox -eflake8`.
- **Coverage**: Run with `tox -ecover`.

Tests create `VSockAgentJob` instances with a mock logger and
`None` connection, serialize protobuf messages into the
`buffered` bytearray, call `_attempt_decode()`, and verify the
responses passed to the mocked `_send_responses()`.

## Logging

All modules use `shakenfist_agent.log.setup_console(__name__)`
for logger initialization. The returned adapter supports
`.with_fields()` for structured key-value output. The main
logger is created in `main.py` and passed to worker threads
via the Click context.
