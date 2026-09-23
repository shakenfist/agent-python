Python side channel agent for Shaken Fist
=========================================

This is the in-guest side channel agent for
[Shaken Fist](https://github.com/shakenfist/shakenfist). It runs
inside virtual machines and provides a vsock-based interface for
the hypervisor to execute commands, transfer files, and gather
system information.

## Documentation

- [docs/index.md](https://github.com/shakenfist/agent-python/blob/develop/docs/index.md) -- overview and feature summary
- [docs/protocol.md](https://github.com/shakenfist/agent-python/blob/develop/docs/protocol.md) -- protobuf protocol
  reference
- [docs/developer-guide.md](https://github.com/shakenfist/agent-python/blob/develop/docs/developer-guide.md) -- building,
  testing, and extending the agent

## Quick Start

```bash
pip install shakenfist-agent
sf-agent daemon run
```

## Development

```bash
# Run unit tests
tox -epy3

# Run linter
tox -eflake8

# Generate coverage report
tox -ecover
```

See [ARCHITECTURE.md](https://github.com/shakenfist/agent-python/blob/develop/ARCHITECTURE.md) for the project structure
and [AGENTS.md](https://github.com/shakenfist/agent-python/blob/develop/AGENTS.md) for AI agent guidance.
