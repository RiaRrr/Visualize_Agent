import asyncio
import json

import pytest

from app.config import Settings
from app.pipeline import Pipeline
from app.profiles import get_profile, runtime_defaults
from app.storage import Store

PLAN = {'given': ['y=x'], 'goal': ['图像'],
        'derivation': [{'from': 'y=x', 'reason': '定义', 'to': '直线'}],
        'answer': '直线', 'storyboard': [{'show': '坐标系', 'action': '移动点', 'insight': '斜率固定'}],
        'state': {'independent': ['x'], 'derived': ['y=x']}, 'checks': ['y-x=0']}
HTML = '<!doctype html><html><body><h1>直线</h1></body></html>'


@pytest.mark.parametrize('gen_mode,rev_mode', [('vision', 'vision'), ('vision', 'ocr_text'), ('ocr_text', 'vision')])
def test_image_planning_inputs_settings_output_and_resume(tmp_path, gen_mode, rev_mode):
    model = 'deepseek-v4-1-flash-261017'
    settings = Settings(tmp_path, 'https://example.invalid', None, model, model)
    profile = get_profile('math-deepseek-patch', gen_mode, rev_mode)
    config = dict(profile_id=profile.id, subject='math', generator_model=model, reviewer_model=model,
                  generator_mode=gen_mode, reviewer_mode=rev_mode, generator_provider='codex', reviewer_provider='codex',
                  plan_reviewer_provider='codex', plan_reviewer_model=model, plan_reviewer_mode=rev_mode,
                  ocr_provider='codex', ocr_model='ocr', generation_strategy='guided_math', max_rounds=1,
                  generator_prompt=profile.generator_prompt, reviewer_prompt=profile.reviewer_prompt,
                  **runtime_defaults(profile.id, model, model))
    store = Store(settings)
    task = store.create('test.png', 'image/png', '.png', b'image', run_config=config)
    calls = []
    fail_review = True
    class Client:
        async def complete(self, requested_model, prompt, images, **kwargs):
            nonlocal fail_review
            if requested_model == 'ocr':
                assert 'ocr_text' in (gen_mode, rev_mode)
                return '原题 y=x；图中有一条直线'
            assert requested_model == model
            assert kwargs['thinking'] == 'enabled' and kwargs['reasoning_effort'] == 'high'
            assert kwargs['max_tokens'] == 64000
            if '施工图' in prompt:
                calls.append('plan')
                assert len(images) == (1 if gen_mode == 'vision' else 0)
                return json.dumps(PLAN, ensure_ascii=False)
            if prompt.startswith('你是独立的数学核对者'):
                calls.append('plan_review')
                assert len(images) == (1 if rev_mode == 'vision' else 0)
                return json.dumps({'passed': True, 'issues': []})
            if '规划 JSON：' in prompt:
                calls.append('html')
                assert len(images) == (1 if gen_mode == 'vision' else 0)
                assert '直线' in prompt
                return HTML
            calls.append('html_review')
            assert len(images) == (2 if rev_mode == 'vision' else 0)
            if fail_review:
                fail_review = False
                raise RuntimeError('模拟审核暂时失败')
            return json.dumps({'passed': True, 'issues': [], 'summary': '完成'})
    pipeline = Pipeline(settings, store, Client(), lambda html: asyncio.sleep(0, result=b'PNG'))
    asyncio.run(pipeline.run(task['id']))
    saved = store.get(task['id'])
    assert saved['status'] == 'error'
    assert 'math_plan.accepted.json' in saved['math_plan_files']
    assert 'math_plan_attempt_01.json' in saved['math_plan_files']
    assert 'math_plan_review_01.json' in saved['math_plan_files']
    for name in saved['math_plan_files']:
        assert store.artifact(task['id'], name).is_file()
    assert json.loads(store.artifact(task['id'], saved['math_plan_name']).read_text()) == PLAN
    asyncio.run(pipeline.run(task['id']))
    saved = store.get(task['id'])
    assert saved['status'] == 'passed', saved['message']
    assert calls == ['plan', 'plan_review', 'html', 'html_review', 'html_review']
    assert [c['role'] for c in saved['api_calls'] if c['role'].startswith('math_plan')] == ['math_plan', 'math_plan_review']
    if gen_mode == rev_mode == 'vision':
        assert saved['question_ocr_name'] is None
        assert all(c['role'] not in ('question_ocr', 'page_description') for c in saved['api_calls'])
