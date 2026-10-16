# 题目可视化工作台

上传一道题目的图片，生成可离线打开的 HTML 解答页，并由另一模型逐轮审核。最多返修 5 轮；每轮 HTML、截图和审核意见均留在本地。任务页面显示当前轮次、处理环节、等待时长和带时间戳的运行日志，历史任务也可查看日志。服务重启中断的任务可在原任务页点击“继续处理”，从已保存的轮次接着运行。

主页面可添加多行配置，用同一张题图比较不同 Prompt 方案、生成模型和审核模型。默认每组只运行一轮，也可选完整迭代。默认“自动识别学科”方案会对每张题图做一次数学、物理、化学或其他的短分类，并拼装相应 Prompt；另有三种数学指导、物理、化学与通用等显式方案用于对比，显式方案不调用分流模型。分类模型可在上传区选择。模型列表从各接口的目录读取；“短识图通过”只表示小图请求成功，不能保证长篇 HTML 请求成功。批量运行直接调用所选模型，不自动消耗额度做短识图测试；需要时可点击配置行中的按钮手动测试。历史页保存组合、Prompt 版本、模型接口、每轮文件和计时；同批次页面可并排预览结果。

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
| `VISUALIZE_REVIEWER_MODEL` | `claude-opus-4-6` | 审核模型 ID |
| `VISUALIZE_DATA_DIR` | 项目内 `data/` | 历史数据目录 |
| `VISUALIZE_USE_PROXY` | `0` | 设为 `1` 时使用系统代理访问模型网关 |

生成和审核模型都需要支持图片输入；使用自动分流时，分流模型也需要支持图片输入。模型接口不兼容或图片权限不足时，任务会显示具体错误；更换模型 ID 后重启服务即可。

## 本地文件

每次上传建立 `data/<上传时间戳>_<任务ID>/`。原图使用 `<上传时间戳>_input.<格式>`；每轮页面使用 `<生成时间戳>_round_01.html` 这样的文件名，同前缀保存 `.png` 截图和 `.review.json` 审核结果。审核通过后另存 `<时间戳>_final.html`。`data/history.sqlite3` 为网页历史索引；手工备份时请同时复制 `data/` 整个目录。

每个新配置任务还保存 `<时间戳>_config.json`，包含运行时的完整生成与审核 Prompt 快照、模型接口、模型 ID 和轮次上限；自动方案完成分流后会写入识别学科、分流模型以及实际使用的两份 Prompt。旧任务显示“配置未记录”。网页中的预览在隔离 iframe 中运行。模型生成的 HTML 仍属于自动生成内容，展示前请核对答案。

每轮 HTML 旁另存同前缀的 `.meta.json`，可从轮次卡片及同题对比卡片下载。它包含该轮的模型、Prompt 方案版本、自动审核结果、文件名和人工评分；`files` 内均为相对该 JSON 所在任务目录的文件名。旧轮次会自动补建，无法确认的旧模型配置保持 `null`。`html_sha256` 用于核对评分记录对应的 HTML。评分规则 v2 包含五项 1–5 整数分：`problem_fidelity`（一致性）、`solution_correctness`（正确性）、`page_quality`（页面效果）、`interaction_quality`（交互效果）、`teaching_effectiveness`（教学效果，学生能否从零清晰了解解法）。新生成轮次要求审核模型给出这五项分数和 0–5 分综合评分，写入 `.review.json` 与 `.meta.json` 的 `model_review.scores` 和 `model_review.overall_score`；未评分或旧记录填 `null`。自动审核的 `passed` 仍用于控制是否继续迭代。`human_review.overall_score` 是独立填写的 0–5 分综合评分，不由五项分数计算；0 表示不合格，3 表示基本合格。旧人工标注自动映射为不合格 0 分、合格 3 分，原有 `passed` 字段保留在历史 JSON 中供追溯。同题对比每组占一行，桌面上左侧评分、右侧为纵向 HTML 预览；窄屏改为上下排列。卡片可选择轮次、查看模型分数并直接填写人工评分；页面下方只显示所选配置的运行进度与日志，避免重复预览。仅当 `model_review.source` 为 `model` 时，该轮的自动判定才来自审核模型；`local_validator` 表示基础检查直接拒绝，旧记录来源不明时为 `null`。

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

```bash
.venv/bin/pytest -q
```
