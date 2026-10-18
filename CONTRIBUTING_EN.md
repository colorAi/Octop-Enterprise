# Contributing to Octop Enterprise

[中文](CONTRIBUTING.md)

Welcome to issues, documentation improvements and code. Octop Enterprise is independently maintained for enterprise scenarios; file issues and pull requests in [this repository](https://github.com/colorAi/Octop-Enterprise). When contributing general fixes back to TencentCloud/Octop, follow upstream's own contribution process.

## Development setup

You need Python 3.12+ and uv. Day-to-day development and Docker builds use Node.js 20 (see `mise.toml`); the portable desktop rebuild script currently uses Node.js 24. The Go version for the communication service is in `services/communication/go.mod`; desktop build tools are described in `desktop/README.md`.

```sh
uv sync --locked
npm --prefix dashboard ci
make build-frontend
```

Enterprise mode depends on PostgreSQL, the communication service and Docker isolation; see the [enterprise deployment guide](docker/enterprise/README_EN.md). Development and tests use separate configuration and temporary data and must not connect to production databases.

## Changes and verification

- Branch from this repository's `main` and open pull requests against `main`.
- Python code lives in `src/octop/`; frontend source lives in `dashboard/`; do not edit the `src/octop/dashboard/` build output directly.
- Change only what is related to the issue, and explain the reason, the actual behavior and the verification scope.
- Run the affected modules and related tests; avoid full runs when they are not necessary. Run Python tests with `uv run pytest`.
- After frontend changes, run the relevant Vitest tests, TypeScript type checking and the build; after communication-service changes, run the relevant Go tests.
- Database migrations, permissions, file writes and task recovery need upgrade-compatibility and failure-path coverage.

Example commands; choose the targets that match your change:

```sh
uv run pytest tests/unit/communication/test_api.py -q
cd services/communication
go test ./internal/groups ./internal/store
```

Real PostgreSQL tests use a dedicated test database; see the communication service documentation. Tests skipped because no database is configured do not count as real-database acceptance.

## License and releases

Contribute only code and material you have the right to provide. Your changes are submitted under the root MIT license; third-party content must retain its license and provenance, and material with redistribution restrictions must not go into the public source package.

Pull requests should describe the change and verification results. Versions, tags and formal releases are managed by the maintainers; see the [source release notes](docs/open-source-release_EN.md).
