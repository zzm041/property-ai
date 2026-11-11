"""No-inference checks for the frozen local work-order AI deployment.

Credentials remain in process memory. Public reports should retain only verdicts.
"""
import hashlib
import json
import subprocess

WORK_KEYS = frozenset({'LLM_API_KEY', 'VLM_API_KEY', 'ASR_API_KEY'})
BASE_URL = 'http://host.docker.internal:11437/v1'
MODEL = 'qwen3.5:4b'


def require_credentials(probe, expected_sha):
    if not probe.get('key_present'):
        raise ValueError('WORK_AI_CREDENTIAL_MISSING')
    if (probe.get('provider') != 'production' or probe.get('base_url') != BASE_URL
            or probe.get('model') != MODEL or probe.get('key_sha256') != expected_sha):
        raise ValueError('WORK_AI_BINDING_MISMATCH')


def inject_missing_credentials(environment, token):
    if not isinstance(token, str) or len(token) < 32 or any(c in token for c in '\r\n\0'):
        raise ValueError('INVALID_LOCAL_CREDENTIAL')
    items = [item.split('=', 1) for item in environment]
    if any(len(item) != 2 for item in items) or len({item[0] for item in items}) != len(items):
        raise ValueError('AMBIGUOUS_ENVIRONMENT')
    values = dict(items)
    for key in WORK_KEYS:
        if key not in values or values[key]:
            raise ValueError('EXISTING_CREDENTIAL_OR_MISSING_SLOT')
        if values.get(key.replace('API_KEY', 'BASE_URL')) != BASE_URL:
            raise ValueError('UNEXPECTED_CREDENTIAL_DESTINATION')
    return [key + '=' + (token if key in WORK_KEYS else value) for key, value in items]


CONTAINER_PROBE = r'''
import hashlib,json,sys
from app.core.config import get_settings
from app.provider.factory import build_llm_provider
s=get_settings();p=build_llm_provider(s)
key=getattr(p,'_api_key','')
result={'provider':s.llm_provider,'base_url':s.llm_base_url,'model':s.llm_model,
 'key_present':bool(key),'key_sha256':hashlib.sha256(key.encode()).hexdigest()}
if sys.argv[1]=='health':
 if not key or s.llm_base_url!='http://host.docker.internal:11437/v1':
  raise RuntimeError('WORK_AI_CREDENTIAL_OR_DESTINATION_INVALID')
 from app.provider.http import build_http_client
 with build_http_client(url=s.llm_base_url,timeout_seconds=4) as client:
  response=client.get(s.llm_base_url.removesuffix('/v1')+'/healthz',headers={'Authorization':'Bearer '+key})
  result['http_status']=response.status_code
  if response.status_code==200:
   health=response.json()
   result['health']={k:health.get(k) for k in ('scope','accepting_requests','reservation_gate')}
print(json.dumps(result))
'''


def probe_container(container_id, token, *, health=False):
    completed = subprocess.run(['docker', 'exec', '-i', container_id, 'python', '-B', '-c',
        CONTAINER_PROBE, 'health' if health else 'settings'], capture_output=True, timeout=20)
    if completed.returncode:
        raise RuntimeError('WORK_AI_CONTAINER_PROBE_FAILED')
    result = json.loads(completed.stdout)
    require_credentials(result, hashlib.sha256(token.encode()).hexdigest())
    if health and (result.get('http_status') != 200 or result.get('health') != {
            'scope': 'work_order_only', 'accepting_requests': True, 'reservation_gate': 'READY'}):
        raise ValueError('WORK_AI_CONTAINER_CONNECTION_NOT_READY')
    return {'credential_matches': True, 'container_connection_ready': True if health else None}
