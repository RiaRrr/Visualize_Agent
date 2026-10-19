"""Review a frozen historical GPT page with DeepSeek; never call a GPT API."""
import argparse
import asyncio
from dataclasses import replace
import json

from app.config import get_settings
from app.pipeline import Pipeline
from app.profiles import get_profile
from app.storage import Store
from scripts.run_1019 import CASES, MODEL, RecordedClient


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=('ellipse','parabola'),default='ellipse')
    args=parser.parse_args()
    settings=replace(get_settings(),reviewer_max_tokens=64000,model_timeout_seconds=900)
    store=Store(settings);source=store.get(CASES[args.case]);ref=source['rounds'][-1]
    config={'profile_id':'math-current','profile_name':'1019 · 历史 GPT 成品 · DeepSeek 同标准重审',
            'generator_provider':source['generator_provider'],'generator_model':source['generator_model'],
            'reviewer_provider':'codex','reviewer_model':MODEL,'generator_mode':'vision','reviewer_mode':'vision',
            'generation_strategy':'direct','subject':'math','run_mode':'single','max_rounds':1,
            'reviewer_thinking':'enabled','reviewer_reasoning_effort':'high','reviewer_max_tokens':64000,
            'reviewer_prompt':get_profile('math-deepseek').reviewer_prompt,
            'frozen_reference':{'task_id':source['id'],'round':ref['number'],'html':ref['html_name']},
            'experiment':'1019_frozen_reference','case':args.case,'variant':'gpt-frozen-review'}
    task=store.create(f'1019_{args.case}_gpt_reference.png','image/png','.png',
        (store.root/source['id']/source['image_name']).read_bytes(),run_config=config)
    folder=store.root/task['id']
    (folder/'.pending_round_01.html').write_bytes((store.root/source['id']/ref['html_name']).read_bytes())
    (store.root/'_1019'/f"{task['id']}.json").write_text(json.dumps({'id':task['id'],'case':args.case,'variant':'gpt-frozen-review','rounds':1}))
    class ReviewOnly(RecordedClient):
        async def complete(self,model,prompt,images,**kwargs):
            if model != MODEL or not prompt.startswith('你是独立的学科教师'):
                raise RuntimeError('Frozen reference evaluation forbids generation and non-DeepSeek calls')
            return await super().complete(model,prompt,images,**kwargs)
    print('REVIEW ONLY',task['id'],flush=True)
    await Pipeline(settings,store,ReviewOnly(settings,folder,'','enabled')).run(task['id'])
    result=store.get(task['id'])
    print(json.dumps({'id':task['id'],'status':result['status'],'rounds':result['rounds']},ensure_ascii=False),flush=True)


if __name__=='__main__':asyncio.run(main())
