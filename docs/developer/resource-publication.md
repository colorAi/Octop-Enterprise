# Resource publication

Employee runtime definitions, skills and attachments are captured as an immutable manifest. The role-based PostgreSQL deployment publishes administrator sources from Docker-isolated workspaces into objects outside the writable workspace.

Publication captures and verifies the source, checks source identity, dirty revision and the publishing Worker's lease, then atomically advances the published pointer. A failed save or interrupted publication keeps the previous complete manifest available; an uncommitted draft does not become an employee runtime definition.

Readers reuse the published manifest and continue checking current employee access, source policy, model grants and department scope. Restoring an old definition does not restore an earlier authorization revision. These snapshots contain references and enabled capabilities rather than copies of provider credentials.

Manifest objects, task assets and pending delivery records have references used by active work and recovery. They must not be removed solely by file age. Backup and recovery requirements are in the [enterprise operator guide](../../docker/enterprise/README.md).
