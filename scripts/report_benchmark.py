"""Summarize local pilot evidence without making additional model requests."""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from app.benchmark import BENCH, read, write


def main():
    db=sqlite3.connect(BENCH/'runs/artifacts/history.sqlite3')
    db.row_factory=sqlite3.Row
    samples={s['task_id']:s for s in read(BENCH/'evaluation/samples.json',[])}
    jobs=read(BENCH/'runs/jobs.json',{})
    records=[]
    for key,tid in jobs.items():
        task=dict(db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        calls=[dict(r) for r in db.execute('SELECT * FROM api_calls WHERE task_id=? ORDER BY id',(tid,))]
        rounds=[dict(r) for r in db.execute('SELECT number,html_name,passed,summary FROM rounds WHERE task_id=? ORDER BY number',(tid,))]
        sample=samples.get(tid,{})
        known_cache=0;known_fields=0
        for call in calls:
            for field,price in [('cached_input_tokens',1.44),('cache_write_input_tokens',5.76)]:
                if type(call.get(field)) is int:
                    known_cache+=call[field]*price/1_000_000;known_fields+=1
        records.append({'job':key,'task_id':tid,'status':task['status'],'sample_id':sample.get('id'),
            'selected_round':sample.get('round_number'),'round_count':len(rounds),'rounds':rounds,
            'api_seconds_including_failures':sum(c['duration_seconds'] for c in calls),
            'known_input_tokens':sum(c.get('input_tokens') or 0 for c in calls),
            'known_output_tokens':sum(c.get('output_tokens') or 0 for c in calls),
            'known_total_tokens':sum(c.get('total_tokens') or 0 for c in calls),
            'calls_missing_usage':sum(c.get('total_tokens') is None for c in calls),
            'failed_api_calls':sum(c['status']!='completed' for c in calls),
            'known_cache_cost_cny':round(known_cache,6) if known_fields else None,
            'api_calls':calls})
    db.close()
    write(BENCH/'reports/pilot_metrics.json',{'updated_at':datetime.now(timezone.utc).isoformat(),
        'note':'运行记录含内部评分；不展示在盲评页面。已知缓存费不是总费用，缺失用量不是零。','runs':records})
    lines=['# 首批 Benchmark 运行记录','',
        '本报告只记录 DeepSeek workflow 的过程与可复现数据，质量结论等待真实人工评分。独立 DeepSeek/GPT judge 另行统计，不计入本表。',
        '数理化各一道开发题，W0 单轮、W1 三轮整页返修、W2 三轮局部修订。全流程使用 deepseek-v4-1-flash-261017；同科使用相同题图、基础生成和审核 Prompt，推理强度均 high。',
        '首批用于校准评分界面与 rubric，不代表三科总体水平；同题三个 HTML 不算三个独立题目。',
        '',
        '| 任务 | 状态 | 已完成轮次 | 选中轮次 | 匿名页面 | 已知 token 总量 | API 累计秒数 | 缺用量调用 |',
        '| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |']
    for r in records:
        lines.append(f'| {r["job"]} | {r["status"]} | {r["round_count"]} | {r["selected_round"] or "—"} | {r["sample_id"] or "—"} | {r["known_total_tokens"]:,} | {r["api_seconds_including_failures"]:.1f} | {r["calls_missing_usage"]} |')
    lines+=['','## 运行中发现的问题',
        '- WSL 对内部网关域名存在间歇性 DNS 解析失败。首次连接失败保留在日志；后续 runner 使用实际解析得到的内部地址作进程内覆盖，原 hostname/SNI 保留，没有修改系统 DNS，也没有改动 8001。',
        '- 初次完整 HTML 生成的推理输出较多。已保存实际用量，不能只按 HTML 字符数估算 token；high 模式统一保持，避免混入新的比较变量。',
        '- 失败请求没有返回 usage 时计为未知，不能当作免费。API 累计时间包含失败调用与请求内重试，且多任务并发，因此不是整个实验的墙钟耗时。',
        '- 自动交互检查只尝试首个可用步骤按钮与可见滑块，缺少控件时明确标记 skipped；没有覆盖所有动画、拖拽或边界，最终仍须人工操作。',
        '- 原题参考答案保存并给出检查状态，首批三题做了初步演算，尚未人工审定。参考疑问会排除出 judge 对比。',
        '',
        '## 评分后如何比较',
        '先在 8002/benchmark 人工评完，再运行独立 judge；judge 只能看到题目、参考资料、页面及操作证据，看不到人工分数。比较五维 MAE、综合分误差、同题排序和严重错误漏判；另按题目划分验证组，不能仅凭校准样本选择最终评委。',
        '',
        '## 费用口径',
        '按用户提供的人民币缓存读取 ¥1.44 / MTok、写入 ¥5.76 / MTok，仅对已返回字段计算缓存小计。普通输入与输出单价未知，故不报伪精确总费用。逐次用量、耗时、已知缓存费在 pilot_metrics.json。']
    (BENCH/'reports/pilot.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'runs':len(records),'samples':len(samples),'known_tokens':sum(r['known_total_tokens'] for r in records)},ensure_ascii=False))

if __name__=='__main__':main()
