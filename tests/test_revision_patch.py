import asyncio
import json

import pytest

from app.config import Settings
from scripts.run_1019 import RecordedClient
from app.pipeline import Pipeline
from app.profiles import get_profile, runtime_defaults
from app.storage import Store


@pytest.mark.parametrize('old, succeeds', [('const x = 1;\n', True), ('const missing = 1;', False)])
def test_patch_preserves_newlines_and_rejects_unmatched_edits(tmp_path, monkeypatch, old, succeeds):
    import scripts.run_1019 as runner
    response = json.dumps({'edits': [{'old': old, 'new': 'const x = 2;\n'}]})
    class Client:
        def __init__(self, settings):
            pass
        async def complete(self, model, prompt, images, **kwargs):
            assert len(images) == 1
            assert '局部代码修订' in prompt
            return response
    monkeypatch.setattr(runner, 'ModelClient', Client)
    html = '<!doctype html><html><script>const x = 1;\n</script></html>'
    settings = Settings(tmp_path, 'https://example.invalid', None, 'deepseek-test', 'deepseek-test')
    client = RecordedClient(settings, tmp_path, '', 'enabled', patch=True)
    call = client.complete('deepseek-test', '上一轮 HTML：\n'+html, [(tmp_path/'image.png','image/png')], max_tokens=1000)
    if succeeds:
        assert asyncio.run(call) == html.replace('const x = 1;', 'const x = 2;')
        assert list(tmp_path.glob('*.response.txt'))
    else:
        with pytest.raises(ValueError, match='精确且仅匹配一次'):
            asyncio.run(call)


@pytest.mark.parametrize('bad_patch', [False, True])
def test_main_pipeline_image_patch_and_failure_recovery(tmp_path, bad_patch):
    model = 'deepseek-v4-1-flash-261017'
    settings = Settings(tmp_path, 'https://example.invalid', None, model, model)
    profile = get_profile('math-deepseek-patch')
    config = dict(generator_model=model, reviewer_model=model,
                  generator_prompt=profile.generator_prompt, reviewer_prompt=profile.reviewer_prompt,
                  generator_mode='vision', reviewer_mode='vision', max_rounds=3,
                  **runtime_defaults(profile.id, model, model))
    store = Store(settings)
    task = store.create('test.png', 'image/png', '.png', b'image', run_config=config)
    html = '<!doctype html><html><body><h1>old</h1></body></html>'
    calls = []
    generations = 0
    reviews = 0
    class Client:
        async def complete(self, model, prompt, images, **kwargs):
            nonlocal generations, reviews
            calls.append((prompt, images, kwargs))
            if len(images) == 1:
                generations += 1
                if generations == 1:
                    assert kwargs['reasoning_effort'] == 'high'
                    assert '本轮输出协议：局部代码修订' not in prompt
                    return html
                assert kwargs['reasoning_effort'] == 'low'
                assert '本轮输出协议：局部代码修订' in prompt and html in prompt
                if bad_patch and generations == 2:
                    return json.dumps({'edits': [{'old': '<h1>old</h1>', 'new': '<h1>partial</h1>'},
                                                 {'old': 'missing', 'new': 'bad'}]})
                return json.dumps({'edits': [{'old': '<h1>old</h1>', 'new': '<h1>new</h1>'}]})
            reviews += 1
            assert len(images) == 2 and kwargs['reasoning_effort'] == 'high'
            return json.dumps({'passed': reviews > 1, 'summary': '检查',
                               'issues': ['改标题'] if reviews == 1 else []})
    asyncio.run(Pipeline(settings, store, Client(), lambda html: asyncio.sleep(0, result=b'PNG')).run(task['id']))
    saved = store.get(task['id'])
    assert saved['status'] == 'passed', saved['message']
    assert saved['question_ocr_name'] is None
    assert len(saved['rounds']) == (3 if bad_patch else 2)
    assert reviews == 2  # A rejected patch is not sent to a paid reviewer.
    assert store.artifact(task['id'], saved['final_name']).read_text() == html.replace('old', 'new')
    if bad_patch:
        rejected = saved['rounds'][1]
        assert store.artifact(task['id'], rejected['html_name']).read_text() == html
        assert rejected['model_review']['source'] == 'local_validator'
    assert (tmp_path / task['id'] / 'round_02.patch_raw.txt').is_file()
    assert store.config(task['id'])['revision_prompt'] == config['revision_prompt']
