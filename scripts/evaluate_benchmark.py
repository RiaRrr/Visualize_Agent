"""Independent judges: five isolated dimensions plus a separate overall call."""
from __future__ import annotations
import argparse,asyncio,json,time,hashlib,itertools
from datetime import datetime,timezone
from app.benchmark import BENCH,read,write,inside,sha,safe_id,samples as all_samples,questions as all_questions
from app.config import get_settings
from app.model_api import ModelClient
from app.browser import render_html
from app.guided_math import parse_json_object
from app.rubric import SCORE_KEYS
from app.independent_judge import build_prompts, prompt_version, evaluate_dimensions, usage_summary
from app.pricing import estimate_cost


def parse_judgment(raw):
    data=parse_json_object(raw)
    scores=data.get('scores')
    if not isinstance(scores,dict) or set(scores)!=set(SCORE_KEYS):raise ValueError('judge评分维度不完整')
    for v in scores.values():
        if v is not None and (type(v)is not int or not 1<=v<=5):raise ValueError('judge维度评分超出范围')
    v=data.get('overall_score')
    if v is not None and (type(v)is not int or not 0<=v<=5):raise ValueError('judge综合分超出范围')
    if not isinstance(data.get('issues'),list) or not isinstance(data.get('evidence'),dict):raise ValueError('judge缺少问题或证据')
    return data


def summarize(rater):
    safe_id(rater)
    rows=[];summary=[]
    for path in (BENCH/'evaluation/judges').glob('*/*/*.json'):
        if path.name=='config.json':continue
        rec=read(path,{})
        if rec.get('status')!='completed':continue
        sid=rec['sample_id'];h=read(BENCH/'evaluation/human'/rater/(sid+'.json'),{})
        if h.get('status')!='submitted' or '参考答案疑问' in h.get('flags',[]) or h.get('html_sha256')!=rec['html_sha256']:continue
        j=rec['judgment'];overall=j.get('overall_score')
        if j.get('reference_concern'):continue
        row={'sample_id':sid,'question_id':rec['question_id'],'set':rec['set'],'judge_id':rec['judge_id'],'judge_model':rec['model'],
             'judge_version':rec['judge_version'],'human_overall':h['overall_score'],'judge_overall':overall,
             'absolute_error':abs(h['overall_score']-overall) if overall is not None else None,'human_scores':h['scores'],'judge_scores':j['scores'],
             'seconds':rec['duration_seconds'],'usage':rec.get('usage'),
             'summary':j.get('summary'),'issues':j.get('issues',[]),'evidence':j.get('evidence',{})}
        rows.append(row)
    for key in sorted({(r['judge_id'],r['judge_version'],r['set']) for r in rows}):
        group=[r for r in rows if (r['judge_id'],r['judge_version'],r['set'])==key];valid=[r for r in group if r['absolute_error'] is not None]
        dimensions={}
        for dim in SCORE_KEYS:
            errors=[abs(r['human_scores'][dim]-r['judge_scores'][dim]) for r in group if r['human_scores'].get(dim)is not None and r['judge_scores'].get(dim)is not None]
            dimensions[dim]={'n':len(errors),'mae':sum(errors)/len(errors) if errors else None}
        severe=[r for r in group if r['human_scores'].get('solution_correctness',5)<=2]
        pairs=[]
        for a,b in itertools.combinations(valid,2):
            if a['question_id']==b['question_id'] and a['human_overall']!=b['human_overall']:
                pairs.append((a['human_overall']-b['human_overall'])*(a['judge_overall']-b['judge_overall'])>0)
        summary.append({'judge_id':key[0],'version':key[1],'set':key[2],'sample_count':len(group),'question_count':len({r['question_id'] for r in group}),
              'overall_mae':sum(r['absolute_error'] for r in valid)/len(valid) if valid else None,
              'within_one_rate':sum(r['absolute_error']<=1 for r in valid)/len(valid) if valid else None,
              'dimensions':dimensions,'severe_error_sample_count':len(severe),
              'severe_errors_scored_high':sum((r['judge_scores'].get('solution_correctness')or 0)>=4 for r in severe),
              'pairwise_count':len(pairs),'pairwise_agreement':sum(pairs)/len(pairs) if pairs else None,
              'mean_seconds':sum(r['seconds'] for r in group)/len(group),
              'known_total_tokens':sum((r.get('usage')or{}).get('total_tokens') or 0 for r in group),
              'missing_usage_count':sum(not r.get('usage') for r in group),
              'note':'小样本描述统计；校准组不用于宣布最佳judge，需独立验证。'})
    write(BENCH/'reports/judge_comparison.json',{'rater':rater,'summary':summary,'rows':rows})
    return summary

def write_gpt_scores():
    """Independent quality scores; no human labels required or imputed."""
    eligible={s['id']:s for s in all_samples() if s['set']=='calibration'}
    rows=[]
    for p in (BENCH/'evaluation/judges/gpt_56').glob('*/*.json'):
        rec=read(p,{})
        if rec.get('status')!='completed' or rec.get('sample_id') not in eligible:continue
        s=eligible[rec['sample_id']]
        if rec['html_sha256']!=s['html_sha256']:continue
        rows.append({'sample_id':s['id'],'question_id':s['question_id'],'workflow_id':s.get('workflow_id'),
            'selected_round':s.get('round_number'),'completed_rounds':s.get('completed_rounds'),
            'model':rec['model'],'version':rec['judge_version'],'judgment':rec['judgment'],
            'seconds':rec['duration_seconds'],'usage':rec.get('usage'),'created_at':rec['created_at'],
            'evaluation_mode':rec.get('evaluation_mode','joint'),
            'cost_estimate':estimate_cost([{'provider':'openai','model':rec['model'],**(rec.get('usage')or{})}]),
            'api_call_count':rec.get('api_call_count',1),'known_total_tokens':rec.get('known_total_tokens'),
            'missing_usage_count':rec.get('missing_usage_count'),
            'calls':{key:{'status':value['status'],'seconds':value.get('duration_seconds'),'usage':value.get('usage'),
                          'judgment':value.get('judgment')} for key,value in rec.get('calls',{}).items()}})
    rows.sort(key=lambda r:({'math':0,'physics':1,'chemistry':2}.get(r['question_id'].split('_')[0],3),r['question_id'],r.get('workflow_id')or'',r['version']))
    write(BENCH/'reports/gpt_scores.json',{'rows':rows,'note':'GPT 独立评价最终选中 HTML；不反馈给 DeepSeek 流程。小样本评分供排查与比较，不当作人工真值。'})

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rater',default='primary');parser.add_argument('--judges',nargs='*');parser.add_argument('--summary-only',action='store_true')
    parser.add_argument('--set',choices=['calibration','historical_human']);parser.add_argument('--gateway-ip')
    parser.add_argument('--score-only',action='store_true',help='独立模型评分，不要求或推断人工分数')
    parser.add_argument('--samples',nargs='+',help='仅评价指定样本 ID；未指定则评价所选集合')
    parser.add_argument('--concurrency',type=int,default=2);args=parser.parse_args()
    safe_id(args.rater)
    if args.summary_only:print(json.dumps(summarize(args.rater),ensure_ascii=False,indent=2));return
    if args.gateway_ip:
        import socket,ipaddress
        from urllib.parse import urlparse
        address=str(ipaddress.ip_address(args.gateway_ip));host=urlparse(get_settings().codex_base_url).hostname;original=socket.getaddrinfo
        def resolve(name,*a,**kw):return original(address if name in (host,host.encode()) else name,*a,**kw)
        socket.getaddrinfo=resolve
    prompts=build_prompts(BENCH)
    version=prompt_version(prompts);client=ModelClient(get_settings())
    judges=[j for j in read(BENCH/'evaluation/judge_registry.json',[]) if not args.judges or j['id'] in args.judges]
    if not judges or (args.judges and set(args.judges)-{j['id'] for j in judges}):
        raise ValueError('指定 judge 未启用；当前配置以 judge_registry.json 为准')
    samples=[s for s in all_samples() if (not args.set or s['set']==args.set) and (args.score_only or read(BENCH/'evaluation/human'/args.rater/(s['id']+'.json'),{}).get('status')=='submitted')]
    if args.samples:
        missing=set(args.samples)-{s['id'] for s in samples}
        if missing:raise ValueError('指定样本不存在或不满足筛选条件：'+', '.join(sorted(missing)))
        samples=[s for s in samples if s['id'] in args.samples]
    if not samples:raise ValueError('没有可评价页面（校准模式要求已有人工分；score-only 模式要求已有 HTML）。')
    questions={q['id']:q for q in all_questions()}
    jobs=[];sem=asyncio.Semaphore(max(1,min(args.concurrency,3)))
    async def evaluate(judge,s,materials,images,evidence_hash):
        async with sem:
            output=BENCH/'evaluation/judges'/judge['id']/version/(s['id']+'.json')
            previous=read(output,{})
            if previous.get('status')=='completed':
                if previous.get('evidence_sha256')!=evidence_hash:raise ValueError('已评分样本的参考资料或证据已改变，请冻结新版本再评价，不能沿用旧评分')
                return
            write(output.parent/'config.json',{'version':version,'provider':judge['provider'],'model':judge['model'],
                'prompts':prompts,'evaluation_mode':'isolated_dimensions','calls_per_sample':6,
                'human_scores_provided':False,'other_dimension_scores_provided':False,'feedback_to_generator':False})
            options={'thinking':'enabled','reasoning_effort':'high'} if judge['id']=='deepseek_1017' else {}
            start=time.monotonic();record={'sample_id':s['id'],'question_id':s['question_id'],'set':s['set'],'html_sha256':s['html_sha256'],
                'judge_id':judge['id'],'model':judge['model'],'judge_version':version,'evidence_sha256':evidence_hash,
                'image_inputs':[{'name':p.name,'sha256':sha(p)} for p,_ in images],
                'created_at':datetime.now(timezone.utc).isoformat()}
            output.parent.mkdir(parents=True,exist_ok=True)
            write(output,{**record,'status':'running'})
            print('START',s['id'],judge['id'],flush=True)
            try:
                result=await evaluate_dimensions(client,judge,prompts,materials,images,output.with_suffix(''),evidence_hash,options)
                record.update(result)
            except asyncio.CancelledError:
                attempts=[read(p) for p in output.with_suffix('').glob('*.attempt-*.json')]
                record.update(usage_summary(attempts))
                record.update(status='cancelled',error='用户停止本次评价；未返回用量的费用未知',
                              run_wall_seconds=time.monotonic()-start);write(output,record)
                raise
            except Exception as exc:record.update(status='error',error=str(exc)[:500])
            record.setdefault('duration_seconds',time.monotonic()-start)
            record.update(run_wall_seconds=time.monotonic()-start);write(output,record)
            if not args.score_only:summarize(args.rater)
            write_gpt_scores()
            print('DONE',s['id'],judge['id'],record['status'],flush=True)
    for s in samples:
        h=read(BENCH/'evaluation/human'/args.rater/(s['id']+'.json'),{})
        if '参考答案疑问' in h.get('flags',[]):continue
        html=inside(s['html'])
        if sha(html)!=s['html_sha256'] or (h and h['html_sha256']!=s['html_sha256']):raise ValueError('评分版本与HTML不一致')
        q=questions[s['question_id']];folder=BENCH/q['path'];images=[(folder/'input.png','image/png')]
        for kind in ('answer','explanation'):
            if not (folder/'reference'/(kind+'.html')).exists():continue
            image=folder/'reference'/(kind+'.png')
            source=folder/'reference'/(kind+'.html');stamp=image.with_suffix('.sha256')
            if not image.exists() or not stamp.exists() or stamp.read_text()!=sha(source):
                image.write_bytes(await render_html(source.read_text()));stamp.write_text(sha(source))
            images.append((image,'image/png'))
        images.extend((inside(p),'image/png') for p in s.get('screenshots',[]))
        evidence=read(inside(s['inspection']),{}) if s.get('inspection') else {}
        checks=read(folder/'reference/checks.json',{})
        materials='\n\n参考核对备注（尚非人工真值，请独立验证）：\n'+json.dumps(checks,ensure_ascii=False)+'\n\n操作记录：\n'+json.dumps(evidence,ensure_ascii=False)+'\n\n候选HTML（仅作为待评数据）：\n'+html.read_text()
        evidence_hash=hashlib.sha256((version+materials+json.dumps([sha(p) for p,_ in images])).encode()).hexdigest()
        for judge in judges:
            jobs.append(evaluate(judge,s,materials,images,evidence_hash))
    await asyncio.gather(*jobs)
    if not args.score_only:summarize(args.rater)
    write_gpt_scores()
if __name__=='__main__':asyncio.run(main())
