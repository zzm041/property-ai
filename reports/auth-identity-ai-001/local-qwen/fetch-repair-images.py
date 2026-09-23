"""Download only four declared public test photos, bounded bytes and TLS."""
from pathlib import Path
from datetime import UTC,datetime
import hashlib
import json
import urllib.request
from urllib.parse import urlsplit

OUT=Path(__file__).parent
assets=json.loads((OUT/'repair-image-sources.json').read_text(encoding='utf-8'))['assets']
folder=OUT/'repair-images';folder.mkdir(exist_ok=True)
result=OUT/'repair-image-downloads.json'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
rows=json.loads(result.read_text(encoding='utf-8')) if result.exists() else []
for item in assets:
    if item.get('excluded'):continue
    target=folder/item['filename']
    previous=next((r for r in rows if r['id']==item['id']),None)
    if previous:
        assert target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==previous['sha256']
        continue
    assert not target.exists()
    assert urlsplit(item['url']).hostname=='upload.wikimedia.org' and item['url'].startswith('https://')
    request=urllib.request.Request(item['url'],headers={'User-Agent':'PropertyAI-Local-Diagnostic/1.0 (noncommercial software test; Wikimedia public images)'})
    with opener.open(request,timeout=40) as response:
        assert urlsplit(response.geturl()).hostname=='upload.wikimedia.org'
        data=response.read(1024*1024+1);assert len(data)<=1024*1024 and data.startswith(b'\xff\xd8')
    target.write_bytes(data)
    rows.append({'id':item['id'],'file':str(target),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'source_url':item['url'],'downloaded_at_utc':datetime.now(UTC).isoformat(),'pixel_review':'PENDING'})
    result.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'id':item['id'],'bytes':len(data),'sha256':rows[-1]['sha256']}),flush=True)
