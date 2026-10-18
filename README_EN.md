# Octop Ent

**An open-source, self-hosted AI work and collaboration platform for enterprise teams.**

Give each employee AI experts they can choose for different tasks. Work on documents, spreadsheets, code and media, then connect with the team through a company directory, direct messages and groups. The company manages accounts, models and permissions centrally; employees work on their own computers and share the results.

> Based on [TencentCloud/Octop](https://github.com/TencentCloud/Octop), independently maintained by [the Octop Ent maintainers](https://github.com/colorAi/Octop-Enterprise). This is not an official Tencent edition. Ent stands for Enterprise.

[中文](README.md) · [Deployment](docker/enterprise/README.md) · [Employee guide](docs/enterprise-client.md) · [Contributing](CONTRIBUTING.md)

## Highlights

| Capability                               | Description                                                                                                                                                           |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Company accounts and permissions**     | Manage multiple users, organizational structure, roles and access to AI features                                                                                      |
| **Central model services**               | Configure internal or external language and image services, grant access centrally, and let employees work without managing provider keys                             |
| **Multiple experts and reusable skills** | Create or choose agents for different roles and tasks, share expert configurations, and reuse skills across the team                                                  |
| **Local task execution**                 | Let AI read and write project files in employee-authorized folders, run Python and terminal tasks, process spreadsheets, create documents, write code and run scripts |
| **Image and media tools**                | Generate and edit images through company-configured services, and process audio and video with local tools                                                            |
| **Company messaging**                    | A built-in directory, colleague direct messages, and company, department and project groups with text, images and files                                               |
| **Message and result sharing**           | Forward messages and files between expert conversations, direct messages and groups; share AI results with teammates or hand them to another expert                   |
| **Long-term memory and knowledge bases** | Retain working context with each expert and configure document knowledge bases to ground answers and tasks in company information                                     |
| **Self-hosted deployment**               | Operate company services and manage accounts, conversations and knowledge, with server workspace isolation and backup and recovery tools                              |

Octop's server-side extensions are also retained: **scheduled tasks, multi-platform IM channels, connectors (OAuth + MCP), plugins and ACP coding-tool integration**. Availability depends on execution mode and isolation policy; see the [employee guide](docs/enterprise-client.md).

## Use cases

- **Office work and research:** organize spreadsheets, summarize materials, and prepare reports and everyday documents.
- **Development and project tasks:** work with local code and files, run project scripts, analyze logs and troubleshoot problems.
- **Content and media production:** generate or edit images, process audio and video, and keep the results in the project folder.
- **Team knowledge and collaboration:** reuse experts and skills, retrieve company information, discuss requirements and share results with colleagues and groups.

## How it works

| Location          | Responsibility                                                                                                        |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| Company server    | Accounts and permissions, model access, expert configurations, long-term memory, conversations and internal messaging |
| Employee computer | Task tools and local project files and results                                                                        |
| Model services    | Company-configured internal or external inference, image generation and related capabilities                          |

Local execution still connects to company services and their configured models; task context and tool results are sent to these services as needed. Local tools run with the employee's OS permissions, and a project folder is not an OS sandbox. Viewing history across devices does not synchronize project files. See the [employee guide](docs/enterprise-client.md) for data boundaries and limitations.

## Getting started

Current source release: **1.0.0**. This is a source-only distribution; prebuilt server images and Windows/macOS installers are not promised. Build clients using the [desktop instructions](desktop/README.md). Source archives omit `.git`; the enterprise packager requires a local repository with a resolvable `HEAD`. Published sources, checksums, platforms and signing status belong in [this repository’s Releases](https://github.com/colorAi/Octop-Enterprise/releases).

### Administrator deployment

The enterprise server targets **Linux with Docker Engine and the Docker Compose plugin**, and is currently built from source. Follow the [deployment guide](docker/enterprise/README.md) to prepare services and databases, configure models and employee access, and distribute matching Windows or macOS enterprise clients.

The guide covers installation, network access, upgrades, backups and recovery. See the [desktop instructions](desktop/README.md) for client builds.

### Employee workflow

1. Install the company-provided client and connect to the administrator-provided server address.
2. Sign in with a company account and check the default working folder. To change it, open Local working folder in the left sidebar's Settings group.
3. Choose an expert and describe a file or project task.
4. Review the result and share it with colleagues, groups or another expert.

See the [employee guide](docs/enterprise-client.md) for detailed instructions.

## Documentation

| Document                                                                                                              | Contents                                                    |
| --------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| [Deployment and recovery](docker/enterprise/README.md)                                                                | Prerequisites, installation, upgrades, backups and recovery |
| [Employee quick start](docs/user-guide.md) | First login, experts and everyday tasks |
| [Employee guide](docs/enterprise-client.md)                                                                           | Tasks, results, supported scope and data boundaries         |
| [Contributing](CONTRIBUTING.md) / [Desktop builds](desktop/README.md)                                                 | Development setup, source builds and client packaging       |
| [Architecture](docs/architecture.md) / [API](docs/api.md) / [Communication service](services/communication/README.md) | System structure, interfaces and company messaging          |
| [Update procedures](docs/updates.md)                                                                                  | Configuration changes, server upgrades and client updates   |
| [Release preparation](docs/open-source-release.md)                                                                    | Public source exports and release instructions              |

## License and acknowledgments

The application and our communication service use the [MIT License](LICENSE), retaining Octop's original copyright and license notices. Third-party dependencies, assets and templates retain their own terms. See [NOTICE](NOTICE.md) for the public source distribution scope.

Thanks to [Octop](https://github.com/TencentCloud/Octop) and its contributors, Octop Harness, Gateway, Memory and the wider open-source community. File ordinary issues in [this repository](https://github.com/colorAi/Octop-Enterprise/issues); report vulnerabilities privately according to [SECURITY.md](SECURITY.md).
