"""PA-168: the deployment wiring the per-client-IP budget depends on.

The rate limit code trusts ``X-Forwarded-For`` only when the socket peer is a
declared proxy, and the api sits behind nginx in the delivery stack. Two things
therefore live in deploy/, not in Python: nginx has to forward the header, and
the api has to be told which hop to trust. If either goes missing every caller
shares one bucket and thirty logins an hour throttle the whole deployment, so
both are pinned here.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPOSITORY_ROOT / "deploy" / "docker-compose.delivery.yml"
NGINX = REPOSITORY_ROOT / "deploy" / "nginx.conf"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_the_forwarded_header_reaches_the_api() -> None:
    text = NGINX.read_text(encoding="utf-8")
    api_block = text.split("location /api/ {", 1)[1].split("}", 1)[0]
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in api_block


def test_the_api_is_told_which_proxy_hop_to_trust() -> None:
    services = _compose()["services"]
    environment = services["api"]["environment"]
    assert "TRUSTED_PROXY_CIDRS" in environment, (
        "the api must declare TRUSTED_PROXY_CIDRS; without it every caller "
        "shares the nginx container's IP bucket"
    )
    default = str(environment["TRUSTED_PROXY_CIDRS"])
    # It is a compose default, so it has to resolve to a concrete CIDR list.
    assert default.startswith("${TRUSTED_PROXY_CIDRS:-"), default
    cidrs = default[len("${TRUSTED_PROXY_CIDRS:-") :].removesuffix("}")
    networks = [ipaddress.ip_network(item.strip()) for item in cidrs.split(",")]
    assert networks, "the default must name at least one network"


def test_the_trusted_default_only_covers_private_container_ranges() -> None:
    services = _compose()["services"]
    environment = services["api"]["environment"]
    cidrs = (
        str(environment["TRUSTED_PROXY_CIDRS"])
        .removeprefix("${TRUSTED_PROXY_CIDRS:-")
        .removesuffix("}")
    )
    networks = [ipaddress.ip_network(item.strip()) for item in cidrs.split(",")]
    # A public client address must never fall inside the trusted range, or a
    # spoofed header would be believed.
    for probe in ("203.0.113.7", "198.51.100.9", "8.8.8.8"):
        assert not any(ipaddress.ip_address(probe) in net for net in networks), probe
    # The compose bridge networks live in 172.16.0.0/12, so the default has to
    # cover them or the nginx hop stays untrusted.
    for probe in ("172.18.0.2", "172.21.0.12", "172.17.0.2"):
        assert any(ipaddress.ip_address(probe) in net for net in networks), probe
