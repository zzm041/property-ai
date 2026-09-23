"""Actual local HTTP/proxy sockets, no external network, DB or business calls."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.core.config import Settings
from app.provider.factory import build_llm_provider, build_vlm_provider
from app.provider.http import _loopback_target, request_json
from app.provider.storage import DevelopmentStorageProvider


@pytest.mark.parametrize('url,expected', [
    ('http://127.0.0.1:11435/v1/chat/completions', True),
    ('http://127.9.8.7:9999/v1', True),
    ('http://[::1]:11435/v1', True),
    ('https://127.0.0.1/v1', True),
    ('https://provider.example/v1?target=127.0.0.1', False),
    ('http://127.0.0.1.example/v1', False),
    ('http://127.0.0.1@provider.example/v1', False),
    ('http://192.168.1.2/v1', False),
    ('http://localhost.example/v1', False),
])
def test_literal_loopback_classification(url, expected):
    assert _loopback_target(url) is expected


@contextmanager
def server():
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append({'path': self.path, 'body': body})
            answer = {'description': '非敏感合成插座', 'labels': ['socket'], 'confidence': 0.8}
            output = json.dumps({'choices': [{'message': {'content': json.dumps(answer)}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(output)))
            self.end_headers()
            self.wfile.write(output)
        def log_message(self, *args):
            pass
    owned = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=owned.serve_forever, daemon=True)
    thread.start()
    try:
        yield owned.server_address[1], received
    finally:
        owned.shutdown()
        owned.server_close()
        thread.join(timeout=3)


def proxy_environment(monkeypatch, port):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.setenv(key, f'http://127.0.0.1:{port}')
    for key in ('NO_PROXY', 'no_proxy'):
        monkeypatch.setenv(key, '')


def test_factory_llm_and_vlm_do_not_send_text_or_image_to_environment_proxy(monkeypatch):
    with server() as (model_port, model_requests), server() as (proxy_port, proxy_requests):
        proxy_environment(monkeypatch, proxy_port)
        settings = Settings(_env_file=None, app_env='test', llm_provider='production', vlm_provider='production',
                            llm_base_url=f'http://127.0.0.1:{model_port}/v1', vlm_base_url=f'http://127.0.0.1:{model_port}/v1',
                            llm_model='synthetic-local', vlm_model='synthetic-local',
                            llm_api_key='synthetic-only', vlm_api_key='synthetic-only')
        build_llm_provider(settings).structured_completion(prompt='本机合成报修', prompt_version='transport-test')
        storage = DevelopmentStorageProvider()
        item = storage.register_object(mime_type='image/png', size_bytes=3, tenant_id=17, property_id=18)
        storage.put_object(item.storage_key, b'png', 'image/png')
        result = build_vlm_provider(settings, storage).inspect_image(storage_key=item.storage_key, mime_type='image/png')
        assert result.description == '非敏感合成插座'
        assert len(model_requests) == 2
        assert model_requests[0]['body']['messages'][0]['content'] == '本机合成报修'
        assert model_requests[1]['body']['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
        assert proxy_requests == []


def test_non_loopback_provider_keeps_explicit_environment_proxy_support(monkeypatch):
    with server() as (proxy_port, proxy_requests):
        proxy_environment(monkeypatch, proxy_port)
        request_json(method='POST', url='http://provider.example.invalid/v1/chat/completions', timeout_seconds=2,
                     json_body={'synthetic': True})
        assert len(proxy_requests) == 1
        assert proxy_requests[0]['path'] == 'http://provider.example.invalid/v1/chat/completions'


def test_caller_supplied_client_is_not_replaced_or_closed():
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True})
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        assert request_json(method='POST', url='http://127.0.0.1:11435/v1', timeout_seconds=1,
                            json_body={}, client=client) == {'ok': True}
        assert not client.is_closed and len(calls) == 1
