# Security reports

[中文](SECURITY.md)

Octop Enterprise is an independently maintained derivative of Octop. Report security issues related to this project to the project maintainers.

## Private reports

Do not post keys, exploit details, real employee data or identifiable business data in public issues.

If the [repository security page](https://github.com/colorAi/Octop-Enterprise/security) offers **Report a vulnerability**, submit privately there. If it is unavailable, request a private channel through an existing maintainer contact, or open an issue in [this repository](https://github.com/colorAi/Octop-Enterprise/issues) that only asks for contact; send details after receiving a private channel.

This project sets no fixed response or fix deadline; supported versions and maintenance scope follow the project release notes.

## Deployment boundaries

- The enterprise control plane holds model credentials and Docker management capability; it is for trusted administrators only.
- Employee local terminal and Python use the system account's permissions; the working folder is not a sandbox.
- Protect administrator passwords, session credentials, database passwords and communication signing keys; use HTTPS for production entry points.
- Backups may contain credentials, sessions and business files; require access control and encryption off-host.
- Self-hosting does not mean all inference is on the intranet; models and external services are configured by the deployer.

See [Enterprise deployment](docker/enterprise/README_EN.md) and [Employee and expert isolation](docs/employee-isolation.md).
