# Delivery candidate verification

This evidence binds the isolated `property-ai-delivery-check` package. It is a
clean local deployment rehearsal, not a Production release claim.

The package was initialized from an empty persistent MySQL volume, migrated by
the guarded one-shot migration service to 0014, and bootstrapped with one local
SYSTEM_ADMIN whose secret values remain only in ignored local files. API,
Worker and Beat run the same image digest. The public Nginx endpoint passed
health and readiness before and after a full stop/start with volumes retained.
It also remained ready after the API container alone was replaced, proving the
dynamic Docker-DNS proxy fix.

The first start exposed and retained three actual packaging defects: a Redis
health-check quoting error, missing Milvus-to-MinIO credentials, and root-owned
Beat state. They were fixed and reverified. The first backup transport and
restore command quoting attempts also failed before a successful restore; the
successful backup and both databases are retained. The restore target refuses
overwrite.

All infrastructure images are pinned to the digests used by this run. The
application image uses Python 3.12.10 and a 61-entry Linux dependency lock that
exactly matches its installed package set. Local populated env files and SQL
backups are Git-ignored and are not part of the deployable source commit.

Limits remain explicit: this candidate uses Development work-order analysis,
has no trusted identity provider, and its live-provider readiness fields remain
`not_checked`. The independent 9B quality review remains `NOT_READY`, so no
real-AI AUTO business run was performed. The clean database now has a guarded
SYSTEM_ADMIN organization-onboarding page which creates a tenant and property
and grants an already normally registered account its initial TENANT_ADMIN
scope in one transaction. The independent target-account run is recorded under
`delivery-org-028-independent-05`; formal provider activation and Production
release remain separate blockers.
