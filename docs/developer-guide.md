# Developer Guide

## Prerequisites

- Python 3.10 or later
- `tox` for running tests and linting

## Project Layout

```
shakenfist_agent/
    main.py              # CLI entry point
    log.py               # Console logging
    commandline/
        daemon.py        # vsock listener and command handlers
    protos/
        agent.proto      # Protobuf definitions
        common.proto     # Shared protobuf definitions
        *_pb2.py         # Generated stubs (do not edit)
    tests/
        test_daemon.py   # Unit tests for the daemon
        test_main.py     # Unit tests for CLI logging setup
```

## Building

The project uses `pyproject.toml` with `setuptools_scm` for
version management. Versions are derived from git tags:

```bash
pip install -e .
```

## Running Tests

```bash
# Unit tests
tox -epy3

# Linting
tox -eflake8

# Coverage report
tox -ecover
```

## Continuous Integration

Pull requests to `develop` run `.github/workflows/functional-tests.yml`.
Its `sanity_checks` job (flake8, a dependency install check, unit tests
and coverage) runs on an ephemeral VM runner, so a `check_paths` job
skips it when a pull request changes nothing outside `docs/`. The
automated reviewer still runs on those pull requests. Commenting
`@shakenfist-bot please retest` re-runs the workflow by dispatch, which
always runs the full set.

`.github/workflows/supply-chain.yml` scans the git history for leaked
credentials with gitleaks on every pull request, every push to
`develop` and weekly. It is deliberately not path filtered: a
credential in a documentation sample is still a credential. The scan
lives in `tools/gitleaks-scan.sh`, which also plants two credentials in
a scratch directory and fails unless gitleaks reports both, so a clean
result means "scanned and found nothing" rather than "could not find
anything". Run it locally with `tools/gitleaks-scan.sh`, or
`tools/gitleaks-scan.sh --gitleaks PATH` to use a specific binary; it
needs a full (not shallow) clone.

The same workflow runs [skillsaw](https://skillsaw.org/) over the agent
context (`AGENTS.md` and anything else an agent is handed) by running
its pre-commit hook. `.pre-commit-config.yaml` carries that hook
alongside actionlint and shellcheck, so run `pre-commit install` once
per clone and `pre-commit run --all-files` before proposing a change.

## Adding a New Command

1. **Define the protobuf messages.** Add request and reply
   message types to `shakenfist_agent/protos/agent.proto`. Add
   the request to the `HypervisorToAgentCommand.request` oneof
   and the reply to `AgentToHypervisorCommand.reply`.

2. **Regenerate stubs.** The proto files and generated stubs are
   maintained in the main
   [shakenfist](https://github.com/shakenfist/shakenfist)
   repository. Use `_copy_stubs.sh` to sync them.

3. **Implement the handler.** Add a `_handle_<command>` method
   to `VSockAgentJob` in `commandline/daemon.py`. Use
   `self._send_responses()` to send the reply.

4. **Register the dispatch.** Add a `HasField` check for your
   new request type in `_attempt_decode()`.

5. **Write tests.** Add a test method to `DaemonAgentV2TestCase`
   in `tests/test_daemon.py`. Tests construct protobuf messages,
   serialize them into the job's buffer, call `_attempt_decode()`,
   and verify the mocked `_send_responses()` calls.

## Dependencies

The agent runs inside guest VMs with minimal environments, so
the dependency list is kept deliberately small:

| Package | Purpose |
|---------|---------|
| click | CLI framework |
| distro | OS distribution detection |
| psutil | Boot time, I/O priority |
| symbolicmode | Symbolic chmod (e.g. `ugo+rw`) |
| setproctitle | Set process name in `ps` output |
| protobuf / grpcio | Protocol buffer serialization |

Several dependencies that were previously used (`linux-utils`,
`oslo.concurrency`, `shakenfist-utilities`) have been replaced
with lightweight inline implementations to reduce install size
and improve compatibility with newer Python versions.
