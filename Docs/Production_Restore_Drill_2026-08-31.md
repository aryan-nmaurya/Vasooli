# Production backup and restore drill — 31 August 2026

Executed on the EC2 production host against the Docker-hosted PostgreSQL 17 database.

- Backup completed in **0.47 seconds**.
- Compressed dump: `vasooli-20260831-160038.sql.gz` (**44 KiB**).
- Restore into the isolated scratch database completed in **2.13 seconds**.
- Validation counts after restore: **8 invoices** and **86 audit logs**.
- The scratch database was removed automatically by the drill cleanup trap.
- Observed drill RPO: effectively zero because the restore used a dump taken immediately before the drill.
- Observed database-only RTO: **2.13 seconds**. This excludes host replacement, DNS, container image retrieval, and application validation.

## Remaining durability gap

`BACKUP_S3_URI` is not configured. The verified dump exists only on the EC2 host, so this drill proves database recoverability from a local dump but does not protect against loss of the host or its attached storage. A durable operational RPO cannot be claimed until scheduled off-host backups and failure alerts are configured.
