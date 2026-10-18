# Employee and expert isolation

This document describes **server-side** expert workspace isolation. Employee
desktop tasks instead run on the employee computer with that OS account's
permissions; their working folder is not a Docker or OS sandbox. See the
[employee guide](enterprise-client.md) for local execution and data boundaries.

Octop Ent enterprise deployment uses `OCTOP_AGENT_ISOLATION=docker`. This is an operator policy; an employee
cannot override it through an expert's backend, workspace, skill paths, memory
database path, or tool settings.

Expert definitions remain in the central catalog. Calling a shared live expert
creates one private instance per employee, reused on later calls. Only published
prompt/skill content is copied: no source employee profile, memory, uploads,
sessions, connector credentials or environment file. Teams resolve shared
members to these private instances before saving their roster.

Each private expert has a separate Docker container and named content volume.
The volume identity includes the database row and owner so reusing a deleted
custom expert ID cannot reopen another employee's files. Containers run as
1000:1000 with a read-only image, no network, all capabilities dropped,
no-new-privileges, and CPU/memory/process limits. Only selected skill package
directories may be mounted read-only. Containers receive neither the Docker
socket nor global environment variables, account/communication databases,
other expert volumes, browser profiles or communication media.

The trusted Octop service holds the Docker socket and performs model requests
and authenticated application operations. Host shell MCP connectors, ACP,
host browser/desktop tools and global environment tools are disabled for Agent
execution, including after hot updates. Employees cannot open the host terminal.
HTTP connectors and other trusted application tools retain their existing user
authorization. Administrator control-plane access remains an administrative
capability; this is not a hostile-administrator boundary.

AI histories, checkpoints and memory remain in trusted server storage
(PostgreSQL and/or server-side files, according to their backend). Agent tools
access a separate volume even though both locations
use the same logical path. This avoids exposing the runtime database or letting
an Agent-created symlink redirect host-side persistence. Isolation restricts
memory to managed SQLite or control-plane PostgreSQL backends, ignoring arbitrary
database paths/DSNs supplied by an expert.

## Cutover and recovery

1. Build and retain old images. Stop Octop and communication writes.
2. Back up both PostgreSQL databases, the complete data tree, communication
   media, Compose, environment and signing keys under a protected directory.
3. Run `tools/migrate_isolated_workspaces.py` without `--apply` for inventory,
   then with `--apply` while the named Octop container is stopped. It copies
   regular files and symlinks to the private volume, keeps runtime databases and
   sessions on the host, compares every file hash, and records completion in
   `isolation-migrations/`. It never removes the original files or overwrites a
   previously migrated volume.
4. Run the communication `im-server --migrate` command (current schema: see communication service README), then start
   the new application and communication images. Keep the sandbox image pinned.
5. Verify existing histories/files, login, denied cross-owner requests, private
   sandbox permissions and a real four-member team turn.

### Running the migration tool

Run from the repository root on the Docker host using the same Compose file,
environment file, image and data mounts as the application. The image does not
contain `tools/`, so mount that directory explicitly. Keep PostgreSQL running;
stop the main application and communication writers before the cutover:

```sh
OCTOP_MIGRATION_CONTAINER=$(sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml ps -a -q octop)
test -n "$OCTOP_MIGRATION_CONTAINER"
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml stop octop communication
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml run --rm --no-deps \
  --volume "$PWD/tools:/opt/octop-tools:ro" octop \
  python3 /opt/octop-tools/migrate_isolated_workspaces.py --home /data/.octop \
  --stopped-container "$OCTOP_MIGRATION_CONTAINER"
```

Review the inventory, then repeat the last command with `--apply`. The named
container must exist and be stopped. The temporary container inherits the
application's database connection, data mount, sandbox image and Docker socket;
using only a host Python command with a different home path can produce
incorrect volume identities. Keep `/data/.octop` identical to the application.
Inspect `/srv/octop/app/isolation-migrations/<agent_id>.json` on the host (adapt
`/srv/octop` to your configured data root): each completed record must contain
`applied: true`, the expected volume and file hashes. Resolve any failure before
restarting writers, then continue with communication migration and verification.

Future backups must include the private named volumes as well as the original
host data and PostgreSQL databases. Before reverting to the old local backend,
copy any new workspace files out of the volumes. Restoring the cutover backups
instead loses changes made after that backup. Do not delete schema migration
records or restore old SQLite control-plane files as a rollback shortcut.

`tools/verify_isolated_sandbox.py` tests disposable volumes on the target Docker
engine without mounting production data, including migration hash validation.
