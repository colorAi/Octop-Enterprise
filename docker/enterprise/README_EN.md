# Octop Enterprise deployment and recovery

[中文](README.md)

This guide targets Linux with Docker Engine and Compose v2. Base single-host mode uses `docker/docker-compose.yml`. Enterprise mode includes the Octop control plane, the Go communication service, two PostgreSQL databases and isolated employee workspaces. Clients run project tools on employee computers; the server keeps accounts, model access, memory, history and recovery receipts.

This guide corresponds to Octop Enterprise **1.0.1b2**, Harness **1.0.3**, main database schema **30**, device protocol **2** and company runtime protocol **1**. Server images and client tool environments must be built from matching source and protocol versions.

## Before you deploy

This is a **source deployment for enterprise IT**: Compose manages the application, communication and PostgreSQL services, and scripts generate private configuration. Administrators prepare Docker, build images, complete initialization, configure the access address and distribute clients. Follow this guide for installation, backup and recovery; offline environments must prepare images and dependencies separately.

Validate one employee's sign-in, model call, local task, messaging and attachments on the company intranet before expanding. Determine hardware and seat quotas through real workload tests, and reserve resources for image builds, databases, media storage and concurrent requests.

### Server prerequisites

| Item | Requirement |
| --- | --- |
| OS and permissions | Linux Docker host; administrator has sudo and Docker operation permissions |
| Container tooling | [Docker Engine](https://docs.docker.com/engine/install/) and the [Docker Compose plugin](https://docs.docker.com/compose/install/linux/); this guide uses commands that support `up --wait` |
| Host tools | Python 3.11+ (configuration and backup scripts), curl; Node, uv and Go are used during image builds and do not need to be installed on the host |
| Build network | Access to base images, system packages and npm, Python and Go dependencies referenced by the Dockerfile; fully offline servers need these build materials prepared separately |
| Access and models | Decide the intranet address employees use; prepare a working model service and credentials. Production entry points may use a company domain and a trusted HTTPS certificate |
| Data and backup | A dedicated persistent directory and a backup location outside it; back up employee-computer projects separately |

Verify the tools first; the following commands only read the environment:

```sh
sudo docker info
sudo docker compose version
python3 --version
```

If the Docker daemon is unreachable, fix Docker startup or socket permissions first. The default socket is `/var/run/docker.sock`; when using another socket, pass `--socket /absolute/path/to/docker.sock` when first generating configuration and make the Docker CLI use the same engine. Do not copy a local development socket or absolute path to the server.

## First installation

Use this project's source archive or a specific release commit and run the steps from the repository root. For a source archive, record `PUBLIC_SOURCE.json` and the archive checksum; for Git, record the commit and local changes. The `enterprise-1.0.1b2` tags below are local image tags used by this guide; each deployment should use unique tags traceable to its source.

### 1. Generate a new private configuration

```sh
sudo python3 docker/enterprise/prepare.py --data /srv/octop --env /etc/octop.env
```

`prepare.py` accepts only a new data directory and a new environment file. It generates three independent database passwords and two communication keys, and sets private directory and file permissions. When run with sudo, it sets UID/GID 1000 for the application, media and secret directories; the PostgreSQL directory is initialized by the official image entrypoint. Deployments without sudo must correct ownership as prompted by the script.

This script must not be used to upgrade or repair an existing instance; existing keys must be preserved.

### 2. Set images and the employee entry point

Edit `/etc/octop.env` with the administrator's editor, keep the generated passwords and keys, and change the three image values in place:

```dotenv
OCTOP_BASE_IMAGE="octop:enterprise-1.0.1b2"
OCTOP_APP_IMAGE="octop-enterprise:enterprise-1.0.1b2"
OCTOP_IM_IMAGE="octop-im:enterprise-1.0.1b2"
```

The default `OCTOP_BIND_ADDRESS="127.0.0.1"` means port 8088 is reachable only from the server itself or a local reverse proxy; employee computers cannot connect directly. Choose according to the deployment scenario:

| Scenario | Configuration and address employees enter |
| --- | --- |
| Company HTTPS entry point | Keep the loopback binding and configure the domain and a system-trusted certificate according to `nginx.conf.example`; employees enter `https://company-domain` |
| Trusted intranet HTTP testing | Change the binding to the server's actual intranet NIC address and allow employee access per company network policy; employees explicitly enter `http://server-intranet-ip:8088` |

When the client omits the scheme it uses HTTPS and does not fall back to HTTP. Changing the bind address does not configure a reverse proxy, certificate or firewall. PostgreSQL and the communication service do not publish host ports; employees only need the application entry point.

### 3. Build, initialize and start

```sh
sudo docker build -f docker/Dockerfile -t octop:enterprise-1.0.1b2 .
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml config -q
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml build
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml up -d --wait postgres
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml run --rm --no-deps communication --migrate
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml run --rm --no-deps octop octop init
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml up -d --wait
```

Confirm each step succeeds before continuing. `config -q` only validates the configuration and does not print parsed values containing passwords; [Compose `--wait`](https://docs.docker.com/reference/cli/docker/compose/up/) waits for services to become running or healthy. Communication migration and administrator initialization do not start the other application services first.

`octop init` prompts for the administrator username and password in the terminal and does not put the password on the command line. Run it only on first install; upgrades must not initialize again and must not use `--force`. PostgreSQL runs `init-databases.sh` only when the data directory is first created, creating `octop_app/octop` and `im_app/octop_im`; application roles are not allowed to access each other's database.

The first build downloads and compiles dependencies; later builds can reuse the cache. A successful container start does not mean employees can work yet; complete the next step.

The base application image preinstalls the locked local embedding runtime (`local-embedding`). The enterprise image build checks that these dependencies can be imported as UID 1000. Model weights are downloaded when an administrator requests them and are stored under `embedding_models/` in the persistent application directory. If an older image reports missing prerequisites or a non-writable Python environment, follow the upgrade procedure to rebuild both application images and replace the application container. Updating source files or restarting the old container does not install the missing image dependencies.

Downloading only stores model files; model tests, knowledge indexing and retrieval load inference sessions on demand. Budget large-model memory separately for each worker's container limit and the host as a whole. A successful download does not establish that the runtime memory limit is sufficient. If `OOMKilled` is followed by `executor_fenced`, check the memory limits and follow the [role recovery procedure](ROLES.md#任务补读与故障核对) instead of directly restarting the old worker.

### 4. Check services and complete the first employee flow

```sh
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml ps
curl -sS http://127.0.0.1:8088/api/health/ready
```

With the default loopback binding, readiness should return `{"ok":true,"checks":{"database":true,"communication":true,"sandbox":true}}`; when the bind address changed, use the corresponding intranet IP. The checks are inside the `checks` object. `sandbox` checks Docker connectivity and image presence and does not replace an actual isolated task run.

1. Open the configured employee access address in a browser and sign in with the administrator account just created.
2. Under **Admin → Model Management**, configure at least one working model service; under **Admin → Users**, create a test employee with the required permissions and prepare experts employees can use.
3. Distribute Mac/Windows enterprise clients matching the server. Deploying only the server does not automatically produce a complete employee installer; the build entry point is in [Desktop builds](../../desktop/README.md).
4. After the employee signs in to the desktop client, open **Settings → Local working folder**, confirm the default folder or choose **Change folder** to select a project. Have the expert read a sample file and produce a summary, then confirm the result and history are viewable.
5. Check colleague messages, attachments and the groups actually needed, then change the test employee's permissions and verify the configuration notice. The manual notice entry point is **Admin → Application Settings → App Updates**. A complete enterprise backup and recovery drill is still required.

Before distributing clients, verify the installer's platform, signing, notarization and device validation records.

### Common issues

| Symptom | Check first |
| --- | --- |
| The server reaches the site but employee computers cannot connect | Whether it is still bound to 127.0.0.1, the intranet address and port, the reverse proxy and company network policy |
| Certificate or connection errors with an intranet HTTP address | Whether the client omitted `http://`; whether the HTTPS certificate is trusted by the client OS |
| `/api/health/ready` returns 503 | The checks that are false in the response; main database connectivity, communication migration, Docker socket and sandbox image |
| Application startup reports directory or key permission errors | Whether data and key directories belong to UID/GID 1000; whether the Docker socket group matches the configuration |
| First build hangs or fails | Base-image and dependency downloads, build host memory and disk; handle the specific failing build stage |
| Employees cannot see an authorized menu | Account permissions, configuration refresh, whether the menu was previously hidden manually; whether the client includes the feature code |
| The web app is updated but the client still shows the old interface | Client assets are bundled in the installer; configuration notices do not download a new interface, and a matching new installer is required |

View recent service logs:

```sh
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml logs --tail 100 octop communication postgres
```

### Deployment boundaries

Key files must not be regenerated during a rebuild; changing the communication identity or cursor signing key breaks existing identity and synchronization state. The server-side isolation workers use the same build as the server image, with UID/GID 1000, a read-only root, no network, all capabilities dropped, no-new-privileges, and separately named volumes. The server's Docker socket is only for the trusted administrator-maintained control plane and must not be mounted into employee workers.

This directory provides a single-host Compose operations entry point and does not include an HA cluster, autoscaling or one-click disaster-recovery failover. Public access requires HTTPS and access policy, plus testing of history messages, image loading and actual concurrency.

## Configuration

The table lists both Compose inputs and in-container process configuration. The current `compose.yml` fixes process settings such as the device channel, local runtime, database connections and Docker isolation; adding the same variable to `/etc/octop.env` does not override those fixed values. Parameters substituted directly from the environment file include the data directory, images, bind address and port, Docker socket/GID, HTTP media origin and timezone; other changes require matching Compose or override adjustments.

| Configuration | Meaning |
| --- | --- |
| `OCTOP_DATA_ROOT` | May be overridden by the environment file; absolute directory on the target Docker host containing app/media/secrets/postgres |
| `OCTOP_DATABASE_URL` | Constructed by Compose and cannot be overridden by the same environment variable; Octop main database connection, built from the independent password |
| `OCTOP_IM_DATABASE_URL` | Constructed by Compose and cannot be overridden; communication-only main database; must not use the Octop role |
| `OCTOP_IM_URL` | Fixed by Compose; fixed internal origin from Octop to the communication service; communication is disabled when unset |
| `OCTOP_IM_IDENTITY_KEY_FILE` / `OCTOP_IM_CURSOR_KEY_FILE` | Fixed Compose paths; stable private key files; the first is shared by both services, the second is used only by communication |
| `OCTOP_DEVICE_CHANNEL_ENABLED` | Fixed to true by Compose; enables the native device channel |
| `OCTOP_COMPANY_RUNTIME_ENABLED` | Fixed to true by Compose; enables enterprise local execution and depends on the device channel |
| `OCTOP_AGENT_ISOLATION=docker` | Fixed by Compose; forces isolation of employee server workspaces |
| `OCTOP_SANDBOX_IMAGE` | Derived from `OCTOP_APP_IMAGE` and cannot be overridden; the enterprise image from the same build; pin a unique tag/digest after release |
| `OCTOP_SANDBOX_HOST_DATA_DIR` | Derived from `OCTOP_DATA_ROOT` and cannot be overridden; host app directory used for read-only skill package mounts |
| `OCTOP_DOCKER_SOCKET` / `OCTOP_DOCKER_GID` | May be overridden by the environment file; Docker Engine socket path and actual group ID |
| `OCTOP_MEDIA_HTTP_ORIGINS` | May be overridden; administrator-approved HTTP image-generation origins, comma-separated, empty by default; exact domain and port match, no wildcards. For example `http://image-service.example.internal:3001` |
| `OCTOP_DEFAULT_TIMEZONE` | May be overridden; defaults to Asia/Shanghai; user dates and scheduled tasks follow the server setting |

When migrating an existing deployment, first map the existing environment variables, databases and keys to this table; keep the existing data and keys and follow the upgrade section below. Do not run `prepare.py`.

## Upgrade and rollback

The enterprise image includes Chromium and its system libraries for the workbench, installed at `/opt/octop-browser`. Each build launches the browser and captures a screenshot offline as UID 1000, and imports the knowledge embedding dependencies. Service readiness does not replace this check. To repeat it against a candidate image:

```sh
docker run --rm --network none --read-only --cap-drop ALL --tmpfs /tmp --tmpfs /data:uid=1000,gid=1000 \
  --entrypoint python octop-enterprise:enterprise-1.0.1b2 /usr/local/libexec/octop/verify_runtime.py
```

Use `Dockerfile.runtime-reuse` for offline assembly from existing local images. `APP_IMAGE` must contain the complete current application, dashboard and Python environment; `RUNTIME_IMAGE` must contain a working browser and its system libraries. Both must have matching OS base layers and Python ABIs. Do not select a runtime based only on application readiness. Supply both local images explicitly with `--pull=false --network=none`:

```sh
docker build --pull=false --network=none -f docker/enterprise/Dockerfile.runtime-reuse \
  --build-arg APP_IMAGE=octop:current-application --build-arg RUNTIME_IMAGE=octop-enterprise:verified-runtime \
  -t octop-enterprise:next-candidate .
```

1. Save the old commit, image IDs, configuration and four-architecture client release manifest; check both database versions. Main database: `SELECT version FROM _schema_version`; the communication service checks its own migration table at startup. Upgrade commands always use the new communication image's `--migrate`.
2. Stop accepting new tasks and wait for local task reporting to finish or mark results unknown. Unknown results must not be retried automatically.
3. Take a complete backup covering the same no-write window as described in the next section; preserve the original projects on employee computers.
4. Restore and verify that backup in a new empty directory or temporary database first; rebuild the application, communication service and payload from the new commit.
5. Stop the application and communication service, run `--migrate` with the new communication image, then start both services; Octop runs main-database incremental migrations at startup. Check readiness, sign-in, attachments, skill downloads, device revocation and task recovery.
6. Application-only rollback: before switching back to old images, verify whether they support the current schema; disabling the company runtime does not delete company tables. Database rollback must restore both databases and file snapshots that match the old code; do not connect old applications to an incompatible schema or overwrite data added after the upgrade. Archive new files separately and reconcile manually.

## Full backup and recovery

The in-product "System backup" covers the main database and selected host directories; company skill caches are archived with the main database. `include_config` is enabled by default and includes `config.json` and the `env` file under the application data directory when present, which may contain plaintext credentials; disable it to exclude those files. It does not include Docker isolation volumes, communication database/media, the deployment environment file `/etc/octop.env`, the contents of external key files referenced by `env`, or employee-computer projects. The manifest records external components, and isolation mode does not mark an empty host directory as a backed-up workspace. Old backups remain readable.

Operations backup entry point:

```sh
sudo docker volume ls --filter label=octop.agent.id
sudo python3 docker/enterprise/backup.py --env /etc/octop.env --output /srv/backups/octop-20261013 \
  --volume octop_workspace_REPLACE_WITH_ACTUAL_24_HEX
```

Every workspace volume belonging to this instance must be passed individually with `--volume` (repeatable); do not provide only one example. Verify the `octop.agent.id` label and worker mounts with `docker volume inspect` and `docker inspect`. Use `--no-sandbox-volumes` only when there are genuinely no isolation workspaces. The command rejects old output directories, unlabeled volumes, volumes with custom driver options, and output paths inside the data directory; it deletes no data.

The backup temporarily stops the running application, communication service and workers associated with the specified volumes, dumps both databases with `pg_dump -Fc`, archives app/media/secrets, the deployment environment and each volume, then restores the previously running components. `complete` in `manifest.json` must be true, and file SHA-256 values must be checked; `complete=false` means the backup did not finish and cannot be used as a complete recovery backup. The deployment environment and backup contain keys; encrypt them when stored off-host and keep them administrator-readable only. External databases, external workspaces and employee-computer projects require separate backups according to their systems.

Recovery runs only on another clean host or an explicitly prepared empty instance:

1. Build matching images from the selected release commit; verify the SHA-256 of every file in the manifest. Do not use a `complete=false` archive.
2. Restore `deployment.env` as the private environment file; adjust host paths, socket and GID, and keep all passwords and signing keys. Safely extract `files.tar.gz` into the new data root, preserving uid/gid 1000; **do not run prepare.py to regenerate keys**.
3. Start empty PostgreSQL so the initialization script creates both databases and roles. Stop the application and communication service. Run:

   ```sh
   sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml exec -T postgres \
     pg_restore -U octop_bootstrap -d octop --single-transaction --exit-on-error --clean --if-exists < /srv/backups/SNAPSHOT/octop.dump
   sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml exec -T postgres \
     pg_restore -U octop_bootstrap -d octop_im --single-transaction --exit-on-error --clean --if-exists < /srv/backups/SNAPSHOT/octop_im.dump
   ```

4. Create **same-named** named volumes according to the manifest and restore the original `octop.agent.id` labels. Use the same enterprise image, root user, no network, and extract the corresponding tar to the volume root; preserve file uid/gid. Do not start workers until all volumes are restored.
5. Start the application and communication service, check readiness and both database migration states, then verify real attachments, history messages, skill downloads, isolated files and device signature authentication. Record recovery duration, data-loss window and verification results, and confirm they meet the company's recovery requirements.

## Operations and capacity

- `/api/health` remains a compatible liveness probe; `/api/health/ready` checks the main database query, enabled communication `/healthz`, Docker reachability and sandbox image presence, returning 503 on failure. It cannot replace acceptance of an actual isolated tool run.
- Administrators can use `/api/health/storage` to view task/event/checkpoint/write/receipt counts, serialized content character counts (not physical database bytes), task states, skill cache files and bytes, device counts, identity delivery backlog and maximum attempts. The endpoint is read-only and returns no messages, paths or credentials. Scanning a large database can be slow and should not be a per-second probe.
- Retention: tasks, checkpoints, receipts and skill hashes are referenced by history recovery; nothing currently expires or is deleted by time. Tasks that are still running, being cancelled, or have unknown results must not be cleaned up. Deleting employees/experts/threads follows cascading cleanup. To reclaim capacity, first confirm references, complete backup and recovery acceptance, then design a dedicated administrative process; never delete skill caches by mtime.
- Set cleanup policy from business retention and employee count; observe database growth, PostgreSQL lock waits, message/file/AI concurrency latency and memory before using load tests to set quotas.
- Readiness 503: check the main database, communication migration and Docker socket permissions first; skill read failures should check the host data path and package directory permissions. Desktop process exits show the security exception type and exit code; raw stderr is not logged, to avoid leaking prompts, paths or credentials.

## Targeted acceptance and four-architecture delivery

```sh
make check-enterprise ARGS='--section backend'
make check-enterprise ARGS='--section frontend'
make check-enterprise ARGS='--section desktop'
make check-enterprise ARGS='--section communication'
make check-enterprise ARGS='--section packaging'
```

Real database tests use `OCTOP_IM_TEST_DATABASE_URL` and `OCTOP_IM_TEST_OWNER` pointing to a **dedicated temporary PostgreSQL**; tests create and drop databases with a test prefix. Do not use production addresses. When not configured, some tests are skipped and do not count as real PostgreSQL acceptance.

Build the enterprise payload for the current architecture from the same source with `desktop/portable/package.sh`, then prepare same-architecture Python, ffmpeg/ffprobe and office dependencies. On the target machine, run `verify_company_runtime.py` with the bundled interpreter. `package-company.py` checks the Octop METADATA version against the SHA-256 of all Python files in the current source, records shell/Octop/Harness/two protocols/commit/dirty/source SHA-256, and rejects old payloads even when the version number happens to match. Login asset generation runs in the shared Wails build, and Go embed also requires both JS and CSS.

After unpacking the four architectures, use `make check-enterprise ARGS='--section packaging --manifest-dir /path/to/unpacked'` to check the version matrix. Supported build targets are macOS arm64/amd64 and Windows amd64/arm64. Each release should record build and manifest results, native tool smoke tests, install/upgrade/uninstall, signing/notarization, and sign-in/disconnect/revocation, with dates. Matrix checks and in-package target smoke tests are not real installation or signing acceptance; actual status follows the corresponding release record.

For employee operations and feature scope, see the [employee guide](../../docs/enterprise-client_EN.md).


The separate `compose.roles.yml` entry uses one initialization role, two web processes and one runtime worker. It enforces stop evidence before runtime replacement and does not support scaling full runtime workers yet. Use `backup.py --roles --profile 4g` for this topology; it leaves writers stopped, including on failure. See [role deployment and recovery (Chinese)](ROLES.md). Hardware capacity and native-client fault acceptance remain unverified.
