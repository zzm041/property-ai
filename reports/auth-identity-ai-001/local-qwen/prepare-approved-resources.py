"""Owner-approved exact files only: immutable official metadata, hashes, no inference.

Pillow is installed only into its named target; no ASR/BGE/runtime packages.
Existing validated files are reused. Partial downloads retained, never hidden.
"""
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[3]
PRIVATE=ROOT/'tmp/selfhost-ai-local'
OUT=Path(__file__).parent/'resources-20260925'
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
import httpx

PILLOW_HASH='a2b55dd6b2a4c4b7d87ffa56bdb33fdc5fdb9a462173861a7bc097f17d91cb09'
WHEEL='pillow-12.3.0-cp312-cp312-win_amd64.whl'
TOTAL=6469222802
MODELS=[
 ('Qwen/Qwen3-ASR-0.6B','5eb144179a02acc5e5ba31e748d22b0cf3e303b0',[
 'model.safetensors','chat_template.json','config.json','generation_config.json','merges.txt','preprocessor_config.json','tokenizer_config.json','vocab.json']),
 ('BAAI/bge-m3','5617a9f61b028005a4858fdac845db406aefb181',[
 'pytorch_model.bin','colbert_linear.pt','sparse_linear.pt','1_Pooling/config.json','config.json','config_sentence_transformers.json','modules.json','sentence_bert_config.json','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json']),
 ('BAAI/bge-reranker-v2-m3','953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e',[
 'model.safetensors','config.json','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json'])]
WEIGHTS={
 'Qwen/Qwen3-ASR-0.6B/model.safetensors':'79d6cbd4c98c7bbffe9db2edac07f56cd6637d0d5944b27f6c2b8353840323ea',
 'BAAI/bge-m3/pytorch_model.bin':'b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38',
 'BAAI/bge-m3/colbert_linear.pt':'19bfbae397c2b7524158c919d0e9b19393c5639d098f0a66932c91ed8f5f9abb',
 'BAAI/bge-m3/sparse_linear.pt':'45c93804d2142b8f6d7ec6914ae23a1eee9c6a1d27d83d908a20d2afb3595ad9',
 'BAAI/bge-reranker-v2-m3/model.safetensors':'d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286'}
def save(name,value):
    OUT.mkdir(exist_ok=True)
    with (OUT/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def digest(path,mode):
    h=hashlib.sha256() if mode=='sha256' else hashlib.sha1()
    if mode=='git-blob-sha1':h.update(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    return h.hexdigest()
def event(value):print(json.dumps({'received_utc':datetime.now(UTC).isoformat(),**value}),flush=True)
def download(client,url,path,size,expected,mode,deadline):
    if path.exists():
        assert path.stat().st_size==size and digest(path,mode)==expected,'Existing file differs; preserve and stop'
        return {'path':str(path),'bytes':size,'sha256':digest(path,'sha256'),'reused':True}
    assert time.monotonic()<deadline
    assert shutil.disk_usage(ROOT).free>=size+8*1024**3,'Insufficient reserved disk'
    path.parent.mkdir(parents=True,exist_ok=True)
    part=path.with_name(path.name+'.part')
    assert not part.exists(),'Partial file retained; do not overwrite or silently redownload'
    count=0;reported=0
    with client.stream('GET',url) as response,part.open('xb') as f:
        response.raise_for_status()
        for chunk in response.iter_bytes(1024*1024):
            assert time.monotonic()<deadline,'Download window45min expired'
            count+=len(chunk);assert count<=size,'Declared file size exceeded'
            f.write(chunk)
            if count-reported>=256*1024**2:
                event({'file':path.name,'bytes_received':count});reported=count
        f.flush();os.fsync(f.fileno())
    assert count==size and digest(part,mode)==expected,'Size/hash mismatch; retain partial and stop'
    result={'path':str(path),'bytes':count,'sha256':digest(part,'sha256'),'verification_mode':mode,'upstream_digest':expected,'reused':False}
    part.rename(path)
    return result

def main():
    action=sys.argv[1];assert action in ('pillow','models')
    attempt=2 if '--connection-retry-once' in sys.argv else 1
    suffix='' if attempt==1 else '-connection-retry'
    if attempt==2:
        prior=json.loads((OUT/'models-result.json').read_text(encoding='utf-8'))
        assert action=='models' and prior['error_type']=='ConnectError' and not prior['files']
    assert shutil.disk_usage(ROOT).free>=TOTAL+12*1024**3
    OUT.mkdir(exist_ok=True)
    receipt={'action':action,'started_utc':datetime.now(UTC).isoformat(),'files':[],'disk_free_before':shutil.disk_usage(ROOT).free}
    checkpoint=OUT/(action+'-result'+suffix+'.json')
    assert not checkpoint.exists(),'Completed resource receipt exists; do not repeat'
    deadline=time.monotonic()+2700
    try:
        with httpx.Client(timeout=httpx.Timeout(60,connect=30),follow_redirects=True,trust_env=False) as client:
            if action=='pillow':
                meta=client.get('https://pypi.org/pypi/pillow/12.3.0/json');meta.raise_for_status();meta=meta.json()
                file=next(x for x in meta['urls'] if x['filename']==WHEEL)
                assert not file['yanked'] and file['size']==7227137 and file['digests']['sha256']==PILLOW_HASH
                assert sys.version_info[:2]==(3,12) and sys.maxsize>2**32 and os.name=='nt'
                path=PRIVATE/'image-tools'/WHEEL
                receipt['files'].append(download(client,file['url'],path,7227137,PILLOW_HASH,'sha256',deadline))
                target=PRIVATE/'image-tools/site-packages';assert not target.exists(),'Existing installation preserved; inspect before reusing'
                uv=shutil.which('uv');assert uv
                command=[uv,'pip','install','--python',sys.executable,'--target',str(target),'--no-deps','--no-index','--no-python-downloads','--cache-dir',str(PRIVATE/'image-tools/uv-cache'),str(path)]
                result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=120)
                receipt['install_exit']=result.returncode;receipt['install_output']=result.stdout+result.stderr
                assert result.returncode==0
                sys.path.insert(0,str(target))
                import PIL
                from PIL import Image
                assert PIL.__version__=='12.3.0' and Path(PIL.__file__).is_relative_to(target)
                im=Image.new('RGB',(8,4));assert im.resize((4,2),Image.Resampling.LANCZOS).size==(4,2)
                receipt['loaded']={'version':PIL.__version__,'module':PIL.__file__,'native':Image.core.__file__,'python':sys.executable,'old_venv_unchanged':True}
            else:
                manifest=[]
                for repo,revision,names in MODELS:
                    response=client.get(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true');response.raise_for_status();meta=response.json()
                    assert meta['sha']==revision
                    files={x['rfilename']:x for x in meta['siblings']}
                    for name in names:
                        f=files[name];lfs=f.get('lfs');mode='sha256' if lfs else 'git-blob-sha1'
                        expected=lfs['sha256'] if lfs else f['blobId']
                        if repo+'/'+name in WEIGHTS:assert expected==WEIGHTS[repo+'/'+name]
                        manifest.append({'repo':repo,'revision':revision,'name':name,'size':f['size'],'mode':mode,'expected':expected})
                assert sum(x['size'] for x in manifest)==TOTAL,'Pinned allowlist total differs; no download'
                manifest_record={'total_bytes':TOTAL,'files':manifest,'source':'official immutable revision metadata; config verified by Git blob SHA1 then recorded SHA256'}
                if (OUT/'model-manifest.json').exists():
                    assert json.loads((OUT/'model-manifest.json').read_text(encoding='utf-8'))==manifest_record
                else:save('model-manifest.json',manifest_record)
                for file in manifest:
                    path=PRIVATE/'models-hf'/file['repo'].split('/')[-1]/file['revision']/file['name']
                    result=download(client,f"https://huggingface.co/{file['repo']}/resolve/{file['revision']}/{file['name']}",path,file['size'],file['expected'],file['mode'],deadline)
                    receipt['files'].append(result);event({'file':file['name'],'result':'verified','bytes':file['size']})
                    (OUT/'model-progress.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        receipt['status']='PREPARED_NOT_MODEL_RUNTIME_PASS'
    except Exception as exc:
        receipt.update(status='STOPPED',error_type=type(exc).__name__)
        # Never expose signed redirect URLs or private local settings in errors.
        raise RuntimeError('Resource preparation stopped; see sanitized receipt') from None
    finally:
        receipt['finished_utc']=datetime.now(UTC).isoformat();receipt['attempt']=attempt;save(action+'-result'+suffix+'.json',receipt)
    event({'action':action,'status':receipt['status'],'files':len(receipt['files'])})
if __name__=='__main__':main()
