"""Scan source for baked-in production secrets. Placeholders in examples are allowed."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2].parent

PLACEHOLDER_VALUES = {
    "",
    "change-me",
    "change-me-root",
    "change-me-to-a-long-random-string",
    "change-me-test",
    "change-me-test-root",
    "minioadmin",
    "staging-rehearsal-secret-key-not-for-production",
}

SOURCE_SUFFIXES = {".py", ".yml", ".yaml", ".conf", ".js", ".json", ".wxml"}


def _iter_source_files(*roots: Path):
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.name == "Dockerfile" or path.suffix.lower() in SOURCE_SUFFIXES:
                if path.is_file():
                    yield path


def test_dockerfile_does_not_copy_dotenv() -> None:
    text = (REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPO / ".dockerignore").read_text(encoding="utf-8")
    assert "COPY .env" not in text
    assert "APP_SECRET_KEY" not in text
    assert "WECHAT_APP_SECRET" not in text
    assert "COPY backend/" in text
    assert "learning/" in dockerignore
    assert "reports/" in dockerignore
    assert "miniprogram/" in dockerignore
    assert "docs/" in dockerignore


def test_miniprogram_does_not_embed_app_secret() -> None:
    hits: list[str] = []
    for path in _iter_source_files(REPO / "miniprogram"):
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        if "appsecret" in text or "app_secret" in text or "session_key" in text:
            hits.append(str(path.relative_to(REPO)))
    assert hits == []


def test_application_source_does_not_hardcode_provider_keys() -> None:
    needles = ("sk-live", "BEGIN RSA PRIVATE KEY", "BEGIN PRIVATE KEY")
    hits: list[str] = []
    for path in _iter_source_files(REPO / "backend" / "app", REPO / "deploy"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(needle in text for needle in needles):
            hits.append(str(path.relative_to(REPO)))
    assert hits == []


def test_env_example_keeps_secrets_empty_or_placeholder() -> None:
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    secret_keys = {
        "APP_SECRET_KEY",
        "WECHAT_APP_SECRET",
        "LLM_API_KEY",
        "VLM_API_KEY",
        "ASR_API_KEY",
        "EMBEDDING_API_KEY",
        "RERANKER_API_KEY",
        "STORAGE_SECRET_KEY",
        "STORAGE_ACCESS_KEY",
        "MYSQL_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "RABBITMQ_PASSWORD",
    }
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key in secret_keys:
            assert value in PLACEHOLDER_VALUES


def test_tls_template_has_no_private_key() -> None:
    text = (REPO / "deploy" / "nginx-tls.conf.example").read_text(encoding="utf-8")
    assert "BEGIN" not in text
    assert "ssl_certificate_key" in text
    assert "/etc/nginx/certs/privkey.pem" in text
