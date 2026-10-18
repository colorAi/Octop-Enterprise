# Octop Enterprise Docker deployment

For enterprise mode, build from this repository and follow the [enterprise deployment guide](enterprise/README.md) for installation, upgrades, backups and recovery. The stack includes PostgreSQL, a separate communication service, the device channel and employee workspace isolation.

`Dockerfile` builds the base application image; `enterprise/compose.yml` assembles the enterprise services. Other Compose files cover base mode and development environments.

[中文](README_CN.md)
