"""Build the local 36-question benchmark; no model calls, no answer inputs to generators."""
from __future__ import annotations
import argparse, asyncio, base64, collections, hashlib, json, re
from pathlib import Path
from html import escape
import bleach
from bleach.css_sanitizer import CSSSanitizer
import httpx
from lxml import html as LH, etree
from app.config import ROOT
from app.browser import render_html

BENCH = ROOT / 'benchmark'
SELECTION = {
'math': [
('3882491008098304','函数与图像','区间、顶点与最值联动'),
('3884019136626688','集合与不等式','数轴上的解集与交集'),
('3742836393762816','三角与向量','数量积与几何投影'),
('3750522406903808','三角与向量','三角函数图像与参数'),
('3738104561139712','立体几何','空间形体、内切外接与体积'),
('3736075637735424','解析几何','椭圆参数与形状'),
('4031018274463744','概率统计','样本空间与条件概率'),
('3730337597849600','概率统计','数据分布、位置与离散程度'),
('3772640427663360','数列','等比数列的项与部分和'),
('4030835597312000','数列','等差数列的项与部分和'),
('4009616516055040','集合与不等式','不等式与最值取得条件'),
('3772640427565056','函数与导数','切线、斜率和导数')],
'physics': [
('3861377907318784','运动与图像','减速运动、末段位移与停车边界'),
('3769195139145728','运动与图像','速度图像面积与追及'),
('3998381450412032','受力与动力学','斜面受力分解、摩擦与加速度'),
('4111203502186496','受力与功','接触力、临界静止与做功'),
('3997173487788032','动量与冲量','反弹前后速度方向与平均作用力'),
('4073368526315520','电场与动力学','电场力、平衡与斜面运动'),
('4111203501596672','电路与自感','电路切换后的瞬态变化'),
('4111203501809664','电学实验','电表改装与校准'),
('4111203501703168','电磁感应','运动磁场、等效电路与图像'),
('4111203502522368','带电粒子运动','电磁场中的空间轨迹'),
('4111203502030848','热学','气体状态变化与热力学第一定律'),
('4111203501916160','光学实验','折射几何与实验测量')],
'chemistry': [
('4086097452187648','溶液与离子反应','粒子浓度变化与电导曲线'),
('4078298205691904','溶液与酸碱','中和过程、pH曲线与操作'),
('4076407763566592','氧化还原','电子转移与计量关系'),
('4103222117957632','氧化还原','电子转移的图示表达'),
('4104616416862208','反应速率与能量','反应历程、能垒与催化'),
('4104616417320960','速率与平衡','浓度时间曲线与反应历程'),
('4104616417198080','电化学与结构','电池电极反应、离子迁移与晶胞'),
('4104616417869824','平衡与电化学','反应能量、平衡与电解'),
('4086373918359552','实验与分散系','粒子尺度与分离及观察方法'),
('4075130608156672','实验与制备','制备流程、溶液配制与误差'),
('4104616416747520','结构与性质','配位结构与物质性质'),
('4104616416649216','有机结构','官能团、聚合物与性质')],
}
TAGS = ['div','p','span','br','b','strong','i','em','u','sup','sub','table','thead','tbody','tfoot','tr','td','th','ul','ol','li','img','h1','h2','h3','h4','hr']
CSS = CSSSanitizer(allowed_css_properties=['text-align','font-weight','font-style','text-decoration','vertical-align','white-space'])
STYLE = '''body{font:20px/1.9 "Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#152c39;background:white;margin:0;padding:36px;box-sizing:border-box}main{max-width:1160px;margin:auto}table{border-collapse:collapse;max-width:100%}td,th{padding:8px;vertical-align:top}img{max-width:100%;height:auto;object-fit:contain}img.xkw-math-img{max-height:3.4em;vertical-align:middle;width:auto}p{margin:8px 0}h1{font-size:23px}'''

def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def digest(raw): return hashlib.sha256(raw).hexdigest()

def normalized(q):
    text = LH.fromstring(q['stem']).text_content()
    return re.sub(r'[\s\d\W_]+','',text)

def family_similarity(a,b):
    def grams(x): return {x[i:i+4] for i in range(max(0,len(x)-3))}
    a,b=grams(a),grams(b)
    return len(a&b)/max(1,len(a|b))

async def local_html(raw, folder, client, sem, assets):
    # Trusted sanitizer strips active content and unsafe styles before the browser sees it.
    raw=re.sub(r'<(script|style|iframe)\b[^>]*>[\s\S]*?</\1\s*>','',raw,flags=re.I)
    cleaned=bleach.clean(raw,tags=TAGS,attributes={'*':['class','style'],'img':['src','alt','class'],'td':['colspan','rowspan'],'th':['colspan','rowspan']},protocols=['https'],css_sanitizer=CSS,strip=True,strip_comments=True)
    tree=LH.fragment_fromstring(cleaned,create_parent='main')
    async def image(node):
        url=node.get('src','')
        from urllib.parse import urlparse
        parsed=urlparse(url)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.xkw.com'):
            raise ValueError('Unexpected image host: '+str(parsed.hostname))
        key=digest(url.encode()); cached=BENCH/'_asset_cache'/key
        async with sem:
            if not cached.exists():
                for attempt in range(3):
                    try:
                        r=await client.get(url);r.raise_for_status()
                        if len(r.content)>8_000_000:raise ValueError('Image too large')
                        cached.parent.mkdir(exist_ok=True);cached.write_bytes(r.content);break
                    except (httpx.HTTPError,ValueError):
                        if attempt==2:raise
                        await asyncio.sleep(attempt+1)
            content=cached.read_bytes()
        if b'<svg' in content[:1000]:
            svg=etree.fromstring(content,parser=etree.XMLParser(resolve_entities=False,no_network=True))
            for element in list(svg.iter()):
                if etree.QName(element).localname in ('script','foreignObject','iframe'):
                    if element.getparent() is not None:element.getparent().remove(element)
                for attr in list(element.attrib):
                    value=element.attrib[attr]
                    if attr.lower().startswith('on') or (etree.QName(attr).localname in ('href','src') and not value.startswith('#')):
                        del element.attrib[attr]
            content=etree.tostring(svg);mime='image/svg+xml';suffix='.svg'
        else:
            from PIL import Image
            import io
            im=Image.open(io.BytesIO(content));im.verify()
            suffix='.png' if im.format=='PNG' else '.jpg' if im.format=='JPEG' else '.webp' if im.format=='WEBP' else '.gif'
            mime={'jpg':'image/jpeg'}.get(suffix[1:],'image/'+suffix[1:])
        dest=folder/'assets'/(key+suffix);dest.parent.mkdir(exist_ok=True);dest.write_bytes(content)
        assets[key]={'source_url':url,'path':dest.relative_to(folder).as_posix(),'sha256':digest(content)}
        node.set('src','data:'+mime+';base64,'+base64.b64encode(content).decode())
    await asyncio.gather(*(image(n) for n in tree.xpath('.//img')))
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+STYLE+'</style><body>'+LH.tostring(tree,encoding='unicode')+'</body></html>'

async def main():
    bank=json.loads((ROOT/'unique-questions.json').read_text());questions={q['id']:q for q in bank['questions']}
    records=[];sem=asyncio.Semaphore(8)
    audit={}
    for subject,selected in SELECTION.items():
        cid=questions[selected[0][0]]['course_id'];rows=[q for q in questions.values() if q['course_id']==cid]
        freq=collections.Counter(k['name'] for q in rows for k in q['kpoints'])
        audit[subject]={'pool_count':len(rows),'knowledge_point_frequency':dict(freq.most_common()),'selected_topics':dict(collections.Counter(x[1] for x in selected)), 'frequency_scope':'本地题库频率，非全国考试频率'}
        for i,(qid,topic,reason) in enumerate(selected,1):
            q=questions[qid];slug=f'{subject}_{i:03d}';folder=BENCH/subject/slug;folder.mkdir(parents=True,exist_ok=True)
            record={'id':slug,'source_id':qid,'subject':subject,'topic':topic,'split':'dev' if i%2 else 'test','family_id':slug,'difficulty_level':q['difficulty_level'],'difficulty':q['difficulty'],'type':q['type'],'knowledge_points':q['kpoints'],'selection_reason':reason,'source_paper_ids':q['_paper_ids'],'path':folder.relative_to(BENCH).as_posix(),'pilot':i==1,'reference_status':'source_answer_not_independently_verified','input_status':'pending','source_sha256':digest(json.dumps(q,ensure_ascii=False,sort_keys=True).encode())}
            dump(folder/'source.json',q);records.append(record)
    related=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            if a['subject']!=b['subject']:continue
            score=family_similarity(normalized(questions[a['source_id']]),normalized(questions[b['source_id']]))
            if score>.62:related.append({'a':a['id'],'b':b['id'],'text_similarity':round(score,3),'requires_review':True})
    audit['near_duplicate_candidates']=related
    audit['limitations']=['物理题池偏运动学；波动主题缺失，光学与热学各仅一题，开发与测试不保证同主题覆盖。','化学实验题较多；有机合成、元素周期律等未充分覆盖。','题型与来源分布受原题池限制；36题只用于小规模流程比较。','source reference 尚需人工核对，不能当作已经验证的标准答案。','旧实验椭圆/抛物线例题未直接入选；语义变式仍需人工复核。']
    async with httpx.AsyncClient(timeout=45,follow_redirects=False,trust_env=False) as client:
        for record in records:
            folder=BENCH/record['path'];q=questions[record['source_id']];assets={}
            try:
                for field,filename in [('stem','stem.html'),('answer','reference/answer.html'),('explanation','reference/explanation.html')]:
                    out=folder/filename;out.parent.mkdir(exist_ok=True)
                    if out.exists():continue
                    out.write_text(await local_html(q[field],folder,client,sem,assets),encoding='utf-8')
                if not (folder/'input.png').exists():
                    (folder/'input.png').write_bytes(await render_html((folder/'stem.html').read_text()))
                record['input_status']='ready';record['input_sha256']=digest((folder/'input.png').read_bytes())
                dump(folder/'reference/checks.json',{'status':'pending_human_verification','items':['核对所有小问及选择题选项','检查公式、图形、单位与原题是否完整','核对参考答案、边界与推导；发现疑问先记录，不强行评分'],'visualization_focus':record['selection_reason']}) if not (folder/'reference/checks.json').exists() else None
                record['reference_status']=json.loads((folder/'reference/checks.json').read_text()).get('status','pending_human_verification')
                if assets:dump(folder/'assets.json',assets)
                print(record['id'],'ready',flush=True)
            except Exception as exc:
                record['input_status']='error';record['error']=str(exc)[:300];print(record['id'],'ERROR',str(exc)[:200],flush=True)
            dump(folder/'question.json',record)
    manifest={'version':'v1-candidate','status':'candidate_pending_reference_and_family_review','bank_sha256':digest((ROOT/'unique-questions.json').read_bytes()),'selection_method':'explicit coverage quotas with local frequency and visualization diversity; no model calls','questions':records,'generation_reference_access':False,'test_policy':'holdout must be explicitly unlocked after workflow and judge freeze'}
    dump(BENCH/'manifest.json',manifest);dump(BENCH/'coverage.json',audit)
    print('ready',sum(r['input_status']=='ready' for r in records),'/',len(records))
if __name__=='__main__':asyncio.run(main())
