"""Explicit customer paths and bounded private runtime configuration."""
from dataclasses import dataclass
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit


def private_address(value):
    try:
        address = ip_address(value)
        if "%" in value or address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError()
        if not (address.is_loopback or address.is_private) or (address.is_reserved and not address.is_loopback):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("CONFIG_PRIVATE_ADDRESS") from None
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    model_root: Path
    bge_python: Path
    asr_python: Path
    token_file: Path
    ollama_url: str
    ollama_version: str
    chat_model: str
    chat_digest: str
    bind: str = "127.0.0.1"
    port: int = 11439
    timeout_seconds: float = 180
    max_body_bytes: int = 15 * 1024 * 1024
    min_available_ram_mib: int = 12288
    reserve_ram_mib: int = 2048
    min_free_gpu_mib: int = 6000
    resource_wait_seconds: float = 15

    @classmethod
    def from_dict(cls, value, base):
        try:
            data = dict(value)
            for key in ("model_root", "bge_python", "asr_python", "token_file"):
                path = Path(data[key]).expanduser()
                data[key] = (Path(base) / path).resolve()
            result = cls(**data)
            private_address(result.bind)
            url = urlsplit(result.ollama_url)
            if url.scheme != "http" or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
                raise ValueError()
            private_address(url.hostname)
            if not ip_address(url.hostname).is_loopback:
                raise ValueError()
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError()
            for field, low, high in (("port", 1, 65535), ("max_body_bytes", 1024, 15 * 1024 * 1024),
                                     ("min_available_ram_mib", 1024, 1048576),
                                     ("reserve_ram_mib", 512, 1048576), ("min_free_gpu_mib", 512, 1048576)):
                number = getattr(result, field)
                if type(number) is not int or not low <= number <= high:
                    raise ValueError()
            if result.reserve_ram_mib >= result.min_available_ram_mib:
                raise ValueError()
            seconds = result.timeout_seconds
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 1 <= seconds <= 600:
                raise ValueError()
            wait = result.resource_wait_seconds
            if type(wait) not in (int, float) or not math.isfinite(wait) or not 0 <= wait <= 30 or wait >= seconds:
                raise ValueError()
            if not re.fullmatch(r"[a-f0-9]{64}", result.chat_digest):
                raise ValueError()
            if result.ollama_version != "0.34.4" or not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,160}", result.chat_model):
                raise ValueError()
            return result
        except (TypeError, KeyError, ValueError):
            raise ValueError("CONFIG_INVALID") from None

    @classmethod
    def read(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)

    def token(self):
        try:
            value = self.token_file.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
                raise ValueError()
            return value
        except (OSError, UnicodeError, ValueError):
            raise ValueError("CONFIG_TOKEN") from None
