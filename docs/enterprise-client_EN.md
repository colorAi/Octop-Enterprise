# Octop Enterprise desktop client

[中文](enterprise-client.md)

## Getting started

1. Get the server address, account and a client build that matches the server from your administrator.
2. Expand **Server address** under the sign-in button, enter the company server root URL, then sign in with your account and password. HTTPS is used when the scheme is omitted; use `http://` only when the deployment explicitly uses intranet HTTP.
3. After signing in, open **Local working folder** in the left sidebar's **Settings** group and confirm the folder. To use an existing project, choose **Change folder**; if no folder is set, choose **Choose folder**.
4. Choose an expert and create a task. Describe the file or project you need; when it finishes, open the result card to locate the output.

With **Remember me** selected, the client can restore the session while the login credential is still valid; passwords are not saved. Signing out ends the current device session.

## Default folder and local tools

The first time a company account is used on this computer, the client creates and authorizes a default working folder. On Windows it prefers `Workspace` under the installation directory and falls back to `Octop Workspace` under the user profile when that is not writable; on macOS it uses `Octop Workspace` under the user profile. Each company account gets its own subfolder; the actual path is shown on the **Local working folder** page.

To change the folder, choose and authorize one in the system folder picker. The client saves the folder per company address and employee account. **Remove folder** revokes the current grant and keeps project files; you can choose a folder again later. If a saved folder is unavailable, the page prompts you to choose another one.

On the **Local working folder** page, select **Check tools** to check the local execution engine; expand **Tool details** to see tool versions.

## Files, models and history

Project files are stored in the authorized folder on this computer. The company server stores accounts, expert definitions, long-term memory, formal conversations and recovery records. The local computer stores execution checkpoints, operation receipts and pending synchronization records outside the project folder.

Before each question, the client recalls relevant memory from the company server. Synchronized user messages and replies are indexed there, and explicit durable facts are saved through server memory tools. Memory is isolated by employee expert instance. Existing experts retain their server SQLite stores; new instances use the configured server backend. The native harness disables personal memory databases and expert bootstrap. Project documents are not automatically loaded as personal memory.

After switching to managed assignments, personal experts with earlier conversations remain visible with a **History** label and read-only chat access. An unambiguous earlier expert belonging to the same employee and template contributes its server memory and personal profile to the new instance. Ambiguous matches are not combined. Existing data stays in place; another employee's or administrator's personal data is never inherited.

Local commands flagged by the tool guard, including removal and moves, display an approval card. Approval permits the operation; rejection or cancellation leaves it unexecuted. Parallel commands require individual decisions. You can enable automatic approval for the current conversation. Pending approvals return when you revisit the page; restarting the client does not automatically replay interrupted commands.

A temporary synchronization failure does not change completed local work into an execution failure. **Local working folder** reports pending synchronization; the client retries when the connection returns, including reconciliation after signing in again. A conversation must finish its previous synchronization before starting another turn. Uploading receipts never repeats completed tool operations.

Local commands have a default fifteen-minute execution timeout, with a per-command ceiling of one hour. Command output is visible while running. A model request times out after ten minutes without stream data. Transient timeouts, rate limits and connection failures before the first returned model chunk are retried up to three times; streams that have already started returning data are not automatically resent. Each turn applies the selected reasoning effort and Ask, Plan or Craft mode; Plan may write only plan files.

Model inference runs on company-configured services, and task execution requires a connection to the company server and model services. File names, excerpts needed to complete a task and tool results may be sent to those services; the whole project is not synchronized automatically. Images and files uploaded or forwarded through chat go to the company server; clients that support attachment import copy the selected attachments into the current local task folder before processing. The terminal and Python use the current OS account's permissions; choosing a folder does not restrict what other processes can access.

Other devices can view history within your permissions, but they do not automatically receive files from the original computer or take over a running process. Older server-side tasks are read-only in the desktop client; start a new conversation to continue work locally.

## Forwarding messages and results

For an employee question, an agent reply or a generated result, **Forward** lets you choose company employees, groups you have joined and may post to, or agents you have enabled. Colleague chats and group messages use the same entry point, and you can select multiple targets. Forwarding to an agent starts a new conversation and begins processing; successful results can be opened directly, partial failures can be retried in the same dialog, and successful targets are not sent twice.

The forward dialog lets you select the original message text, each image and each file separately, or choose **Only images** / **Only files**. Images have thumbnails, and you can forward only selected images from a multi-image message. After choosing targets, add optional instructions, for example "organize this file and list action items"; the selected attachments and instructions are sent in the same agent request. Sending fixes the content and instructions for that attempt; retries do not resend successful deliveries, and changing the content requires closing and forwarding again.

Local results sent to an employee or group are verified against the original file's SHA-256 from the message, then uploaded as company chat attachments; recipients view or download them according to company chat permissions. Attachments forwarded to a local agent are saved in the target conversation's task folder; forwarding results between local agents copies the files and keeps the originals. The per-file limit is 50 MiB; long text and multiple attachments are split according to chat limits.

Local file transfer requires a client shell that includes the feature, the computer that holds the result and an authorized working folder. If only the web app has been updated but the client shell has not, local file forwarding reports that the feature is missing; plain text forwarding still works. Forwarding does not automatically enable group bots or synchronize the entire working folder.

## Company knowledge bases

Administrators create knowledge bases, configure retrieval models and maintain documents on the Knowledge Base page. Edit an employee under Management → User Management and select their Knowledge base access, or use the Knowledge tab in Company structure → Employee settings. Only administrator accounts can assign knowledge bases; expert and skill assignments do not grant access automatically.

Employees can browse assigned documents and select a knowledge base in chat, without creating their own. Grants permit reading and retrieval, not editing administrator-maintained documents. Revoking a grant prevents further reads and searches. Existing personal material is preserved.

Shared knowledge bases remain available to everyone. For access limited to selected employees, turn off sharing and assign the base in employee settings. Saving access sends a configuration notification; clients also detect changes after reconnecting. The server checks access on each read and search.

## Employee model access

Administrators select available chat models under Company structure → Employee settings → Models. The employee editor under Management → User Management edits the same assignments. Only administrator accounts can change them.

New employees and existing employees with no model assignments cannot use models. Newly configured models require an explicit assignment, and the same model on different providers is assigned separately. Automatic selection, vision fallback and company local tasks use only assigned models. Administrators retain access to all available models.

After an assignment is removed, the server rejects subsequent model calls, including the next call in a running task. Requests already sent are not rolled back. Saving assignments notifies the employee to update their configuration.

## Permissions, configuration and software updates

When an administrator changes an employee's role, permissions or policy, the affected employee is notified automatically. After saving model access or other feature settings, an administrator can also publish a configuration notice under **Admin → Application Settings**, on the **App Updates** tab. When **Company configuration update** appears, select **Update settings** after any task execution/preparation, tool approval, local message editing, attachment upload, unsent attachment or queued message, recording/transcription, or browser recording/playback has finished; you do not need to restart the client, and the signed-in state and saved drafts are kept.

Newly authorized menu entries are added to their default groups; entries you hid manually and your custom order are preserved. Revoked permissions are enforced by the server immediately and cannot be kept by choosing **Later**.

**Update settings** only applies permissions and feature settings supported by the current program. A new client interface, program or tool environment requires a new build from your administrator. See [Update procedures](updates_EN.md) for the difference.

## Scope and limitations

- The enterprise client supports single-expert local tasks. Manually forwarding a result to another expert is supported; automatic multi-expert team collaboration is not.
- You can import images and files already uploaded by the current expert before a task starts, and forward attachments from company messages; the client must include attachment import, and arbitrary external links cannot be imported directly. **Insert** during a running task currently supports text only, not attachments.
- Administrators configure and authorize knowledge bases; a local task can search the selected company knowledge base, and retrieval runs on the company server.
- The local runtime does not load external connectors or ACP coding tools. Connectors, browser, terminal and ACP capabilities retained on the server are also subject to permissions and Docker isolation policy; they are not local capabilities of the employee computer. Scheduled tasks and external IM channels are server-side entry points as well.
- Group bots and AI group chat summaries are not supported.
- Installation, signing, notarization and real-hardware validation status differ by platform; see the release notes for the corresponding build.
- A completed write or image generation must not be blindly repeated after a disconnect. When a result is unknown, check the original task, files and recovery state first.

## Administrators and builders

The server must enable the device channel and the company local runtime, and provide matching experts, model access and communication configuration; see [Enterprise deployment](../docker/enterprise/README_EN.md).

The desktop shell and the bundled Python tool environment must be built together. See [Desktop builds](../desktop/README.md) for offline dependencies, target architecture validation and parameters. Upstream standalone personal-edition clients should not replace this project's enterprise client.

This repository provides source code. Administrators build a client that matches the server according to the desktop instructions and distribute it to employees.
