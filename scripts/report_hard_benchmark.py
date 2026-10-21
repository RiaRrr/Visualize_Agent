"""Report the hard extension and controlled judge comparison, without API calls."""
from __future__ import annotations
import collections, itertools, json, sqlite3
from datetime import datetime, timezone
from app.benchmark import BENCH, read, write
from app.pricing import estimate_cost
from app.rubric import SCORE_KEYS

def score_metrics(rows, key):
    pairs=[(r['human'][key],r['model'][key]) for r in rows if r['human'].get(key) is not None and r['model'].get(key) is not None]
    if not pairs:return {'n':0}
    n=len(pairs);human=collections.Counter(h for h,j in pairs);judge=collections.Counter(j for h,j in pairs)
    # 1-D Wasserstein/earth mover distance on the original integer scale.
    distance=sum(abs(sum(human[i] for i in range(k+1))/n-sum(judge[i] for i in range(k+1))/n) for k in range(5))
    ordering=[];ties=[]
    for a,b in itertools.combinations(rows,2):
        if a['question_id']!=b['question_id']:continue
        values=[a['human'].get(key),b['human'].get(key),a['model'].get(key),b['model'].get(key)]
        if None in values:continue
        h1,h2,j1,j2=values
        if h1!=h2:ordering.append((h1-h2)*(j1-j2)>0)
        else:ties.append(j1==j2)
    return {'n':n,'human_mean':sum(h for h,j in pairs)/n,'judge_mean':sum(j for h,j in pairs)/n,
            'mae':sum(abs(h-j) for h,j in pairs)/n,'bias':sum(j-h for h,j in pairs)/n,
            'human_histogram':dict(sorted(human.items())),'judge_histogram':dict(sorted(judge.items())),
            'distribution_wasserstein':distance,'same_question_unequal_pairs':len(ordering),
            'pairwise_order_agreement':sum(ordering)/len(ordering) if ordering else None,
            'same_question_human_ties':len(ties),'tie_agreement':sum(ties)/len(ties) if ties else None}

def money(value):
    return f"{value:.4f}" if value is not None else "未知"

def main():
    protocol=read(BENCH/'evaluation/calibration-v3/protocol.json');comparisons={};judge_calls=[]
    for label in ('baseline','calibrated'):
        version=protocol[label+'_version'];rows=[];excluded=[];all_rows=[]
        for sid in protocol['validation_sample_ids']:
            r=read(BENCH/'evaluation/judges/gpt_56'/version/(sid+'.json'),{})
            if r.get('status')!='completed':continue
            h=read(BENCH/'evaluation/human/primary'/(sid+'.json'),{})
            if h.get('status')!='submitted' or h.get('html_sha256')!=r['html_sha256']:continue
            judgment=r['judgment']
            item={'sample_id':sid,'question_id':r['question_id'],'human':{**h['scores'],'overall_score':h['overall_score']},
                  'model':{**judgment['scores'],'overall_score':judgment['overall_score']},'reference_concern':judgment.get('reference_concern','')}
            all_rows.append(item)
            if judgment.get('reference_concern') or '参考答案疑问' in h.get('flags',[]):
                excluded.append({'sample_id':sid,'reason':judgment.get('reference_concern') or '人工参考疑问'});continue
            rows.append(item)
        comparisons[label]={'version':version,'rows':rows,'excluded':excluded,
                            'all_rows':all_rows,'all_metrics':{key:score_metrics(all_rows,key) for key in (*SCORE_KEYS,'overall_score')},
                            'metrics':{key:score_metrics(rows,key) for key in (*SCORE_KEYS,'overall_score')}}
    # Report matched samples separately so reference exclusions cannot manufacture gains.
    common=set(r['sample_id'] for r in comparisons['baseline']['rows'])&set(r['sample_id'] for r in comparisons['calibrated']['rows'])
    for item in comparisons.values():
        item['matched_metrics']={key:score_metrics([r for r in item['rows'] if r['sample_id'] in common],key) for key in (*SCORE_KEYS,'overall_score')}
    samples=[s for s in read(BENCH/'evaluation/samples.json',[]) if s.get('batch')=='hard-v1']
    db=sqlite3.connect(BENCH/'runs/artifacts/history.sqlite3');db.row_factory=sqlite3.Row
    runs=[];all_calls=[]
    for key,tid in read(BENCH/'runs/jobs.json',{}).items():
        if not key.startswith('hard-v1_'):continue
        calls=[dict(r) for r in db.execute('SELECT * FROM api_calls WHERE task_id=? ORDER BY id',(tid,))];all_calls.extend(calls)
        task=dict(db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        selected=next((s for s in samples if s['task_id']==tid),{})
        rounds=[dict(r) for r in db.execute('SELECT number,html_name,passed,summary FROM rounds WHERE task_id=? ORDER BY number',(tid,))]
        evaluation=read(BENCH/'evaluation/judges/gpt_56'/protocol['calibrated_version']/(selected.get('id','pending')+'.json'),{})
        runs.append({'job':key,'task_id':tid,'status':task['status'],'sample':selected,'rounds':rounds,
                     'api_seconds':sum(c['duration_seconds'] for c in calls),
                     'successful_api_seconds':sum(c['duration_seconds'] for c in calls if c['status']=='completed'),
                     'failed_api_seconds':sum(c['duration_seconds'] for c in calls if c['status']!='completed'),
                     'stage_metrics':{role:{'calls':len([c for c in calls if c['role']==role]),
                        'seconds':sum(c['duration_seconds'] for c in calls if c['role']==role),
                        'known_tokens':sum(c.get('total_tokens')or 0 for c in calls if c['role']==role),
                        'cost':estimate_cost([c for c in calls if c['role']==role])} for role in {c['role'] for c in calls}},
                     'known_tokens':sum(c.get('total_tokens') or 0 for c in calls),
                     'calls_missing_usage':sum(c.get('total_tokens') is None for c in calls),
                     'cost':estimate_cost(calls),'judgment':evaluation.get('judgment'),
                     'judge_status':evaluation.get('status','pending')})
    db.close()
    for version in (protocol['baseline_version'],protocol['calibrated_version']):
        for p in (BENCH/'evaluation/judges/gpt_56'/version).glob('*/*.attempt-*.json'):
            a=read(p);judge_calls.append({'provider':'openai','model':'gpt-5.6-sol',**(a.get('usage')or{}),'duration_seconds':a.get('duration_seconds',0)})
    totals={'deepseek':estimate_cost(all_calls),'gpt':estimate_cost(judge_calls),
            'deepseek_known_tokens':sum(c.get('total_tokens')or 0 for c in all_calls),
            'gpt_known_tokens':sum(c.get('total_tokens')or 0 for c in judge_calls),
            'gpt_calls':len(judge_calls),'gpt_api_seconds':sum(c['duration_seconds'] for c in judge_calls)}
    report={'updated_at':datetime.now(timezone.utc).isoformat(),'protocol':protocol,'comparisons':comparisons,'runs':runs,'totals':totals}
    write(BENCH/'reports/hard-v1.json',report)
    lines=['# 综合题扩充与 Judge 校准实验（2026-10-21）','',
           '原36题的题干、split、21轮HTML和人工评分保留；审计中另修复两张未运行留出题的空白题图。新增数理化各2题，共42题：21开发题、21留出题。原有机/力学覆盖的局限仍在，不代表全国考题分布。',
           '本批仅运行math_013、physics_013、chemistry_013；各科014留出未运行。原题、公式图片、参考答案和解析全部本地化。题库参考解仍待人工审定。',
           '','## 本批流程与控制变量','',
           'DeepSeek deepseek-v4-1-flash-261017直接看原题图片，无OCR、无GPT参与生成。比较W0单轮（也记录内部审核）和W2固定3轮局部修订。输入图、学科生成/审核prompt、high推理、64000输出上限和900秒超时一致；唯一流程差异为修订。首轮独立采样，尚不能完全排除随机性。',
           '表中failed表示已按轮次正常结束但未通过内部审核；error才表示接口/运行尚未完成，不能混同。',
           '生成与审核prompt冻结在workflows/hard-v1/；本次没有修改生成prompt。与pilot-v1的内部审核标准已不同，不能把两批当成只改变题目的一次严格对照。所有轮次保留，内部综合分最高轮入选，同分取后轮；独立GPT不参与选轮或修订。',
           '', '| 题目/流程 | 完成轮数 | 选中轮 | 页面 | GPT综合 | 已知token | API累计秒 | DeepSeek估算¥ |',
           '| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |']
    for r in runs:
        s=r['sample'];sid=s.get('id','待完成');link=f'[打开 {sid}](http://localhost:8002/benchmark?sample={sid})' if s else sid
        lines.append(f"| {r['job']} | {len(r['rounds'])} | {s.get('round_number','—')} | {link} | {(r.get('judgment')or{}).get('overall_score','待评')} | {r['known_tokens']:,} | {r['api_seconds']:.1f} | {money(r['cost']['known_cost_cny'])} |")
    lines+=['','## 新综合题的逐轮观察','']
    for r in runs:
        lines+=['',f"### {r['job']}（{r['status']}）"]
        for rnd in r['rounds']:lines.append(f"- 第{rnd['number']}轮内部审核：{rnd['summary']}")
        lines.append(f"成功API累计{r['successful_api_seconds']:.1f}秒，失败/中断等待累计{r['failed_api_seconds']:.1f}秒；阶段token与费用见同名JSON的stage_metrics。不要把连接异常等待当作模型正常推理速度。")
        j=r.get('judgment')
        if j:
            lines.append('独立GPT：'+j.get('summary',''))
            lines.append('五维分数：'+json.dumps(j['scores'],ensure_ascii=False)+'；综合分：'+str(j['overall_score']))
            if (j['scores'].get('teaching_effectiveness')or 0)<=2 and (j.get('overall_score')or 0)>=4:
                lines.append('评分冲突提示：教学维度≤2但独立综合call≥4。分开调用避免串分，也可能导致判定不一致；这里保留原始分，不人为改成看似一致。')
        elif r['sample']:lines.append('已有页面，独立GPT评分待完成；未填造人工评分。')
        else:lines.append('暂无可评价的完整轮次；接口错误不记为作品0分。')
    lines+=['','## Judge：相同六个页面的prompt对照','',
            '仅使用math_001的3份人工标注归纳评分尺度，基于页面证据而非分数配额；物理和化学原6份页面作回顾性验证。两组都是同一个GPT、五维各一次＋综合一次，互不传分。当前页面的人工评分不进入输入。旧联合评分不作为此对照的baseline。',
            '六份页面只有两道独立题目，旧分数此前可见，因此不是严格盲测。新大题还没有人工评分；不能用GPT自评证明人机一致。原人工值不改，模型结果按prompt hash分别保存。',
            '各版本每维只有一次成功采样，模型随机性仍在；小幅分数差异不能单独归因为prompt，也不构成稳定优劣结论。',
            '', '| 维度 | 原版MAE | 校准版MAE | 原版偏差 | 校准版偏差 |', '| --- | ---: | ---: | ---: | ---: |']
    names=['一致性','正确性','页面效果','交互效果','教学效果','综合分']
    for key,name in zip((*SCORE_KEYS,'overall_score'),names):
        a=comparisons['baseline']['matched_metrics'][key];b=comparisons['calibrated']['matched_metrics'][key]
        def fmt(x):return f'{x:.3f}' if x is not None else '待完成'
        lines.append(f"| {name} | {fmt(a.get('mae'))} | {fmt(b.get('mae'))} | {fmt(a.get('bias'))} | {fmt(b.get('bias'))} |")
    for label in ('baseline','calibrated'):
        item=comparisons[label];m=item['matched_metrics']['overall_score']
        lines+=['',f"{label}版本 `{item['version']}`；可配对页面{m['n']}份。综合分统计：`{json.dumps(m,ensure_ascii=False)}`。排除项：`{json.dumps(item['excluded'],ensure_ascii=False)}`。"]
    lines+=['','MAE越小越接近人工；bias正值表示偏宽。分布距离为整数分值上的一维Wasserstein距离；同题排序只比较人工非并列样本，并单独记录人工并列保持率。若没有足够样本，只报告缺失，不以0冒充。',
            '', '## 成本与已知限制','',
            f"本批DeepSeek已知{totals['deepseek_known_tokens']:,} token、约¥{money(totals['deepseek']['known_cost_cny'])}；GPT新增{totals['gpt_calls']}次调用、已知{totals['gpt_known_tokens']:,} token、约¥{money(totals['gpt']['known_cost_cny'])}。不含旧pilot费用，失败无usage记未知。",
            '人民币输入/输出：DeepSeek每百万1.20/4.80元，GPT为5/30元；缓存和推理子项不重复计费。API累计耗时不是并行实验墙钟时间。',
            '自动操作只覆盖有限步骤/滑块；完整源码与截图都给judge，但仍可能漏查深层状态。综合题会放大遗漏小问、跳步解释、图形与变量脱节等问题，需人工逐小问检查。',
            '', '## 全部回顾样本的原始分数（含参考疑问，不当作已核实真值）','',
            '| 页面 | 人工综合 | 原版GPT | 校准版GPT |', '| --- | ---: | ---: | ---: |']
    old={r['sample_id']:r for r in comparisons['baseline']['all_rows']};new={r['sample_id']:r for r in comparisons['calibrated']['all_rows']}
    for sid in protocol['validation_sample_ids']:
        h=read(BENCH/'evaluation/human/primary'/(sid+'.json'),{})
        lines.append(f"| {sid} | {h.get('overall_score')} | {old.get(sid,{}).get('model',{}).get('overall_score','待完成')} | {new.get(sid,{}).get('model',{}).get('overall_score','待完成')} |")
    lines+=['', '全体六份页面的描述统计（不因judge的参考提示筛掉样本）：',
            '| 版本 | 页数 | 人工均分 | GPT均分 | 综合MAE | 同题非并列排序一致 | 人工并列保持 |',
            '| --- | ---: | ---: | ---: | ---: | --- | --- |']
    for label in comparisons:
        m=comparisons[label]['all_metrics']['overall_score']
        if m['n']:
            lines.append(f"| {label} | {m['n']} | {m['human_mean']:.2f} | {m['judge_mean']:.2f} | {m['mae']:.2f} | {m['pairwise_order_agreement']}（{m['same_question_unequal_pairs']}对） | {m['tie_agreement']}（{m['same_question_human_ties']}对） |")
    lines+=['','| 综合分 | 人工页数 | 原版GPT页数 | 校准版GPT页数 |','| --- | ---: | ---: | ---: |']
    for score in range(6):
        a=comparisons['baseline']['all_metrics']['overall_score'];b=comparisons['calibrated']['all_metrics']['overall_score']
        lines.append(f"| {score} | {a.get('human_histogram',{}).get(score,0)} | {a.get('judge_histogram',{}).get(score,0)} | {b.get('judge_histogram',{}).get(score,0)} |")
    lines+=[
            '部分reference_concern只是理想化模型说明，未必是真正参考错误，因此同时公开全体与严格排除口径；不能通过排除低一致样本制造改进。',
            '本次文字档位校准没有证明人机一致性改善：GPT仍会因源码实现完整、方程正确而高估学生端的教学效果。先保留两版结果，不继续围绕这两道验证题调到分数一致。下一步增加真正低/中/高质量的人工锚点及具体扣分理由，再在新综合题上验证。新prompt属于实验候选，不代表已经校准成功。',
            '', '## 实际排查与修复','',
            '1. 有机题自包含HTML约1.86MB，转为data URL后超过浏览器地址长度限制，初次截图全白。改用Page.setDocumentContent，补白图检查；修复在化学生成开始前完成，实际模型题图核对哈希。',
            '额外检查发现原留出题physics_012、chemistry_008题图也是白图，已依据原始stem重新渲染；原题内容不变，未运行模型，修复前后哈希在extensions/hard-v1_input_fixes.json。',
            '2. 数学参考解析含82张公式图片，普通data导航加载超时；参考材料改为直接文档注入并等待60秒。化学解析长达15689像素，原5000像素限制无法截图；参考资料允许30000像素并分段送评，生成页面的常规限制不变。',
            '3. GPT评分出现连接/请求超时，保留已成功的维度和费用，仅补失败维度。原先同步扫描文件生成报告会阻塞请求事件循环，现改到后台线程。',
            '4. 数学W0首次生成用完64000输出token（包含推理），失败用量已计入。原流程同预算重试随后成功，未启用128000预算恢复；另提供显式恢复命令，保留原配置并标记参数变化，供再次遇到预算截断时使用。',
            '5. 多个请求长时间停滞，停止本次实验进程后从检查点续跑；8001/8002服务未重启。增加请求总时限，中断且未返回usage的费用未知。',
            '6. 主DeepSeek凭据返回HTTP402，明确为分配额度耗尽。使用既有备用凭据续跑同一网关与同一模型，未修改.env；request文件记录credential_alias。402后不再重复请求同一凭据，初次发现时的失败记录仍保留。',
            '', '## 下一步','',
            '在8002中给新增页面匿名评分，重点标注哪里读不懂、哪一小问遗漏。随后用冻结的judge计算新题人机误差和同题排序。不要根据新题人工分反复调prompt后仍称它们为未见测试；进一步修改时另留验证题。']
    (BENCH/'reports/hard-v1.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'runs':len(runs),'samples':len(samples),'totals':totals},ensure_ascii=False))

if __name__=='__main__':main()
