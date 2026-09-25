"""Small offline CPU model adapters, imported only by isolated model processes.

No project imports, network clients, user paths, dynamic model names or retries.
The supervisor verifies the fixed model directory and enforces time/RAM limits.
"""
from pathlib import Path
import os

MODEL_DIRS = {
    'asr': ('Qwen3-ASR-0.6B', '5eb144179a02acc5e5ba31e748d22b0cf3e303b0'),
    'embedding': ('bge-m3', '5617a9f61b028005a4858fdac845db406aefb181'),
    'reranker': ('bge-reranker-v2-m3', '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'),
}

def offline(cache):
    for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):
        os.environ[name]='1'
    os.environ['GRADIO_ANALYTICS_ENABLED']='False'
    os.environ['HF_HOME']=str(cache)
    os.environ['TOKENIZERS_PARALLELISM']='false'
    os.environ['OMP_NUM_THREADS']='4'
    os.environ['MKL_NUM_THREADS']='4'

def load(kind, root):
    import torch
    assert torch.__version__=='2.9.1+cpu' and torch.version.cuda is None
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    name, revision=MODEL_DIRS[kind]
    path=Path(root)/name/revision
    assert path.is_dir()
    if kind=='asr':
        from qwen_asr import Qwen3ASRModel
        return Qwen3ASRModel.from_pretrained(str(path),device_map='cpu',dtype=torch.float32,
            local_files_only=True,trust_remote_code=False,max_inference_batch_size=1,max_new_tokens=128)
    from transformers import AutoTokenizer, AutoModel, AutoModelForSequenceClassification
    tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True,trust_remote_code=False)
    cls=AutoModel if kind=='embedding' else AutoModelForSequenceClassification
    model=cls.from_pretrained(str(path),local_files_only=True,trust_remote_code=False,dtype=torch.float32).cpu().eval()
    return tokenizer,model

def infer(kind, loaded, value):
    import torch
    if kind=='asr':
        import numpy as np
        import wave
        import io
        # Caller passes bytes, never an arbitrary path or URL to the loader.
        with wave.open(io.BytesIO(value),'rb') as audio:
            assert audio.getnchannels()==1 and audio.getsampwidth()==2 and audio.getframerate()==16000
            assert 0<audio.getnframes()<=30*16000
            samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype('float32')/32768
        result=loaded.transcribe(audio=(samples,16000),language='Chinese',return_time_stamps=False)
        assert len(result)==1 and isinstance(result[0].text,str) and result[0].text.strip()
        return {'text':result[0].text,'language':result[0].language,'model':'Qwen3-ASR-0.6B'}
    tokenizer,model=loaded
    if kind=='embedding':
        assert isinstance(value,list) and 1<=len(value)<=16 and all(isinstance(t,str) and 0<len(t)<=4000 for t in value)
        inputs=tokenizer(value,padding=True,truncation=False,return_tensors='pt')
    else:
        assert set(value)=={'query','documents'} and isinstance(value['query'],str) and 0<len(value['query'])<=1000
        assert 1<=len(value['documents'])<=16 and all(isinstance(t,str) and 0<len(t)<=4000 for t in value['documents'])
        inputs=tokenizer([(value['query'],t) for t in value['documents']],padding=True,truncation=False,return_tensors='pt')
    assert inputs['input_ids'].shape[1]<=2048,'Token budget exceeded; never silently truncate'
    with torch.inference_mode():
        result=model(**inputs)
        if kind=='embedding':
            vectors=torch.nn.functional.normalize(result.last_hidden_state[:,0],p=2,dim=1)
            assert vectors.shape[1]==1024 and torch.isfinite(vectors).all()
            return {'data':[{'index':i,'embedding':v.tolist()} for i,v in enumerate(vectors)],'model':'bge-m3'}
        scores=torch.sigmoid(result.logits.reshape(-1).float())
        assert len(scores)==len(value['documents']) and torch.isfinite(scores).all()
        return {'scores':scores.tolist(),'model':'bge-reranker-v2-m3'}
