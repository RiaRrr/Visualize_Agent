# Benchmark：数据、实验与浏览入口

## 2026-10-21 综合题扩展与Judge校准
现有题目共42道：原36道不变，新增数理化各2道综合题；开发集21道、留出集21道。新增题图、原题和参考答案均本地保存。按原题库等级，新增5题为较难/困难；physics_014为“一般”但包含分阶段动力学与图像反推。物理新增仍偏力学，不宣称全面覆盖难题。
| 题目 | 内容 | 用途 |
| --- | --- | --- |
| math_013 | 导数、恒成立、双零点与不等式证明 | 本批开发评测 |
| math_014 | 椭圆面积、投影、斜率约束与定圆 | 留出，未运行 |
| physics_013 | 停车边界、多阶段追及与两次相遇 | 本批开发评测 |
| physics_014 | 斜面—水平面运动、摩擦与受力反推 | 留出，未运行 |
| chemistry_013 | 七小问有机合成、官能团、异构与合成迁移 | 本批开发评测 |
| chemistry_014 | 制备、气体净化干燥、装置连接与验证 | 留出，未运行 |
本批使用DeepSeek 261017图片直传，W0单轮和W2固定三轮局部修订；独立评分用GPT 5.6 sol，五维与综合分共六次互不传分的call。各轮、选中页面、用量、费用、缺陷及当前完成状态见 [hard-v1报告](reports/hard-v1.md)。新增页面出现在同一个8002评分界面，仍待人工评分；不会自动代填。
| 新开发题 | W0 单轮页面 | W2 三轮修订页面 |
| --- | --- | --- |
| math_013 | [B9A724ED8](http://localhost:8002/benchmark?sample=B9A724ED8) | [B03715FF7](http://localhost:8002/benchmark?sample=B03715FF7) |
| physics_013 | [BF326FBCC](http://localhost:8002/benchmark?sample=BF326FBCC) | [B936A825C](http://localhost:8002/benchmark?sample=B936A825C) |
| chemistry_013 | [B6AA79355](http://localhost:8002/benchmark?sample=B6AA79355) | [B63B46592](http://localhost:8002/benchmark?sample=B63B46592) |

Judge新版按人工案例归纳档位，强调可见证据、相邻档区别与同题相对质量；不强制分数分布、不输入目标页面人工分。数学旧题用于尺度校准，物理/化学旧题作回顾性验证，未声称盲测；新题需补人工分再测泛化。
复现已有结果只需按下文启动8002，不需模型Key。以下命令会产生付费调用，仅在有自己的账号时运行：
```bash
python -m scripts.run_benchmark --batch hard-v1 --questions math_013 physics_013 chemistry_013 --workflows W0 W2 --concurrency 2
python -m scripts.evaluate_benchmark --score-only --judges gpt_56 --samples SAMPLE_ID --prompt-bundle benchmark/evaluation/calibration-v3/calibrated_prompts.json
python -m scripts.report_hard_benchmark
```
`SAMPLE_ID`换为报告中具体编号。已有完成任务与维度评分会复用。批次配置冻结，改prompt或模型须用新的`--batch`；不能用同一个版本名覆盖实验。不要运行`prepare_benchmark`重建原36题来复现，完整原题库没有随仓库分发。
以下“36题”“9份页面”等数字若用于pilot-v1，均指原始首批快照；新扩展单独统计。

这是一套用于比较“题图→交互式HTML”流程的小型数理化测试集。当前是 **v1-candidate**，参考答案和题族仍需复核，不是已全面审定的标准答案集。
## 先启动，看看包含什么
从克隆后的仓库根目录执行（安装步骤见[根README](../README.md)）：
```bash
python -m uvicorn app.benchmark:standalone --host 127.0.0.1 --port 8002
```
打开 **http://127.0.0.1:8002/benchmark**。无需API Key、数据库服务或原作者的本地文件，无模型收费调用；已保存的题图、HTML、截图、参考解和评分已随仓库提供。
- **GPT评分**：当前9份页面的五维分、综合分、理由、用量、耗时；点击题目/方案进入对应页面。新旧评分会按版本标识。
- **人工评分**：查看原题、参考答案和解析、真实HTML预览及操作截图；可操作滑块、按钮，填写五维1–5分和综合0–5分，保存草稿或提交。已保存评分在 `primary` 编号下；新评分者请使用自己的编号。
- **题目浏览**：36道题、学科、知识点、开发/留出划分与入选理由。
- **workflow**：各方案的说明与实现状态。登记了方案不表示所有方案已完成本批实验。
`web/benchmark.html` 依赖本地服务，不要直接双击它。**生成的解题HTML可单独用浏览器打开**，位置由 `evaluation/samples.json` 的 `html` 相对路径指出；该路径相对于 `benchmark/`。需要方便查找时看 [汇总表](reports/分享汇总.md)，其中每行都有HTML文件链接。GitHub只展示HTML源码，需要下载/clone后打开，或用上面的本地服务预览。
示例：化学W1为 http://127.0.0.1:8002/benchmark?sample=B50DFB016 。端口被占用时将启动命令和浏览器地址中的8002同时改成其他端口。
## 数据怎么选、怎么构造
- 从去重题库1875题中选36道：数学题池1719、物理69、化学87；数理化各选12道。不是按原题库比例随机抽样，而是按知识主题名额、题型和可视化表达方式选题，参考本地知识点频率。
- 每科开发6题、留出6题，总开发18、留出18；划分见 `manifest.json`。文本相似度只做辅助筛查，不能证明没有同题族泄漏。
- 每题保留题库原文、题干HTML、公式/图形资源及参考答案解析；本地渲染成统一题图 `input.png`，不是先用模型OCR生成题干。记录来源ID、入选理由与文件哈希。
- 原始完整题库不随仓库分发；已经选好的36题及所需资源已提供，**浏览不需要先运行prepare脚本**。
- 物理波动、化学有机等主题覆盖不足，参考解并非全部人工审定。具体见 [coverage.json](coverage.json)。
## 做了什么实验，用什么模型
首批仅运行 `math_001`（二次函数值域）、`physics_001`（匀减速运动）、`chemistry_001`（导电能力与离子反应）3道开发题，其他33题尚未跑这批workflow，留出集未用于调参。
| 方案 | 流程 | 轮次 | 生成/内部审核模型 |
| --- | --- | ---: | --- |
| W0 | 原图→HTML→检查/渲染→审核记录，不返修 | 1 | deepseek-v4-1-flash-261017 |
| W1 | 原图→HTML→审核→完整HTML重写 | 固定3 | 同上 |
| W2 | 原图→HTML→审核→精确代码补丁→完整HTML | 固定3 | 同上 |
9任务共保存21轮HTML，每任务按内部综合分最高选一轮，同分选后轮，得到9份评分页面。不是固定展示最后一轮，例如数学W1选中第2/3轮。
同科题图、基础生成和审核Prompt、high推理、64k输出上限统一；没有OCR，没有GPT参与生成，没有参考答案进入生成/内部审核。各组首轮独立采样，因此小样本差异同时含生成随机性，不能完全归因于返修方案。W3规划流程只登记为后续方案，不计入本批结果。
独立评分用 **gpt-5.6-sol**，看原题＋参考答案解析＋完整HTML源码＋操作前后截图与记录。现有9份GPT结果来自一次联合评分的旧版本；用户已完成这9份人工评分。另有3份来自2道历史题的人工评分归档，单独标为 `historical_human`，不混进首批统计。
最新教学标准强调“具备高中基础、刚读题但不会做题的学生能否循序渐进理解”，新judge拆为五维各一次＋综合一次，共6个互不传评分的call。**代码已实现，新版本尚未重新评分。** 详情见 [evaluation/README.md](evaluation/README.md)。
## benchmark/下面是什么
| 路径 | 内容 |
| --- | --- |
| `manifest.json` | 36道题清单、学科/知识点、开发/留出划分、来源与哈希 |
| `coverage.json` | 题库频率、主题覆盖与缺口、相似文本检查 |
| `math/`、`physics/`、`chemistry/` | 每题一个目录；`question.json`为题目元数据，`source.json`为题库原文，`stem.html`为题干，`input.png`为生成输入，`assets/`为本地资源，`reference/`为答案、解析及核查备注 |
| `workflows/registry.json` | 当前与历史方案登记、模型和实现状态 |
| `workflows/pilot-v1/` | 本批各方案、各学科的冻结配置和实际Prompt |
| `runs/artifacts/<任务ID>/` | 按时间戳保存的逐轮HTML、截图、审核、元数据和请求记录；`evidence/`为操作证据 |
| `runs/jobs.json`、`runs/artifacts/history.sqlite3` | 本批任务索引与数据库快照，供追溯/恢复；浏览界面主要读取JSON，不要求额外数据库服务 |
| `evaluation/samples.json` | 9份选中页面的ID→题目、workflow、轮次、相对HTML路径和哈希 |
| `evaluation/human/primary/` | 用户真实人工评分，`history/`保留修改记录，不用模型代填 |
| `evaluation/judges/<judge>/<version>/` | 模型评分、原始回复、Prompt版本、token与耗时 |
| `evaluation/*prompt.md`、`dimensions.json` | 当前独立judge的共同约束、逐维标准和输出要求 |
| `人工评分/`、`evaluation/historical_samples.json` | 两道历史题的3份页面、原始人工分、来源及独立参考解；与新题分组 |
| `rubric.md` | 当前五维和综合评分标准；旧标注保留原有版本字段，不回填新标准 |
| `reports/分享汇总.md` | 推荐先读：9份人工/GPT对照、HTML链接、token和人民币估算 |
| `reports/分享汇总.csv`、`分享汇总.json` | 可直接分析的明细表：五维、综合、轮次、模型用量、成本 |
| `reports/pilot_metrics.json` | DeepSeek逐任务/逐调用的原始用量与耗时 |
| `reports/gpt_scores.json` | 页面直接读取的GPT评分；保留版本与证据 |
模型产物保留原样，包含已发现缺陷，不能当作标准题解。例如化学示例的自由离子计数折线与题目电导曲线不是同一个量，这是需要评估的教学问题。
原始实验记录可能含当时运行环境的来源路径或DNS诊断信息，仅用于追溯；页面索引和资源加载使用仓库内相对路径，不依赖作者电脑的 `E:` 盘。运行中的临时目录、模型密钥、完整旧工作台历史和下载缓存不分发。
## 人工与GPT目前差异
当前9份已提交人工综合分均值约3.11，旧GPT均值约4.56，综合MAE约1.44；GPT在7/9份上高于人工分。说明旧judge需要进一步校准，不证明任一模型或workflow在三科上普遍最好。只有3个独立题目和单个评分者，不能把9份HTML当9道独立题。
## 用量与人民币费用
| 环节 | 已知token | 已知用量费用估算 |
| --- | ---: | ---: |
| DeepSeek 261017生成、内部审核、修订 | 1,620,461 | ¥6.0857 |
| GPT-5.6-sol旧联合评分 | 259,160 | ¥1.6678 |
| 合计 | 1,879,621 | ¥7.7535 |
单位为人民币/百万token：DeepSeek输入1.20、输出4.80；GPT输入5、输出30。缓存和推理token均不重复计费，不另设缓存折扣。另有16次失败调用没有usage，费用未知；以上是按用户给定单价的估算，不是完整账单。API耗时是逐次调用累计，不等于并行实验的墙钟时间。
## 更新汇总、重新生成或评分
以下两条只读已有数据并更新报告，不调用模型：
```bash
python -m scripts.report_benchmark
python -m scripts.export_benchmark_summary
```
重新独立评一个页面（会收费；先按根README配置自己的GPT网关与Key）：
```bash
python -m scripts.evaluate_benchmark --score-only --judges gpt_56 --samples B50DFB016 --concurrency 1
```
新版本每页面6次调用，成功的维度会复用，失败项可续跑；旧版本评分不会覆盖。默认传递已保存证据，若缺参考截图需要Chrome重新渲染。
运行现有pilot生成器（会在新建/未完成任务上调用DeepSeek；要求Chrome与Key）：
```bash
python -m scripts.run_benchmark --concurrency 2
```
随仓库提供的9任务已完成，因此原样运行会复用已有任务；不要删掉历史数据库或 `jobs.json` 强行重跑。若要做新的比较实验，应先复制仓库/冻结新实验版本，保留原始评分样本。扩展指定开发题可用 `--questions math_003 physics_003 chemistry_003`；脚本拒绝留出题。历史冻结Prompt与当前新版审核要求不同，扩展实验须另标版本，不与pilot-v1混报。
只想浏览，不需要 `prepare_benchmark`。该准备脚本用于从原始题库重建，依赖未随仓库分发的大题库和下载资源。
## 后续计划
先用已有人工分校准新逐维judge，再扩展开发题、增加重复运行并按题划分验证组；冻结workflow和评委后再测留出集。用户可继续在本地页面人工评分，`primary`原始记录与新评分者分开保存。

题图完整性补充：hard-v1审计修复了原留出题`physics_012`、`chemistry_008`的空白截图，原题内容与split不变，未调用模型；哈希记录见`benchmark/extensions/hard-v1_input_fixes.json`（benchmark目录内路径去掉首层benchmark）。

### 实验中断与续跑
同一条生成命令会复用已完成任务、已保存轮次和待审核HTML；同一评分命令只补失败维度。遇到402先更换自己有额度的凭据，不继续重复请求。`--credential-alias alternate`只使用已配置的`VISUALIZE_EDUMIND_ALT_KEY`，不修改`.env`，请求记录只保存别名。
若任务明确因输出预算耗尽而处于error，可显式运行：
```bash
python -m scripts.recover_benchmark_tokens TASK_ID --max-tokens 128000
```
此命令保留原配置，生成新的恢复配置和日志；是否支持128000由网关决定。本次仅提供该恢复工具，实际64k截断的数学任务在原预算重试成功，未调用128k。参数改变后不应把结果宣称为完全相同预算的对照。
