# R6 AUTH001 isolated backup and restore result

Run `restore027-20260925r6` completed on 2026-09-25. This is an isolated database recovery check, not media-byte recovery or Production disaster-recovery acceptance.

- Source: `127.0.0.1:3307/property_ai_auth001_test@20260925_0012_task_intents`.
- Destination: new `property_ai_auth001_restore_20260925`; it did not exist at preflight and was never used by API or Worker.
- Host backup: `tmp/real-ai-integration-027/restore/restore027-20260925r6/auth001-0012.sql`, 749523 bytes, SHA-256 `49a4273a77f8298d2ced2c7fd4a4f1f868b82972950dffeaebc436ec2d826c0c`.
- At final verification time, source and destination each had revision `20260925_0012_task_intents`, 58 tables, identical per-table counts and schema-shape SHA-256 `d1032908f88d4041ca2226e92930d672ae97884275eea5ed9e6c93b482139111`.
- Representative equal counts: task intents 39, assignments 36, work orders with contact snapshots 28, audit logs 754, resident relations 65.
- Verification used container-local read-only root queries. The application account intentionally has no privilege on the recovery database; no credential was broadened.
- Source, destination and host backup are retained. No queue replay, migration, source overwrite, cleanup or media restore occurred.

Retained execution history: two startup/path failures occurred before database I/O. The first usable dump was created before a shell-quoting defect prevented destination creation; the destination remained absent. After quoting was fixed, create/import succeeded, but application-account verification correctly failed because that account is intentionally ungranted. The final verification switched only to container-local root read-only inspection. These failures were not erased or represented as successful earlier attempts.

Evidence correction: the legacy `--resume-verification` branch wrote its final source sample into both `source_before` and `source_after`. Therefore the original pre-restore source snapshot is **UNAVAILABLE** and the manifest does not independently prove that the source stayed unchanged across the restore window. `restore-r6-clarification.json` binds this limitation to the unchanged original manifest hash. The helper now persists a first baseline before dumping and fails closed on drift; this run was not repeated.

Still open: media/object bytes, persistent MySQL storage instead of tmpfs, loopback-only 3307 publication, target-host backup scheduling/retention/encryption, production restore/rollback timing and shared-schema ownership.
