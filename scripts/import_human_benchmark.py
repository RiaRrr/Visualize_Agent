"""Copy existing human-rated artifacts without modifying the data_v2 history."""
from __future__ import annotations
import hashlib, shutil
from pathlib import Path
from datetime import datetime, timezone
from app.benchmark import BENCH, read, write, sha, ROOT, human_path
from app.rubric import SCORE_KEYS
from scripts.inspect_1019 import inspect
from scripts.run_benchmark import ACTIONS

def main():
    destination=BENCH/'人工评分';questions={};items=[];skipped=[]
    previous_questions={q['id']:q for q in read(destination/'manifest.json',{}).get('questions',[])}
    for meta in sorted((ROOT/'data_v2').glob('*/*.meta.json')):
        m=read(meta,{});h=m.get('human_review') or {}
        if not (any(v is not None for v in (h.get('scores') or {}).values()) or h.get('overall_score') is not None or h.get('comment')):continue
        files=m.get('files') or {};source=meta.parent
        html=source/(files.get('html') or '');image=source/(files.get('image') or '')
        if not html.is_file() or not image.is_file() or (m.get('html_sha256') and sha(html)!=m['html_sha256']):
            skipped.append({'metadata':str(meta.relative_to(ROOT)),'reason':'题图或HTML缺失，或HTML与评分哈希不一致'});continue
        sid='H'+hashlib.sha256(m['record_id'].encode()).hexdigest()[:10].upper()
        qid='HQ'+sha(image)[:12].upper();qpath=destination/'题目'/qid;qpath.mkdir(parents=True,exist_ok=True)
        shutil.copy2(image,qpath/'input.png')
        questions[qid]={'id':qid,'subject':m.get('profile',{}).get('subject') or 'math',
            'topic':'已有人工评分题目','path':qpath.relative_to(BENCH).as_posix(),'input_sha256':sha(image),
            'split':'historical','reference_status':'not_provided','has_stem':False,'has_reference':False}
        if qid in previous_questions:questions[qid].update(previous_questions[qid])
        folder=destination/'样本'/sid;folder.mkdir(parents=True,exist_ok=True)
        shutil.copy2(html,folder/html.name);shutil.copy2(meta,folder/'source.meta.json')
        screenshot=source/(files.get('screenshot') or '')
        if screenshot.is_file():shutil.copy2(screenshot,folder/'original.png')
        write(folder/'human_original.json',h)
        evidence=folder/'evidence'
        if not (evidence/'inspection.json').exists():
            try:inspect(folder/html.name,evidence,ACTIONS)
            except Exception as exc:write(evidence/'inspection.json',{'error':str(exc),'states':[]})
        report=read(evidence/'inspection.json',{})
        write(evidence/'public.json',{'viewport':report.get('viewport'),'runtime_errors':report.get('runtime_errors',[]),
            'error':report.get('error'),'states':[{'name':s['name'],'action':s['action'],'result':s.get('result')} for s in report.get('states',[])]})
        shots=sorted(evidence.glob('*.png')) or ([folder/'original.png'] if (folder/'original.png').exists() else [])
        record={'id':sid,'question_id':qid,'set':'historical_human','task_id':m['task_id'],'round_number':m['round'],
            'html':(folder/html.name).relative_to(BENCH).as_posix(),'html_sha256':sha(html),
            'inspection':(evidence/'public.json').relative_to(BENCH).as_posix(),
            'screenshots':[p.relative_to(BENCH).as_posix() for p in shots],
            'source_metadata':meta.relative_to(ROOT).as_posix(),'source_metadata_sha256':sha(meta),
            'source_rubric_version':m.get('rubric_version'),'source_generator':m.get('generator'),
            'selection_rule':'all existing human-rated data_v2 pages with unchanged HTML'}
        write(folder/'provenance.json',record);items.append(record)
        target=human_path(sid,'primary');previous=read(target,{})
        # Keep benchmark-side edits. Archive the original label separately above on every import.
        if not previous:
            complete=all(type((h.get('scores') or {}).get(k)) is int for k in SCORE_KEYS) and type(h.get('overall_score')) is int
            write(target,{'rater':'primary','sample_id':sid,'question_id':qid,'revision':1,
                'html_sha256':record['html_sha256'],'status':'submitted' if complete else 'draft',
                'scores':h.get('scores',{}),'overall_score':h.get('overall_score'),'comment':h.get('comment',''),
                'flags':[],'rubric_version':m.get('rubric_version'),'updated_at':h.get('updated_at'),
                'imported_from':record['source_metadata'],'imported_at':datetime.now(timezone.utc).isoformat()})
    write(destination/'manifest.json',{'set':'historical_human','questions':list(questions.values()),
        'samples':items,'skipped':skipped,'note':'复制保留原data_v2历史；人工评分原文在每个样本human_original.json。历史题不进入新测试集。'})
    write(BENCH/'evaluation/historical_samples.json',items)
    print(f'已归档 {len(items)} 个人工评分页面，{len(questions)} 道题；跳过 {len(skipped)}',flush=True)

if __name__=='__main__':main()
