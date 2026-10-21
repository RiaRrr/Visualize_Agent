"""Append six comprehensive questions without rebuilding or changing the pilot."""
from __future__ import annotations
import asyncio, json
import httpx
from scripts.prepare_benchmark import BENCH, ROOT, dump, digest, local_html, normalized, family_similarity
from app.browser import render_html

SELECTION = [
    ('math', '3725816550539264', '导数、恒成立与双零点证明', 'dev', '参数分类、零点区间与辅助函数的因果链'),
    ('math', '3730389030707200', '椭圆、投影与定圆', 'test', '三小问、斜率约束转化、投影与定圆证明'),
    ('physics', '3888992658481152', '多阶段追及与两次相遇', 'dev', '停车边界、相对位移与两次相遇的参数范围'),
    ('physics', '3915158089498624', '斜面—水平面动力学', 'test', '从速度图像反推分阶段运动、摩擦和受力'),
    ('chemistry', '4104616418041856', '有机合成与结构推断', 'dev', '七小问、官能团变化、保护基、异构与迁移合成'),
    ('chemistry', '3892456722882560', '制备与气体检验综合实验', 'test', '装置顺序、除杂干燥、证据排干扰与反应计量'),
]

async def main():
    manifest=json.loads((BENCH/'manifest.json').read_text())
    bank=json.loads((ROOT/'unique-questions.json').read_text())
    by_id={q['id']:q for q in bank['questions']}
    sem=asyncio.Semaphore(8); additions=[]
    async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False) as client:
        for subject,source_id,topic,split,focus in SELECTION:
            slug=subject+('_013' if split=='dev' else '_014')
            existing=next((x for x in manifest['questions'] if x['id']==slug),None)
            if existing:
                if existing['source_id']!=source_id:raise ValueError('题目ID冲突')
                additions.append(existing);continue
            q=by_id[source_id];folder=BENCH/subject/slug;folder.mkdir(parents=True,exist_ok=True)
            assets={}
            for field,name in [('stem','stem.html'),('answer','reference/answer.html'),('explanation','reference/explanation.html')]:
                dest=folder/name;dest.parent.mkdir(exist_ok=True)
                dest.write_text(await local_html(q[field],folder,client,sem,assets),encoding='utf-8')
            (folder/'input.png').write_bytes(await render_html((folder/'stem.html').read_text()))
            from PIL import Image, ImageChops
            image=Image.open(folder/'input.png').convert('RGB')
            if ImageChops.difference(image,Image.new('RGB',image.size,'white')).getbbox() is None:
                raise ValueError('题图渲染为空白：'+slug)
            record={'id':slug,'source_id':source_id,'subject':subject,'topic':topic,'split':split,'family_id':slug,
                    'difficulty_level':q['difficulty_level'],'difficulty':q['difficulty'],'type':q['type'],
                    'knowledge_points':q['kpoints'],'selection_reason':focus,'source_paper_ids':q['_paper_ids'],
                    'path':folder.relative_to(BENCH).as_posix(),'pilot':False,'cohort':'hard-v1',
                    'reference_status':'source_answer_not_independently_verified','input_status':'ready',
                    'input_sha256':digest((folder/'input.png').read_bytes()),
                    'source_sha256':digest(json.dumps(q,ensure_ascii=False,sort_keys=True).encode())}
            dump(folder/'source.json',q);dump(folder/'question.json',record);dump(folder/'assets.json',assets)
            dump(folder/'reference/checks.json',{'status':'pending_human_verification','visualization_focus':focus,
                'items':['核对全部小问、原图和公式','参考答案未经人工审定；歧义必须显式报告','本题为综合题，遗漏小问应影响一致性与教学评价']})
            additions.append(record);print(slug,'ready',flush=True)
    ids={q['id'] for q in manifest['questions']}
    manifest['questions'].extend(q for q in additions if q['id'] not in ids)
    manifest.update(version='v1-plus-hard-v1',extension='原36题及原split不变；新增3开发题与3留出题')
    dump(BENCH/'manifest.json',manifest)
    near=[]
    for a in additions:
        for b in manifest['questions']:
            if a['id']==b['id'] or a['subject']!=b['subject']:continue
            similarity=family_similarity(normalized(by_id[a['source_id']]),normalized(by_id[b['source_id']]))
            if similarity>.45:near.append({'a':a['id'],'b':b['id'],'text_similarity':round(similarity,3),'requires_review':True})
    dump(BENCH/'extensions/hard-v1.json',{'questions':[q['id'] for q in additions],
        'selection':'按多小问、跨知识点、条件转化与可视化需求选题；题库难度系数仅作辅助，不声称覆盖所有难题。',
        'near_duplicate_candidates':near,'limitation':'物理题库难题数量少，新增题仍集中在力学；参考解未人工审定。',
        'dev':[q['id'] for q in additions if q['split']=='dev'],'test':[q['id'] for q in additions if q['split']=='test']})

if __name__=='__main__':asyncio.run(main())
