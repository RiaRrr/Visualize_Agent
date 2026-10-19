"""Refresh the measured experiment table in 1019.md from saved task records."""
from __future__ import annotations
import json
from pathlib import Path
from app.config import ROOT, get_settings
from app.storage import Store

store=Store(get_settings())
records=[]
for p in sorted((store.root/"_1019").glob("202*.json")):
    info=json.loads(p.read_text())
    task=store.get(info['id'])
    if task:records.append((info,task))
lines=['<!-- measured-results -->','## 实测结果（自动从已保存任务汇总）',
       '| 题目 / 方案 | 轮数 / 状态 | 已报告输入 / 输出 tokens | 其中推理 tokens | API 累计耗时 | 历史任务 |',
       '|---|---|---:|---:|---:|---|']
total_in=total_out=total_time=0
for info,task in records:
    calls=task['api_calls'];inputs=sum(c.get('input_tokens') or 0 for c in calls)
    outputs=sum(c.get('output_tokens') or 0 for c in calls)
    reasoning=sum(c.get('reasoning_output_tokens') or 0 for c in calls)
    seconds=sum(c.get('duration_seconds') or 0 for c in calls)
    total_in+=inputs;total_out+=outputs;total_time+=seconds
    task_id=task['id']
    lines.append(f"| {info['case']} / {info['variant']} | {len(task['rounds'])} / {task['status']} | {inputs:,} / {outputs:,} | {reasoning:,} | {seconds:.1f} 秒 | [{task_id}](http://localhost:8001/?task={task_id}) |")
lines.append(f'本节合计：已报告输入 {total_in:,}、输出 {total_out:,} tokens；累计 API 调用耗时 {total_time:.1f} 秒。推理 token 已包含在输出中，不重复相加。失败请求若网关未回传 usage，费用未知，不能当作零收费。并行时累计 API 用时不等于墙钟用时。网关费率未知，暂不推算金额。')
for info,task in records:
    lines.append(f"### {info['case']} / {info['variant']} · {task['id']}")
    lines.append(task['message'])
    for r in task['rounds']:
        rev=r['model_review'];file=Path('data_v2')/task['id']/r['html_name']
        lines.append(f"第 {r['number']} 轮：[HTML]({file.as_posix()})；自动综合分 {rev.get('overall_score')}，自动判定 {'通过' if rev.get('passed') else '未通过'}。{rev.get('summary','')}")
        calls=[c for c in task['api_calls'] if c.get('round_number')==r['number']]
        for stage,label in [('generator','生成'),('reviewer','审核')]:
            stage_calls=[c for c in calls if c.get('role')==stage]
            if stage_calls:
                seconds=sum(c.get('duration_seconds') or 0 for c in stage_calls)
                inputs=sum(c.get('input_tokens') or 0 for c in stage_calls)
                outputs=sum(c.get('output_tokens') or 0 for c in stage_calls)
                lines.append(f'{label}：{seconds:.1f} 秒，输入/输出 {inputs:,}/{outputs:,} tokens（含有记录的重试）。')
        for issue in rev.get('issues',[])[:3]:lines.append('- '+issue.replace('\n',' ')[:600])
        if len(rev.get('issues',[]))>3:
            review_path=Path('data_v2')/task['id']/r['review_name']
            lines.append(f'其余意见见[完整审核记录]({review_path.as_posix()})。')
lines.append('<!-- /measured-results -->')
report=ROOT/'1019.md';text=report.read_text(encoding='utf-8')
start=text.find('<!-- measured-results -->');end=text.find('<!-- /measured-results -->')
block='\n'.join(lines)+'\n'
if start>=0 and end>=start:text=text[:start]+block+text[end+len('<!-- /measured-results -->'):].lstrip('\n')
else:text=text.rstrip()+'\n'+block
report.write_text(text,encoding='utf-8')
print(f'Updated {len(records)} tasks; {total_in+total_out:,} reported tokens')
