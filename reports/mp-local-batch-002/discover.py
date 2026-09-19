"""Read-only discovery of live synthetic IDs. Does not create or delete records."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8082/api/v1"
OUT = Path(__file__).resolve().parent / "00-discovery.json"


def call(path: str, token: str | None = None, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return {"status": resp.status, "data": payload.get("data")}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return {"status": exc.code, "error": payload}
    except URLError as exc:
        return {"status": 0, "error": str(exc)}


def token(seed: str) -> str | None:
    result = call("/auth/dev/token", method="POST", body={"seed_key": seed})
    data = result.get("data") or {}
    return data.get("access_token")


def me_projection(me: dict | None) -> dict | None:
    if not me:
        return None
    return {
        "display_name": me.get("display_name"),
        "roles": me.get("roles"),
        "scopes": [
            {
                "role_code": row.get("role_code"),
                "scope_type": row.get("scope_type"),
                "tenant_id": row.get("tenant_id"),
                "property_id": row.get("property_id"),
            }
            for row in (me.get("scopes") or [])
        ],
        "permissions": me.get("permissions"),
    }


def main() -> None:
    report: dict = {"base": BASE, "identities": {}}
    for seed in ("__DEV_RESIDENT__", "__DEV_PROPERTY_MANAGER__", "__DEV_STAFF__"):
        tok = token(seed)
        info: dict = {"has_token": bool(tok)}
        if not tok:
            report["identities"][seed] = info
            continue
        info["me"] = me_projection((call("/me", tok).get("data")))
        if seed == "__DEV_RESIDENT__":
            info["houses"] = call("/me/houses", tok)
            info["bind_requests"] = call("/resident-bind-requests/mine", tok)
            info["work_orders"] = call("/work-orders", tok)
            info["announcements"] = None
            info["notifications"] = call("/notifications", tok)
        if seed == "__DEV_PROPERTY_MANAGER__":
            me = info["me"] or {}
            scopes = me.get("scopes") or []
            first = scopes[0] if scopes else {}
            tenant_id = first.get("tenant_id")
            property_id = first.get("property_id")
            info["bind_queue"] = call(
                f"/admin/resident-bind-requests?property_id={property_id}&tenant_id={tenant_id}",
                tok,
            )
            info["supervisor_queue"] = call(
                f"/supervisor/work-orders?property_id={property_id}",
                tok,
            )
            info["staff_daily"] = call(
                f"/supervisor/staff/daily?property_id={property_id}&tenant_id={tenant_id}",
                tok,
            )
            info["announcements"] = call(f"/announcements?property_id={property_id}", tok)
            info["incidents"] = call(f"/supervisor/incidents?property_id={property_id}", tok)
        if seed == "__DEV_STAFF__":
            info["staff_orders"] = call("/staff/work-orders", tok)
        report["identities"][seed] = info
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(OUT))


if __name__ == "__main__":
    main()
