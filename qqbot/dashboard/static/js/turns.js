/* core.js sub-section: 对话流水(turn) -- auto-split */
// ── 对话流水(turn)──
let _turns = [];                // [{turn_id, started_at, meta, steps, status, duration}]
let _turnsEventSource = null;   // EventSource 实例

async function loadTurnsHistory(){
  try{
    const r = await safeFetch('/api/turns/history?limit=200');
    const data = await r.json();
    _turns = data.turns || [];
    renderTurns();
  }catch(e){toast('加载 turn 历史失败',false)}
}
function reloadTurnsHistory(){loadTurnsHistory()}

function connectTurnsStream(){
  if(_turnsEventSource){try{_turnsEventSource.close()}catch(e){}}
  try{
    // EventSource 不能带 header，通过 query 传 token（main.py 中间件已支持）
    const token=encodeURIComponent(window._DASHBOARD_TOKEN||'');
    const es = new EventSource('/api/turns/stream?token='+token);
    es.onmessage = (ev) => {
      try{
        const e = JSON.parse(ev.data);
        applyTurnEvent(e);
      }catch(err){console.warn('turns stream parse error',err)}
    };
    es.onerror = () => { /* EventSource 会自动重连 */ };
    _turnsEventSource = es;
  }catch(e){console.warn('connect turns stream failed',e)}
}

function applyTurnEvent(ev){
  const t = ev.type;
  if(t === 'turn_started'){
    const turn = ev.turn;
    turn.steps = turn.steps || [];
    _turns.unshift(turn);
    if(_turns.length > 200) _turns.length = 200;
    renderTurns(true);
  } else if(t === 'step_added'){
    const turn = _turns.find(x => x.turn_id === ev.turn_id);
    if(turn){
      (turn.steps = turn.steps || []).push(ev.step);
      renderTurns(true);
    }
  } else if(t === 'turn_ended'){
    const turn = _turns.find(x => x.turn_id === ev.turn_id);
    if(turn){
      turn.status = ev.status;
      turn.duration = ev.duration;
      renderTurns(true);
    }
  }
}

function _stepKindBadge(kind){
  const map = {
    msg: ['💬', '#4a90e2'],
    image: ['🖼️', '#9b59b6'],
    queue: ['⏳', '#f39c12'],
    probe: ['🔍', '#16a085'],
    llm_call: ['🧠', '#8e44ad'],
    thinking: ['💭', '#7f8c8d'],
    tool_call: ['🔧', '#e67e22'],
    tool_result: ['📦', '#d35400'],
    stream_chunk: ['⚡', '#3498db'],
    stream_reply: ['📤', '#2ecc71'],
    reply: ['📤', '#2ecc71'],
    error: ['❌', '#e74c3c'],
    warning: ['⚠️', '#f39c12'],
  };
  return map[kind] || ['•', '#95a5a6'];
}

function _statusBadge(status){
  const map = {
    running: ['进行中', '#f39c12'],
    ok: ['成功', '#2ecc71'],
    empty: ['空回复', '#95a5a6'],
    error: ['失败', '#e74c3c'],
  };
  return map[status] || [status||'?', '#bdc3c7'];
}

function _formatTime(ts){
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', {hour12:false});
}

function _formatDuration(sec){
  if(sec == null) return '';
  if(sec < 1) return (sec*1000).toFixed(0) + 'ms';
  if(sec < 60) return sec.toFixed(1) + 's';
  return Math.floor(sec/60) + 'm' + Math.floor(sec%60) + 's';
}

function _stepToHtml(step, idx){
  // stream_chunk / queue 噪音太大,直接过滤掉
  if(step.kind === 'stream_chunk') return '';
  const [icon, color] = _stepKindBadge(step.kind);
  const title = escapeHtml(step.title || '');
  const ts = _formatTime(step.ts || 0);
  // thinking / probe / llm_call 默认折叠(细节噪音),其他展开
  const collapsed = (step.kind === 'thinking' || step.kind === 'probe' || step.kind === 'llm_call');
  const body = _renderStepBody(step);
  return `<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px dashed var(--border)">
    <div style="flex:0 0 50px;font-size:10px;color:var(--text2);font-family:monospace;padding-top:2px">${ts}</div>
    <div style="flex:0 0 22px;text-align:center;font-size:14px">${icon}</div>
    <div style="flex:1;min-width:0">
      ${collapsed
        ? `<details><summary style="cursor:pointer;color:${color};font-size:12px;font-weight:600">${title}</summary>${body}</details>`
        : `<div style="color:${color};font-size:12px;font-weight:600;margin-bottom:2px">${title}</div>${body}`}
    </div>
  </div>`;
}

function _renderStepBody(step){
  if(!step.data) return '';
  const d = step.data;
  switch(step.kind){
    case 'msg': {
      const text = d.text || '';
      return `<div style="background:var(--bg-2);padding:8px 10px;border-radius:4px;font-size:12px;margin:4px 0;white-space:pre-wrap;max-height:240px;overflow-y:auto;border-left:3px solid #4a90e2"><span style="font-size:10px;color:#4a90e2;font-weight:600">👤 用户</span><div style="margin-top:4px">${escapeHtml(text)}</div>${d.has_images?`<div style="margin-top:4px;font-size:10px;color:var(--text2)">🖼️ ${d.has_images} 张图</div>`:''}</div>`;
    }
    case 'reply':
    case 'stream_reply': {
      const text = d.reply || d.text || '';
      const len = d.full_len || (d.len || text.length);
      return `<div style="background:var(--bg-2);padding:8px 10px;border-radius:4px;font-size:12px;margin:4px 0;white-space:pre-wrap;max-height:300px;overflow-y:auto;border-left:3px solid #2ecc71"><span style="font-size:10px;color:#2ecc71;font-weight:600">🤖 助手</span><div style="margin-top:4px">${escapeHtml(text)}</div><div style="margin-top:4px;font-size:10px;color:var(--text2)">${len} 字${d.segments?` · ${d.segments} 段`:''}</div></div>`;
    }
    case 'thinking': {
      return `<pre style="background:#1e1e2e;color:#cdd6f4;padding:8px;border-radius:4px;font-size:11px;overflow:auto;margin:4px 0;max-height:180px;white-space:pre-wrap">${escapeHtml(d.text||'')}</pre>`;
    }
    case 'image': {
      const imgs = d.image_data || [];
      return `<div style="margin:4px 0;font-size:11px;color:var(--text2)">📷 ${imgs.length} 张图片</div>`;
    }
    case 'tool_call': {
      const tools = d.tools || [];
      return '<div style="margin:4px 0">' + tools.map(t=>{
        const args = t.arguments || '{}';
        return `<div style="background:#fef9e7;padding:6px 8px;border-radius:4px;font-size:11px;margin:2px 0;border-left:3px solid #e67e22"><b>${escapeHtml(t.name)}</b> <span style="color:var(--text2);font-family:monospace;font-size:10px">${escapeHtml(args.length>200?args.slice(0,200)+'…':args)}</span></div>`;
      }).join('') + '</div>';
    }
    case 'tool_result': {
      const preview = d.result_preview || '';
      const len = d.result_len || preview.length;
      return `<div style="background:#eafaf1;padding:8px;border-radius:4px;font-size:11px;margin:4px 0;font-family:monospace;max-height:200px;overflow-y:auto;white-space:pre-wrap;border-left:3px solid #d35400"><div style="font-size:10px;color:#d35400;font-weight:600;margin-bottom:4px">📦 ${escapeHtml(d.tool||'工具结果')} (${len} 字)</div>${escapeHtml(preview)}</div>`;
    }
    case 'llm_call': {
      return `<div style="font-size:11px;color:var(--text2);margin:4px 0;display:flex;gap:12px;flex-wrap:wrap">
        <span><b style="color:var(--text)">provider:</b> ${escapeHtml(d.provider||'?')}</span>
        <span><b style="color:var(--text)">model:</b> ${escapeHtml(d.model||'?')}</span>
        <span><b style="color:var(--text)">msgs:</b> ${d.messages_count||0}</span>
        <span><b style="color:var(--text)">tools:</b> ${d.tools_count||0}</span>
        ${d.reasoning_effort?`<span><b style="color:var(--text)">reasoning:</b> ${escapeHtml(d.reasoning_effort)}</span>`:''}
      </div>`;
    }
    case 'probe': {
      const decisions = {true:'需工具', false:'直接答', skipped:'跳过探测'};
      const decision = decisions[String(d.needs_tools)] || (d.skipped?'跳过':'?');
      return `<div style="font-size:11px;color:var(--text2);margin:4px 0">
        决策: <b style="color:var(--text)">${decision}</b>
        ${d.reason?` · ${escapeHtml(d.reason)}`:''}
      </div>`;
    }
    case 'error': {
      return `<div style="background:#fadbd8;padding:6px 8px;border-radius:4px;font-size:11px;margin:4px 0;color:#c0392b;border-left:3px solid #e74c3c">${escapeHtml(d.msg||d.error||JSON.stringify(d))}</div>`;
    }
    case 'warning': {
      return `<div style="background:#fef9e7;padding:6px 8px;border-radius:4px;font-size:11px;margin:4px 0;color:#7d6608;border-left:3px solid #f39c12">${escapeHtml(d.msg||d.warning||JSON.stringify(d))}</div>`;
    }
    default: {
      return `<pre style="background:var(--bg-2);padding:4px 8px;border-radius:4px;font-size:10px;margin:4px 0;max-height:120px;overflow:auto;color:var(--text2)">${escapeHtml(JSON.stringify(d,null,2))}</pre>`;
    }
  }
}

function _turnSummaryChips(turn){
  const steps = turn.steps || [];
  let provider='', model='', reasoning='', msgCount=0, toolCount=0, toolsCalled=[];
  let replyLen=0, replySegments=0, thinkingRounds=0;
  for(const s of steps){
    if(s.kind === 'llm_call'){
      provider = s.data.provider || provider;
      model = s.data.model || model;
      msgCount = s.data.messages_count || msgCount;
      toolCount = s.data.tools_count || toolCount;
      reasoning = s.data.reasoning_effort || reasoning;
    }
    if(s.kind === 'tool_call'){
      const ts = s.data.tools || [];
      toolsCalled.push(...ts.map(t=>t.name));
    }
    if(s.kind === 'thinking') thinkingRounds++;
    if(s.kind === 'reply' || s.kind === 'stream_reply'){
      replyLen = s.data.full_len || s.data.len || replyLen;
      replySegments = s.data.segments || replySegments;
    }
  }
  const chipStyle = 'display:inline-block;padding:2px 8px;margin:2px 4px 2px 0;border-radius:10px;font-size:10px;font-weight:600;line-height:1.5';
  const provChip = provider
    ? `<span style="${chipStyle};background:${provider==='minimax'?'#fef9e7':'#e8f4fd'};color:${provider==='minimax'?'#7d6608':'#1f4e79'}">${escapeHtml(provider)}</span>`
    : '';
  const modelChip = model
    ? `<span style="${chipStyle};background:var(--bg-2);color:var(--text2)">${escapeHtml(model.replace(/^MiniMax-/,''))}</span>`
    : '';
  const reasoningChip = reasoning
    ? `<span style="${chipStyle};background:${reasoning==='high'?'#fadbd8':reasoning==='low'?'#d5f5e3':'#fef9e7'};color:${reasoning==='high'?'#922b21':reasoning==='low'?'#1e8449':'#7d6608'}">🧠 ${reasoning}</span>`
    : '';
  const toolsChip = toolCount > 0
    ? `<span style="${chipStyle};background:#eafaf1;color:#1e8449">🔧 ${toolCount}</span>`
    : '';
  const toolsListChip = toolsCalled.length
    ? `<span style="${chipStyle};background:#fef9e7;color:#7d6608">${toolsCalled.map(escapeHtml).join(', ')}</span>`
    : '';
  const replyChip = replyLen > 0
    ? `<span style="${chipStyle};background:#d5f5e3;color:#1e8449">📤 ${replyLen}字${replySegments>1?`·${replySegments}段`:''}</span>`
    : '';
  const thinkingChip = thinkingRounds > 0
    ? `<span style="${chipStyle};background:#f4f4f5;color:#555">💭 ${thinkingRounds}轮</span>`
    : '';
  const durChip = turn.duration
    ? `<span style="${chipStyle};background:${turn.duration>10?'#fadbd8':turn.duration>5?'#fef9e7':'#d5f5e3'};color:${turn.duration>10?'#922b21':turn.duration>5?'#7d6608':'#1e8449'}">⏱️ ${_formatDuration(turn.duration)}</span>`
    : '';
  return [provChip, modelChip, reasoningChip, toolsChip, toolsListChip, thinkingChip, replyChip, durChip].filter(Boolean).join('');
}

function _turnToHtml(turn){
  const m = turn.meta || {};
  const [statusText, statusColor] = _statusBadge(turn.status);
  const sender = m.sender_name ? `${escapeHtml(m.sender_name)} (${m.uid})` : (m.uid || '?');
  const skill = m.skill || '-';
  const imgCount = m.image_count || 0;
  const text = m.text || '';
  const dur = _formatDuration(turn.duration);
  const startedAt = _formatTime(turn.started_at);
  const chatLocation = m.chat_type === 'GROUP'
    ? `<span style="background:#f4f4f5;color:#555">群 ${m.group_id||'?'}</span>`
    : `<span style="background:#f4f4f5;color:#555">私聊</span>`;
  const chips = _turnSummaryChips(turn);

  return `<div class="card" style="margin-bottom:14px;border-left:4px solid ${statusColor};padding:10px 12px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text2);flex-wrap:wrap">
        <span style="background:${statusColor};color:#fff;padding:1px 8px;border-radius:3px;font-size:10px;font-weight:600">${statusText}</span>
        ${chatLocation}
        <span style="font-family:monospace">${startedAt}</span>
        <span><b style="color:var(--text)">${sender}</b></span>
        <span>→ <b style="color:#9b59b6">${escapeHtml(skill)}</b></span>
        ${imgCount ? `<span style="color:#9b59b6">🖼️ ${imgCount}</span>` : ''}
      </div>
      <div style="font-size:10px;color:var(--text2);font-family:monospace">${turn.turn_id?turn.turn_id.slice(0,8):''}</div>
    </div>
    ${chips ? `<div style="margin-bottom:8px;line-height:1.8">${chips}</div>` : ''}
    ${text ? `<details><summary style="cursor:pointer;font-size:11px;color:var(--text2);user-select:none">📩 用户消息 ${text.length} 字</summary><div style="background:var(--bg-2);padding:8px 10px;border-radius:4px;margin:4px 0;font-size:12px;white-space:pre-wrap;max-height:160px;overflow-y:auto;border-left:3px solid #4a90e2">${escapeHtml(text)}</div></details>` : ''}
    <div style="margin-top:6px;border-top:1px solid var(--border);padding-top:4px">
      ${(turn.steps||[]).map(_stepToHtml).join('')}
    </div>
  </div>`;
}

function renderTurns(onlyAppend){
  const filterEl = document.getElementById('turnFilter');
  const statusEl = document.getElementById('turnStatusFilter');
  const listEl = document.getElementById('turnsList');
  const kw = (filterEl?.value || '').trim().toLowerCase();
  const statusFilter = statusEl?.value || '';

  let filtered = _turns;
  if(statusFilter) filtered = filtered.filter(t => (t.status||'running') === statusFilter);
  if(kw){
    filtered = filtered.filter(t => {
      const m = t.meta || {};
      return (m.text||'').toLowerCase().includes(kw)
        || (m.uid||'').toLowerCase().includes(kw)
        || (m.sender_name||'').toLowerCase().includes(kw)
        || (m.skill||'').toLowerCase().includes(kw)
        || (m.group_id||'').toLowerCase().includes(kw);
    });
  }

  // 渲染聚合统计（始终基于全量 _turns,不受筛选影响,反映全局状态）
  _renderTurnsStats(_turns);

  if(!filtered.length){
    listEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text2)">暂无 turn 数据(给 bot 发条消息试试)</div>';
    return;
  }
  listEl.innerHTML = filtered.map(_turnToHtml).join('');

  const autoEl = document.getElementById('turnAutoScroll');
  if(autoEl && autoEl.checked){
    listEl.scrollTop = 0;  // 最新在最上面
  }
}

function _renderTurnsStats(turns){
  const total = turns.length;
  let okCount = 0, errCount = 0, emptyCount = 0, runningCount = 0;
  let totalDur = 0, durCount = 0;
  const providerMap = {};   // provider -> {count, dur_sum, dur_n}
  const skillMap = {};      // skill -> count
  const userMap = {};       // uid -> count
  const statusMap = {};      // status -> count

  for(const t of turns){
    const status = t.status || 'running';
    statusMap[status] = (statusMap[status]||0) + 1;
    if(status === 'ok') okCount++;
    else if(status === 'error') errCount++;
    else if(status === 'empty') emptyCount++;
    else if(status === 'running') runningCount++;

    if(typeof t.duration === 'number'){
      totalDur += t.duration;
      durCount++;
    }
    const m = t.meta || {};
    const skill = m.skill || '?';
    skillMap[skill] = (skillMap[skill]||0) + 1;
    const uid = m.uid || '?';
    userMap[uid] = (userMap[uid]||0) + 1;

    // 从 llm_call steps 抽 provider
    let provider = '?';
    for(const s of (t.steps||[])){
      if(s.kind === 'llm_call' && s.data && s.data.provider){
        provider = s.data.provider;
        break;
      }
    }
    if(!providerMap[provider]) providerMap[provider] = {count:0, dur_sum:0, dur_n:0};
    providerMap[provider].count++;
    if(typeof t.duration === 'number'){
      providerMap[provider].dur_sum += t.duration;
      providerMap[provider].dur_n++;
    }
  }

  // 主统计卡
  const totalEl = document.getElementById('stat-total');
  const okEl = document.getElementById('stat-ok');
  const okSubEl = document.getElementById('stat-ok-sub');
  const errEl = document.getElementById('stat-err');
  const avgEl = document.getElementById('stat-avg');
  const avgSubEl = document.getElementById('stat-avg-sub');
  if(!totalEl) return;
  totalEl.textContent = total;
  okEl.textContent = okCount;
  const okRate = total > 0 ? (okCount/total*100).toFixed(0) : 0;
  okSubEl.textContent = total > 0 ? `${okRate}% 成功率 · 空回复 ${emptyCount} · 进行中 ${runningCount}` : '';
  errEl.textContent = errCount;
  const avg = durCount > 0 ? totalDur/durCount : 0;
  avgEl.textContent = avg > 0 ? _formatDuration(avg) : '-';
  avgSubEl.textContent = durCount > 0 ? `${durCount} 个 turn 有耗时` : '';

  // 细分 chip
  const breakdown = document.getElementById('turnsBreakdown');
  if(!breakdown) return;
  let h = '';
  // Provider 分组（最关键：用户想知道 minimax vs deepseek 各自表现）
  for(const [prov, info] of Object.entries(providerMap).sort((a,b)=>b[1].count-a[1].count)){
    const avgDur = info.dur_n > 0 ? info.dur_sum/info.dur_n : 0;
    const durStr = avgDur > 0 ? ` · 平均 ${_formatDuration(avgDur)}` : '';
    h += `<span style="background:${prov==='minimax'?'#fef9e7':prov==='deepseek'?'#e8f4fd':'#f4f4f5'};color:${prov==='minimax'?'#7d6608':prov==='deepseek'?'#1f4e79':'#555'};padding:3px 10px;border-radius:12px;font-weight:600">
      ${escapeHtml(prov)} · <b>${info.count}</b> 次${durStr}
    </span>`;
  }
  // 角色 Top 5
  const topSkills = Object.entries(skillMap).sort((a,b)=>b[1]-a[1]).slice(0,5);
  if(topSkills.length){
    h += `<span style="background:var(--bg-2);color:var(--text2);padding:3px 10px;border-radius:12px">角色: ${topSkills.map(([s,c])=>`<span style="color:#9b59b6">${escapeHtml(s)}</span> <b>${c}</b>`).join(' · ')}</span>`;
  }
  // 用户 Top 5
  const topUsers = Object.entries(userMap).sort((a,b)=>b[1]-a[1]).slice(0,5);
  if(topUsers.length){
    h += `<span style="background:var(--bg-2);color:var(--text2);padding:3px 10px;border-radius:12px">用户: ${topUsers.map(([u,c])=>`<span style="font-family:monospace">${escapeHtml(u)}</span> <b>${c}</b>`).join(' · ')}</span>`;
  }
  breakdown.innerHTML = h;
}
async function fetchTwitter(ev){const sn=document.getElementById('twScreenName').value.trim();if(!sn){toast('请输入 Screen Name',false);return}
  const allTweets=document.getElementById('twAll').checked;const replies=document.getElementById('twReplies').checked;const count=parseInt(document.getElementById('twCount').value)||200;
  const btn=ev;btnLoading(btn,true,'抓取中...');const log=document.getElementById('twLog');log.style.display='block';log.textContent='> 启动抓取任务 @'+sn+'（可能耗时数分钟）...';
  try{const fd=new FormData();fd.append('screen_name',sn);fd.append('count',String(count));fd.append('all_tweets',String(allTweets));fd.append('replies',String(replies));
    const d=await(await safeFetch('/api/twitter/fetch',{method:'POST',body:fd})).json();
    if(d.ok){log.textContent+='\n\n\u2713 '+d.msg;if(d.stdout_tail)log.textContent+='\n\n--- stdout (末段) ---\n'+d.stdout_tail;toast('抓取完成',true);loadTwitterCorpora();loadCorpus()}
    else{log.textContent+='\n\n\u2717 '+d.msg;if(d.stderr_tail)log.textContent+='\n\n--- stderr (末段) ---\n'+d.stderr_tail;if(d.stdout_tail)log.textContent+='\n\n--- stdout (末段) ---\n'+d.stdout_tail;toast('抓取失败',false)}}
  catch(e){log.textContent+='\n错误: '+e.message;toast('抓取失败',false)}
  finally{btnLoading(btn,false)}}

