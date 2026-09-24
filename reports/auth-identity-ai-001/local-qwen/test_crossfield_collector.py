import json
from pathlib import Path
import runpy
import pytest
BASE=Path(__file__).parent
h=runpy.run_path(str(BASE/'run-crossfield-diagnostic.py'))['h']

def test_same_fixed_cases_with_prior_conflict_and_risk_first():
    plans=h.plans()
    assert [p['case']['id'] for p in plans[:6]]==['GOLD-005','GOLD-006','GOLD-007','GOLD-009','GOLD-010','GOLD-011']
    assert len(plans)==32 and len({p['case']['id'] for p in plans})==28
    original=h.read(BASE/'gold-set-24.json')['cases']
    assert {p['case']['id']:p['case'] for p in plans if p['case']['id'].startswith('GOLD')}=={p['id']:p for p in original}

def test_preprocessed_copies_preserve_hashes_and_aspect_without_upscale():
    from PIL import Image
    for row in h.read(h.OUT/'images.json'):
        assert h.sha(Path(row['copy']))==row['copy_sha256']
        with Image.open(row['copy']) as image:assert list(image.size)==row['copy_size']
        w,hgt=row['original_size'];a,b=row['copy_size']
        assert a<=w and b<=hgt and max(a,b)<=1024
        assert abs(a/b-w/hgt)<.01

def test_new_profile_child_uses_mock_only_and_preserves_raw_and_v9(tmp_path,monkeypatch):
    import httpx
    import app.provider.factory
    client=httpx.Client
    value=dict(category='cleaning',subcategory='纸盒清扫',severity='low',priority='p4',summary='楼道纸盒需要清扫，通行正常。',risk='none',required_role='STAFF',required_skill='cleaning',confidence=.9)
    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})
    def factory(*args,**kwargs):
        kwargs['transport']=httpx.MockTransport(lambda r:httpx.Response(200,text=raw))
        return client(*args,**kwargs)
    monkeypatch.setattr(httpx,'Client',factory)
    images=h.read(h.OUT/'images.json')
    monkeypatch.setattr(h,'OUT',tmp_path)
    h.append_file(tmp_path/'images.json',images)
    folder=tmp_path/'request-01';folder.mkdir();h.append_file(folder/'reservation.json',{'number':1})
    h.child(1)
    saved=h.read(folder/'response.json')
    assert saved['provider_result'][0]==value and saved['prompt_version']=='wo-analysis-v9-duty-contract'
    assert saved['raw_response_body']==raw and saved['contract_errors']==[]
    assert h.read(folder/'request.json')['body']['max_tokens']==512
