import hashlib
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import benchmark as b
from app.rubric import SCORE_KEYS
from scripts.evaluate_benchmark import parse_judgment

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'BENCH',tmp_path)
    b.write(tmp_path/'manifest.json',{'version':'test','questions':[{'id':'math_001','subject':'math','topic':'函数','path':'math/math_001'}]})
    html=tmp_path/'runs/page.html';html.parent.mkdir();html.write_text('<!doctype html><html>test</html>')
    b.write(tmp_path/'evaluation/samples.json',[{'id':'B001','question_id':'math_001','set':'calibration','html':'runs/page.html','html_sha256':b.sha(html),'workflow_id':'secret-workflow','model':'secret-model'}])
    return TestClient(b.standalone)

def payload(client):
    item=client.get('/api/benchmark/samples/B001').json()
    return {'rater':'primary','revision':0,'html_sha256':item['html_sha256'],'status':'submitted','scores':{k:3 for k in SCORE_KEYS},'overall_score':0,'comment':'核对记录','flags':[]}

def test_blind_listing_no_workflow_or_model_leak(client):
    r=client.get('/api/benchmark/samples').json()
    assert len(r['samples'])==1
    assert 'secret-workflow' not in json.dumps(r)
    assert 'secret-model' not in json.dumps(r)
    assert client.get('/api/benchmark/samples/B001/files/source.json').status_code==404

def test_human_zero_preserved_revision_and_history(client):
    data=payload(client)
    r=client.put('/api/benchmark/samples/B001/rating',json=data)
    assert r.status_code==200,r.text
    assert r.json()['overall_score']==0
    assert client.put('/api/benchmark/samples/B001/rating',json=data).status_code==409
    data.update(revision=1,comment='修订')
    assert client.put('/api/benchmark/samples/B001/rating',json=data).status_code==200
    assert b.read(b.BENCH/'evaluation/human/primary/history/B001_0001.json')['comment']=='核对记录'
    other=client.get('/api/benchmark/samples/B001?rater=second').json()
    assert other['rating']['revision']==0

def test_version_mismatch_and_missing_scores_rejected(client):
    data=payload(client);data['scores']={}
    assert client.put('/api/benchmark/samples/B001/rating',json=data).status_code==400
    data['status']='draft'
    assert client.put('/api/benchmark/samples/B001/rating',json=data).status_code==200
    (b.BENCH/'runs/page.html').write_text('changed')
    data['revision']=1
    assert client.put('/api/benchmark/samples/B001/rating',json=data).status_code==409
    assert client.get('/api/benchmark/samples/B001/files/html').status_code==409

def test_rater_path_and_boolean_scores_rejected(client):
    assert client.get('/api/benchmark/samples?rater=../escape').status_code==400
    data=payload(client);data['scores'][SCORE_KEYS[0]]=True
    assert client.put('/api/benchmark/samples/B001/rating',json=data).status_code in (400,422)

def test_judge_schema_does_not_coerce_invalid_scores():
    data={'scores':{k:3 for k in SCORE_KEYS},'overall_score':0,'issues':[],'evidence':{}}
    assert parse_judgment(json.dumps(data))['overall_score']==0
    data['scores'][SCORE_KEYS[0]]=True
    with pytest.raises(ValueError):parse_judgment(json.dumps(data))

def test_gpt_score_only_accepts_unrated_pages_without_filling_human_scores(client,monkeypatch):
    import asyncio,sys
    from scripts import evaluate_benchmark as ev
    monkeypatch.setattr(ev,'BENCH',b.BENCH)
    (b.BENCH/'math/math_001').mkdir(parents=True)
    (b.BENCH/'math/math_001/input.png').write_bytes(b'fixture-image-for-mock')
    source=Path(__file__).resolve().parents[1]/'benchmark'
    (b.BENCH/'rubric.md').write_text((source/'rubric.md').read_text())
    for name in ('judge_prompt.md','dimension_prompt.md','overall_prompt.md','dimensions.json'):
        (b.BENCH/'evaluation'/name).write_text((source/'evaluation'/name).read_text())
    b.write(b.BENCH/'evaluation/judge_registry.json',[{'id':'gpt_56','model':'gpt-5.6-sol','provider':'openai'}])
    requests=[]
    class Client:
        def __init__(self,settings):pass
        async def complete(self,model,prompt,images,**kwargs):
            requests.append((model,prompt,len(images)))
            kwargs['usage']({'total_tokens':42})
            key=prompt.split('当前评分维度：',1)[1].splitlines()[0]
            return json.dumps({'dimension':key,'score':4 if key=='overall_score' else 3,
                               'issues':[],'evidence':'独立核对证据','summary':'模拟评价','reference_concern':''})
    monkeypatch.setattr(ev,'ModelClient',Client)
    monkeypatch.setattr(sys,'argv',['evaluate','--score-only','--set','calibration','--judges','gpt_56'])
    asyncio.run(ev.main())
    assert len(requests)==6 and all(r[0]=='gpt-5.6-sol' for r in requests)
    assert all('<!doctype html><html>test</html>' in r[1] and r[2]==1 for r in requests)
    assert all('独立核对证据' not in r[1] for r in requests)  # No previous responses fed to another call.
    assert not (b.BENCH/'evaluation/human').exists()
    report=b.read(b.BENCH/'reports/gpt_scores.json')
    assert report['rows'][0]['judgment']['overall_score']==4
    assert report['rows'][0]['usage']['total_tokens']==252
    assert report['rows'][0]['api_call_count']==6
    assert set(report['rows'][0]['calls'])=={*SCORE_KEYS,'overall_score'}
    # A second execution reuses a completed judgment for the exact same evidence.
    asyncio.run(ev.main())
    assert len(requests)==6
