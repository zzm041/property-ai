"""Resolve the caller IP for the credential rate limiter.

The header is only trusted when the socket peer is one of the configured
proxies, so a client that spoofs ``X-Forwarded-For`` cannot escape the IP
budget.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.rate_limit import parse_trusted_proxies, resolve_client_ip


def get_client_ip(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str | None:
    peer = request.client.host if request.client is not None else None
    return resolve_client_ip(
        peer=peer,
        forwarded_for=request.headers.get("x-forwarded-for"),
        trusted_proxies=parse_trusted_proxies(settings.trusted_proxy_cidrs),
    )


ClientIp = Annotated[str | None, Depends(get_client_ip)]
