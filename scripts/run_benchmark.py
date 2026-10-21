"""Generate blind calibration samples. Paid calls: DeepSeek only; test split stays locked."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, shutil, time
from dataclasses import replace
from pathlib import Path
from app.benchmark import BENCH, read, write, sha, LOCK
from app.config import get_settings, ROOT
from app.model_api import ModelClient
from app.pipeline import Pipeline
from app.profiles import get_profile, PROMPTS
from app.storage import Store, best_round
from scripts.inspect_1019 import inspect

MODEL='deepseek-v4-1-flash-261017'
WORKFLOWS={
 'W0': {'name':'原图单次生成','max_rounds':1,'revision_strategy':'full'},
 'W1': {'name':'原图＋审核＋整页返修','max_rounds':3,'revision_strategy':'full'},
 'W2': {'name':'原图＋审核＋局部修订','max_rounds':3,'revision_strategy':'patch'},
}

class RecordingClient:
    def __init__(self,settings,folder,credential_alias='primary'):self.client=ModelClient(settings);self.folder=folder;self.credential_alias=credential_alias;self.counter=len(list(folder.glob('request_*.json')))
    async def complete(self,model,prompt,images,**kwargs):
        if model!=MODEL:raise ValueError('Benchmark workflow 只允许 DeepSeek；GPT 仅用于独立评估')
        if any('reference' in path.parts for path,_ in images):raise ValueError('参考答案不得进入生成流程')
        self.counter+=1;stem=f'request_{self.counter:03d}'
        write(self.folder/(stem+'.json'),{'model':model,'credential_alias':self.credential_alias,'prompt':prompt,'images':[{'name':p.name,'sha256':sha(p)} for p,_ in images],
              'max_tokens':kwargs['max_tokens'],'thinking':kwargs.get('thinking'),'reasoning_effort':kwargs.get('reasoning_effort')})
        result=await self.client.complete(model,prompt,images,**kwargs)
        (self.folder/(stem+'.response.txt')).write_text(result,encoding='utf-8');return result

ACTIONS=[
 {'name':'next_step','js':"(()=>{const e=[...document.querySelectorAll('button')].find(e=>/下一|继续|next|开始/i.test(e.innerText)&&!e.disabled);if(e){e.click();return {action:'click',label:e.innerText}}return {skipped:'no matching visible step control'}})()"},
 {'name':'slider_mid','js':"(()=>{const e=[...document.querySelectorAll('input[type=range]')].find(e=>e.getClientRects().length);if(e){e.value=(Number(e.min||0)+Number(e.max||100))/2;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return {action:'range',value:e.value}}return {skipped:'no visible slider'}})()"},
 {'name':'slider_high','js':"(()=>{const e=[...document.querySelectorAll('input[type=range]')].find(e=>e.getClientRects().length);if(e){e.value=e.max||100;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return {action:'range',value:e.value}}return {skipped:'no visible slider'}})()"},
]

def export_sample(store,task,q,wid,batch="pilot-v1"):
    # A resumable error is not a finished comparison sample. Keep its round files,
    # but do not expose a mutable "final" HTML for human/judge annotation.
    if task['status'] not in ('passed','failed'):return None
    chosen=best_round(task['rounds'])
    if not chosen:return None
    folder=store.root/task['id'];html=folder/chosen['html_name']
    previous=read(folder/'selection.json',{})
    # Preserve evidence already evaluated; a different selected HTML gets its own folder.
    evidence=(BENCH/previous['inspection']).parent if previous.get('html_sha256')==sha(html) and previous.get('inspection') else folder/'evidence'/sha(html)[:16]
    if not (evidence/'inspection.json').exists():
        try:inspect(html,evidence,ACTIONS)
        except Exception as exc:write(evidence/'inspection.json',{'error':str(exc),'states':[]})
    report=read(evidence/'inspection.json',{})
    # Blind public evidence excludes absolute paths and experiment labels.
    public={'viewport':report.get('viewport'),'runtime_errors':report.get('runtime_errors',[]),'error':report.get('error'),
            'states':[{'name':s['name'],'action':s['action'],'buttons':s.get('buttons'), 'result':s.get('result')} for s in report.get('states',[])]}
    write(evidence/'public.json',public)
    sid='B'+hashlib.sha256((q['id']+wid+batch).encode()).hexdigest()[:8].upper()
    record={'run_status':task['status'],'completed_rounds':len(task['rounds']),'id':sid,'question_id':q['id'],'set':'calibration','workflow_id':wid,'batch':batch,'task_id':task['id'],
        'round_number':chosen['number'],'html':html.relative_to(BENCH).as_posix(),'html_sha256':sha(html),
        'selection_rule':'highest internal overall_score; tie later; not selected by independent evaluator',
        'inspection':(evidence/'public.json').relative_to(BENCH).as_posix(),
        'screenshots':[p.relative_to(BENCH).as_posix() for p in sorted(evidence.glob('*.png'))]}
    with LOCK:
        choices=read(BENCH/'evaluation/samples.json',[])
        choices=[s for s in choices if s['id']!=sid]+[record];write(BENCH/'evaluation/samples.json',choices)
    write(folder/'selection.json',record)
    return sid

async def main():
    args=argparse.ArgumentParser();args.add_argument('--questions',nargs='*');args.add_argument('--concurrency',type=int,default=2);args.add_argument('--gateway-ip',help='Optional task-local DNS override; preserves original hostname/SNI');args.add_argument('--batch',default='pilot-v1');args.add_argument('--workflows',nargs='+',choices=list(WORKFLOWS),default=list(WORKFLOWS));args.add_argument('--credential-alias',choices=['primary','alternate'],default='primary');opts=args.parse_args()
    from app.benchmark import safe_id
    safe_id(opts.batch)
    workflows={key:WORKFLOWS[key] for key in opts.workflows}
    if opts.gateway_ip:
        import socket, ipaddress
        from urllib.parse import urlparse
        address=str(ipaddress.ip_address(opts.gateway_ip));host=urlparse(get_settings().codex_base_url).hostname
        original_resolver=socket.getaddrinfo
        def resolve(name,*args,**kwargs):
            return original_resolver(address if name in (host,host.encode()) else name,*args,**kwargs)
        socket.getaddrinfo=resolve
    manifest=read(BENCH/'manifest.json',{})
    candidates=manifest.get('questions',[]) or [read(p) for p in BENCH.glob('*/*/question.json')]
    selected=[q for q in candidates if (q['id'] in opts.questions if opts.questions else q.get('pilot'))]
    if not selected:raise ValueError('没有可用题目')
    if any(q['split']!='dev' for q in selected):raise ValueError('留出测试集未解锁；不得用来调参')
    if any(q['input_status']!='ready' for q in selected):raise ValueError('题图尚未准备完成')
    settings=replace(get_settings(),data_dir=BENCH/'runs/artifacts',generator_model=MODEL,reviewer_model=MODEL,
                     generator_max_tokens=64000,reviewer_max_tokens=64000,model_timeout_seconds=900,deepseek_thinking='enabled')
    if opts.credential_alias=='alternate':
        if not settings.edumind_alt_key:raise ValueError('未配置备用凭据')
        settings=replace(settings,codex_key=settings.edumind_alt_key)
    store=Store(settings);jobs=read(BENCH/'runs/jobs.json',{});sem=asyncio.Semaphore(max(1,min(opts.concurrency,3)))
    quota_exhausted=False
    registry=[]
    for wid,w in WORKFLOWS.items():
        registry.append({'id':wid,**w,'version':opts.batch,'generator_model':MODEL,'reviewer_model':MODEL,'input_mode':'vision','thinking':'enabled','reasoning_effort':'high','status':'implemented','reference_access':False})
    registry.append({'id':'W3','name':'原图＋规划＋核对＋生成＋修订','status':'planned_not_in_pilot','reason':'先用三种基础流程建立人工校准样本；目前工作台规划仅数学，跨学科版本另行冻结'})
    registry.extend([{'id':wid,'name':name,'status':'historical_not_comparable','source_report':report} for wid,name,report in [('H_OCR','OCR＋文字路径','OCR对照实验.md'),('H_TEMPLATE','固定教学模块模板','1019.md'),('H_REASONING','不同推理强度','1019.md')]])
    existing=read(BENCH/'workflows/registry.json',[])
    write(BENCH/'workflows/registry.json',existing+[x for x in registry if (x['id'],x.get('version')) not in {(y['id'],y.get('version')) for y in existing}])
    def progress():
        states=[store.get(taskid) for taskid in jobs.values()]
        write(BENCH/'runs/progress.json',{'planned':len(selected)*len(workflows),'batch':opts.batch,'ready_samples':len(read(BENCH/'evaluation/samples.json',[])),
               'running':sum(t['status']=='running' for t in states),'errors':sum(t['status']=='error' for t in states),'note':'当前批次 '+opts.batch+'；留出测试集不运行'})
    async def run(q,wid,w):
        nonlocal quota_exhausted
        async with sem:
            if quota_exhausted:
                print('SKIP',q['id'],wid,'当前凭据额度耗尽，不继续请求',flush=True);return
            # Preparation may finish while earlier jobs are running. Validate the
            # actual saved input rather than trusting a stale startup manifest.
            q=read(BENCH/q['path']/'question.json')
            if sha(BENCH/q['path']/'input.png')!=q['input_sha256']:
                raise ValueError('题图与manifest哈希不符：'+q['id'])
            key=('' if opts.batch=='pilot-v1' else opts.batch+'_')+q['id']+'_'+wid
            profile=get_profile('math-deepseek' if q['subject']=='math' else q['subject'])
            config={'profile_id':profile.id,'profile_name':profile.name,'profile_version':profile.version,'subject':q['subject'],
                'generator_provider':'codex','generator_model':MODEL,'reviewer_provider':'codex','reviewer_model':MODEL,
                'generator_mode':'vision','reviewer_mode':'vision','generator_prompt':profile.generator_prompt,'reviewer_prompt':profile.reviewer_prompt,
                'generator_thinking':'enabled','reviewer_thinking':'enabled','generator_reasoning_effort':'high','reviewer_reasoning_effort':'high',
                'generator_max_tokens':64000,'reviewer_max_tokens':64000,'max_rounds':w['max_rounds'],'run_mode':'single' if wid=='W0' else 'fixed_five',
                'revision_strategy':w['revision_strategy'],'revision_reasoning_effort':'high',
                'network_override':{'gateway_ip':opts.gateway_ip,'scope':'this runner only'},
                'benchmark':{'version':manifest['version'],'batch':opts.batch,'question_id':q['id'],'input_sha256':q['input_sha256'],'workflow':wid,'split':q['split']}}
            if wid=='W2':config['revision_prompt']=(PROMPTS/'html_revision_patch.md').read_text()
            snap=BENCH/'workflows'/opts.batch/wid/q['subject'];snap.mkdir(parents=True,exist_ok=True)
            if (snap/'config.json').exists():
                frozen=read(snap/'config.json')
                for field in ('generator_prompt','reviewer_prompt','revision_prompt','generator_model','reviewer_model','generator_reasoning_effort','reviewer_reasoning_effort','revision_reasoning_effort','max_rounds','revision_strategy'):
                    if frozen.get(field)!=config.get(field):raise ValueError('冻结配置已变化，请使用新的 --batch：'+field)
            if not (snap/'config.json').exists():
                write(snap/'config.json',config);(snap/'generator.md').write_text(profile.generator_prompt);(snap/'reviewer.md').write_text(profile.reviewer_prompt)
            if key in jobs:task=store.get(jobs[key])
            else:
                task=store.create(q['id']+'.png','image/png','.png',(BENCH/q['path']/'input.png').read_bytes(),run_config=config,batch_id='benchmark-'+opts.batch)
                jobs[key]=task['id'];write(BENCH/'runs/jobs.json',jobs)
            print('START',key,task['id'],flush=True);progress()
            if task['status'] not in ('passed','failed'):
                client=RecordingClient(settings,store.root/task['id'],opts.credential_alias)
                store.log_event(task['id'],'benchmark_resume',task['current_round'],'本次运行凭据别名：'+opts.credential_alias+'；模型和prompt不变')
                for attempt in range(2):
                    await Pipeline(settings,store,client).run(task['id']);task=store.get(task['id']);progress()
                    if task['status']!='error':break
                    print('ERROR',key,task['message'],flush=True)
                    if 'HTTP 402' in task['message']:
                        quota_exhausted=True;break
                    if attempt==0:await asyncio.sleep(3)
            task=store.get(task['id']);sid=await asyncio.to_thread(export_sample,store,task,q,wid,opts.batch)
            progress();print('DONE',key,task['status'],sid,flush=True)
    # Every task is independent; model concurrency is limited, and no sub-agents are used.
    await asyncio.gather(*(run(q,wid,w) for q in selected for wid,w in workflows.items()))
    progress()
if __name__=='__main__':asyncio.run(main())
