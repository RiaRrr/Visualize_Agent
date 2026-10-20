"""Write a readable pilot report from saved GPT scores; no model requests."""
from app.benchmark import BENCH, read

def main():
    rows=read(BENCH/'reports/gpt_scores.json',{}).get('rows',[])
    metrics=read(BENCH/'reports/pilot_metrics.json',{}).get('runs',[])
    keys=['problem_fidelity','solution_correctness','page_quality','interaction_quality','teaching_effectiveness']
    names={'W0':'单轮生成','W1':'三轮整页返修','W2':'三轮局部修订'}
    rows.sort(key=lambda r:({'math':0,'physics':1,'chemistry':2}.get(r['question_id'].split('_')[0],3),r['question_id'],r['workflow_id']))
    lines=['# 首批 Benchmark 结果',
        f'当前已保存 {len(rows)}/9 份独立 GPT 评分。'+('首批已完成。' if len(rows)==9 else '实验仍在进行，下面不是完整结果。'),
        '模型分用于比较与排查，不当作人工真值。人工校准和双评委对比已按用户要求暂停。',
        '入口：http://localhost:8002/benchmark 。打开“GPT 评分”，点击题目/方案可查看实际 HTML。',
        '## 配置与边界',
        '数据集共 36 道候选题，数理化各 12 道，开发集和留出集各 18 道。首批仅运行每科 1 道开发题，不使用留出集调参。',
        '生成、内部审核与返修均为 deepseek-v4-1-flash-261017，原图直传，无独立 OCR 步骤，无 GPT 参与生成。已知学科直接使用冻结的学科 Prompt，不额外调用自动分流模型。',
        '同科题图、基础生成 Prompt、审核 Prompt、thinking=enabled、reasoning_effort=high、64k 输出上限固定。W0 一轮，W1/W2 三轮，差异为整页或局部修订。',
        '各组首轮分别调用模型，未固定随机种子或复用同一首轮 HTML。因此单次差异也包含生成随机性，本批用于跑通流程与观察问题，不把差异完全归因于修订方式。',
        'W0 也有一次流程内审核用于记录，但不把意见用于返修；它不是省掉审核成本的纯生成计费方案。',
        '最终页面按流程内部最高综合分选择，同分选后轮。GPT-5.6-sol 只评价这个最终选中页面，不参与选轮，不回传修改意见。',
        'GPT 输入为原题、参考答案和解析、完整 HTML/CSS/JavaScript、实际操作前后截图与记录，不含人工分和内部 reviewer 分。',
        '## GPT 独立评分',
        '| 题目 | 流程 | 选中/总轮数 | 一致性 | 正确性 | 页面 | 交互 | 教学 | 综合 | GPT 秒数 | GPT token |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        j=r['judgment'];score=j['scores'];usage=r.get('usage') or {}
        values=[r['question_id'],names.get(r['workflow_id'],r['workflow_id']),f'{r["selected_round"]}/{r["completed_rounds"]}',
            *[str(score.get(k) if score.get(k) is not None else '—') for k in keys],
            str(j.get('overall_score') if j.get('overall_score') is not None else '—'),f'{r["seconds"]:.1f}',str(usage.get('total_tokens','未知'))]
        lines.append('| '+' | '.join(values)+' |')
    lines+=['## 运行用量',
        '| 题目/流程 | 已完成轮数 | 已知 DeepSeek token | API 累计秒数（含失败） | 无 usage 调用 | 已知缓存费（人民币，非总价） |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in metrics:
        cost=r.get('known_cache_cost_cny')
        lines.append(f'| {r["job"]} | {r["round_count"]} | {r["known_total_tokens"]:,} | {r["api_seconds_including_failures"]:.1f} | {r["calls_missing_usage"]} | '+(f'¥{cost:.4f}' if cost is not None else '未知')+' |')
    lines+=['## 目前能得出的结论',
        '这是 3 道题上的试运行，尚不能判断哪种 workflow 在三科总体上最好，也不能把多轮页面数当成独立题数。',
        '数学三份页面的 GPT 评分全部为 5，出现评分集中于上限的情况；这表示本批评分没有区分出差异，不证明三个页面同样优秀。内部 reviewer 的扣分与 GPT 不完全一致，原始意见均保留，后续可以人工复核。',
        '本批没有中途调整生成或 judge Prompt 来追求更高分。后续应冻结下一版本，再扩大到其余开发题；留出题用于最终检验。',
        '网络异常与模型质量分开记录：WSL 曾无法稳定解析内部网关，使用进程内 DNS 覆盖后恢复。历史失败请求及重试保留；总 API 时间含这些开销，不是模型纯推理时间，也不是并发实验的墙钟总时间。',
        '缓存费只按已知人民币读 ¥1.44 / MTok、写 ¥5.76 / MTok 计入。普通输入/输出和 GPT 单价未知，不给出伪精确总费用。',
        '自动检查只覆盖有限按钮、滑块与截图状态，不保证所有交互正确。参考答案仍保留待人工复核状态。',
        '## 对应页面']
    for r in rows:lines.append(f'- {r["question_id"]} / {r["workflow_id"]}：http://localhost:8002/benchmark?sample={r["sample_id"]}')
    (BENCH/'reports/首批结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Report updated: {len(rows)}/9 GPT-scored pages')

if __name__=='__main__':main()
