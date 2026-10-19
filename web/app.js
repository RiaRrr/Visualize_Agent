// User-specified CNY rates; these cover cache components only, not a full bill.
function cacheCost(calls = []) {
  let read = 0, write = 0, known = 0, missing = 0, unpriced = 0;
  for (const call of calls) {
    if (call.provider !== 'codex' || call.model !== 'deepseek-v4-1-flash-261017') { unpriced++; continue; }
    for (const [key, rate] of [['cached_input_tokens', 1.44], ['cache_write_input_tokens', 5.76]]) {
      const count = call[key];
      if (!Number.isSafeInteger(count) || count < 0) { missing++; continue; }
      known++;
      if (key === 'cached_input_tokens') read += count * rate / 1000000;
      else write += count * rate / 1000000;
    }
  }
  return {amount: known ? read + write : null, read, write, known, missing, unpriced, calls: calls.length};
}

function cacheCostText(calls = []) {
  const cost = cacheCost(calls);
  if (!cost.calls) return '费用待记录';
  if (cost.amount == null) return '费用未知（缺用量或单价）';
  return `已知缓存费约 ¥${cost.amount.toFixed(4)}（非总费用）`;
}

const historyList = document.getElementById('history-list');
const workspace = document.getElementById('workspace');
const form = document.getElementById('upload-form');
const imageInput = document.getElementById('image-input');
const fileLabel = document.getElementById('file-label');
const formMessage = document.getElementById('form-message');
const submitButton = document.getElementById('submit-button');
let selectedTask = null;
let selectedRound = null;
let selectedRoundManually = false;
let lastTaskSignature = '';
let lastLayoutKey = '';
let maxRounds = 5;
let optionsData = null;
const annotationDrafts = new Map();
const batchSelectedRounds = new Map();
const scoreFields = [
  ['problem_fidelity', '一致性'],
  ['solution_correctness', '正确性'],
  ['page_quality', '页面效果'],
  ['interaction_quality', '交互效果'],
  ['teaching_effectiveness', '教学效果']
];

function buildModelScores(round) {
  const panel = el('div', 'model-scores');
  const overall = round.model_review?.overall_score;
  panel.append(el('strong', '', `模型评分${overall == null ? '' : ` · 综合 ${overall}/5`}`));
  const scores = round.model_review?.scores || {};
  if (overall == null && !scoreFields.some(([name]) => scores[name] != null)) {
    panel.append(el('small', '', round.review_source === 'local_validator' ? '基础检查未通过，模型未评分' : '本轮无模型分数'));
    return panel;
  }
  const grid = el('div', 'model-score-grid');
  for (const [name, label] of scoreFields) {
    const item = el('span', 'model-score-item');
    item.append(el('span', '', label), el('b', '', scores[name] == null ? '—' : `${scores[name]}/5`));
    grid.append(item);
  }
  panel.append(grid);
  return panel;
}

function buildAnnotation(task, round) {
  const key = `${task.id}:${round.number}`;
  const saved = round.human_review || {};
  const current = annotationDrafts.get(key) || saved;
  const panel = el('div', 'annotation-panel');
  panel.append(el('h5', '', '我的评分'));
  panel.append(el('p', 'annotation-hint', '五个维度各评 1–5 分；综合评分评 0–5 分，0＝不合格、3＝基本合格、5＝优秀，可留空。教学效果看学生能否从零清晰了解解法。'));
  const controls = {};
  function readForm() {
    return {
      scores: Object.fromEntries(scoreFields.map(([name]) => [name, controls[name].value ? Number(controls[name].value) : null])),
      overall_score: controls.overall_score.value === '' ? null : Number(controls.overall_score.value),
      comment: controls.comment.value
    };
  }
  function remember() { annotationDrafts.set(key, readForm()); status.textContent = '未保存'; }
  for (const [name, label] of scoreFields) {
    const wrap = el('label', 'score-field'); wrap.append(el('span', '', label));
    const select = el('select');
    const blank = el('option', '', '未评分'); blank.value = ''; select.append(blank);
    for (let score = 1; score <= 5; score++) {
      const option = el('option', '', `${score} 分`); option.value = String(score); select.append(option);
    }
    select.value = current.scores?.[name] == null ? '' : String(current.scores[name]);
    select.addEventListener('change', remember); controls[name] = select; wrap.append(select); panel.append(wrap);
  }
  const overallField = el('label', 'score-field'); overallField.append(el('span', '', '综合评分'));
  const overallSelect = el('select');
  const blankOverall = el('option', '', '未评分'); blankOverall.value = ''; overallSelect.append(blankOverall);
  for (let score = 0; score <= 5; score++) {
    const option = el('option', '', `${score} 分`); option.value = String(score); overallSelect.append(option);
  }
  const overallScore = current.overall_score ?? (current.passed === true ? 3 : current.passed === false ? 0 : null);
  overallSelect.value = overallScore == null ? '' : String(overallScore);
  overallSelect.addEventListener('change', remember); controls.overall_score = overallSelect; overallField.append(overallSelect); panel.append(overallField);
  const commentField = el('label', 'score-field annotation-comment'); commentField.append(el('span', '', '评语（可选）'));
  const comment = el('textarea'); comment.maxLength = 2000; comment.rows = 3; comment.value = current.comment || '';
  comment.addEventListener('input', remember); controls.comment = comment; commentField.append(comment); panel.append(commentField);
  const actions = el('div', 'annotation-actions');
  const save = el('button', 'save-annotation', '保存评分'); save.type = 'button';
  const status = el('span', 'annotation-status', annotationDrafts.has(key) ? '未保存' : saved.updated_at ? `已保存 · ${new Date(saved.updated_at).toLocaleString('zh-CN')}` : '尚未标注');
  save.addEventListener('click', async () => {
    save.disabled = true; status.textContent = '保存中…';
    try {
      await fetchJson(`/api/tasks/${encodeURIComponent(task.id)}/rounds/${round.number}/annotation`, {
        method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(readForm())
      });
      annotationDrafts.delete(key); lastTaskSignature = ''; lastLayoutKey = '';
      await loadTask();
    } catch (error) { status.textContent = `保存失败：${error.message}`; save.disabled = false; }
  });
  actions.append(save, status); panel.append(actions);
  return panel;
}

function modelChoice(value) {
  const [provider, ...parts] = value.split('::');
  return {provider, model:parts.join('::')};
}
function populateModel(select, preferred, imageOnly = false) {
  select.replaceChildren();
  for (const model of optionsData.models.filter(item => item.candidate && (!imageOnly || item.capability?.image_input === true))) {
    const capability = model.capability || {};
    const status = capability.image_input === true ? '已测可识图' : capability.image_input === false ? '仅文字' : '图片未测';
    const option = el('option', '', `${model.provider} / ${model.id} · ${status}`);
    option.value = `${model.provider}::${model.id}`;
    select.append(option);
  }
  if (preferred && [...select.options].some(x => x.value === preferred)) select.value = preferred;
}
function showModelCapabilities() {
  const panel = document.getElementById('model-capabilities');
  const models = optionsData.models.filter(item => item.provider === 'edumind_alt' && item.candidate);
  const image = models.filter(item => item.capability?.image_input === true).map(item => item.id);
  const text = models.filter(item => item.capability?.text_input === true && item.capability?.image_input === false).map(item => item.id);
  panel.textContent = `2026-10-16 短测：可输入图片 ${image.length} 个：${image.join('、') || '无'}。仅文字 ${text.length} 个：${text.join('、') || '无'}。短测通过不代表长篇 HTML 一定成功。`;
}
function addConfigRow(values = {}) {
  const container = document.getElementById('config-rows');
  if (container.children.length >= 12) { formMessage.textContent = '每批最多 12 个配置'; return; }
  const card = el('div', 'config-row');
  const profile = el('select', 'profile-select');
  for (const item of optionsData.profiles) {
    const option = el('option', '', item.name); option.value = item.id; profile.append(option);
  }
  profile.value = values.profile_id || 'auto';
  const generator = el('select', 'generator-model-select');
  const reviewer = el('select', 'reviewer-model-select');
  const ocr = el('select', 'ocr-model-select');
  const generatorMode = el('select', 'generator-mode-select');
  const reviewerMode = el('select', 'reviewer-mode-select');
  const strategy = el('select', 'generation-strategy-select');
  for (const [value, label] of [['direct','直接生成（原版）'], ['guided_math','实验：数学规划＋核对＋生成']]) {
    const option = el('option', '', label); option.value = value; strategy.append(option);
  }
  strategy.value = values.generation_strategy || 'direct';
  for (const select of [generatorMode, reviewerMode]) {
    for (const [value, label] of [['vision','直接输入图片'], ['ocr_text','OCR 描述＋文本模型']]) {
      const option = el('option', '', label); option.value = value; select.append(option);
    }
  }
  generatorMode.value = values.generator_mode || 'vision';
  reviewerMode.value = values.reviewer_mode || 'vision';
  populateModel(generator, values.generator || 'edumind_alt::qwen3.6-35b-a3b', generatorMode.value === 'vision');
  populateModel(reviewer, values.reviewer || 'edumind_alt::qwen3.6-flash', reviewerMode.value === 'vision');
  populateModel(ocr, values.ocr || 'edumind_alt::qwen3.6-flash', true);
  const remove = el('button', 'remove-row', '删除'); remove.type = 'button';
  remove.addEventListener('click', () => { if (container.children.length > 1) card.remove(); });
  const preview = el('details', 'prompt-preview');
  preview.append(el('summary', '', '查看本方案完整 Prompt'));
  const promptText = el('pre'); preview.append(promptText);
  function updatePrompt() {
    const item = optionsData.profiles.find(x => x.id === profile.value);
    const routing = item.id === 'auto' ? `分流 Prompt\n\n${optionsData.router_prompt}\n\n识别学科后，会在通用可视化要求与输入输出格式之间插入相应学科经验；其他题目不插入学科经验。下方是分流前的通用模板，任务配置文件会保存实际发送的完整 Prompt。\n\n` : '';
    const generatorPrompt = generatorMode.value === 'ocr_text' ? item.generator_text_prompt : item.generator_prompt;
    const reviewerPrompt = reviewerMode.value === 'ocr_text' ? item.reviewer_text_prompt : item.reviewer_prompt;
    const guided = optionsData.guided_math_prompts || {};
    const stages = strategy.value === 'guided_math' ? `规划由所选生成模型输出，核对由所选审核模型执行；分别沿用对应输入方式，支持直接看原图。规划内容会独立保存并显示在任务中。\n\n数学规划 Prompt\n\n${guided.plan || ''}\n\n数学规划核对 Prompt\n\n${guided.plan_review || ''}\n\nHTML 生成附加要求\n\n${guided.html_addendum || ''}\n\n` : '';
    const runtimeNote = item.runtime_note ? `调用设置\n${item.runtime_note}\n\n` : '';
    const revisionPrompt = item.revision_prompt ? `\n\n第二轮起的局部修订协议\n\n${item.revision_prompt}` : '';
    promptText.textContent = `${runtimeNote}${routing}${stages}生成 Prompt\n\n${generatorPrompt}${revisionPrompt}\n\n审核 Prompt\n\n${reviewerPrompt}`;
  }
  for (const select of [profile, generatorMode, reviewerMode, strategy]) select.addEventListener('change', updatePrompt);
  updatePrompt();
  function field(label, input) { const wrap = el('label', 'config-field'); wrap.append(el('span', '', label), input); return wrap; }
  const ocrField = field('识图模型（题目 OCR；文字审核还描述每轮截图）', ocr);
  function updateMode() {
    ocrField.hidden = generatorMode.value !== 'ocr_text' && reviewerMode.value !== 'ocr_text';
    populateModel(generator, generator.value, generatorMode.value === 'vision');
    populateModel(reviewer, reviewer.value, reviewerMode.value === 'vision');
  }
  for (const select of [generatorMode, reviewerMode]) select.addEventListener('change', updateMode);
  card.append(field('Prompt 方案', profile), field('生成方式', generatorMode), field('生成策略', strategy), field('生成模型', generator),
    field('审核方式', reviewerMode), field('审核模型', reviewer), ocrField);
  updateMode();
  const probe = el('button', 'probe-button', '按需短识图测试'); probe.type = 'button';
  const result = el('div', 'probe-result');
  probe.addEventListener('click', async () => {
    probe.disabled = true; result.textContent = '测试中…';
    try {
      const choices = [...new Set([generator.value, reviewer.value, ...(ocrField.hidden ? [] : [ocr.value])])];
      const results = [];
      for (const value of choices) {
        const choice = modelChoice(value);
        const status = await fetchJson('/api/models/probe', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(choice)});
        results.push(`${choice.provider}/${choice.model}: ${status.message} (${status.seconds}s)`);
      }
      result.textContent = results.join('；');
      optionsData = await fetchJson('/api/options');
      populateModel(generator, generator.value, generatorMode.value === 'vision');
      populateModel(reviewer, reviewer.value, reviewerMode.value === 'vision');
      populateModel(ocr, ocr.value, true);
      showModelCapabilities();
    } catch (error) { result.textContent = error.message; }
    finally { probe.disabled = false; }
  });
  card.append(preview, probe, result, remove); container.append(card);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
function fileUrl(task, name, download = false) {
  return `/api/tasks/${encodeURIComponent(task.id)}/files/${encodeURIComponent(name)}${download ? '?download=true' : ''}`;
}
const labels = {queued:'排队中',running:'处理中',passed:'审核通过',failed:'审核未通过',error:'出错',interrupted:'已中断'};
const stageLabels = {upload:'上传处理',queued:'排队等待',running:'处理中',routing:'识别学科',question_ocr:'识别原题',math_planning:'数学规划',math_plan_review:'核对规划',generating:'生成页面',validating:'检查文件',rendering:'渲染页面',page_description:'描述截图',reviewing:'审核解答',revising:'准备返修',passed:'已完成',failed:'审核未通过',error:'运行出错',interrupted:'已中断'};
const progressSteps = [
  {key:'queued', label:'上传'}, {key:'routing', label:'分流'}, {key:'generating', label:'生成'}, {key:'validating', label:'检查'},
  {key:'rendering', label:'渲染'}, {key:'reviewing', label:'审核'}, {key:'passed', label:'完成'}
];

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { const data = await response.json(); message = data.detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

async function loadHistory() {
  const tasks = await fetchJson('/api/tasks');
  historyList.replaceChildren();
  if (!tasks.length) { historyList.append(el('p', 'notice', '还没有生成记录')); return; }
  for (const task of tasks) {
    const button = el('button', `history-item ${task.id === selectedTask ? 'active' : ''}`);
    button.type = 'button';
    button.dataset.taskId = task.id;
    button.append(el('span', `badge ${task.status}`, labels[task.status] || task.status));
    button.append(el('strong', '', task.original_name));
    button.append(el('small', '', new Date(task.created_at).toLocaleString('zh-CN')));
    button.append(el('small', 'history-stage', `${task.current_round ? `第 ${task.current_round} 轮 · ` : ''}${stageLabels[task.stage] || labels[task.status] || task.status}`));
    button.append(el('small', 'history-config', task.profile_name ? `${task.profile_name} · ${task.generator_mode === 'ocr_text' ? 'OCR→' : '图片→'}${task.generator_provider}/${task.generator_model} · ${task.reviewer_mode === 'ocr_text' ? '文字→' : '图片→'}${task.reviewer_provider}/${task.reviewer_model}` : '配置未记录'));
    button.addEventListener('click', () => {
      selectedTask = task.id; selectedRound = null; selectedRoundManually = false; lastTaskSignature = ''; lastLayoutKey = '';
      const url = new URL(location.href); url.searchParams.set('task', task.id); window.history.replaceState(null, '', url);
      loadTask(); loadHistory();
    });
    historyList.append(button);
  }
}

function addLink(parent, label, url) {
  const link = el('a', '', label); link.href = url; link.download = ''; parent.append(link);
}

function buildPlanPanel(task) {
  const panel = el('details', 'plan-panel');
  panel.append(el('summary', '', '查看解题与可视化规划'));
  const files = task.math_plan_files || [];
  if (!files.length) { panel.append(el('p', '', '规划尚未输出，生成后可在这里查看。')); return panel; }
  const chooser = el('select'); chooser.setAttribute('aria-label', '选择规划或核对记录');
  for (const name of files) {
    const label = name === task.math_plan_name ? '已核对的最终规划' : name;
    const option = el('option', '', label); option.value = name; chooser.append(option);
  }
  chooser.value = task.math_plan_name || files.filter(name => name.startsWith('math_plan_attempt_')).at(-1) || files[0];
  const content = el('div', 'plan-content');
  const download = el('a', '', '下载当前记录'); download.download = '';
  panel.append(chooser, download, content);
  let loaded = '', request = 0;
  async function showPlan() {
    const name = chooser.value;
    if (loaded === name) return;
    const token = ++request;
    download.href = fileUrl(task, name, true);
    content.textContent = '正在读取规划…';
    try {
      const response = await fetch(fileUrl(task, name));
      if (!response.ok) throw new Error(`读取失败：HTTP ${response.status}`);
      const raw = await response.text();
      if (token !== request) return;
      content.replaceChildren();
      let data;
      try { data = JSON.parse(raw); } catch (_) {}
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        const names = {given:'题设条件', goal:'所求问题', derivation:'关键推导', answer:'答案与范围', storyboard:'可视化步骤', state:'控制量与联动关系', checks:'验证与边界检查', passed:'核对是否通过', issues:'核对意见', summary:'核对结论'};
        for (const [key, value] of Object.entries(data)) {
          content.append(el('h4', '', names[key] || key), el('pre', '', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
        }
      } else content.append(el('pre', '', raw));
      loaded = name;
    } catch (error) { if (token === request) content.textContent = error.message; }
  }
  panel.addEventListener('toggle', () => { if (panel.open) showPlan(); });
  chooser.addEventListener('change', showPlan);
  return panel;
}

function buildProblemPanel(task, compact = false) {
  const panel = el('section', `panel ${compact ? 'batch-problem' : ''}`);
  panel.append(el('h3', '', '原题图片'));
  const image = el('img', 'problem-image'); image.src = fileUrl(task, task.image_name); image.alt = '上传的题目图片';
  panel.append(image);
  const links = el('div', 'link-row'); addLink(links, '下载原图', fileUrl(task, task.image_name, true)); panel.append(links);
  return panel;
}

function durationText(seconds) {
  if (seconds == null) return '未记录';
  return seconds < 60 ? `${seconds.toFixed(1)}秒` : `${Math.floor(seconds / 60)}分${Math.floor(seconds % 60)}秒`;
}

function costBadge(calls) {
  const badge = el('small', 'cost-estimate', cacheCostText(calls));
  const cost = cacheCost(calls);
  badge.title = `按用户指定人民币单价估算：读取 ¥1.44、写入 ¥5.76 / 百万 token；仅适用于 codex 网关的 deepseek-v4-1-flash-261017。已知读取费 ¥${cost.read.toFixed(4)}，已知写入费 ¥${cost.write.toFixed(4)}；缺失缓存字段 ${cost.missing} 项，未配置价格的调用 ${cost.unpriced} 次。普通输入与输出单价未知，未计入。包含有用量记录的失败调用；未知费用不按零计算。`;
  return badge;
}

function roundUsage(task, round) {
  const calls = (task.api_calls || []).filter(call => call.round_number === round.number);
  const row = el('div', 'round-usage');
  const seconds = calls.reduce((sum, call) => sum + (call.duration_seconds || 0), 0);
  row.append(el('small', '', `本轮 API 累计耗时 ${calls.length ? durationText(seconds) : '未记录'}`), costBadge(calls));
  return row;
}

function buildTimeline(task) {
  const wrap = el('div', 'timeline');
  const heading = el('div', 'timeline-heading');
  heading.append(el('strong', '', '环节时间轴'));
  const stages = task.stage_runs || [];
  if (stages.length) {
    const recorded = stages.reduce((sum, row) => sum + (row.duration_seconds || 0), 0);
    const total = el('small', 'timeline-total'); total.dataset.recorded = String(recorded);
    heading.append(total);
  }
  const calls = task.api_calls || [];
  heading.append(costBadge(calls));
  if (calls.length) {
    const known = calls.filter(call => call.input_tokens != null || call.output_tokens != null);
    const inputs = known.reduce((sum, call) => sum + (call.input_tokens || 0), 0);
    const outputs = known.reduce((sum, call) => sum + (call.output_tokens || 0), 0);
    heading.append(el('small', '', known.length ? `Token：输入 ${inputs} · 输出 ${outputs}${known.length < calls.length ? '（部分接口未提供）' : ''}` : 'Token：接口未提供'));
  }
  wrap.append(heading);
  wrap.append(el('small', 'cost-note', '人民币估算：缓存读 ¥1.44、写 ¥5.76 / 百万 token。仅计已返回用量的缓存项；输入、输出单价及缺失用量尚未计入。'));
  if (!stages.length) {
    wrap.append(el('small', 'timeline-empty', '旧任务未记录分段用时；可在上方查看已有的生成与审核累计耗时。'));
    return wrap;
  }
  const groups = new Map();
  const remainingCalls = new Map();
  for (const call of calls) {
    const key = `${call.round_number}:${call.role}`;
    if (!remainingCalls.has(key)) remainingCalls.set(key, []);
    remainingCalls.get(key).push(call);
  }
  for (const stage of stages) {
    if (!groups.has(stage.round_number)) groups.set(stage.round_number, []);
    groups.get(stage.round_number).push(stage);
  }
  for (const [round, rows] of groups) {
    const group = el('div', 'timeline-group');
    group.append(el('span', 'timeline-round', round ? `第 ${round} 轮` : '开始'));
    const track = el('div', 'timeline-track');
    const roundCalls = calls.filter(call => call.round_number === round);
    const summary = el('span', 'timeline-round-summary');
    summary.append(el('small', '', `API 累计 ${roundCalls.length ? durationText(roundCalls.reduce((sum, call) => sum + (call.duration_seconds || 0), 0)) : '未记录'}`), costBadge(roundCalls));
    track.append(summary);
    for (const row of rows) {
      const item = el('span', `timeline-item ${row.stage}`);
      item.append(el('b', '', stageLabels[row.stage] || row.stage));
      const elapsed = el('span', row.status === 'running' && !row.ended_at ? 'timeline-live' : '',
        row.duration_seconds != null ? durationText(row.duration_seconds) : row.status === 'interrupted' ? '已中断' : '进行中');
      if (row.status === 'running' && !row.ended_at) elapsed.dataset.start = row.started_at;
      item.append(elapsed);
      const role = {routing:'router', question_ocr:'question_ocr', math_planning:'math_plan', math_plan_review:'math_plan_review', generating:'generator',
        page_description:'page_description', reviewing:'reviewer'}[row.stage];
      const call = role && remainingCalls.get(`${round}:${role}`)?.shift();
      if (call) {
        item.append(costBadge([call]));
        item.append(el('small', '', call.input_tokens == null && call.output_tokens == null
          ? 'Token 未提供' : `入 ${call.input_tokens ?? '—'} / 出 ${call.output_tokens ?? '—'}`));
      }
      track.append(item);
    }
    group.append(track); wrap.append(group);
  }
  return wrap;
}

function defaultRoundNumber(task) {
  if (task.status === 'failed' && task.best_round_number != null) return task.best_round_number;
  return task.rounds.at(-1)?.number ?? null;
}

function buildActivity(task) {
  const activity = el('section', 'activity');
  const progress = el('div', 'panel progress-panel');
  const heading = el('div', 'activity-heading');
  heading.append(el('h3', '', '当前环节'));
  heading.append(el('span', 'round-indicator', task.current_round ? `第 ${task.current_round} / ${task.max_rounds || maxRounds} 轮` : '等待开始'));
  progress.append(heading);
  const current = task.stage || task.status;
  const description = el('p', 'stage-description', `${stageLabels[current] || current} · ${task.message || labels[task.status]}`);
  description.append(el('span', 'stage-elapsed'));
  progress.append(description);
  const stepper = el('ol', 'stepper');
  const steps = progressSteps.filter(step => step.key !== 'routing' || task.profile_id === 'auto');
  if (task.generator_mode === 'ocr_text' || task.reviewer_mode === 'ocr_text')
    steps.splice(steps.findIndex(step => step.key === 'generating'), 0, {key:'question_ocr', label:'识题'});
  if (task.generation_strategy === 'guided_math')
    steps.splice(steps.findIndex(step => step.key === 'generating'), 0, {key:'math_planning', label:'规划'}, {key:'math_plan_review', label:'核对'});
  if (task.reviewer_mode === 'ocr_text')
    steps.splice(steps.findIndex(step => step.key === 'reviewing'), 0, {key:'page_description', label:'识图'});
  let activeIndex = steps.findIndex(step => step.key === current);
  if (current === 'revising') activeIndex = 1;
  if (current === 'failed' || current === 'error' || current === 'interrupted') {
    const lastWorking = [...(task.events || [])].reverse().find(event => steps.some(step => step.key === event.stage));
    activeIndex = Math.max(0, steps.findIndex(step => step.key === lastWorking?.stage));
  }
  for (const [index, step] of steps.entries()) {
    const mode = task.status === 'passed' || index < activeIndex ? 'done' : index === activeIndex ? 'active' : 'pending';
    const item = el('li', `step ${mode}`);
    item.append(el('span', 'step-dot', mode === 'done' ? '✓' : String(index + 1)), el('span', 'step-label', step.label));
    if (mode === 'active') item.setAttribute('aria-current', 'step');
    stepper.append(item);
  }
  progress.append(stepper, buildTimeline(task));
  const logPanel = el('div', 'panel log-panel');
  const logHeading = el('div', 'activity-heading');
  logHeading.append(el('h3', '', '运行日志'), el('span', 'log-count', `${(task.events || []).length} 条`));
  logPanel.append(logHeading);
  const logWindow = el('div', 'log-window');
  logWindow.setAttribute('role', 'log');
  logWindow.setAttribute('aria-label', '任务运行日志');
  for (const event of task.events || []) {
    const row = el('div', `log-line ${event.stage}`);
    row.append(el('time', '', new Date(event.created_at).toLocaleTimeString('zh-CN', {hour12:false})),
               el('span', 'log-stage', stageLabels[event.stage] || event.stage),
               el('span', 'log-message', event.message));
    logWindow.append(row);
  }
  logPanel.append(logWindow);
  activity.append(progress, logPanel);
  return activity;
}

function updateActivity(task) {
  const oldWindow = workspace.querySelector('.log-window');
  const stayAtBottom = !oldWindow || oldWindow.scrollHeight - oldWindow.scrollTop - oldWindow.clientHeight < 30;
  const scrollTop = oldWindow?.scrollTop || 0;
  const slot = workspace.querySelector('.activity-slot');
  if (slot) {
    slot.replaceChildren(buildActivity(task));
    const newWindow = slot.querySelector('.log-window');
    newWindow.scrollTop = stayAtBottom ? newWindow.scrollHeight : scrollTop;
  }
  const message = workspace.querySelector('.status-line');
  if (message) message.textContent = task.message || labels[task.status];
  const badge = workspace.querySelector('.task-head .badge');
  if (badge) { badge.className = `badge ${task.status}`; badge.textContent = labels[task.status] || task.status; }
  refreshElapsed(task);
}

function refreshElapsed(task) {
  let runningSeconds = 0;
  for (const item of workspace.querySelectorAll('.timeline-live')) {
    const seconds = Math.max(0, (Date.now() - new Date(item.dataset.start).getTime()) / 1000);
    runningSeconds += seconds;
    item.textContent = durationText(seconds);
  }
  const total = workspace.querySelector('.timeline-total');
  if (total) total.textContent = `环节合计 ${durationText(Number(total.dataset.recorded) + runningSeconds)}`;
  const element = workspace.querySelector('.stage-elapsed');
  if (!element) return;
  if (!['running', 'queued'].includes(task.status)) { element.textContent = ''; return; }
  const current = task.stage || task.status;
  const event = [...(task.events || [])].reverse().find(item => item.stage === current);
  if (!event) { element.textContent = ''; return; }
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(event.created_at).getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  element.textContent = ` · 已等待 ${minutes} 分 ${String(seconds % 60).padStart(2, '0')} 秒`;
}

async function loadTask() {
  if (!selectedTask) return;
  const task = await fetchJson(`/api/tasks/${encodeURIComponent(selectedTask)}`);
  const batchState = task.batch_id ? await fetchJson(`/api/batches/${encodeURIComponent(task.batch_id)}`) : null;
  const batchKey = batchState ? batchState.tasks.map(x => [x.id, x.status, x.stage, x.current_round, x.generation_seconds, x.review_seconds]) : null;
  const signature = JSON.stringify([task, batchKey]);
  if (signature === lastTaskSignature) { refreshElapsed(task); return; }
  lastTaskSignature = signature;
  const layoutKey = JSON.stringify([task.id, task.status, task.math_plan_files, task.best_round_number, task.profile_name, task.profile_version, task.route_subject,
    task.rounds.map(round => [round.html_name, round.human_review?.updated_at]), task.final_name, batchKey]);
  if (layoutKey === lastLayoutKey && workspace.querySelector('.activity-slot')) {
    updateActivity(task);
    return;
  }
  lastLayoutKey = layoutKey;
  workspace.replaceChildren();
  const head = el('div', 'task-head');
  const title = el('div');
  title.append(el('small', '', task.id), el('h2', '', task.original_name), el('div', 'status-line', task.message || labels[task.status]));
  head.append(title, el('span', `badge ${task.status}`, labels[task.status] || task.status));
  const meta = el('div', 'task-meta');
  meta.append(el('span', '', task.profile_name ? `${task.profile_name} · v${task.profile_version}` : '配置未记录'));
  if (task.route_subject) meta.append(el('span', '', `自动识别：${{math:'数学',physics:'物理',chemistry:'化学',general:'其他'}[task.route_subject] || task.route_subject} · ${task.router_provider || '默认接口'}/${task.router_model}`));
  if (task.profile_name) {
    meta.append(el('span', '', `生成 ${task.generation_strategy === 'guided_math' ? '数学规划＋核对 → ' : ''}${task.generator_mode === 'ocr_text' ? 'OCR＋文字' : '图片直传'} ${task.generator_provider}/${task.generator_model} · 审核 ${task.reviewer_mode === 'ocr_text' ? 'OCR＋文字' : '图片直传'} ${task.reviewer_provider}/${task.reviewer_model}`));
    if (task.math_plan_name) addLink(meta, '查看数学规划', fileUrl(task, task.math_plan_name, true));
    if (task.ocr_model) meta.append(el('span', '', `识图 ${task.ocr_provider}/${task.ocr_model}`));
    if (task.question_ocr_name) addLink(meta, '查看题目 OCR', fileUrl(task, task.question_ocr_name));
    meta.append(el('span', '', `${task.run_mode === 'single' ? '单轮比较' : task.run_mode === 'fixed_five' ? `调研固定${task.max_rounds || 5}轮` : '完整迭代'} · 生成 ${Number(task.generation_seconds || 0).toFixed(1)}s · 审核 ${Number(task.review_seconds || 0).toFixed(1)}s`));
    if (task.config_name) addLink(meta, '下载本次 Prompt 与配置', fileUrl(task, task.config_name, true));
  }
  title.append(meta);
  if (['interrupted', 'error'].includes(task.status)) {
    const resume = el('button', 'resume-button', '继续处理');
    resume.type = 'button';
    resume.addEventListener('click', async () => {
      resume.disabled = true;
      try {
        await fetchJson(`/api/tasks/${encodeURIComponent(task.id)}/resume`, {method:'POST'});
        lastTaskSignature = ''; await loadTask(); await loadHistory();
      } catch (error) { formMessage.textContent = error.message; resume.disabled = false; }
    });
    head.append(resume);
  }
  workspace.append(head);
  if (task.generation_strategy === 'guided_math') workspace.append(buildPlanPanel(task));
  if (task.batch_id) workspace.append(buildProblemPanel(task, true));
  if (task.batch_id) await showBatch(task.batch_id, batchState);
  const activitySlot = el('div', 'activity-slot');
  workspace.append(activitySlot);
  updateActivity(task);
  if (task.batch_id) return;
  const grid = el('div', 'task-grid');
  const left = el('div');
  left.append(buildProblemPanel(task));
  const list = el('div', 'round-list');
  for (const round of task.rounds) {
    const card = el('div', 'round-card');
    card.append(el('h4', '', `第 ${round.number} 轮 · ${round.passed ? '通过' : '未通过'}${task.status === 'failed' && round.number === task.best_round_number ? ' · 最佳草稿' : ''}`));
    card.append(roundUsage(task, round));
    card.append(el('p', '', round.summary || '无审核摘要'));
    if (round.issues.length) { const ul = el('ul'); for (const issue of round.issues) ul.append(el('li', '', issue)); card.append(ul); }
    const links = el('div', 'link-row');
    addLink(links, 'HTML', fileUrl(task, round.html_name, true));
    if (round.screenshot_name) addLink(links, '截图', fileUrl(task, round.screenshot_name, true));
    if (round.page_description_name) addLink(links, '截图描述', fileUrl(task, round.page_description_name, true));
    addLink(links, '审核记录', fileUrl(task, round.review_name, true));
    if (round.metadata_name) addLink(links, '评测 JSON', fileUrl(task, round.metadata_name, true));
    card.append(links, buildModelScores(round), buildAnnotation(task, round)); list.append(card);
  }
  if (!task.rounds.length) list.append(el('p', 'notice', '尚未生成第一轮页面。'));
  left.append(list);
  const right = el('section', 'panel');
  const previewHead = el('div', 'preview-head'); previewHead.append(el('h3', '', '页面预览'));
  const chooser = el('select');
  for (const round of task.rounds) {
    const option = el('option', '', `第 ${round.number} 轮${round.passed ? ' · 通过' : ''}${task.status === 'failed' && round.number === task.best_round_number ? ' · 最佳草稿' : ''}`); option.value = String(round.number); chooser.append(option);
  }
  previewHead.append(chooser); right.append(previewHead);
  const frame = el('iframe', 'preview-frame'); frame.title = '生成页面预览'; frame.sandbox = 'allow-scripts';
  function showRound() {
    const round = task.rounds.find(item => item.number === Number(chooser.value));
    if (round) { selectedRound = round.number; frame.src = fileUrl(task, round.html_name); }
  }
  chooser.addEventListener('change', () => { selectedRoundManually = true; showRound(); });
  if (task.rounds.length) {
    chooser.value = String(selectedRoundManually && task.rounds.some(item => item.number === selectedRound) ? selectedRound : defaultRoundNumber(task));
    showRound(); right.append(frame);
  } else right.append(el('div', 'no-preview', '等待生成页面…'));
  if (task.final_name) { const links = el('div', 'link-row'); addLink(links, '下载审核通过的最终 HTML', fileUrl(task, task.final_name, true)); right.append(links); }
  if (task.status === 'failed' && task.best_round_number != null) {
    const best = task.rounds.find(item => item.number === task.best_round_number);
    const links = el('div', 'link-row');
    addLink(links, `下载最佳草稿 · 第 ${best.number} 轮${task.best_round_score == null ? '' : ` · ${task.best_round_score}/5`}`,
      fileUrl(task, best.html_name, true));
    right.append(links);
  }
  right.append(el('p', 'notice', '预览在隔离区域中运行；下载后可断网打开。'));
  grid.append(left, right); workspace.append(grid);
}

async function showBatch(batchId, batch) {
  const area = el('section', 'batch-panel panel');
  const heading = el('div', 'batch-heading'); heading.append(el('h3', '', `同题对比 · ${batch.tasks.length} 组`));
  if (batch.tasks.some(x => x.status === 'interrupted')) {
    const resume = el('button', 'text-button', '恢复未完成任务'); resume.type = 'button';
    resume.addEventListener('click', async () => { await fetchJson(`/api/batches/${encodeURIComponent(batchId)}/resume`, {method:'POST'}); await loadTask(); });
    heading.append(resume);
  }
  area.append(heading);
  const cards = el('div', 'batch-cards');
  for (const item of batch.tasks) {
    const card = el('div', `batch-card ${item.id === selectedTask ? 'selected' : ''}`);
    card.append(el('strong', '', item.profile_name || '配置未记录'));
    if (item.route_subject) card.append(el('small', '', `识别学科：${{math:'数学',physics:'物理',chemistry:'化学',general:'其他'}[item.route_subject] || item.route_subject}`));
    card.append(el('small', '', `${item.generation_strategy === 'guided_math' ? '数学规划＋核对 · ' : ''}${item.generator_mode === 'ocr_text' ? 'OCR＋文字' : '图片直传'} ${item.generator_provider}/${item.generator_model} → ${item.reviewer_mode === 'ocr_text' ? 'OCR＋文字' : '图片直传'} ${item.reviewer_provider}/${item.reviewer_model}`));
    const taskUsage = el('div', 'round-usage');
    taskUsage.append(el('small', '', `${labels[item.status] || item.status} · 第 ${item.current_round} 轮 · 生成 ${Number(item.generation_seconds || 0).toFixed(1)}s / 审核 ${Number(item.review_seconds || 0).toFixed(1)}s`));
    card.append(taskUsage);
    const open = el('button', 'text-button', '查看此配置日志'); open.type = 'button';
    open.addEventListener('click', () => { selectedTask = item.id; selectedRound = null; selectedRoundManually = false; lastTaskSignature = ''; lastLayoutKey = ''; loadTask(); loadHistory(); });
    card.append(open);
    if (item.current_round || item.generation_strategy === 'guided_math') {
      const detail = await fetchJson(`/api/tasks/${encodeURIComponent(item.id)}`);
      taskUsage.append(costBadge(detail.api_calls || []));
      if (item.generation_strategy === 'guided_math' && item.id !== selectedTask) card.append(buildPlanPanel(detail));
      if (detail.config_name) {
        const configLink = el('div', 'link-row'); addLink(configLink, '下载此配置的 Prompt', fileUrl(detail, detail.config_name, true)); card.append(configLink);
      }
      if (detail.rounds.length) {
        const chooserLabel = el('label', 'batch-round-chooser'); chooserLabel.append(el('span', '', '选择轮次'));
        const chooser = el('select');
        for (const round of detail.rounds) {
          const option = el('option', '', `第 ${round.number} 轮 · ${round.passed ? '自动审核通过' : '自动审核未通过'}${detail.status === 'failed' && round.number === detail.best_round_number ? ' · 最佳草稿' : ''}`);
          option.value = String(round.number); chooser.append(option);
        }
        const preferred = batchSelectedRounds.get(item.id);
        chooser.value = String(detail.rounds.some(x => x.number === preferred) ? preferred : defaultRoundNumber(detail));
        chooserLabel.append(chooser); card.append(chooserLabel);
        const roundSlot = el('div', 'batch-round-slot'); card.append(roundSlot);
        function showRound() {
          const round = detail.rounds.find(x => x.number === Number(chooser.value));
          if (!round) return;
          roundSlot.replaceChildren();
          const evaluation = el('div', 'batch-evaluation');
          evaluation.append(roundUsage(detail, round));
          evaluation.append(el('small', 'batch-review-summary', `${round.review_source === 'local_validator' ? '基础检查' : '模型审核'}：${round.summary}`));
          if (round.issues.length) {
            const issues = el('details', 'batch-issues'); issues.append(el('summary', '', `查看审核意见 · ${round.issues.length} 条`));
            const list = el('ul'); for (const issue of round.issues) list.append(el('li', '', issue)); issues.append(list); evaluation.append(issues);
          }
          evaluation.append(buildModelScores(round), buildAnnotation(detail, round));
          const links = el('div', 'link-row');
          addLink(links, 'HTML', fileUrl(detail, round.html_name, true));
          if (detail.status === 'failed' && round.number === detail.best_round_number)
            addLink(links, '下载最佳草稿', fileUrl(detail, round.html_name, true));
          if (round.screenshot_name) addLink(links, '截图', fileUrl(detail, round.screenshot_name, true));
          if (round.page_description_name) addLink(links, '截图描述', fileUrl(detail, round.page_description_name, true));
          addLink(links, '审核记录', fileUrl(detail, round.review_name, true));
          if (round.metadata_name) addLink(links, '评测 JSON', fileUrl(detail, round.metadata_name, true));
          evaluation.append(links);
          const preview = el('div', 'batch-preview-column');
          preview.append(el('strong', 'batch-preview-title', 'HTML 预览'));
          const frame = el('iframe', 'batch-preview'); frame.title = `${item.profile_name} 第 ${round.number} 轮预览`;
          frame.sandbox = 'allow-scripts'; frame.src = fileUrl(detail, round.html_name); preview.append(frame);
          roundSlot.append(evaluation, preview);
        }
        chooser.addEventListener('change', () => { batchSelectedRounds.set(item.id, Number(chooser.value)); showRound(); });
        showRound();
      }
    }
    cards.append(card);
  }
  area.append(cards); workspace.append(area);
}

form.addEventListener('submit', async (event) => {
  event.preventDefault(); formMessage.textContent = '';
  if (!imageInput.files.length) return;
  submitButton.disabled = true;
  try {
    const rows = [...document.querySelectorAll('.config-row')].map(card => {
      const generator = modelChoice(card.querySelector('.generator-model-select').value);
      const reviewer = modelChoice(card.querySelector('.reviewer-model-select').value);
      const ocr = modelChoice(card.querySelector('.ocr-model-select').value);
      return {profile_id:card.querySelector('.profile-select').value,
        generation_strategy:card.querySelector('.generation-strategy-select').value,
        generator_provider:generator.provider, generator_model:generator.model,
        reviewer_provider:reviewer.provider, reviewer_model:reviewer.model,
        generator_mode:card.querySelector('.generator-mode-select').value,
        reviewer_mode:card.querySelector('.reviewer-mode-select').value,
        ocr_provider:ocr.provider, ocr_model:ocr.model};
    });
    const body = new FormData(); body.append('image', imageInput.files[0]); body.append('rows', JSON.stringify(rows));
    body.append('run_mode', document.getElementById('run-mode').value);
    const router = modelChoice(document.getElementById('router-model').value);
    body.append('router_provider', router.provider); body.append('router_model', router.model);
    const batch = await fetchJson('/api/batches', {method:'POST', body});
    selectedTask = batch.tasks[0].id; selectedRound = null; selectedRoundManually = false; lastTaskSignature = ''; lastLayoutKey = ''; await loadHistory(); await loadTask();
  } catch (error) { formMessage.textContent = error.message; }
  finally { submitButton.disabled = false; }
});
imageInput.addEventListener('change', () => { fileLabel.textContent = imageInput.files[0]?.name || '选择 PNG、JPG 或 WebP'; });
document.getElementById('refresh-button').addEventListener('click', async () => { await loadHistory(); await loadTask(); });

async function init() {
  try {
    const config = await fetchJson('/api/config');
    maxRounds = config.max_rounds || 5;
    document.getElementById('history-title').textContent = `历史任务 · ${config.data_directory || '本地数据'}`;
    optionsData = await fetchJson('/api/options');
    populateModel(document.getElementById('router-model'), 'edumind_alt::qwen3.6-flash', true);
    showModelCapabilities();
    addConfigRow();
    document.getElementById('add-row').addEventListener('click', () => addConfigRow());
    document.getElementById('add-ocr-comparison').addEventListener('click', () => {
      const rows = document.getElementById('config-rows');
      if (rows.children.length >= 12) { formMessage.textContent = '每批最多 12 个配置'; return; }
      const existing = rows.lastElementChild;
      if (existing && rows.children.length === 1) {
        existing.querySelector('.generator-mode-select').value = 'ocr_text';
        existing.querySelector('.reviewer-mode-select').value = 'ocr_text';
        existing.querySelector('.generator-mode-select').dispatchEvent(new Event('change'));
        existing.querySelector('.reviewer-mode-select').dispatchEvent(new Event('change'));
      } else addConfigRow({generator_mode:'ocr_text', reviewer_mode:'ocr_text'});
      addConfigRow({generator_mode:'vision', reviewer_mode:'vision'});
    });
    if (Object.keys(optionsData.errors).length) formMessage.textContent = `模型目录部分不可用：${Object.entries(optionsData.errors).map(([k,v]) => `${k}: ${v}`).join('；')}`;
    document.getElementById('model-info').textContent = `逐组选择生成与审核模型${config.key_configured ? '' : ' · 请检查接口密钥'}`;
    await loadHistory();
    const requested = new URLSearchParams(location.search).get('task');
    const first = [...historyList.querySelectorAll('.history-item')].find(item => item.dataset.taskId === requested)
      || historyList.querySelector('.history-item');
    if (first) first.click();
  } catch (error) { formMessage.textContent = error.message; }
  setInterval(async () => { try { await loadHistory(); if (selectedTask) await loadTask(); } catch (_) {} }, 3000);
}
init();
