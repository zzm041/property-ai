"""Collector-only checks; no inference, business requests or fixtures."""
import json
from pathlib import Path
import runpy
helpers=runpy.run_path(str(Path(__file__).with_name('run-quality28.py')))
check=helpers['raw_json_checks']
def body(value):return json.dumps({'choices':[{'message':{'content':json.dumps(value)}}]})
def test_non_json_response_preserved_by_safe_check():
    assert check('<html>502</html>','LLM')=={'content_strict_json_object':False}
def test_scalar_and_fenced_json_not_strict_objects():
    assert check(body([]),'LLM')['content_strict_json_object'] is False
    assert check(json.dumps({'choices':[{'message':{'content':'```json\n{}\n```'}}]}),'LLM')['content_strict_json_object'] is False
def test_raw_vision_types_and_confidence_not_coerced_to_pass():
    good={'description':'插座','labels':['electrical'],'confidence':0.8}
    assert all(check(body(good),'VLM').values())
    for confidence in (float('nan'),1.2,True,'0.8'):
        assert check(body({**good,'confidence':confidence}),'VLM')['confidence_valid'] is False
    assert check(body({**good,'labels':'electrical'}),'VLM')['labels_type'] is False
def test_low_confidence_and_rule_fallback_are_separate():
    output=helpers['WorkOrderAnalysisOutput'].model_validate({'category':'other','subcategory':'unknown','severity':'low','priority':'p4','summary':'尚不清楚','risk':'none','required_role':'STAFF','required_skill':'other','confidence':0.99})
    assert helpers['map_analysis_output'](output).low_confidence
    assert not output.confidence<0.70
def test_existing_media_reference_contract():
    media=helpers['MediaRef'](storage_key='normal-socket.jpg',mime_type='image/jpeg',kind='IMAGE')
    assert media.kind=='IMAGE'

def test_budget_blocks_transport_failure_without_remote_response():
    state=helpers['budget_state_from_events']([
        {'event':'RESERVED','request_id':'request-1'},
        {'event':'COMPLETED','request_id':'request-1','status':'EXCEPTION','model_seconds':60,
         'remote_completion':'UNKNOWN'},
    ])
    assert state=={'attempted':1,'model_seconds':60.0,'unknown':['request-1']}

def test_budget_accepts_response_backed_provider_rejection():
    state=helpers['budget_state_from_events']([
        {'event':'RESERVED','request_id':'request-1'},
        {'event':'COMPLETED','request_id':'request-1','status':'EXCEPTION','model_seconds':3.5,
         'remote_completion':'RESPONSE_RECEIVED'},
    ])
    assert state=={'attempted':1,'model_seconds':3.5,'unknown':[]}
