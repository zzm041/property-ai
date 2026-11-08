"""Source-bound customer bucket initialization; no implicit service startup."""
from __future__ import annotations

import argparse
import hashlib
import hmac
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from app.core.config import get_settings
from app.provider.errors import ProviderError, ProviderInvalidOutputError
from app.provider.storage import ProductionStorageProvider, StorageBucketConflictError


PROTECTED = {"property-ai", "property-ai-test", "property-ai-staging", "property-ai-day4",
             "property-ai-delivery-check", "property-ai-auth001-test"}


def source_digest():
    app = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in (Path(__file__), app / "provider/storage/__init__.py", app / "provider/http.py"):
        digest.update(path.relative_to(app).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def build_plan(settings):
    url = urlsplit(settings.storage_endpoint)
    bucket = settings.storage_bucket
    if (settings.app_env not in {"staging", "production"} or settings.storage_provider != "production"
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket)
            or bucket in PROTECTED or bucket.startswith(("property-ai-delivery-", "property-ai-auth001-"))
            or not re.fullmatch(r"[a-z0-9-]{1,32}", settings.storage_region)):
        raise ValueError("CUSTOMER_STORAGE_TARGET")
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("CUSTOMER_STORAGE_ENDPOINT")
    if url.scheme == "http" and url.hostname != "minio":
        try:
            ip = ip_address(url.hostname)
        except ValueError:
            raise ValueError("CUSTOMER_STORAGE_ENDPOINT") from None
        if not (ip.is_private or ip.is_loopback) or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("CUSTOMER_STORAGE_ENDPOINT")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("CUSTOMER_STORAGE_ENDPOINT")
    return {"format": "property-ai-customer-storage-v1", "app_env": settings.app_env,
            "endpoint": settings.storage_endpoint.rstrip("/"), "bucket": bucket,
            "region": settings.storage_region, "source_sha256": source_digest(),
            "application_access_key_sha256": hashlib.sha256(settings.storage_access_key.get_secret_value().encode()).hexdigest(),
            "application_policy": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"], "Resource": [f"arn:aws:s3:::{bucket}/*"]}]},
            "steps": ["refuse_existing_bucket", "create_private_bucket", "application_put_get", "admin_remove_owned_probe"]}


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_approval(settings, plan, approved):
    if plan != build_plan(settings):
        raise ValueError("CUSTOMER_STORAGE_PLAN")
    if not hmac.compare_digest(plan_digest(plan), approved):
        raise ValueError("CUSTOMER_STORAGE_APPROVAL")


def providers(settings):
    access = os.environ.get("STORAGE_BOOTSTRAP_ACCESS_KEY", "")
    secret = os.environ.get("STORAGE_BOOTSTRAP_SECRET_KEY", "")
    app_access = settings.storage_access_key.get_secret_value()
    app_secret = settings.storage_secret_key.get_secret_value()
    if (not 3 <= len(access) <= 128 or len(secret) < 8 or not app_access or not app_secret
            or access == app_access or secret == app_secret
            or any(value.lower().startswith("replace") for value in (access, secret, app_access, app_secret))):
        raise ValueError("CUSTOMER_STORAGE_CREDENTIALS")
    common = dict(endpoint=settings.storage_endpoint, bucket=settings.storage_bucket, region=settings.storage_region)
    return (ProductionStorageProvider(**common, access_key=access, secret_key=secret),
            ProductionStorageProvider(**common, access_key=app_access, secret_key=app_secret))


def verify_application(settings, plan, approved, *, admin, application):
    validate_approval(settings, plan, approved)
    key = "_installation_probe/" + uuid4().hex
    content = b"Property AI synthetic storage installation probe\n"
    failed = False
    try:
        application.put_object(key, content, "text/plain")
        if application.get_object(key) != content:
            failed = True
    except ProviderError:
        failed = True
    finally:
        try:
            # Only this invocation's unpredictable key may be removed. No list,
            # prefix deletion, existing-object overwrite or bucket deletion.
            admin._request("DELETE", key)
        except ProviderError:
            raise ValueError("CUSTOMER_STORAGE_CLEANUP") from None
    if failed:
        raise ValueError("CUSTOMER_STORAGE_PROBE")


def initialize(settings, plan, approved, *, admin, application):
    validate_approval(settings, plan, approved)
    try:
        admin._request("HEAD", "")
    except ProviderInvalidOutputError:
        pass
    except ProviderError:
        raise ValueError("CUSTOMER_STORAGE_UNAVAILABLE") from None
    else:
        raise ValueError("CUSTOMER_STORAGE_EXISTS")
    try:
        admin.create_bucket_strict()
    except StorageBucketConflictError:
        raise ValueError("CUSTOMER_STORAGE_EXISTS") from None
    except ProviderError:
        raise ValueError("CUSTOMER_STORAGE_CREATE") from None
    verify_application(settings, plan, approved, admin=admin, application=application)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "apply", "probe"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--approve-plan", default="")
    args = parser.parse_args()
    try:
        settings = get_settings()
        if args.operation == "plan":
            plan = build_plan(settings)
            if args.plan.exists() or (args.policy and args.policy.exists()):
                raise ValueError("CUSTOMER_STORAGE_OUTPUT_EXISTS")
            with args.plan.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(plan, indent=2) + "\n")
            if args.policy:
                with args.policy.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(plan["application_policy"], indent=2) + "\n")
            print("CUSTOMER_STORAGE_PLAN " + plan_digest(plan))
        else:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            validate_approval(settings, plan, args.approve_plan)
            admin, application = providers(settings)
            operation = initialize if args.operation == "apply" else verify_application
            operation(settings, plan, args.approve_plan, admin=admin, application=application)
            print("CUSTOMER_STORAGE_COMPLETE" if args.operation == "apply" else "CUSTOMER_STORAGE_PROBE_COMPLETE")
        return 0
    except (ValueError, OSError, ProviderError) as error:
        code = str(error)
        print(code if re.fullmatch(r"CUSTOMER_STORAGE_[A-Z_]+", code) else "CUSTOMER_STORAGE_FAILED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
