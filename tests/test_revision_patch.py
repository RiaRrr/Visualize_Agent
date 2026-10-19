import asyncio
import json

import pytest

from app.config import Settings
from scripts.run_1019 import RecordedClient


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
