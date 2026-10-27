const state = { data: null, lens: "personal", member: null, evidenceQuery: "", layer: "overview", refreshing: false, revision: 0, investigationRequest: 0 };

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
const label = (kind) => ({goal:"Goal", task:"Task", decision:"Decision", assumption:"Assumption"}[kind] || kind || "Material");
const statusLabel = (status) => status === "accepted" ? "confirmed" : status || "proposed";
const claimLabel = (type) => ({assistant_report:"助手报告", user_statement:"用户表达", conversation_exchange:"问答记录", document_statement:"项目文档", execution_record:"执行记录"}[type] || "来源声明");
const commandOriginLabel = (origin) => ({native_trace:'原生命令记录',agent_report:'Agent命令报告',reported:'命令报告'}[origin] || '命令报告');
const sourceCurrent = (fact) => !["superseded", "unavailable"].includes(fact.source_state?.status);
const sourceNote = (fact) => fact.source_state?.status === "superseded" ? "来源已换版，需复核" : fact.source_state?.status === "unavailable" ? "来源当前不可用，需复核" : "";
const olderState = (payload) => Number.isFinite(payload.state_version) && Number.isFinite(state.data?.state_version) && payload.state_version < state.data.state_version;
const formatTime = (value) => { if (!value) return "time unavailable"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {hour12: false}); };
const sourceTimeLabel = (record) => record.source_kind === 'file_snapshot' ? '材料采集时间' : '来源记录时间';
const hasSourceTime = (record) => !!record.observed_at && !Number.isNaN(new Date(record.observed_at).getTime());
const sourceTimeText = (record, title = sourceTimeLabel(record)) => hasSourceTime(record) ? `${title} ${formatTime(record.observed_at)}` : `${title}未提供`;
const sourceTimeHTML = (record, title = sourceTimeLabel(record)) => hasSourceTime(record) ? `<time datetime="${esc(record.observed_at)}">${esc(sourceTimeText(record, title))}</time>` : `<span>${esc(sourceTimeText(record, title))}</span>`;

async function readInitialState() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/state", {cache: "no-store", signal: controller.signal});
    if (!response.ok) throw new Error(`state ${response.status}`);
    const payload = await response.json();
    const validView = (view) => view && typeof view === "object" && Array.isArray(view.facts) && Array.isArray(view.evidence_ids);
    if (!payload || !Number.isInteger(payload.project?.evidence_count) || payload.project.evidence_count < 0 ||
        !Number.isInteger(payload.project?.fact_count) || payload.project.fact_count < 0 ||
        !payload.personal || typeof payload.personal !== "object" || Array.isArray(payload.personal) ||
        !Object.values(payload.personal).every(validView) || !validView(payload.team) || !Array.isArray(payload.evidence)) {
      throw new Error("invalid dashboard state");
    }
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

async function loadState() {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let payload;
    try {
      payload = await readInitialState();
    } catch (error) {
      if (attempt === 2 || !["TypeError", "AbortError"].includes(error.name)) throw error;
      document.getElementById("connection-label").textContent = `连接暂时中断，重试 ${attempt + 2}/3`;
      await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 250 : 750));
      continue;
    }
    state.data = payload;
    state.revision += 1;
    render();
    return;
  }
}

function selectAvailableMember() {
  const members = Object.keys(state.data.personal || {});
  if (members.includes(state.member)) return;
  const preferred = (state.data.intake?.enabled && state.data.intake.member_id) || (state.data.extraction?.enabled && state.data.extraction.member_id);
  state.member = members.includes(preferred) ? preferred : members.find((member) => member !== "project-documents") || members[0] || null;
}

function renderMemberPicker(members) {
  const picker = document.getElementById("member-picker");
  const select = document.getElementById("member-select");
  picker.hidden = state.lens !== "personal";
  select.innerHTML = members.length ? members.map((member) => `<option value="${esc(member)}">${member === "project-documents" ? "项目材料" : esc(member)}</option>`).join("") : `<option value="">No member data</option>`;
  select.value = state.member || "";
}

function activeView() {
  if (state.lens === "team") return state.data.team || {facts: [], evidence_ids: []};
  return (state.data.personal || {})[state.member] || {facts: [], evidence_ids: []};
}

function render() {
  if (!state.data) return;
  if (document.body) document.body.dataset.layer = state.layer;
  document.querySelectorAll(".layer-choice").forEach((button) => button.classList.toggle("active", button.dataset.layerChoice === state.layer));
  const submit=document.getElementById('investigate-submit');
  if (submit) submit.disabled=false;
  const reviewer = document.getElementById('reviewer-input');
  reviewer.readOnly = state.data.agent_api?.enabled === true;
  if (reviewer.readOnly) reviewer.value = state.data.agent_api.owner_id;
  selectAvailableMember();
  renderIntake();
  renderProjectScan();
  renderExtraction();
  const view = activeView();
  const facts = view.facts || [];
  const relations = [...(state.data.candidate_relations || []), ...(state.data.confirmed_relations || [])];
  const factIds = new Set(facts.map((fact) => fact.id));
  const sourceIds = new Set(view.evidence_ids || []);
  facts.forEach((fact) => [...(fact.evidence_ids || []), ...(fact.source_state?.replacement_ids || []), ...(fact.source_state?.observation_ids || [])].forEach((id) => sourceIds.add(id)));
  (state.data.confirmed_relations || []).filter((relation) => factIds.has(relation.fact_id)).forEach((relation) => sourceIds.add(relation.evidence_id));
  const catalog = state.data.evidence || [];
  const evidence = state.lens === "team" ? catalog : catalog.filter((item) => sourceIds.has(item.id));
  const query = state.evidenceQuery.trim().toLowerCase();
  const visibleEvidence = query ? evidence.filter((item) => `${item.id} ${item.content_preview} ${item.command_preview || ''} ${item.source_ref || ""} ${item.author || ""}`.toLowerCase().includes(query)) : evidence;
  const candidates = relations.filter((relation) => relation.status === "candidate" && factIds.has(relation.fact_id)).length;
  const tasks = facts.filter((fact) => fact.kind === "task").length;
  const goal = facts.find((fact) => fact.kind === "goal" && fact.status === "accepted" && sourceCurrent(fact)) || facts.find((fact) => fact.kind === "goal" && sourceCurrent(fact));

  document.getElementById("project-title").textContent = state.data.project?.name || "Project Compass";
  document.getElementById("evidence-count").textContent = state.data.project?.evidence_count ?? catalog.length;
  document.getElementById("evidence-kicker").textContent = state.lens === "team" ? "PROJECT EVIDENCE" : state.member === "project-documents" ? "PROJECT MATERIALS" : "PERSONAL EVIDENCE";
  document.getElementById("goal-title").textContent = goal?.value || "此视图尚无目标候选";
  document.getElementById("goal-note").textContent = goal ? `${statusLabel(goal.status)} · ${goal.evidence_ids?.length || 0} direct source(s) · ${sourceTimeText(goal)}${goal.status === 'accepted' ? '' : ' · 当前有效性待核对'}` : state.data.project?.fact_count ? "可查看本视图的任务和证据，或切换团队视图。" : "扫描项目并整理候选后，这里会显示带来源的目标。";
  document.getElementById("goal-status").textContent = statusLabel(goal?.status || "proposed");
  document.getElementById("facts-kicker").textContent = state.lens === "team" ? "TEAM STATEMENTS" : state.member === "project-documents" ? "PROJECT MATERIAL CANDIDATES" : "PERSONAL CANDIDATES";
  document.getElementById("facts-count").textContent = facts.length;
  document.getElementById("spine-count").textContent = query ? `${visibleEvidence.length}/${evidence.length}` : evidence.length;
  document.getElementById("metrics").innerHTML = [
    metric(facts.length, state.lens === "team" ? "team statements" : state.member === "project-documents" ? "document candidates" : "personal candidates"),
    metric(tasks, "task candidates"),
    metric(candidates, "candidate relations"),
    metric((state.data.team?.facts || []).filter((fact) => fact.status === "accepted" && sourceCurrent(fact)).length, "current accepted facts"),
  ].join("");
  document.getElementById("fact-list").innerHTML = facts.length ? facts.map((fact) => factCard(fact, relations)).join("") : `<p class="empty">当前视图还没有结构化事实。原始材料仍可在证据脊柱中查看。</p>`;
  renderTrace(facts, relations, evidence);
  renderDirections(view.directions || {candidates:[],current:[],history:[]}, state.lens === 'team');
  renderReasonPreview();
  renderTeamHistory();
  document.getElementById("evidence-filter").value = state.evidenceQuery;
  const evidencePageSize = 50;
  const renderedEvidence = visibleEvidence.slice(0, evidencePageSize);
  const hiddenEvidenceCount = visibleEvidence.length - renderedEvidence.length;
  const evidenceLimitNote = hiddenEvidenceCount > 0
    ? `<p class="empty">原文层当前显示 ${renderedEvidence.length} 条，另有 ${hiddenEvidenceCount} 条可继续缩小筛选；完整原文可从过程层来源或证据 ID 打开。</p>`
    : "";
  document.getElementById("evidence-spine").innerHTML = renderedEvidence.length
    ? renderedEvidence.map(evidenceCard).join("") + evidenceLimitNote
    : `<p class="empty">没有匹配的证据材料。</p>`;
  document.querySelectorAll(".lens").forEach((button) => button.classList.toggle("active", button.dataset.lens === state.lens));
  renderMemberPicker(Object.keys(state.data.personal || {}));
  bindDynamicControls();
}

function renderProjectScan() {
  const scan = state.data?.project_scan;
  document.getElementById("project-scan-bar").hidden = !scan?.enabled;
  document.getElementById("project-scan-status").textContent = !scan?.last_checked_at ? "文件视图来自已保存扫描，可重新检查项目。" : `文件检查 ${formatTime(scan.last_checked_at)} · ${scan.file_count} 份当前文件 · ${scan.changed_files} 份换版 · ${scan.unavailable_files} 份当前不可用。新内容需提取后才会形成候选。`;
}

function extractionMember() {
  return state.lens === 'personal' && state.member && state.member !== 'project-documents' ? state.member : null;
}

function renderExtraction() {
  const job = state.data?.extraction;
  document.getElementById("extraction-bar").hidden = !job?.enabled;
  if (!job?.enabled) return;
  const names = {idle:"等待整理", running:"正在整理候选", complete:"本批已完成", failed:"本批失败，自动重试已停止", stopped:"本次启动预算已停止"};
  const member = extractionMember();
  const scope = job.batches_used > 0 ? job.scope : member ? {kind:'member',member_id:member} : {kind:'project'};
  const scopeNote = scope?.kind === 'member' ? `成员 ${scope.member_id}` : '项目材料与对话';
  document.getElementById("extraction-status").textContent = `${job.automatic ? "自动整理" : "手动整理"} · ${scopeNote} · ${names[job.state] || job.state} · 本次启动 ${job.batches_used}/${job.max_batches} 批 · ${job.running ? "本批用量待回报 · " : ""}已知 ${job.known_tokens} tokens，停止阈值 ${job.max_tokens}，单次可能超出 · 未知用量 ${job.unknown_usage_requests} 次 · 项目待检查 ${job.pending_chars ?? "未知"} 字符。候选仍需负责人确认。`;
  document.getElementById("extract-candidates").textContent = member ? '整理当前成员对话' : '整理一批项目候选';
  document.getElementById("extract-candidates").disabled = job.running || job.closed || job.batches_used >= job.max_batches || job.known_tokens >= job.max_tokens || job.unknown_usage_requests > 0 || job.pending_chars === 0 || (member && Array.isArray(job.eligible_members) && !job.eligible_members.includes(member));
}

async function extractCandidates() {
  try {
    const member = extractionMember();
    const response = await fetch("/api/extract", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(member ? {member_id:member} : {})});
    const payload = await response.json();
    if (payload.extraction) { state.data.extraction = payload.extraction; renderExtraction(); }
    if (!response.ok) throw new Error(payload.error || "extraction failed");
  } catch (error) {
    document.getElementById("extraction-status").textContent = `候选整理未启动。${error.message}`;
  }
}

async function rescanProject() {
  if (state.rescanning || !state.data?.project_scan?.enabled) return;
  state.rescanning = true;
  document.getElementById("rescan-project").disabled = true;
  try {
    const response = await fetch("/api/rescan", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "rescan failed");
    if (olderState(payload)) return;
    state.data = payload;
    render();
    if (payload.project_scan.updated) invalidateInvestigation("项目来源已更新。历史确认保留，换版或不可用的来源需要复核，请重新提问。");
  } catch (error) {
    document.getElementById("project-scan-status").textContent = `项目扫描失败，已保存记录保留。${error.message}`;
  } finally {
    state.rescanning = false;
    document.getElementById("rescan-project").disabled = false;
  }
}

function renderIntake() {
  const intake = state.data?.intake;
  const api = state.data?.agent_api;
  const ruleScan = intake?.direction_scan;
  const userScan = intake?.user_task_scan;
  const sample = state.data?.sample_mode === true;
  document.getElementById("connection-label").textContent = sample ? "合成样例 · 本地工作区" : "Local workspace";
  document.getElementById("intake-bar").hidden = !sample && !intake?.enabled && !api?.enabled && !ruleScan?.enabled && !userScan?.enabled;
  document.getElementById("refresh-intake").hidden = sample;
  if (sample) {
    document.getElementById("intake-status").textContent = "合成样例供试用。包含两名成员和一组冲突，审核仅保存在独立样例工作区，不代表真实项目验收或赞助服务已连接。";
    return;
  }
  const apiNote = api?.enabled ? `Agent HTTP 已启用 · ${api.clients?.length || 0} 个 Agent 上下文可提交证据与候选。` : '';
  const ruleNote = ruleScan?.enabled ? `方向规则已扫 ${ruleScan.completed_sources}/${ruleScan.eligible_sources} 条公开消息，字符进度 ${ruleScan.scanned_chars}/${ruleScan.total_chars}。${ruleScan.pending_sources ? `仍有 ${ruleScan.pending_sources} 条待继续，可点击刷新。` : '本轮材料规则扫描结束。'}规则只识别明确表述，超过 ${ruleScan.max_statement_chars} 字的句段仍保留原文，暂不生成方向候选；该进度不代表语义提取完整。` : '';
  const userNote = userScan?.enabled ? `用户任务规则已扫 ${userScan.completed_sources}/${userScan.eligible_sources} 条短消息，结果保持候选。${userScan.pending_sources ? `仍有 ${userScan.pending_sources} 条待继续。` : ''}${userScan.skipped_long_sources} 条长消息保留原文，需另行整理；该进度不代表完整语义提取。` : '';
  if (!intake?.enabled) {
    document.getElementById('intake-status').textContent = apiNote + ruleNote + userNote;
    return;
  }
  const extractionNote = state.data?.extraction?.enabled ? "候选整理状态见下方。" : "新材料需单独提取。";
  const toolNote = intake.tools_enabled ? `工具记录 ${intake.stored_tool_records || 0} 条，最多回填 ${intake.max_tool_pairs} 组文本调用与结果；命令执行 ${intake.stored_native_commands || 0} 条，最多回填 ${intake.max_native_commands || 200} 条。` : intake.stored_tool_records || intake.stored_native_commands ? `保留 ${intake.stored_tool_records || 0} 条工具历史与 ${intake.stored_native_commands || 0} 条命令执行，当前未采集新的工具记录。` : "当前不包含工具输出。";
  const backfill = intake.backfill;
  const backfillNote = backfill?.enabled ? `历史原文回填 ${backfill.state === 'complete' ? '已完成' : backfill.state === 'pending' ? '进行中' : '尚未开始'} · 已完成 ${backfill.batches_completed || 0} 批 · 仍有 ${backfill.remaining_tool_pairs || 0} 组工具调用与 ${backfill.remaining_commands || 0} 条命令记录 · ${backfill.raw_only ? '只收录原文，不代表语义整理完整。' : ''}` : '';
  document.getElementById("intake-status").textContent = `Codex 已连接 · ${intake.captured_messages || 0} 条公开消息 · ${intake.skipped_sensitive || 0} 条敏感或环境消息与 ${intake.skipped_control || 0} 条平台标记已跳过。${toolNote}${backfillNote}${apiNote}${extractionNote}${ruleNote}${userNote}`;
}

function invalidateInvestigation(message) {
  state.revision += 1;
  const panel = document.getElementById("investigation-answer");
  panel.hidden = false;
  panel.innerHTML = `<p class="empty">${esc(message)}</p>`;
}

async function refreshNative() {
  if ((!state.data?.intake?.enabled && !state.data?.intake?.direction_scan?.enabled && !state.data?.intake?.user_task_scan?.enabled && !state.data?.extraction?.enabled && !state.data?.agent_api?.enabled) || state.refreshing) return;
  state.refreshing = true;
  document.getElementById("refresh-intake").disabled = true;
  try {
    const response = state.data.intake?.enabled || state.data.intake?.direction_scan?.enabled || state.data.intake?.user_task_scan?.enabled ? await fetch("/api/refresh", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"}) : await fetch("/api/state", {cache:"no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "refresh failed");
    if (olderState(payload)) return;
    const evidenceChanged = payload.project.evidence_count !== state.data.project.evidence_count;
    const reviewChanged = payload.review_history.length !== state.data.review_history.length;
    const candidatesChanged = payload.project.fact_count !== state.data.project.fact_count;
    const versionChanged = payload.state_version !== state.data.state_version;
    if (evidenceChanged || reviewChanged || candidatesChanged || versionChanged) {
      state.data = payload;
      render();
      invalidateInvestigation(candidatesChanged ? "新候选已更新，原始材料和负责人确认记录保留，请重新提问。" : evidenceChanged ? payload.intake?.new_tool_records ? "已接入工具记录，原有事实与确认历史保留，请重新提问。" : "已接入新消息。原有事实与确认记录保留，新材料需提取后再重新调查。" : "项目状态或负责人确认记录已更新，请重新提问以使用最新状态。");
    } else {
      state.data.intake = payload.intake;
      state.data.extraction = payload.extraction;
      renderIntake();
      renderExtraction();
    }
  } catch (error) {
    document.getElementById("intake-status").textContent = `会话刷新失败，已保存内容保留。${error.message}`;
  } finally {
    state.refreshing = false;
    document.getElementById("refresh-intake").disabled = false;
  }
}

function rawFindingMeta(item) {
  if (item.status !== 'raw') return '';
  const kind = ({git_commit:'Git 提交',command_execution:'命令记录',test_result:'测试记录',tool_result:'工具结果',assistant_message:'助手发言',user_message:'用户发言'})[item.source_kind] || item.source_kind || '来源记录';
  const execution = ['command_execution','test_result','tool_result'].includes(item.source_kind);
  const command = item.command_preview ? `<span class="evidence-command">${esc(item.command_preview)}</span>` : '';
  const origin = item.command_origin ? ` · ${commandOriginLabel(item.command_origin)}` : '';
  const exit = execution ? ` · 退出码 ${Number.isInteger(item.exit_code) ? item.exit_code : '未知'}` : '';
  return `<div class="raw-finding-meta"><strong>${esc(kind)} · 原始记录</strong><span>${esc(origin + exit)}</span>${command}${item.source_ref ? `<span class="trace-meta">${esc(item.source_ref)}</span>` : ''}</div>`;
}

const directionLabel = (value) => ({abandoned:'已放弃',deferred:'已暂缓',active:'已恢复推进'}[value] || '状态待核对');
const directionCandidateLabel = (value) => ({abandoned:'放弃表述',deferred:'暂缓表述'}[value] || '状态待核对');
function directionStateText(fact) {
  const current=(state.data?.team?.directions?.current || []).find((row)=>row.fact_id===fact.id);
  return current ? `团队方向${directionLabel(current.direction_state)} · 本卡保留原始状态表述` : '';
}
function directionFindingMeta(item) {
  if (!item.direction_state) return '';
  return `<p class="reviewed-note">${esc(item.status === 'proposed' ? directionCandidateLabel(item.direction_state) : directionLabel(item.direction_state))} · ${item.status === 'proposed' ? '状态候选' : item.current ? '当前确认' : '历史确认'} · 下方保留原始状态表述</p>`;
}

function renderDirections(directions, isTeam) {
  const surface=document.getElementById('direction-surface');
  if (!surface) return;
  const current=directions.current || [], history=directions.history || [];
  const rows=isTeam ? current.filter((row)=>row.direction_state !== 'active') : directions.candidates || [];
  document.getElementById('direction-title').textContent=isTeam ? '舍弃与暂缓方向' : '方向状态候选';
  document.getElementById('direction-count').textContent=rows.length;
  document.getElementById('direction-note').textContent=isTeam ? '清单仅收录负责人明确确认的方向。恢复及旧状态保留在下方历史。' : '明确表述仍是候选。未被采用的建议不会自动归为已放弃。';
  const entry=(row, historical=false)=>`<div class="direction-entry"><div class="detail-label">${esc(!isTeam && row.status === 'proposed' ? directionCandidateLabel(row.direction_state) : directionLabel(row.direction_state))} · ${historical ? row.current ? '当前确认' : '历史确认' : isTeam ? '负责人已确认' : '待负责人确认'}</div><button class="direction-open" data-fact-id="${esc(row.fact_id)}">${esc(row.quote)}</button><p class="reviewed-note">${esc(row.member_id || '')} · ${sourceTimeHTML(row)}</p>${row.reviewed_by ? `<p class="reviewed-note">${esc(row.reviewed_by)} · ${esc(formatTime(row.reviewed_at))}</p>` : ''}<div>${(row.evidence_ids || []).map((id)=>`<button class="answer-evidence" data-evidence-id="${esc(id)}">${esc(id)}</button>`).join('')}</div>${sourceNote(row) ? `<p class="source-warning">${esc(sourceNote(row))}</p>` : ''}</div>`;
  document.getElementById('direction-list').innerHTML=rows.length ? rows.map((row)=>entry(row)).join('') : `<p class="empty">${isTeam ? '尚无当前已确认的放弃或暂缓方向。' : '尚无可归类的明确方向表述。可继续查看个人候选和原始来源。'}</p>`;
  document.getElementById('direction-history').innerHTML=isTeam && history.length ? `<div class="history-label">方向状态历史 · ${history.length}</div>${history.map((row)=>entry(row,true)).join('')}` : '';
}

function reasonPreviewHTML(preview) {
  const rows=preview?.candidates || [], gaps=preview?.gaps || [];
  const links=(ids)=>(ids || []).map((id)=>`<button class="answer-evidence" data-evidence-id="${esc(id)}">${esc(id)}</button>`).join('');
  const target=(row)=>`<button class="reason-target" data-fact-id="${esc(row.target_fact_id)}">对应陈述 · ${esc(row.target_quote)}</button>`;
  const quotes=(items,label)=>!items?.length ? '' : `<details class="reason-sources"><summary>${esc(label)} · ${items.length}</summary>${items.map((q)=>`<div class="detail-block"><p class="reviewed-note">${esc(q.member_id)} · ${esc(claimLabel(q.claim_type))} · ${esc(q.reported_by)} · ${sourceTimeHTML(q)}</p><pre class="reason-quote">${esc(q.quote)}</pre>${links([q.source_id])}</div>`).join('')}</details>`;
  return `<p class="empty">只读预览 · 待负责人核对。引文匹配仅核对出处，原因联系与模型分类仍需核对。</p>${preview?.unavailable_targets ? `<p class="source-warning">${esc(preview.unavailable_targets)} 项目标或来源已变化，相关预览暂未展示。</p>` : ''}${rows.map((row)=>`<article class="reason-card"><div class="detail-label">${row.relation_type_suggested==='hypothesized_reason' ? '推断假说' : '来源说明候选'} · 待核对</div>${target(row)}<p class="detail-value">${esc(row.summary)}</p>${row.assumptions?.length ? `<div class="reason-assumptions"><strong>推断前提</strong>${row.assumptions.map((a)=>`<p>${esc(a)}</p>`).join('')}</div>` : ''}${quotes(row.source_quotes,'原文引文')}${quotes(row.counter_evidence,'反向或替代线索')}<p class="reviewed-note">生成模型 ${esc(row.model || '')}</p></article>`).join('')}${gaps.map((gap)=>`<div class="reason-gap">${target(gap)}<p class="detail-label">所选材料中的缺证说明</p><p class="detail-value">${esc(gap.text)}</p>${links(gap.scope_source_ids)}</div>`).join('')}${!rows.length&&!gaps.length ? '<p class="empty">当前问题或视图没有匹配的原因预览，可继续核对原始材料。</p>' : ''}`;
}

function renderReasonPreview() {
  const surface=document.getElementById('reason-preview-surface');
  if (!surface) return;
  const preview=state.data?.reason_preview;surface.hidden=!preview?.enabled;
  if (!preview?.enabled) return;
  const selected={...preview,candidates:(preview.candidates || []).filter((r)=>state.lens==='team'||r.member_id===state.member),gaps:(preview.gaps || []).filter((r)=>state.lens==='team'||r.member_id===state.member)};
  document.getElementById('reason-preview-count').textContent=selected.candidates.length;
  document.getElementById('reason-preview-list').innerHTML=reasonPreviewHTML(selected);
}

function renderInvestigation(answer) {
  const panel = document.getElementById("investigation-answer");
  panel.hidden = false;
  const evidenceLinks = (ids) => ids.map((id) => `<button class="answer-evidence" data-evidence-id="${esc(id)}">${esc(id)}</button>`).join("");
  const scopeLabel = answer.scope ? "MEMBER RECORDS" : "CURRENT TEAM STATE";
  const basis = answer.next_action_basis;
  const actionLabel = answer.next_action_status === 'accepted' ? 'CONFIRMED NEXT ACTION' : basis ? 'NEXT ACTION CANDIDATE' : 'NEXT ACTION';
  const basisHTML = basis ? `<div class="detail-block"><p class="reviewed-note">${sourceTimeHTML(basis)} · ${esc(basis.member_id || '')} · ${esc(claimLabel(basis.claim_type))}</p>${basis.status === 'accepted' ? `<p class="reviewed-note">${esc(basis.reviewed_by || 'owner')} · ${sourceTimeHTML({observed_at:basis.reviewed_at}, '负责人确认时间')}</p>` : '<p class="source-warning">候选来源的当前版本需核对，历史任务不会自动成为当前确认。</p>'}${evidenceLinks(basis.evidence_ids || [])}</div>` : '';
  panel.innerHTML = `<div class="answer-conclusion">${esc(answer.conclusion)}</div><div class="answer-grid"><div><div class="detail-label">${scopeLabel}</div><div class="detail-value">${esc(answer.goal)} · ${answer.active_tasks.map(esc).join(" · ") || "No active task"}</div></div><div><div class="detail-label">WHY / EVIDENCE</div><div class="answer-evidence-list">${evidenceLinks(answer.evidence_ids)}</div></div><div><div class="detail-label">${actionLabel}</div><div class="detail-value">${esc(answer.next_action)}</div>${basisHTML}</div><div><div class="detail-label">ACCEPTANCE CONDITION</div><div class="detail-value">${esc(answer.acceptance_condition)}</div></div></div>${answer.conflicts.length ? `<div class="answer-conflicts"><div class="detail-label">CONFLICT HISTORY</div>${answer.conflicts.map((conflict) => `<div class="answer-conflict"><strong>${esc(conflict.subject_key)}</strong>${conflict.sides.map((side) => `<span>${esc(side.status)} · ${esc(side.value)} · ${sourceTimeHTML(side)} · ${evidenceLinks(side.evidence_ids)}</span>`).join("")}</div>`).join("")}</div>` : ""}`;
  if (answer.findings?.length) {
    panel.querySelector(".answer-evidence-list").innerHTML = answer.findings.map((item) => `<div class="detail-block"><div class="detail-label">${esc(claimLabel(item.claim_type))} · ${esc(item.status)}</div>${rawFindingMeta(item)}${directionFindingMeta(item)}${item.member_id ? `<p class="reviewed-note">${esc(item.member_id)} · ${sourceTimeHTML(item)}</p>` : ""}${item.reviewed_by ? `<p class="reviewed-note">${esc(item.reviewed_by)} · ${esc(formatTime(item.reviewed_at))}</p>` : ""}<p class="detail-value">${esc(item.quote)}</p>${item.excerpt_truncated ? '<p class="reviewed-note">原文摘录，完整内容可从来源打开。</p>' : ""}${evidenceLinks(item.evidence_ids)}</div>`).join("");
    panel.querySelector(".answer-grid .detail-value").textContent = answer.scope ? answer.goal : `${answer.goal} · ${answer.active_tasks.length} 条任务声明，原文见下方看板。`;
  }
  if (answer.memory?.enabled) {
    const note = document.createElement("p");
    note.className = "reviewed-note";
    note.textContent = `${answer.memory.engine} · ${answer.memory.fact_count} facts · ${answer.memory.support_count} support links · ${answer.memory.review_count} owner reviews · ${answer.memory.commit?.slice(0, 7)}`;
    panel.appendChild(note);
  }
  if (answer.source_warnings?.length) {
    const warning = document.createElement("div");
    warning.className = "source-warning";
    warning.innerHTML = `<div class="detail-label">来源变更，需复核</div>${answer.source_warnings.map((item) => `<p>${esc(item.fact_id)} · ${esc(item.source_state.status)} · 历史确认 ${esc(item.status)}</p>${evidenceLinks([...item.evidence_ids, ...item.source_state.replacement_ids, ...item.source_state.observation_ids])}`).join("")}`;
    panel.prepend(warning);
  }
  if (answer.zetaris?.queried) {
    const note = document.createElement("p");
    note.className = "reviewed-note";
    note.textContent = `${answer.zetaris.engine} · ${answer.zetaris.source_count} verified sources · ${answer.zetaris.family_count} source families · ${answer.zetaris.elapsed_ms} ms`;
    panel.appendChild(note);
  }
  if (answer.next_action_status) {
    const action = document.createElement("p");
    action.className = "reviewed-note";
    action.textContent = answer.next_action_status === "accepted" ? "下一步已由负责人确认" : "下一步仍为待确认建议";
    panel.append(action);
  }
  if (answer.evidence_gaps?.length) {
    const gaps = document.createElement("div");
    gaps.className = "answer-conflicts";
    gaps.innerHTML = `<div class="detail-label">EVIDENCE GAPS</div>${answer.evidence_gaps.map((gap) => `<p class="detail-value">${esc(gap)}</p>`).join("")}`;
    panel.prepend(gaps);
  }
  if (answer.reason_preview?.enabled) {
    const reasons=document.createElement('div');reasons.className='answer-conflicts';
    reasons.innerHTML=`<div class="detail-label">原因候选预览</div>${reasonPreviewHTML(answer.reason_preview)}`;
    panel.appendChild(reasons);
  }
  bindDynamicControls();
}

async function investigateProject(event) {
  event.preventDefault();
  if (!state.data) {
    const panel=document.getElementById('investigation-answer');
    panel.hidden=false;
    panel.innerHTML='<p class="empty">项目状态尚未加载完成，请加载成功后再提问。</p>';
    return;
  }
  const question = document.getElementById("investigation-question").value.trim();
  if (!question) return;
  const revision = state.revision;
  const requestId = ++state.investigationRequest;
  const panel = document.getElementById("investigation-answer");
  panel.hidden = false;
  panel.innerHTML = `<p class="empty">正在按证据链调查…</p>`;
  try {
    const response = await fetch("/api/investigate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question})});
    const answer = await response.json();
    if (revision !== state.revision || requestId !== state.investigationRequest) return;
    if (!response.ok) throw new Error(answer.error || "investigation failed");
    renderInvestigation(answer);
  } catch (error) {
    if (revision !== state.revision || requestId !== state.investigationRequest) return;
    panel.innerHTML = `<p class="empty">调查失败：${esc(error.message)}</p>`;
  }
}

function metric(value, text) { return `<div class="metric"><span class="metric-value">${esc(value)}</span><span class="metric-label">${esc(text)}</span></div>`; }

function buildTraceGroups(facts, relations, catalog, reviews, teamFacts) {
  const sources = new Map(catalog.map((item) => [item.id, item]));
  const team = new Map(teamFacts.map((fact) => [fact.id, fact]));
  const groups = new Map();
  const timestamp = (record) => hasSourceTime(record) ? new Date(record.observed_at).getTime() : Infinity;
  for (const fact of facts) {
    const subject = fact.subject_key || fact.id;
    if (!groups.has(subject)) groups.set(subject, {subject, facts: [], time: Infinity, latestTime: -Infinity});
    const related = relations.filter((relation) => relation.fact_id === fact.id);
    const ids = [...new Set([...(fact.evidence_ids || []), ...related.map((relation) => relation.evidence_id)])];
    const citations = ids.filter((id) => sources.has(id)).map((id) => ({source: sources.get(id),
      confirmed: related.some((relation) => relation.evidence_id === id && relation.status === 'confirmed')}));
    const accepted = reviews.filter((review) => review.action === 'accept_fact' && review.fact_id === fact.id);
    const markers = accepted.map((review, index) => ({...review, current: index === accepted.length - 1 &&
      team.get(fact.id)?.status === 'accepted' && sourceCurrent(team.get(fact.id))}));
    const group = groups.get(subject);
    const times = [timestamp(fact), ...citations.map(({source}) => timestamp(source))];
    group.time = Math.min(group.time, ...times);
    group.latestTime = Math.max(group.latestTime, ...times.filter(Number.isFinite), ...markers.map(timestamp).filter(Number.isFinite));
    group.facts.push({fact, citations, missing: ids.filter((id) => !sources.has(id)), reviews: markers});
  }
  for (const group of groups.values()) group.facts.sort((a, b) => timestamp(a.fact) - timestamp(b.fact));
  return [...groups.values()].sort((a, b) => a.time - b.time);
}

function renderTrace(facts, relations, catalog) {
  const scroller = document.getElementById('trace-scroller');
  const track = document.getElementById('trace-track');
  const key = JSON.stringify([state.lens, state.lens === 'personal' ? state.member : null]);
  state.tracePositions ||= {};
  if (state.traceViewKey && Number.isFinite(scroller.scrollLeft)) state.tracePositions[state.traceViewKey] = scroller.scrollLeft;
  const groups = buildTraceGroups(facts, relations, catalog, state.data.fact_reviews || [], state.data.team?.facts || []);
  state.traceLatestIndex = groups.reduce((latest, group, index) => Number.isFinite(group.latestTime) &&
    (latest < 0 || group.latestTime > groups[latest].latestTime) ? index : latest, -1);
  document.getElementById('trace-latest').disabled = state.traceLatestIndex < 0;
  document.getElementById('trace-count').textContent = `${groups.length} 主题 · ${facts.length} 条记录`;
  track.innerHTML = groups.length ? groups.map((group, index) => `<section class="trace-group" data-trace-index="${index}">
    <header class="trace-group-head"><span class="trace-sequence">${String(index + 1).padStart(2, '0')}</span><div><strong>${esc(label(group.facts[0].fact.kind))}${group.facts.length > 1 ? ` · ${group.facts.length} 条记录` : ''}</strong><small>${esc(group.subject)}</small></div></header>
    <div class="trace-branches">${group.facts.map(({fact, citations, missing, reviews}) => { const shownCitations = state.layer === 'process' ? citations.slice(0, 2) : citations; const hiddenCitationCount = citations.length - shownCitations.length; return `<div class="trace-branch">
      <div class="trace-sources">${shownCitations.map(({source, confirmed}) => `<div class="trace-citation"><button class="trace-source" data-evidence-id="${esc(source.id)}"><span class="fact-kind">原始来源</span><span class="trace-copy">${esc(source.content_preview || source.id)}</span><span class="trace-meta">${esc(source.author || '作者未提供')} · ${esc(source.kind || '来源记录')}</span><span class="trace-meta">${sourceTimeHTML({...source, source_kind: source.kind})}</span></button><div class="trace-wire ${confirmed ? 'confirmed' : 'candidate'}"><span>${confirmed ? '支持关系已确认' : '引用关联待核对'}</span></div></div>`).join('')}
      ${hiddenCitationCount > 0 ? `<p class="trace-gap">过程层显示 ${shownCitations.length} 条来源，另有 ${hiddenCitationCount} 条可在原文层查看。</p>` : ''}
      ${missing.length || !citations.length ? `<p class="trace-gap">缺失来源 ${esc(missing.join(' · ') || '未提供来源引用')}</p>` : ''}</div>
      <button class="trace-fact ${fact.status === 'conflicted' ? 'conflicted' : ''}" data-fact-id="${esc(fact.id)}"><span class="fact-kind">${esc(label(fact.kind))} · ${esc(statusLabel(fact.status))}</span><span class="trace-copy">${esc(fact.value)}</span><span class="trace-meta">${esc(fact.author || '作者未提供')} · ${esc(claimLabel(fact.claim_type))}</span><span class="trace-meta">${sourceTimeHTML(fact)}</span>${directionStateText(fact) ? `<span class="trace-meta">${esc(directionStateText(fact))}</span>` : ''}${sourceNote(fact) ? `<span class="trace-gap">${esc(sourceNote(fact))}</span>` : ''}</button>
      ${reviews.map((review) => `<div class="trace-review ${review.current ? 'current' : 'historical'}"${review.current ? ' data-current-team="true"' : ''}><strong>${review.current ? '当前团队确认' : '历史确认'}</strong><span>${esc(review.reviewer || 'owner')} · ${esc(formatTime(review.observed_at))}</span></div>`).join('')}
    </div>`}).join('')}</div></section>`).join('') : `<p class="empty">当前视图尚无结构化记录。来源材料仍可在下方查看，整理后才会形成历程节点。</p>`;
  if (Object.prototype.hasOwnProperty.call(state.tracePositions, key)) scroller.scrollLeft = state.tracePositions[key];
  else {
    const current = track.querySelector?.('[data-current-team]');
    const group = current?.closest?.('.trace-group');
    if (group) scroller.scrollLeft = group.offsetLeft;
    else jumpTrace(true);
  }
  state.traceViewKey = key;
}

function jumpTrace(latest) {
  const scroller = document.getElementById('trace-scroller');
  const group = latest && state.traceLatestIndex >= 0 ? document.getElementById('trace-track').querySelector?.(`[data-trace-index="${state.traceLatestIndex}"]`) : null;
  scroller.scrollLeft = Math.min(group?.offsetLeft || 0, Math.max(0, (scroller.scrollWidth || 0) - (scroller.clientWidth || 0)));
}

function factCard(fact, relations) {
  const related = relations.filter((relation) => relation.fact_id === fact.id);
  const confirmed = related.some((relation) => relation.status === "confirmed");
  const currentStatus = fact.status === "accepted" ? "accepted" : confirmed ? "confirmed" : fact.status === "conflicted" ? "conflicted" : "candidate";
  const author = fact.author ? ` · ${esc(fact.author)}` : "";
  return `<button class="fact-card" data-fact-id="${esc(fact.id)}"><div class="fact-main"><div class="fact-kind">${esc(label(fact.kind))}</div><div class="fact-value">${esc(fact.value)}</div><div class="fact-meta">${esc(claimLabel(fact.claim_type))}${author} · ${related.length} relation(s)</div><div class="fact-meta">${sourceTimeHTML(fact)}${fact.status === 'accepted' ? '' : ' · 当前有效性待核对'}</div>${directionStateText(fact) ? `<div class="fact-meta">${esc(directionStateText(fact))}</div>` : ''}${sourceNote(fact) ? `<p class="source-warning">${esc(sourceNote(fact))} · 原记录保留</p>` : ""}</div><span class="relation-label ${currentStatus}">${esc(currentStatus)}</span></button>`;
}

function renderTeamHistory() {
  const surface = document.getElementById("team-truth-history");
  const conflicts = state.data.team?.conflicts || [];
  const history = state.data.review_history || [];
  surface.hidden = state.lens !== "team" || (!conflicts.length && !history.length);
  document.getElementById("conflict-count").textContent = conflicts.length;
  document.getElementById("conflict-list").innerHTML = conflicts.length ? conflicts.map((conflict) => `<div class="conflict-card"><div class="conflict-key">${esc(conflict.subject_key)}</div>${conflict.sides.map((side) => `<div class="conflict-side"><span class="conflict-status ${side.status === "accepted" ? "accepted" : "conflicted"}">${esc(side.status)}</span><span>${esc(side.value)}</span><span class="conflict-meta">${esc(side.author)} · ${sourceTimeHTML(side)} · ${side.evidence_ids.map(esc).join(" · ")}</span></div>`).join("")}</div>`).join("") : `<p class="empty">当前没有冲突记录。</p>`;
    document.getElementById("history-list").innerHTML = history.length ? `<div class="history-label">REVIEW HISTORY</div>${history.map((entry) => `<div class="history-item"><strong>${esc(({accept_fact:'确认团队事实',confirm_relation:'确认证据关系',confirm_abandoned:'确认放弃方向',confirm_deferred:'确认暂缓方向',restore_direction:'恢复方向'})[entry.action] || entry.action)}</strong><span>${esc(entry.reviewer || "owner")} · ${esc(entry.fact_id || entry.relation_id || "")} · ${esc(formatTime(entry.observed_at))}</span></div>`).join("")}` : "";
}

function evidenceCard(item) {
  const links = (item.candidate_fact_ids?.length || 0) + (item.confirmed_fact_ids?.length || 0);
  const sourceKind = ({assistant_message:"助手发言",user_message:"用户发言",tool_call:"工具调用",tool_result:"工具结果",command_execution:"命令执行"})[item.kind] || item.kind || "来源记录";
  const commandNote = item.command_preview ? `<span class="evidence-command">${esc(item.command_preview)}</span><span class="evidence-meta">${esc(commandOriginLabel(item.command_origin))} · 退出码 ${esc(Number.isInteger(item.exit_code) ? item.exit_code : '未知')}</span>` : '';
  return `<button class="evidence-item" data-evidence-id="${esc(item.id)}"><span class="evidence-dot ${esc(item.classification)}"></span><span><span class="evidence-title">${esc(item.content_preview || item.id)}</span>${commandNote}<span class="evidence-meta">${esc(sourceKind)} · ${esc(formatTime(item.observed_at))} · ${esc(item.classification)} · ${esc(item.source_ref || item.author)}</span></span><span class="evidence-links">${links ? `${links} link(s)` : "raw"}</span></button>`;
}

function bindDynamicControls() {
  document.querySelectorAll("[data-fact-id]").forEach((button) => button.addEventListener("click", () => openFact(button.dataset.factId)));
  document.querySelectorAll("[data-evidence-id]").forEach((button) => button.addEventListener("click", () => openEvidence(button.dataset.evidenceId)));
  document.querySelectorAll("[data-review-id]").forEach((button) => button.addEventListener("click", () => confirmRelation(button.dataset.reviewId, button.dataset.factId)));
  document.querySelectorAll("[data-fact-review-id]").forEach((button) => button.addEventListener("click", () => acceptFact(button.dataset.factReviewId)));
  document.querySelectorAll('[data-direction-review-id]').forEach((button)=>button.addEventListener('click',()=>acceptFact(button.dataset.directionReviewId,button.dataset.directionAction)));
}

function openDrawer(title, body) {
  document.getElementById("evidence-drawer").hidden = false;
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-body").innerHTML = body;
  document.getElementById("evidence-drawer").classList.add("open");
  document.getElementById("evidence-drawer").setAttribute("aria-hidden", "false");
  document.getElementById("scrim").hidden = false;
}

function openFact(factId) {
  const fact = (state.lens === "team" ? state.data.team?.facts || [] : [...(state.data.personal?.[state.member]?.facts || []), ...(state.data.team?.facts || [])]).find((item) => item.id === factId);
  if (!fact) return;
  const relations = [...(state.data.candidate_relations || []), ...(state.data.confirmed_relations || [])].filter((relation) => relation.fact_id === fact.id);
  const ids = [...new Set([...(fact.evidence_ids || []), ...relations.map((relation) => relation.evidence_id), ...(fact.source_state?.replacement_ids || []), ...(fact.source_state?.observation_ids || [])])];
  const direction=(state.data.team?.directions?.current || []).find((row)=>row.fact_id===fact.id);
  const directionAction=direction ? direction.direction_state === 'active' ? '<p class="reviewed-note">该方案已恢复推进，旧状态保留在历史。</p>' : `<button class="review-action" data-direction-review-id="${esc(fact.id)}" data-direction-action="restore_direction">恢复推进并保留历史</button>` : fact.direction_suggestion ? `<button class="review-action" data-direction-review-id="${esc(fact.id)}" data-direction-action="confirm_${fact.direction_suggestion}">确认${fact.direction_suggestion==='abandoned' ? '放弃' : '暂缓'}该方案</button>` : '';
  const acceptAction = !sourceCurrent(fact) ? `<p class="source-warning">${esc(sourceNote(fact))} · 请核对当前来源。原确认与证据仍保留。</p>` : state.lens === 'team' && directionAction ? directionAction : state.lens === "team" && fact.status !== "accepted" ? `<button class="review-action" data-fact-review-id="${esc(fact.id)}">Confirm as team fact</button>` : "";
  openDrawer(fact.value, `<div class="detail-block"><div class="detail-value">${sourceTimeHTML(fact)}</div><p class="reviewed-note">${esc(fact.session_id || "会话未提供")} · ${fact.status === "accepted" ? "负责人已确认，来源版本仍可核对" : "当前有效性待核对"}</p></div><div class="detail-block"><div class="detail-label">Fact status</div><div class="detail-value">${esc(statusLabel(fact.status))} · ${esc(fact.subject_key)}</div>${acceptAction}</div><div class="detail-block"><div class="detail-label">Evidence relations</div>${relations.length ? relations.map((relation) => `<div class="relation-item"><button class="evidence-item" data-evidence-id="${esc(relation.evidence_id)}"><span class="evidence-dot ${relation.status === "confirmed" ? "fact_source" : "execution_result"}"></span><span><span class="evidence-title">${esc(relation.basis)}</span><span class="evidence-meta">${esc(relation.method)} · ${esc(relation.status)} · ${relation.score == null ? "score unknown" : `score ${relation.score}`}</span></span><span class="evidence-links">open</span></button>${relation.status === "candidate" ? `<button class="review-action" data-review-id="${esc(relation.id)}" data-fact-id="${esc(fact.id)}">Confirm support</button>` : `<span class="reviewed-note">Confirmed by owner</span>`}</div>`).join("") : `<p class="empty">No relation has been proposed yet.</p>`}</div><div class="detail-block"><div class="detail-label">Source IDs</div><div class="detail-value">${ids.map(esc).join(" · ") || "Evidence gap"}</div></div>`);
  bindDynamicControls();
}

async function acceptFact(factId, action = 'accept_fact') {
  const reviewer = document.getElementById("reviewer-input").value.trim();
  if (!reviewer) {
    openDrawer("负责人确认", `<p class="empty">请先填写负责人姓名，再确认团队事实。</p>`);
    return;
  }
  try {
    const response = await fetch("/api/review", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action, fact_id: factId, reviewer})});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "review failed");
    if (olderState(payload)) return;
    state.data = payload;
    render();
    invalidateInvestigation("负责人确认记录已更新，请重新提问以使用最新状态。");
    openFact(factId);
  } catch (error) {
    openDrawer("负责人确认失败", `<p class="empty">${esc(error.message)}</p>`);
  }
}

async function confirmRelation(relationId, factId) {
  const reviewer = document.getElementById("reviewer-input").value.trim();
  if (!reviewer) {
    openDrawer("负责人确认", `<p class="empty">请先填写负责人姓名，再确认这条证据关系。</p>`);
    return;
  }
  try {
    const response = await fetch("/api/review", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({relation_id: relationId, reviewer}),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "review failed");
    if (olderState(payload)) return;
    state.data = payload;
    render();
    invalidateInvestigation("证据关系确认记录已更新，请重新提问以使用最新状态。");
    openFact(factId);
  } catch (error) {
    openDrawer("负责人确认失败", `<p class="empty">${esc(error.message)}</p>`);
  }
}

async function openEvidence(evidenceId) {
  openDrawer(evidenceId, `<p class="empty">Loading original material…</p>`);
  try {
    const response = await fetch(`/api/evidence?id=${encodeURIComponent(evidenceId)}`, {cache: "no-store"});
    const evidence = await response.json();
    if (!response.ok) throw new Error(evidence.error || "not found");
    const paired = evidence.metadata?.paired_evidence_id;
    const pairLink = paired ? `<div class="detail-block"><div class="detail-label">关联调用与结果</div><button class="answer-evidence" data-evidence-id="${esc(paired)}">${esc(paired)}</button><p class="empty">这条关联记录执行来源，完成状态仍需核对。</p></div>` : "";
    const catalogEntry = state.data?.evidence?.find((item) => item.id === evidence.id);
    const exitCode = catalogEntry && Object.prototype.hasOwnProperty.call(catalogEntry, 'exit_code') ? catalogEntry.exit_code : evidence.metadata?.exit_code;
    const origin = catalogEntry?.command_origin || 'reported';
    const heading = origin === 'agent_report' ? 'AGENT COMMAND REPORT' : evidence.kind === 'command_execution' ? 'COMMAND EXECUTION' : 'COMMAND REPORT';
    const command = (catalogEntry?.command_preview || evidence.kind === 'command_execution') ? `<div class="detail-block"><div class="detail-label">${heading}</div><div class="raw-content">${esc(JSON.stringify(evidence.metadata?.command || []))}</div><div class="detail-value">${esc(commandOriginLabel(origin))} · ${esc(evidence.metadata?.cwd || "工作目录未知")} · ${esc(evidence.metadata?.execution_status || "报告状态未提供")} · 退出码 ${esc(Number.isInteger(exitCode) ? exitCode : "未知")}</div>${evidence.metadata?.reported_source_ref ? `<p class="detail-value">Agent声明的来源 ${esc(evidence.metadata.reported_source_ref)}</p>` : ''}<p class="empty">退出码只描述这条命令或报告，测试范围、版本和任务完成仍需独立核对。</p></div>` : "";
    openDrawer(evidence.id, `<div class="detail-block"><div class="detail-label">Source</div><div class="detail-value">${esc(evidence.metadata?.source_ref || evidence.kind)} · ${esc(evidence.author)} · ${esc(evidence.observed_at)}</div></div>${pairLink}${command}<div class="detail-block"><div class="detail-label">Original material</div><div class="raw-content">${esc(evidence.content || (evidence.kind === "command_execution" ? "此命令没有文本输出" : ""))}</div></div><div class="detail-block"><div class="detail-label">Public metadata</div><div class="detail-value">${esc(JSON.stringify(evidence.metadata || {}, null, 2))}</div></div>`);
    bindDynamicControls();
  } catch (error) {
    openDrawer(evidenceId, `<p class="empty">Could not load this material: ${esc(error.message)}</p>`);
  }
}

function closeDrawer() {
  document.getElementById("evidence-drawer").classList.remove("open");
  document.getElementById("evidence-drawer").setAttribute("aria-hidden", "true");
  document.getElementById("evidence-drawer").hidden = true;
  document.getElementById("scrim").hidden = true;
}

document.querySelectorAll(".lens").forEach((button) => button.addEventListener("click", () => { state.lens = button.dataset.lens; render(); }));
document.querySelectorAll(".layer-choice").forEach((button) => button.addEventListener("click", () => { state.layer = button.dataset.layerChoice; render(); }));
document.getElementById("member-select").addEventListener("change", (event) => { state.member = event.target.value; render(); });
document.getElementById("drawer-close").addEventListener("click", closeDrawer);
document.getElementById("scrim").addEventListener("click", closeDrawer);
document.getElementById("investigation-form").addEventListener("submit", investigateProject);
document.getElementById("refresh-intake").addEventListener("click", refreshNative);
document.getElementById("rescan-project").addEventListener("click", rescanProject);
document.getElementById("extract-candidates").addEventListener("click", extractCandidates);
document.getElementById('trace-start').addEventListener('click', () => jumpTrace(false));
document.getElementById('trace-latest').addEventListener('click', () => jumpTrace(true));
setInterval(() => { if (!document.hidden) refreshNative(); }, 5000);
document.getElementById("evidence-filter").addEventListener("input", (event) => { state.evidenceQuery = event.target.value; render(); });
loadState().catch((error) => { document.getElementById("connection-label").textContent = `State unavailable: ${error.message}`; });
