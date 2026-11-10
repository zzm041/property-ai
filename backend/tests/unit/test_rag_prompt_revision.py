"""RAG prompt rollout preserves scope and rejects unknown protocol versions."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('rag_revision_adapter',ROOT/'scripts/ops/local_ai_http.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

def payload():
    return json.dumps({'model':adapter.MODELS['qwen'],'messages':[{'role':'user','content':'Synthetic evidence question'}],'response_format':{'type':'json_object'},'temperature':0,'reasoning_effort':'none','max_tokens':512}).encode()

@pytest.mark.parametrize('version',['rag-answer-v2','rag-answer-v3','rag-answer-v4'])
def test_rollout_versions_accepted_only_for_rag(version):
    assert adapter.decode('/v1/chat/completions','application/json',payload(),version,'rag_only')[0]=='qwen'
    with pytest.raises(ValueError,match='WORK_ORDER_ONLY_NO_RAG_AUTHORIZATION'):
        adapter.decode('/v1/chat/completions','application/json',payload(),version,'work_order_only')

def test_future_version_does_not_widen_model_authorization():
    with pytest.raises(ValueError,match='RAG_ONLY_NO_AUTO_AUTHORIZATION'):
        adapter.decode('/v1/chat/completions','application/json',payload(),'rag-answer-v999','rag_only')


def test_allowed_citations_are_database_ids_not_locator_ordinals():
    from app.service.retrieval import EvidenceSnapshot, RetrievalService
    from app.provider.llm import DevelopmentLLMProvider
    from app.domain.knowledge import RAG_ANSWER_PROMPT_VERSION
    chunks = tuple(EvidenceSnapshot(id=cid, document_id=doc, tenant_id=1, property_id=1,
        visibility='RESIDENT', version=3, excerpt=text, source_locator=locator, title='合成规定')
        for cid, doc, text, locator in [(97, 5, '登记时段为工作日。', '规定#chunk-1'),
                                      (4002, 9, '申请前须登记。', '流程#chunk-2')])
    prompt = RetrievalService._prompt('什么时候登记？', chunks)
    assert 'Allowed citation_chunk_ids: [97, 4002]' in prompt
    assert 'not document positions or the chunk numbers inside locators' in prompt
    # The development adapter must still receive only the actual evidence text;
    # the new protocol hint must not become a cited document or returned excerpt.
    output = DevelopmentLLMProvider().structured_completion(prompt=prompt, prompt_version=RAG_ANSWER_PROMPT_VERSION)
    assert output['citation_chunk_ids'] == [97, 4002]
    assert output['answer'] == '根据物业知识库，登记时段为工作日。'
