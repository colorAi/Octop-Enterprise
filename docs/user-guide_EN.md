# Octop Enterprise user guide

[中文](user-guide.md)

This guide covers the current enterprise edition. Administrators deploy the company server and configure accounts, models and permissions; employees use the enterprise client to run local tasks and collaborate through the company directory, direct messages and groups.

## Choose your entry point

| Role or task                                              | Read                                                                          |
| :-------------------------------------------------------- | :---------------------------------------------------------------------------- |
| Administrator: first deployment, upgrades, backup/recovery | [Enterprise deployment and recovery](../docker/enterprise/README_EN.md)       |
| Employee: sign-in, working folder, tasks and results       | [Employee guide](enterprise-client_EN.md)                                     |
| Build Windows/macOS enterprise clients from source         | [Desktop builds](../desktop/README.md)                                        |
| Permission changes and software updates                    | [Update procedures](updates_EN.md)                                            |
| Development environment and code verification              | [Contributing](../CONTRIBUTING_EN.md)                                         |

Administrators build the server from this repository's source and build matching clients; employees install the client provided by the company. Software upgrades follow [Update procedures](updates_EN.md).

## Administrator quick start

1. Prepare Linux, Docker, databases and the communication service according to the deployment guide; on first install, run `octop init` to create the administrator.
2. Open the configured company address in a browser and sign in as the administrator.
3. Under **Admin → Model Management**, configure model services; under **Admin → Users**, prepare employee accounts, roles and permissions, and prepare experts for employees.
4. Distribute matching clients, and validate one employee's local task, messages, attachments and results before expanding.

To assign experts and skills, open **Admin → Users → Company structure**, select the source administrator, set the department resource pools, then use the employee's expert and skill controls. The **Basic settings** tab manages the department and position. See [expert and skill assignments](expert-assignments.md) (Chinese), including existing-account behavior and definition-update limits.

`octop init` creates the administrator account only on first install. See the deployment guide for databases, keys and recovery.

## Employee quick start

1. On the sign-in page, expand **Server address**, enter the address from the administrator, then enter your account and password. HTTPS is used when no scheme is given; intranet HTTP must explicitly use `http://`.
2. After signing in, open **Settings → Local working folder** and confirm the automatically created default folder; choose **Change folder** for an existing project.
3. From the **Agent** entry, choose an expert and create a task; describe the file or project you need.
4. Review the output and locate local results in the system file manager; forward them to colleagues, groups or another expert.

Default folder rules, attachment import, permissions and data boundaries are in the [employee guide](enterprise-client_EN.md).

## Where to find common features

The names below are the defaults; what you see depends on account permissions, client type and custom menu layout.

| Feature                                                   | Entry point                                                                                          |
| :-------------------------------------------------------- | :--------------------------------------------------------------------------------------------------- |
| AI chat and tasks                                         | **Agent** in the left sidebar                                                                        |
| Expert configuration and templates                        | **Experts** in the left sidebar                                                                      |
| Company directory, colleague messages and group chats     | **Company**; shows a custom name after a company name is configured                                  |
| Local working folder and tool check                       | **Settings → Local working folder** in the desktop client                                           |
| Scheduled tasks                                           | **Automation** in the left sidebar; a server-side task entry                                         |
| Models, users and roles                                   | **Admin → Model Management** and **Admin → Users**                                                               |
| Backup, environment variables, HTTPS and updates          | **Admin → Application Settings**                                                               |
| Admin configuration notices                               | **Admin → Application Settings → App Updates → Employee client configuration updates**              |

Configuration updates refresh permissions and feature settings; changes to the interface, native program or tool environment require a matching software release.

## Scope

Enterprise clients focus on single-expert local tasks and support project files, images and documents, plus attachments uploaded or forwarded in compatible clients. Local tasks can search authorized company knowledge bases.

Automatic multi-expert team collaboration, external connectors and ACP are not part of the current local runtime. Upstream browser, terminal, connector, ACP, scheduled-task and external IM capabilities are server-side features limited by deployment, permissions and isolation policy.

Project files do not automatically synchronize to another computer with history. Local tools use the current system account's permissions; the working folder is not an OS sandbox. Attachments uploaded or forwarded through chat go to the company server.

Issues and suggestions go to this repository's Issues; report security issues privately according to [SECURITY.md](../SECURITY_EN.md).
