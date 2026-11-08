from pathlib import Path
import importlib.util
import importlib

import pytest


def api():
    assert importlib.util.find_spec("app.cli.customer_environment"), "customer application environment exporter missing"
    return importlib.import_module("app.cli.customer_environment")


def test_export_removes_infrastructure_and_bootstrap_secrets(tmp_path):
    source = tmp_path / "customer.env"
    target = tmp_path / "application.env"
    source.write_text("APP_ENV=production\nDEV_AUTH_ENABLED=false\nAPP_SECRET_KEY='synthetic$#value'\nMYSQL_ROOT_PASSWORD=synthetic-root\nMINIO_ROOT_PASSWORD=synthetic-root\nSTORAGE_BOOTSTRAP_SECRET_KEY=synthetic-root\nBOOTSTRAP_ADMIN_PASSWORD=synthetic-root\nDATABASE_URL=mysql+pymysql://app:synthetic@mysql/customer\n", encoding="utf-8")
    api().export(source, target)
    content = target.read_text(encoding="utf-8")
    assert "synthetic-root" not in content
    assert "APP_SECRET_KEY=synthetic$#value\n" in content
    assert "DATABASE_URL=" in content


@pytest.mark.parametrize("extra", ["APP_ENV=staging\n", "invalid line without assignment\n", "APP_SECRET_KEY='unterminated\n", "APP_SECRET_KEY=${FROM_HOST}\n", "APP_SECRET_KEY='line1\nline2'\n"])
def test_rejects_ambiguous_or_ambient_interpolation(tmp_path, extra):
    s = tmp_path / "source"; t = tmp_path / "target"
    s.write_text("APP_ENV=production\nDEV_AUTH_ENABLED=false\n" + extra, encoding="utf-8")
    with pytest.raises(ValueError): api().export(s, t)
    assert not t.exists()


def test_existing_output_is_never_replaced(tmp_path):
    s = tmp_path / "source"; t = tmp_path / "target"
    s.write_text("APP_ENV=production\nDEV_AUTH_ENABLED=false\n", encoding="utf-8")
    t.write_text("preserve me")
    with pytest.raises(FileExistsError): api().export(s, t)
    assert t.read_text() == "preserve me"


@pytest.mark.parametrize("content", ["APP_ENV=development\nDEV_AUTH_ENABLED=false\n", "APP_ENV=production\nDEV_AUTH_ENABLED=true\n"])
def test_customer_application_rejects_development_auth(tmp_path, content):
    s = tmp_path / "source"; s.write_text(content)
    with pytest.raises(ValueError): api().export(s, tmp_path / "target")
