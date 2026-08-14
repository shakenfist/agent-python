# Architecture

The Shaken Fist in-guest agent runs inside virtual machines and
provides a side channel for the hypervisor to interact with the
guest OS. Communication uses protobuf over a vsock connection.

This document is the map. The message-by-message wire protocol is
[docs/protocol.md](docs/protocol.md), building and testing is
[docs/developer-guide.md](docs/developer-guide.md), and the
user-facing overview is [docs/index.md](docs/index.md).

## Directory Structure

```
shakenfist_agent/
    __init__.py
    main.py              # CLI entry point (Click-based)
    log.py               # Console logging with .with_fields()
    commandline/
        daemon.py        # vsock listener, command handlers
    protos/
        agent.proto      # Agent-specific protobuf definitions
        common.proto     # Shared protobuf definitions
        agent_pb2.py     # Generated Python stubs
        agent_pb2_grpc.py
        common_pb2.py
        common_pb2_grpc.py
        _copy_stubs.sh   # Script to sync protos from main repo
    tests/
        __init__.py
        test_daemon.py   # Unit tests for command handlers

pyproject.toml           # Build config (setuptools + setuptools_scm)
tox.ini                  # Test runner configuration

.github/
    workflows/
        functional-tests.yml  # CI: lint, unit tests, coverage
```

## Communication Model

The agent listens on vsock port 1025 for connections from the
hypervisor. Each connection is handled in a separate thread by
a `VSockAgentJob` instance.

```
Hypervisor                          Guest VM
    |                                  |
    |   vsock connection (port 1025)   |
    |--------------------------------->|
    |                                  |
    |  HypervisorToAgent (protobuf)    |
    |--------------------------------->|
    |                                  |
    |  AgentToHypervisor (protobuf)    |
    |<---------------------------------|
```

### Message Envelope

All messages are wrapped in envelope types:

- `HypervisorToAgent` contains a list of
  `HypervisorToAgentCommand` messages, each with a `command_id`
  and a `oneof request` field
- `AgentToHypervisor` contains a list of
  `AgentToHypervisorCommand` messages, each with a `command_id`
  and a `oneof reply` field

The `command_id` correlates requests with responses.

### Available Commands

| Command | Request | Reply | Purpose |
|---------|---------|-------|---------|
| Welcome | `HypervisorWelcome` | `AgentWelcome` | Version exchange |
| Departure | `HypervisorDeparture` | (none) | Graceful disconnect |
| Ping | `PingRequest` | `PingReply` | Liveness check |
| System Running | `IsSystemRunningRequest` | `IsSystemRunningReply` | systemd state |
| Gather Facts | `GatherFactsRequest` | `GatherFactsReply` | OS info, mounts, SSH keys |
| Execute | `ExecuteRequest` | `ExecuteReply` | Run a command |
| Put File | `PutFileRequest` | `FileChunkReply` | Upload file to guest |
| Get File | `GetFileRequest` | `StatResult` + `FileChunk`s | Download file from guest |
| Chmod | `ChmodRequest` | `ChmodReply` | Change file permissions |
| File Chunk | `FileChunk` | `FileChunkReply` | Continuation of file transfer |

### File Transfer

Files are transferred in chunks of up to 100KB, base64-encoded.

**Upload (put file):** The hypervisor sends a `PutFileRequest`
containing the path, mode, and first chunk. Subsequent chunks
arrive as `FileChunk` messages. An empty payload signals
end-of-file.

**Download (get file):** The agent responds with a `StatResult`
followed by a series of `FileChunk` messages. An empty payload
signals end-of-file.

### Command Execution

The `ExecuteRequest` supports:
- Shell command execution (via `subprocess.Popen` with `shell=True`)
- Environment variables
- Working directory
- Network namespace (via `ip netns exec`)
- I/O priority control (via `ionice`)

## Threading Model

```
Main Thread (daemon_run)
    |
    +-- Listens on vsock port 1025
    |
    +-- For each connection:
    |       Spawns VSockAgentJob in daemon thread
    |
    +-- Monitors worker threads
    |       Reaps completed threads
    |
    +-- On SIGTERM:
            Sets EXIT event
            Waits for all workers to finish
```

Worker threads run `VSockAgentJob.run()`, which reads from the
connection in a loop, buffers data, and calls `_attempt_decode()`
to parse and dispatch protobuf messages.

## CLI Structure

The CLI uses Click with a group/subcommand pattern:

```
sf-agent [--verbose/--no-verbose]
    daemon run    # Start the vsock listener
```

The `--verbose` flag sets the log level to DEBUG.

## Supported Platforms

The agent must install and run on the system Python provided by
each distribution. The oldest Python in this table determines the
`requires-python` floor in `pyproject.toml` and the
`constraints.python` value in `renovate.json`.

| Distribution | Python Version |
|--------------|----------------|
| Ubuntu 20.04 | 3.8 |
| Ubuntu 22.04 | 3.10 |
| Ubuntu 24.04 | 3.12 |
| Debian 11 | 3.9 |
| Debian 12 | 3.11 |
| Debian 13 | 3.13 |
| CentOS 9-stream | 3.9 |
| Fedora 41-43 | 3.13 |

**Current minimum: Python 3.8** (Ubuntu 20.04).

When dropping a distribution from this table, update:

1. `requires-python` in `pyproject.toml`
2. `constraints.python` in `renovate.json`
