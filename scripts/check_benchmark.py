"""Offline acceptance check for the shared dataset. No model, network, or writes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT/'benchmark'


def main():
    errors=[]
    def file(relative, digest=None):
        p=(BENCH/relative).resolve()
        if not p.is_relative_to(BENCH.resolve()) or not p.is_file():
            errors.append('missing/unsafe: '+str(relative)); return
        if digest and hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            errors.append('hash mismatch: '+str(relative))
    def read(relative):
        file(relative)
        return json.loads((BENCH/relative).read_text(encoding='utf-8'))
    questions=read('manifest.json')['questions']
    for q in questions:
        file(q['path']+'/input.png',q['input_sha256'])
        for name in ('question.json','source.json','stem.html','assets.json','reference/answer.html','reference/explanation.html','reference/checks.json'):
            file(q['path']+'/'+name)
    samples=read('evaluation/samples.json')+read('evaluation/historical_samples.json')
    historic=read('人工评分/manifest.json')['questions']
    for q in historic:
        file(q['path']+'/input.png')
        if q.get('has_reference'):
            for name in ('answer.html','explanation.html'):file(q['path']+'/reference/'+name)
    for s in samples:
        file(s['html'],s['html_sha256'])
        file(s['inspection'])
        for path in s['screenshots']:file(path)
        for p in (BENCH/'evaluation/human').glob('*/'+s['id']+'.json'):
            human=json.loads(p.read_text(encoding='utf-8'))
            if human['html_sha256']!=s['html_sha256']:errors.append('human/HTML mismatch: '+s['id'])
    for name in ('reports/gpt_scores.json','reports/pilot_metrics.json','reports/分享汇总.json','reports/分享汇总.csv',
                 'runs/jobs.json','runs/artifacts/history.sqlite3','rubric.md'):
        file(name)
    rounds=list((BENCH/'runs/artifacts').glob('*/*_round_*.html'))
    if len(questions)!=36 or len(samples)!=12 or len(rounds)!=21:errors.append('pilot snapshot counts differ from README')
    print(json.dumps({'questions':len(questions),'samples':len(samples),'round_htmls':len(rounds),'errors':errors},ensure_ascii=False,indent=2))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
