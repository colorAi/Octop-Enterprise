# Employee client permission and feature configuration updates

[中文](client-config-update.md)

This feature applies to the server web app and the Mac and Windows enterprise employee clients. The first independent release is `1.0.0`.

## Usage

- After an administrator changes an employee's role, permissions or policy in user management, saving notifies that employee automatically.
- After changing model access or other feature settings, the administrator can open **Application Settings → App Updates** in the left sidebar, enter an optional message in the **Employee client configuration updates** area, and select **Notify clients to update settings**.
- When **Company configuration update** appears, the employee selects **Update settings** to refresh the current interface, or **Later**. The program and local execution process keep running; no restart is needed.
- Newly authorized feature entries are added to their default menu groups, compatible with previously saved layouts; entries hidden manually and the existing order are preserved.
- The server always checks requests against current permissions. When the client receives a change it also synchronizes roles and permissions immediately; a delayed refresh does not retain revoked permissions.

Existing clients need one install that includes this feature. After that, permission and configuration updates can refresh while running. New interface code and program upgrades still follow the company's own build and installer release process.

## Refresh protection

During task execution or preparation, a pending tool approval, local message editing, attachment upload, unsent attachment or queued message, recording/transcription, or browser recording/playback, the client prompts to finish the current operation before refreshing. Text drafts are saved first; refreshing keeps the current site's sign-in state and saved drafts. Completed, failed, cancelled and pending-verification results in local state are only task receipts and are not treated as running tasks; history does not need to be cleared before updating.

Existing WebSocket events are preferred for configuration changes; reconnect, window focus and a 30-second check on visible pages are fallbacks. After choosing **Later**, polling for the same version does not prompt repeatedly; the next configuration change prompts again. Reopening the page reads the latest configuration directly.

## Implementation boundaries

- `GET /api/client-config/status`: a signed-in user reads only their own effective roles, permissions, configuration summary and update message; responses are not cached and do not expose private policy values.
- `POST /api/client-config/publish`: administrators only; the message is at most 500 characters; the publication record is stored in the existing settings table with an audit event and does not change the database schema.
- The `client_config_changed` event only tells the client to query its own status; it carries no other employee's information.
- Desktop `refresh_status` only queries existing local tasks; it does not create an execution environment or require project pairing first, and still validates the account and nonce scope.
- This feature does not call upstream installers, system software-update interfaces or service restart endpoints.

## Verification scope

Source includes tests for configuration notices, task protection and menu authorization. Each release should verify automatic role-change notices, manual administrator notices, blocking refresh while busy, draft retention and newly added menu entries. Installer, signing, real-device and deployment verification results follow the specific release record; one development test does not mean later versions are accepted.
