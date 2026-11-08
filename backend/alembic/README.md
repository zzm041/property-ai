# Alembic migrations

Task 2 initializes Alembic for the Tenant/Property/Building/Unit/House
foundation. The migration environment uses the same synchronous PyMySQL engine
factory and explicit UTC MySQL session setup as the FastAPI application.

Schema changes must use revision files. Never replace migrations with
`create_all`. Downgrade and destructive migration checks are allowed only
against the guarded disposable Test database.
