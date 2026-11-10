"""Customer storage bootstrap guards; no shared services or real credentials."""
from importlib import util, import_module
from types import SimpleNamespace

from pydantic import SecretStr
import pytest

from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError


def api():
    assert util.find_spec("app.cli.customer_storage") is not None, "customer storage installer missing"
    return import_module("app.cli.customer_storage")


def settings(**updates):
    return SimpleNamespace(**(dict(app_env="staging", storage_provider="production",
        storage_endpoint="http://127.0.0.1:19030", storage_bucket="customer-new-media", storage_region="us-east-1",
        storage_access_key=SecretStr("synthetic-application-user"), storage_secret_key=SecretStr("synthetic-application-secret")) | updates))


class Store:
    def __init__(self, *, exists=False, fail_probe=False, deny_head=False):
        self.exists, self.fail_probe, self.deny_head = exists, fail_probe, deny_head
        self.calls, self.data = [], {}

    def _request(self, method, key, **kwargs):
        self.calls.append((method, key))
        if method == "HEAD":
            if self.deny_head: raise ProviderUnavailableError("denied")
            if not self.exists: raise ProviderInvalidOutputError("not found")
        elif method == "DELETE":
            self.data.pop(key, None)
        return b""

    def ensure_bucket(self):
        self.calls.append(("CREATE", "")); self.exists = True

    create_bucket_strict = ensure_bucket

    def put_object(self, key, content, mime_type):
        self.calls.append(("PUT", key)); self.data[key] = content

    def get_object(self, key):
        self.calls.append(("GET", key))
        if self.fail_probe: raise ProviderUnavailableError("read denied")
        return self.data[key]


def test_plan_is_offline_and_contains_only_bucket_scoped_app_policy():
    m = api(); plan = m.build_plan(settings())
    assert "synthetic-application-secret" not in str(plan)
    statement = plan["application_policy"]["Statement"]
    assert statement == [{"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
                          "Resource": ["arn:aws:s3:::customer-new-media/*"]}]
    assert plan["endpoint"] == "http://127.0.0.1:19030"


@pytest.mark.parametrize("updates", [{"app_env": "development"}, {"storage_provider": "development"},
    {"storage_bucket": "property-ai-delivery-check"}, {"storage_bucket": "../bucket"},
    {"storage_endpoint": "http://user:secret@minio:9000"}, {"storage_endpoint": "http://minio:9000/?x=1"},
    {"storage_endpoint": "http://example.com"}, {"storage_region": "../../x"}])
def test_plan_rejects_legacy_or_unsafe_target(updates):
    with pytest.raises(ValueError): api().build_plan(settings(**updates))


def test_approval_and_changed_target_fail_before_network():
    m = api(); s = settings(); plan = m.build_plan(s); store = Store()
    with pytest.raises(ValueError, match="APPROVAL"):
        m.initialize(s, plan, "wrong", admin=store, application=store)
    s.storage_bucket = "different-media"
    with pytest.raises(ValueError, match="PLAN"):
        m.initialize(s, plan, m.plan_digest(plan), admin=store, application=store)
    assert store.calls == []


@pytest.mark.parametrize("exists,deny,code", [(True, False, "EXISTS"), (False, True, "UNAVAILABLE")])
def test_existing_or_unreachable_bucket_is_never_recreated(exists, deny, code):
    m = api(); s = settings(); p = m.build_plan(s); st = Store(exists=exists, deny_head=deny)
    with pytest.raises(ValueError, match=code):
        m.initialize(s, p, m.plan_digest(p), admin=st, application=st)
    assert st.calls == [("HEAD", "")]


def test_fresh_bucket_and_application_put_get_cleanup():
    m = api(); s = settings(); p = m.build_plan(s); st = Store()
    m.initialize(s, p, m.plan_digest(p), admin=st, application=st)
    assert [c[0] for c in st.calls] == ["HEAD", "CREATE", "PUT", "GET", "DELETE"]
    assert st.data == {}
    assert all(not key or key.startswith("_installation_probe/") for _, key in st.calls)


def test_failed_probe_cleans_only_owned_object_and_retains_bucket():
    m = api(); s = settings(); p = m.build_plan(s); st = Store(fail_probe=True)
    with pytest.raises(ValueError, match="PROBE"):
        m.initialize(s, p, m.plan_digest(p), admin=st, application=st)
    assert st.exists is True
    assert st.data == {}
    assert st.calls[-1][0] == "DELETE"


def test_cleanup_failure_is_not_reported_as_pass():
    m = api(); s = settings(); p = m.build_plan(s); st = Store()
    original = st._request
    def failure(method, key, **kwargs):
        if method == "DELETE": raise ProviderUnavailableError("private details")
        return original(method, key, **kwargs)
    st._request = failure
    with pytest.raises(ValueError, match="CLEANUP"):
        m.initialize(s, p, m.plan_digest(p), admin=st, application=st)


def test_root_and_application_credentials_must_be_separate(monkeypatch):
    m = api(); s = settings()
    monkeypatch.setenv("STORAGE_BOOTSTRAP_ACCESS_KEY", s.storage_access_key.get_secret_value())
    monkeypatch.setenv("STORAGE_BOOTSTRAP_SECRET_KEY", "synthetic-bootstrap-secret")
    with pytest.raises(ValueError, match="CREDENTIALS"):
        m.providers(s)


def test_create_race_refuses_409_without_application_probe():
    import httpx
    from app.provider.storage import ProductionStorageProvider
    requests = []
    def handle(request):
        requests.append(request.method)
        return httpx.Response(404 if request.method == "HEAD" else 409)
    m = api(); s = settings(); p = m.build_plan(s); application = Store()
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        admin = ProductionStorageProvider(endpoint=s.storage_endpoint, bucket=s.storage_bucket,
            access_key="synthetic-admin", secret_key="synthetic-admin-secret", client=http)
        with pytest.raises(ValueError, match="EXISTS"):
            m.initialize(s, p, m.plan_digest(p), admin=admin, application=application)
    assert requests == ["HEAD", "PUT"]
    assert application.calls == []


def test_redirect_does_not_count_as_probe_cleanup_success():
    import httpx
    from app.provider.storage import ProductionStorageProvider
    m = api(); s = settings(); p = m.build_plan(s); app = Store()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(307))) as http:
        admin = ProductionStorageProvider(endpoint=s.storage_endpoint, bucket=s.storage_bucket,
            access_key="synthetic-admin", secret_key="synthetic-admin-secret", client=http)
        with pytest.raises(ValueError, match="CLEANUP"):
            m.verify_application(s, p, m.plan_digest(p), admin=admin, application=app)
