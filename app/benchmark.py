"""Local benchmark browser and blind human annotation API.
No endpoint starts a paid model request. Evaluation records never enter the workflow.
"""
from __future__ import annotations
import hashlib, json, os, re, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ConfigDict, StrictInt
from .config import ROOT
from .rubric import SCORE_KEYS

BENCH = ROOT/'benchmark'
LOCK = threading.RLock()
router = APIRouter()

def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default

def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(temp,path)

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def safe_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,90}', value):raise HTTPException(400,'编号无效')
    return value

def inside(relative):
    path=(BENCH/relative).resolve()
    if not path.is_relative_to(BENCH.resolve()):raise HTTPException(400,'路径无效')
    return path

def questions():
    return (read(BENCH/'manifest.json',{}) or {}).get('questions',[])+(read(BENCH/'人工评分/manifest.json',{}) or {}).get('questions',[])
def samples():
    return (read(BENCH/'evaluation/samples.json',[]) or [])+(read(BENCH/'evaluation/historical_samples.json',[]) or [])
def question(qid):
    item=next((q for q in questions() if q['id']==qid),None)
    if not item:raise HTTPException(404,'题目不存在')
    return item

def sample(sid):
    item=next((s for s in samples() if s['id']==sid),None)
    if not item:raise HTTPException(404,'样本不存在')
    return item

def human_path(sid,rater): return BENCH/'evaluation/human'/safe_id(rater)/(safe_id(sid)+'.json')

@router.get('/benchmark')
@router.get('/benchmark/')
def page(): return FileResponse(ROOT/'web/benchmark.html',media_type='text/html')
@router.get('/benchmark/app.js')
def javascript():return FileResponse(ROOT/'web/benchmark.js',media_type='text/javascript')
@router.get('/benchmark/style.css')
def css():return FileResponse(ROOT/'web/benchmark.css',media_type='text/css')

@router.get('/api/benchmark/workflows')
def workflow_list():return read(BENCH/'workflows/registry.json',[])

@router.get('/api/benchmark/questions')
def list_questions():
    return {'version':read(BENCH/'manifest.json',{}).get('version'), 'questions':read(BENCH/'manifest.json',{}).get('questions',[]), 'coverage':read(BENCH/'coverage.json',{})}

@router.get('/api/benchmark/questions/{qid}/{kind}')
def question_file(qid:str,kind:str):
    q=question(qid)
    name={'image':'input.png','stem':'stem.html','answer':'reference/answer.html','explanation':'reference/explanation.html','checks':'reference/checks.json'}.get(kind)
    if not name:raise HTTPException(404,'文件不存在')
    path=inside(q['path'])/name
    if not path.is_file():raise HTTPException(404,'资源尚未准备完成')
    return artifact_response(path)

def artifact_response(path):
    return FileResponse(path,headers={'Content-Security-Policy':"default-src 'none'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'",'Cache-Control':'no-store'})

@router.get('/api/benchmark/samples')
def list_samples(rater:str='primary'):
    safe_id(rater)
    result=[]
    for s in samples():
        q=question(s['question_id']);rating=read(human_path(s['id'],rater),{})
        result.append({'id':s['id'],'question_id':s['question_id'],'subject':q['subject'],
                       'topic':q['topic'],'set':s['set'],'html_sha256':s['html_sha256'],
                       'status':rating.get('status','unrated'),'overall_score':rating.get('overall_score')})
    result.sort(key=lambda s: hashlib.sha256(('blind-v1:'+s['id']).encode()).hexdigest())
    return {'samples':result,'rubric':(BENCH/'rubric.md').read_text(encoding='utf-8') if (BENCH/'rubric.md').exists() else '',
            'progress':read(BENCH/'runs/progress.json',{}),'judges':read(BENCH/'evaluation/judge_registry.json',[])}

@router.get('/api/benchmark/samples/{sid}')
def get_sample(sid:str,rater:str='primary'):
    s=sample(sid);q=question(s['question_id'])
    return {'id':s['id'],'question':q,'html_sha256':s['html_sha256'],
            'reference_checks':read(inside(q['path'])/'reference/checks.json',{}),
            'rating':read(human_path(sid,rater),{'revision':0,'status':'unrated','scores':{},'overall_score':None,'comment':'','flags':[]}),
            'inspection':read(inside(s['inspection']),{}) if s.get('inspection') else {},
            'screenshots':[f'/api/benchmark/samples/{sid}/files/shot{i}' for i,_ in enumerate(s.get('screenshots',[]))]}

@router.get('/api/benchmark/samples/{sid}/files/{kind}')
def sample_file(sid:str,kind:str):
    s=sample(sid)
    if kind=='html':
        path=inside(s['html'])
        if sha(path)!=s['html_sha256']:raise HTTPException(409,'页面发生变化，原评分不再适用')
    elif re.fullmatch(r'shot\d+',kind):
        i=int(kind[4:]);shots=s.get('screenshots',[])
        if i>=len(shots):raise HTTPException(404,'截图不存在')
        path=inside(shots[i])
    else:raise HTTPException(404,'资源不存在')
    return artifact_response(path)

class Annotation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rater:str=Field(default='primary',max_length=90)
    html_sha256:str
    revision:int=Field(ge=0,strict=True)
    status:str
    scores:dict[str,StrictInt|None]
    overall_score:int|None=Field(default=None,ge=0,le=5,strict=True)
    comment:str=Field(default='',max_length=4000)
    flags:list[str]=Field(default_factory=list,max_length=20)

@router.put('/api/benchmark/samples/{sid}/rating')
def save_rating(sid:str,payload:Annotation):
    s=sample(sid);path=human_path(sid,payload.rater)
    if payload.status not in ('draft','submitted','skipped'):raise HTTPException(400,'状态无效')
    if set(payload.scores)-set(SCORE_KEYS):raise HTTPException(400,'评分维度无效')
    if any(v is not None and (type(v)is not int or not 1<=v<=5) for v in payload.scores.values()):raise HTTPException(400,'维度分数必须是1–5或留空')
    if any(len(f)>100 for f in payload.flags):raise HTTPException(400,'标签过长')
    if payload.status=='submitted' and (payload.overall_score is None or any(payload.scores.get(k) is None for k in SCORE_KEYS)):
        raise HTTPException(400,'提交前请填写五维评分和综合评分；暂未完成可保存草稿')
    if payload.html_sha256!=s['html_sha256'] or sha(inside(s['html']))!=s['html_sha256']:
        raise HTTPException(409,'页面版本变化，请重新加载')
    with LOCK:
        previous=read(path,{})
        if previous.get('revision',0)!=payload.revision:raise HTTPException(409,'评分已在其他页面修改，请重新加载后再编辑')
        rubric_path=BENCH/'rubric.md'
        rubric_text=rubric_path.read_text(encoding='utf-8') if rubric_path.exists() else ''
        rubric_version=re.search(r'\bv\d+\b',rubric_text.splitlines()[0] if rubric_text else '')
        record={**payload.model_dump(),'sample_id':sid,'question_id':s['question_id'],
                'rubric_version':rubric_version.group() if rubric_version else 'unknown',
                'rubric_sha256':sha(rubric_path) if rubric_path.exists() else None,
                'revision':payload.revision+1,'updated_at':datetime.now(timezone.utc).isoformat()}
        if previous:write(path.parent/'history'/f'{sid}_{previous["revision"]:04d}.json',previous)
        write(path,record)
    return record

@router.get('/api/benchmark/comparison')
def comparison(rater:str='primary'):
    safe_id(rater)
    # Human-first comparison; no judge score leaks into unrated examples.
    report=read(BENCH/'reports/judge_comparison.json',{})
    if report.get('rater')!=rater:report={}
    permitted={s['id'] for s in samples() if read(human_path(s['id'],rater),{}).get('status')=='submitted'}
    return {'summary':report.get('summary',[]),'rows':[r for r in report.get('rows',[]) if r['sample_id'] in permitted],
            'note':'仅显示已完成人工评分的样本。模型验证集不足时不宣布胜者。'}

@router.get('/api/benchmark/gpt-scores')
def gpt_scores():
    return read(BENCH/'reports/gpt_scores.json',{'rows':[],'note':'GPT 独立评分正在准备；不需要先填人工分，也不会代填人工评分。'})

standalone=FastAPI(title='Visualization Benchmark — local')
standalone.include_router(router)
