Python side channel agent for Shaken Fist
=========================================

This is the in-guest side channel agent for
[Shaken Fist](https://github.com/shakenfist/shakenfist). It runs
inside virtual machines and provides a vsock-based interface for
the hypervisor to execute commands, transfer files, and gather
system information.

## Documentation

- [docs/index.md](docs/index.md) -- overview and feature summary
- [docs/protocol.md](docs/protocol.md) -- protobuf protocol
  reference
- [docs/developer-guide.md](docs/developer-guide.md) -- building,
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

See [ARCHITECTURE.md](ARCHITECTURE.md) for the project structure
and [AGENTS.md](AGENTS.md) for AI agent guidance.
