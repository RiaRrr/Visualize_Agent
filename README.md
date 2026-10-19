# 题目可视化工作台

10 月 19 日 DeepSeek 原图实验与推荐结果见 [1019.md](1019.md)。主页面新增“数学 · 分步 SVG 与教学示例（1019）”：选择 DeepSeek 时，生成/审核显式开启 high 推理，单次输出上限各 64,000；使用原图直传无需 OCR。实验使用的精确型号为 `deepseek-v4-1-flash-261017`。固定框架与局部 JSON 修订暂保留在 `scripts/run_1019.py` 的实验配置中，未隐式改变其他页面配置的生成流程。

上传一道题目的图片，生成可离线打开的 HTML 解答页，并由另一模型逐轮审核。最多返修 5 轮；每轮 HTML、截图和审核意见均留在本地。任务页面显示当前轮次、处理环节、等待时长和带时间戳的运行日志，历史任务也可查看日志。服务重启中断的任务可在原任务页点击“继续处理”，从已保存的轮次接着运行。

新任务还记录上传、排队、分流与每轮生成、检查、渲染、审核的环节用时，页面上方以时间轴展示；模型接口返回用量时记录输入和输出 token，未返回则明确标注“接口未提供”。旧任务没有可靠的分段用时或 token 记录时保持空缺。五轮均未通过的任务仍标为未通过，但默认预览模型综合分最高的草稿；同分取较晚一轮，缺分排后，全部缺分时取最后一轮。可下载最佳草稿，所有轮次也可手动切换；人工评分不改变自动选择。项目目前不折算 API 费用。

主页面可添加多行配置，用同一张题图比较不同 Prompt 方案、生成模型和审核模型。默认每组只运行一轮，也可选完整迭代。默认“自动识别学科”方案会对每张题图做一次数学、物理、化学或其他的短分类，并拼装相应 Prompt；另有三种数学指导、物理、化学与通用等显式方案用于对比，显式方案不调用分流模型。分类模型可在上传区选择。模型列表从各接口的目录读取；“短识图通过”只表示小图请求成功，不能保证长篇 HTML 请求成功。批量运行直接调用所选模型，不自动消耗额度做短识图测试；需要时可点击配置行中的按钮手动测试。历史页保存组合、Prompt 版本、模型接口、每轮文件和计时；同批次页面可并排预览结果。

调研时还可选“固定五轮”：即使中途审核通过，也继续改进到第 5 轮并保存每轮文件；有通过轮时最终文件取模型综合评分最高的一轮。普通“完整迭代”仍在审核通过时停止。模型接口出错会保留任务日志，之后可在任务页恢复；接口未返回完整 HTML 时无法形成该轮页面文件。

同一 Edumind 网关支持独立的 `edumind_alt` 密钥配置（`VISUALIZE_EDUMIND_ALT_KEY`），主页面会将它与原 `codex` 密钥分别列为模型接口。包含密钥、调用格式和实测模型状态的本地记录写在被 Git 忽略的 `api-capabilities.local.md`。

## 运行

需要 Python 3.10+ 和 Chrome/Chromium。Windows 建议在 WSL 中运行，在浏览器打开 `http://127.0.0.1:8000`。

```bash
cd /mnt/e/WORK/Visualize_Agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

项目根目录的 `.env` 当前配置为 `gpt-6-sol` 生成、`gpt-5.6-sol` 审核。未设置模型变量时，程序默认用 Claude Sonnet 4.6 生成、Claude Opus 4.6 审核。Claude 密钥优先使用 `VISUALIZE_ANTHROPIC_KEY` 或 `ANTHROPIC_AUTH_TOKEN`，未设置时从 `apikeys.md` 读取。Grok 等 Responses 兼容模型读取 `VISUALIZE_API_KEY` 或 `XAI_API_KEY`。密钥文件不会由网页提供。可选设置：

使用 `gpt-5.5` 等 OpenAI 模型时，服务端读取 `VISUALIZE_OPENAI_KEY`、`OPENAI_API_KEY` 或 `apikeys.md` 中 Codex 配置的 `OPENAI_API_KEY`，并通过 Responses 接口发送题图。OpenAI 密钥与 Grok 密钥分开使用。

`gpt-5-codex` 等名称中包含 `-codex` 的模型使用独立的网关配置。把 `VISUALIZE_CODEX_KEY` 和 `VISUALIZE_CODEX_BASE_URL` 放在项目根目录的 `.env` 中；该文件已列入 `.gitignore`。Chat Completions 的文本、图片、输出上限、响应解析和错误处理统一写在 [app/model_api.py](app/model_api.py)。若内部网关使用未安装到系统的 CA，可在 `.env` 中设 `VISUALIZE_CODEX_VERIFY_TLS=0`；安装可信 CA 后删去这项即可恢复证书校验。网关模型列表列出模型不代表其推理通道可用，启动任务前可用网页的模型检查功能验证图片调用。

当网关的 `gpt-5-codex` 推理通道可用时，将 `.env` 中的 `VISUALIZE_GENERATOR_MODEL` 改为 `gpt-5-codex` 并重启服务即可使用。接口调用遵循提供的 `POST /v1/chat/completions` 示例；图片作为 `messages[].content` 中的 `image_url` 数据 URL 发送。`max_completion_tokens` 控制生成上限。密钥仅由服务端读取。

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `VISUALIZE_BASE_URL` | `https://sub2api.minutex.cn/v1` | Responses 兼容接口地址 |
| `VISUALIZE_OPENAI_BASE_URL` | `https://sub2api.minutex.cn/v1` | OpenAI/Codex 模型的 Responses 接口地址 |
| `VISUALIZE_CODEX_BASE_URL` | `https://llm-gateway.edumind.ai/v1` | 独立 Codex 网关的 Chat Completions 接口地址 |
| `VISUALIZE_ANTHROPIC_BASE_URL` | `https://sub2api.minutex.cn` | Claude Messages 兼容接口地址 |
| `VISUALIZE_GENERATOR_MODEL` | `claude-sonnet-4-6` | 生成模型 ID |
| `VISUALIZE_GENERATOR_MAX_TOKENS` | `16000` | 单次生成的输出上限，可试 `8192` 或 `4096`；修改后重启服务 |
| `VISUALIZE_REVIEWER_MAX_TOKENS` | `6000` | 单次审核输出上限；推理模型输出截断时可提高 |
| `VISUALIZE_DESCRIPTION_MAX_TOKENS` | `3000` | OCR 审核路径中截图描述的输出上限 |
| `VISUALIZE_MODEL_TIMEOUT_SECONDS` | `300` | 单次模型请求的读写超时秒数，最多 `1800` |
| `VISUALIZE_DEEPSEEK_THINKING` | `default` | 可设为 `disabled`，避免 DeepSeek 长任务将输出额度全部消耗于推理；网关需支持该参数 |
| `VISUALIZE_REVIEWER_MODEL` | `claude-opus-4-6` | 审核模型 ID |
| `VISUALIZE_DATA_DIR` | 项目内 `data/` | 历史数据目录 |
| `VISUALIZE_USE_PROXY` | `0` | 设为 `1` 时使用系统代理访问模型网关 |

图片直传模式下，对应的生成或审核模型需要支持图片输入；OCR＋文字模式下，只有识图模型需要支持图片输入。使用自动分流时，分流模型也需要支持图片输入。模型接口不兼容或图片权限不足时，任务会显示具体错误。

## 本地文件

每次上传建立 `data/<上传时间戳>_<任务ID>/`。原图使用 `<上传时间戳>_input.<格式>`；每轮页面使用 `<生成时间戳>_round_01.html` 这样的文件名，同前缀保存 `.png` 截图和 `.review.json` 审核结果。审核通过后另存 `<时间戳>_final.html`。`data/history.sqlite3` 为网页历史索引；手工备份时请同时复制 `data/` 整个目录。

每个新配置任务还保存 `<时间戳>_config.json`，包含运行时的完整生成与审核 Prompt 快照、模型接口、模型 ID 和轮次上限；自动方案完成分流后会写入识别学科、分流模型以及实际使用的两份 Prompt。旧任务显示“配置未记录”。网页中的预览在隔离 iframe 中运行。模型生成的 HTML 仍属于自动生成内容，展示前请核对答案。

每轮 HTML 旁另存同前缀的 `.meta.json`，可从轮次卡片及同题对比卡片下载。它包含该轮的模型、Prompt 方案版本、自动审核结果、文件名和人工评分；`files` 内均为相对该 JSON 所在任务目录的文件名。旧轮次会自动补建，无法确认的旧模型配置保持 `null`。`html_sha256` 用于核对评分记录对应的 HTML。评分规则 v2 包含五项 1–5 整数分：`problem_fidelity`（一致性）、`solution_correctness`（正确性）、`page_quality`（页面效果）、`interaction_quality`（交互效果）、`teaching_effectiveness`（教学效果，学生能否从零清晰了解解法）。新生成轮次要求审核模型给出这五项分数和 0–5 分综合评分，写入 `.review.json` 与 `.meta.json` 的 `model_review.scores` 和 `model_review.overall_score`；未评分或旧记录填 `null`。自动审核的 `passed` 仍用于控制是否继续迭代。`human_review.overall_score` 是独立填写的 0–5 分综合评分，不由五项分数计算；0 表示不合格，3 表示基本合格。旧人工标注自动映射为不合格 0 分、合格 3 分，原有 `passed` 字段保留在历史 JSON 中供追溯。同题对比每组占一行，桌面上左侧评分、右侧为纵向 HTML 预览；窄屏改为上下排列。卡片可选择轮次、查看模型分数并直接填写人工评分；页面下方只显示所选配置的运行进度与日志，避免重复预览。仅当 `model_review.source` 为 `model` 时，该轮的自动判定才来自审核模型；`local_validator` 表示基础检查直接拒绝，旧记录来源不明时为 `null`。
新生成轮次的 `.meta.json` 还包含 `metrics.stages` 与 `metrics.api_calls`，用于保存各环节实际耗时和接口返回的 token 用量。缺少用量的接口不会按文本长度估算 token；自动分流在同批次中只请求一次，调用记录只写入发起请求的任务。

当前本机 `.env` 将 `VISUALIZE_DATA_DIR` 指向新建的 `data_v2/`，用于重新测试；此前的 `data/` 保持原状。两个目录都被 Git 忽略。当前代码基线已保存为 Git 标签 `v1`。

生成 Prompt 把页面读者设为刚读完题、尚不知道解法的学生。对斜率、参数、辅助量等非题设直接给出的表示，页面须先解释原题条件、转化依据和合法范围，再让学生操作；左右并排时，这段解释也要在控件旁边、按实际视觉阅读顺序先出现。初始值若只是示例，须明确标注。审核 Prompt 用同一学生视角评价教学效果及其余四项指标。

## 可视化指导与模型对比

生成 Prompt 统一由三部分拼装：[通用可视化要求](app/prompts/common_visual.md) + 学科经验 + [输入输出格式](app/prompts/io_format.md)。数学经验由 [math_current.md](app/prompts/math_current.md) 提供基础规则，可选叠加交互白板或推导工作流经验；物理、化学使用各自学科文件；“其他”只使用通用要求与输入输出格式。`{{QUESTION}}` 在请求时替换为题图说明，题图以图片内容发送。审核 Prompt 的通用核对、学科核对和 [评分输出格式](app/prompts/reviewer_format.md) 也放在 [app/prompts](app/prompts)。[分流 Prompt](app/prompts/router.md) 要求模型只返回四类 JSON。

通用可视化要求以图形、动画、状态变化和交互为主，文字只作必要辅助；输入输出格式要求模型在 API 响应正文直接输出 HTML 源码，文件保存由本项目处理，不需要模型访问工作区或请求路径。审核会检查有无“开头一张图、后续关键推导全是脱节文字”的问题。Prompt 版本根据内容计算；已生成任务继续使用其保存的配置快照。项目仍对输出执行离线资源与文件大小检查。

可用以下命令对本地三道题做同提示词、单轮生成对比。HTML、截图、审核意见和请求耗时保存到 `data/benchmarks/<时间戳>/`，原有任务历史不受影响：

```bash
.venv/bin/python scripts/compare_generators.py
```

## 测试

### OCR 与图片直传对照

数学题现在另有可选的“数学规划＋核对＋生成”策略，须显式选择数学 Prompt 方案，支持原图直传和 OCR＋文字输入。它先让所选生成模型输出公开的解题与交互规划 JSON，再由所选审核模型单独核对（沿用审核输入方式），最多修订两次；通过后仍使用原版数学 HTML Prompt，加上已核对规划生成页面。默认“直接生成”策略、原 Prompt 和现有历史不受影响。规划原文、逐次核对意见、API 用时与 token 单独保存；任务页及同题对比卡片可展开查看每次规划、核对意见和已通过的最终规划，并下载原始记录。两端均选图片输入时不会调用 OCR；规划、核对和 HTML 生成都直接接收原图。历史任务恢复沿用原配置。规划核对与 HTML 自动审核都不能代替人工数学核查。实现和实验口径见 `数学可视化迁移实验.md`。

上传区的每组配置可分别选择生成和审核的输入方式：`直接输入图片` 或 `OCR 描述＋文本模型`。点击“加入 OCR 对照”可添加两组同模型配置，用一张题图比较完整链路；生成模型默认 `edumind_alt/qwen3.6-35b-a3b`，审核与识图模型默认 `edumind_alt/qwen3.6-flash`。两组的 Prompt 方案和轮次设置相同。原有任务或未传入新选项的 API 请求仍按图片直传运行。

需要文字输入时，先用识图模型转写原题文字、条件及图形关系，存为 `<时间戳>_question_ocr.md`。同任务后续轮次直接复用；同批次且识图模型相同的任务也复用该结果。文字生成模型只收到这份转写和生成 Prompt。文字审核还会让识图模型每轮描述页面截图，存为 `<时间戳>_round_XX.page_description.md`，再把原题 OCR、当轮截图描述和 HTML 源码交给文本审核模型；原题无需逐轮重新 OCR。图片直传审核仍接收原图与当轮截图。各调用会记录到 `api_calls`，配置快照和 `.meta.json` 记录所选方式与文件名。

模型选择器显示 2026-10-16 的短文本与短识图测试状态。识图下拉框及图片直传模式只列出已通过图片输入测试的入口；已知只支持文本的别名仍可用于文字生成或文字审核。实测列表在 [app/model_capabilities.py](app/model_capabilities.py)，本地带密钥的详细记录在被 Git 忽略的 `api-capabilities.local.md`。短测只验证接口可接收输入，不保证完整 HTML 质量。配置行的短识图按钮仅在手动点击时调用接口。

```bash
.venv/bin/pytest -q
```

## 分步 SVG 与局部修订

选择“数学 · 分步 SVG＋局部修订（1019）”，生成与审核均选 `deepseek-v4-1-flash-261017`、输入方式均选“直接输入图片”。首轮输出完整 HTML，后续按审核反馈返回精确代码替换 JSON，本地组装完整页面后再渲染与审核。DeepSeek 首轮与审核使用 high 推理，局部修订使用 low，上限各 64,000 token。每轮保留完整 HTML、审核结果、用量及原始补丁；无效补丁不会部分写入，记录为本地失败并交下一轮修复。选择“单轮”只会生成一次，不会触发修订；要比较各轮可选“固定五轮”。

原“分步 SVG 与教学示例”配置保持整页返修。新配置的协议可在页面 Prompt 预览和任务配置快照中查看。针对历史两道题的数学验算仍是独立实验工具，不是任意题目的自动证明器。

## 人民币费用显示

任务时间轴、每轮汇总、各模型调用环节和同题对比卡片显示“已知缓存费约 ¥…（非总费用）”，与耗时并列。按用户指定的人民币单价（读取 ¥1.44、写入 ¥5.76 / 百万 token）计算，仅适用于 `codex/deepseek-v4-1-flash-261017`。未返回的用量及未配置价格的模型显示未知，不将输入/输出的“—”当作免费，也不把未命中输入推算为缓存写入。任务汇总含前置环节和已记录用量的失败调用，单轮仅计该轮调用，推理 token 不另加。金额是当前单价下的展示估算，不修改历史用量，也不代表网关实际账单。
