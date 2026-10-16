const historyList = document.getElementById('history-list');
const workspace = document.getElementById('workspace');
const form = document.getElementById('upload-form');
const imageInput = document.getElementById('image-input');
const fileLabel = document.getElementById('file-label');
const formMessage = document.getElementById('form-message');
const submitButton = document.getElementById('submit-button');
let selectedTask = null;
let selectedRound = null;
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
  panel.append(el('strong', '', '模型评分'));
  const scores = round.model_review?.scores || {};
  if (!scoreFields.some(([name]) => scores[name] != null)) {
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
  panel.append(el('p', 'annotation-hint', '1 分＝严重问题，3 分＝部分达标，5 分＝优秀；可留空。教学效果看学生能否从零清晰了解解法。合格判定独立填写。'));
  const controls = {};
  function readForm() {
    return {
      scores: Object.fromEntries(scoreFields.map(([name]) => [name, controls[name].value ? Number(controls[name].value) : null])),
      passed: controls.passed.value === '' ? null : controls.passed.value === 'true',
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
  const passField = el('label', 'score-field'); passField.append(el('span', '', '是否合格'));
  const passSelect = el('select');
  for (const [value, label] of [['', '未标注'], ['true', '合格'], ['false', '不合格']]) {
    const option = el('option', '', label); option.value = value; passSelect.append(option);
  }
  passSelect.value = current.passed == null ? '' : String(current.passed);
  passSelect.addEventListener('change', remember); controls.passed = passSelect; passField.append(passSelect); panel.append(passField);
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
function populateModel(select, preferred) {
  select.replaceChildren();
  for (const model of optionsData.models.filter(item => item.candidate)) {
    const option = el('option', '', `${model.provider} / ${model.id} · ${model.probe.state === 'passed' ? '短识图通过' : model.probe.state === 'failed' ? '最近失败' : '未测试'}`);
    option.value = `${model.provider}::${model.id}`;
    select.append(option);
  }
  if (preferred && [...select.options].some(x => x.value === preferred)) select.value = preferred;
}
function addConfigRow(values = {}) {
  const container = document.getElementById('config-rows');
  if (container.children.length >= 12) { formMessage.textContent = '每批最多 12 个配置'; return; }
  const card = el('div', 'config-row');
  const profile = el('select', 'profile-select');
  for (const item of optionsData.profiles) {
    const option = el('option', '', item.name); option.value = item.id; profile.append(option);
  }
  profile.value = values.profile_id || 'math-current';
  const generator = el('select', 'model-select');
  const reviewer = el('select', 'model-select');
  populateModel(generator, values.generator || 'openai::gpt-6-sol');
  populateModel(reviewer, values.reviewer || 'openai::gpt-5.6-sol');
  const remove = el('button', 'remove-row', '删除'); remove.type = 'button';
  remove.addEventListener('click', () => { if (container.children.length > 1) card.remove(); });
  const preview = el('details', 'prompt-preview');
  preview.append(el('summary', '', '查看本方案完整 Prompt'));
  const promptText = el('pre'); preview.append(promptText);
  function updatePrompt() {
    const item = optionsData.profiles.find(x => x.id === profile.value);
    promptText.textContent = `生成 Prompt · 版本 ${item.version}\n\n${item.generator_prompt}\n\n审核 Prompt\n\n${item.reviewer_prompt}`;
  }
  profile.addEventListener('change', updatePrompt); updatePrompt();
  function field(label, input) { const wrap = el('label', 'config-field'); wrap.append(el('span', '', label), input); return wrap; }
  card.append(field('Prompt 方案', profile), field('生成模型', generator), field('审核模型', reviewer));
  const probe = el('button', 'probe-button', '短识图测试这两个模型'); probe.type = 'button';
  const result = el('div', 'probe-result');
  probe.addEventListener('click', async () => {
    probe.disabled = true; result.textContent = '测试中…';
    try {
      const choices = [...new Set([generator.value, reviewer.value])];
      const results = [];
      for (const value of choices) {
        const choice = modelChoice(value);
        const status = await fetchJson('/api/models/probe', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(choice)});
        results.push(`${choice.provider}/${choice.model}: ${status.message} (${status.seconds}s)`);
      }
      result.textContent = results.join('；');
      optionsData = await fetchJson('/api/options');
      populateModel(generator, generator.value); populateModel(reviewer, reviewer.value);
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
const stageLabels = {queued:'已上传',running:'处理中',generating:'生成页面',validating:'检查文件',rendering:'渲染页面',reviewing:'审核解答',revising:'准备返修',passed:'已完成',failed:'审核未通过',error:'运行出错',interrupted:'已中断'};
const progressSteps = [
  {key:'queued', label:'上传'}, {key:'generating', label:'生成'}, {key:'validating', label:'检查'},
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
    button.append(el('span', `badge ${task.status}`, labels[task.status] || task.status));
    button.append(el('strong', '', task.original_name));
    button.append(el('small', '', new Date(task.created_at).toLocaleString('zh-CN')));
    button.append(el('small', 'history-stage', `${task.current_round ? `第 ${task.current_round} 轮 · ` : ''}${stageLabels[task.stage] || labels[task.status] || task.status}`));
    button.append(el('small', 'history-config', task.profile_name ? `${task.profile_name} · ${task.generator_provider}/${task.generator_model} → ${task.reviewer_provider}/${task.reviewer_model}` : '配置未记录'));
    button.addEventListener('click', () => { selectedTask = task.id; selectedRound = null; lastTaskSignature = ''; lastLayoutKey = ''; loadTask(); loadHistory(); });
    historyList.append(button);
  }
}

function addLink(parent, label, url) {
  const link = el('a', '', label); link.href = url; link.download = ''; parent.append(link);
}

function buildProblemPanel(task, compact = false) {
  const panel = el('section', `panel ${compact ? 'batch-problem' : ''}`);
  panel.append(el('h3', '', '原题图片'));
  const image = el('img', 'problem-image'); image.src = fileUrl(task, task.image_name); image.alt = '上传的题目图片';
  panel.append(image);
  const links = el('div', 'link-row'); addLink(links, '下载原图', fileUrl(task, task.image_name, true)); panel.append(links);
  return panel;
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
  let activeIndex = progressSteps.findIndex(step => step.key === current);
  if (current === 'revising') activeIndex = 1;
  if (current === 'failed' || current === 'error' || current === 'interrupted') {
    const lastWorking = [...(task.events || [])].reverse().find(event => progressSteps.some(step => step.key === event.stage));
    activeIndex = Math.max(0, progressSteps.findIndex(step => step.key === lastWorking?.stage));
  }
  for (const [index, step] of progressSteps.entries()) {
    const mode = task.status === 'passed' || index < activeIndex ? 'done' : index === activeIndex ? 'active' : 'pending';
    const item = el('li', `step ${mode}`);
    item.append(el('span', 'step-dot', mode === 'done' ? '✓' : String(index + 1)), el('span', 'step-label', step.label));
    if (mode === 'active') item.setAttribute('aria-current', 'step');
    stepper.append(item);
  }
  progress.append(stepper);
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
  const batchKey = batchState ? batchState.tasks.map(x => [x.id, x.status, x.current_round, x.generation_seconds, x.review_seconds]) : null;
  const signature = JSON.stringify([task, batchKey]);
  if (signature === lastTaskSignature) { refreshElapsed(task); return; }
  lastTaskSignature = signature;
  const layoutKey = JSON.stringify([task.id, task.rounds.map(round => [round.html_name, round.human_review?.updated_at]), task.final_name, batchKey]);
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
  if (task.profile_name) {
    meta.append(el('span', '', `生成 ${task.generator_provider}/${task.generator_model} · 审核 ${task.reviewer_provider}/${task.reviewer_model}`));
    meta.append(el('span', '', `${task.run_mode === 'single' ? '单轮比较' : '完整迭代'} · 生成 ${Number(task.generation_seconds || 0).toFixed(1)}s · 审核 ${Number(task.review_seconds || 0).toFixed(1)}s`));
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
    card.append(el('h4', '', `第 ${round.number} 轮 · ${round.passed ? '通过' : '未通过'}`));
    card.append(el('p', '', round.summary || '无审核摘要'));
    if (round.issues.length) { const ul = el('ul'); for (const issue of round.issues) ul.append(el('li', '', issue)); card.append(ul); }
    const links = el('div', 'link-row');
    addLink(links, 'HTML', fileUrl(task, round.html_name, true));
    if (round.screenshot_name) addLink(links, '截图', fileUrl(task, round.screenshot_name, true));
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
    const option = el('option', '', `第 ${round.number} 轮${round.passed ? ' · 通过' : ''}`); option.value = String(round.number); chooser.append(option);
  }
  previewHead.append(chooser); right.append(previewHead);
  const frame = el('iframe', 'preview-frame'); frame.title = '生成页面预览'; frame.sandbox = 'allow-scripts';
  function showRound() {
    const round = task.rounds.find(item => item.number === Number(chooser.value));
    if (round) { selectedRound = round.number; frame.src = fileUrl(task, round.html_name); }
  }
  chooser.addEventListener('change', showRound);
  if (task.rounds.length) {
    chooser.value = String(task.rounds.some(item => item.number === selectedRound) ? selectedRound : task.rounds.at(-1).number);
    showRound(); right.append(frame);
  } else right.append(el('div', 'no-preview', '等待生成页面…'));
  if (task.final_name) { const links = el('div', 'link-row'); addLink(links, '下载审核通过的最终 HTML', fileUrl(task, task.final_name, true)); right.append(links); }
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
    card.append(el('small', '', `${item.generator_provider}/${item.generator_model} → ${item.reviewer_provider}/${item.reviewer_model}`));
    card.append(el('small', '', `${labels[item.status] || item.status} · 第 ${item.current_round} 轮 · 生成 ${Number(item.generation_seconds || 0).toFixed(1)}s / 审核 ${Number(item.review_seconds || 0).toFixed(1)}s`));
    const open = el('button', 'text-button', '查看此配置日志'); open.type = 'button';
    open.addEventListener('click', () => { selectedTask = item.id; selectedRound = null; lastTaskSignature = ''; lastLayoutKey = ''; loadTask(); loadHistory(); });
    card.append(open);
    if (item.current_round) {
      const detail = await fetchJson(`/api/tasks/${encodeURIComponent(item.id)}`);
      if (detail.config_name) {
        const configLink = el('div', 'link-row'); addLink(configLink, '下载此配置的 Prompt', fileUrl(detail, detail.config_name, true)); card.append(configLink);
      }
      if (detail.rounds.length) {
        const chooserLabel = el('label', 'batch-round-chooser'); chooserLabel.append(el('span', '', '选择轮次'));
        const chooser = el('select');
        for (const round of detail.rounds) {
          const option = el('option', '', `第 ${round.number} 轮 · ${round.passed ? '自动审核通过' : '自动审核未通过'}`);
          option.value = String(round.number); chooser.append(option);
        }
        const preferred = batchSelectedRounds.get(item.id);
        chooser.value = String(detail.rounds.some(x => x.number === preferred) ? preferred : detail.rounds.at(-1).number);
        chooserLabel.append(chooser); card.append(chooserLabel);
        const roundSlot = el('div', 'batch-round-slot'); card.append(roundSlot);
        function showRound() {
          const round = detail.rounds.find(x => x.number === Number(chooser.value));
          if (!round) return;
          batchSelectedRounds.set(item.id, round.number);
          roundSlot.replaceChildren();
          roundSlot.append(el('small', 'batch-review-summary', `${round.review_source === 'local_validator' ? '基础检查' : '模型审核'}：${round.summary}`));
          if (round.issues.length) {
            const issues = el('details', 'batch-issues'); issues.append(el('summary', '', `查看审核意见 · ${round.issues.length} 条`));
            const list = el('ul'); for (const issue of round.issues) list.append(el('li', '', issue)); issues.append(list); roundSlot.append(issues);
          }
          roundSlot.append(buildModelScores(round));
          roundSlot.append(buildAnnotation(detail, round));
          const links = el('div', 'link-row');
          addLink(links, 'HTML', fileUrl(detail, round.html_name, true));
          if (round.screenshot_name) addLink(links, '截图', fileUrl(detail, round.screenshot_name, true));
          addLink(links, '审核记录', fileUrl(detail, round.review_name, true));
          if (round.metadata_name) addLink(links, '评测 JSON', fileUrl(detail, round.metadata_name, true));
          roundSlot.append(links);
          const frame = el('iframe', 'batch-preview'); frame.title = `${item.profile_name} 第 ${round.number} 轮预览`;
          frame.sandbox = 'allow-scripts'; frame.src = fileUrl(detail, round.html_name); roundSlot.append(frame);
        }
        chooser.addEventListener('change', showRound);
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
      const generator = modelChoice(card.querySelectorAll('.model-select')[0].value);
      const reviewer = modelChoice(card.querySelectorAll('.model-select')[1].value);
      return {profile_id:card.querySelector('.profile-select').value,
        generator_provider:generator.provider, generator_model:generator.model,
        reviewer_provider:reviewer.provider, reviewer_model:reviewer.model};
    });
    const body = new FormData(); body.append('image', imageInput.files[0]); body.append('rows', JSON.stringify(rows));
    body.append('run_mode', document.getElementById('run-mode').value);
    const batch = await fetchJson('/api/batches', {method:'POST', body});
    selectedTask = batch.tasks[0].id; selectedRound = null; lastTaskSignature = ''; lastLayoutKey = ''; await loadHistory(); await loadTask();
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
    addConfigRow();
    document.getElementById('add-row').addEventListener('click', () => addConfigRow());
    if (Object.keys(optionsData.errors).length) formMessage.textContent = `模型目录部分不可用：${Object.entries(optionsData.errors).map(([k,v]) => `${k}: ${v}`).join('；')}`;
    document.getElementById('model-info').textContent = `生成：${config.generator_model} · 审核：${config.reviewer_model}${config.key_configured ? '' : ' · 缺少密钥'}`;
    await loadHistory();
    const first = historyList.querySelector('.history-item');
    if (first) first.click();
  } catch (error) { formMessage.textContent = error.message; }
  setInterval(async () => { try { await loadHistory(); if (selectedTask) await loadTask(); } catch (_) {} }, 3000);
}
init();
