/* 记忆面板（P2）——依赖 core_shared.js 的 safeFetch / toast */
let _memSkills = [];
let _memInited = false;

function _esc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function _memTime(ts) {
  const n = Number(ts || 0);
  if (!n) return '-';
  return new Date(n * 1000).toLocaleString('zh-CN', { hour12: false });
}
const _MEM_KIND = { fact: '事实', preference: '偏好', promise: '约定', relationship: '关系', interest: '兴趣', boundary: '边界', event: '事件' };
const _USAGE_PURPOSE = {
  chat: '主对话', chat_stream: '主对话(流式)', chat_tool: '工具轮', chat_final: '收尾',
  probe: '工具判断', memory_extract: '记忆抽取', profile: '用户画像',
  learning_summary: '群学习摘要', vision: '识图', web_search: '联网搜索',
  search_events: '活动检索',
};

async function loadMemory(reset) {
  try {
    const d = await (await safeFetch('/api/memory/overview')).json();
    if (!d.ok) { toast('记忆读取失败: ' + (d.msg || ''), false); return; }
    _memSkills = d.skills || [];
    document.getElementById('mem-total').textContent = d.total ?? 0;
    document.getElementById('mem-proactive').textContent = d.proactive ?? 0;
    const sel = document.getElementById('memSkill');
    const old = reset ? '' : sel.value;
    sel.innerHTML = '<option value="">全部角色</option>' +
      _memSkills.map(s => `<option value="${_esc(s.skill)}">${_esc(s.skill)} (${s.n})</option>`).join('');
    if (old && [...sel.options].some(o => o.value === old)) sel.value = old;
    document.getElementById('mem-skill-count').textContent = _memSkills.length;
    _memInited = true;
    await loadMemoryScopes();
    await loadMemoryList();
    await loadMemoryProactive();
    await loadMemoryLearning();
    await loadUsage();
    await loadLearningReport();
  } catch (e) {
    toast('记忆读取失败: ' + e.message, false);
  }
}

async function loadMemoryScopes() {
  const skill = document.getElementById('memSkill').value;
  const sel = document.getElementById('memScope');
  const old = sel.value;
  if (!skill) {
    sel.innerHTML = '<option value="">全部作用域</option>';
    return;
  }
  try {
    const d = await (await safeFetch('/api/memory/scopes?skill=' + encodeURIComponent(skill))).json();
    const scopes = d.scopes || [];
    sel.innerHTML = '<option value="">全部作用域</option>' +
      scopes.map(s => `<option value="${_esc(s.scope)}">${_esc(s.scope)} (${s.n})</option>`).join('');
    if (old && [...sel.options].some(o => o.value === old)) sel.value = old;
  } catch (e) {
    sel.innerHTML = '<option value="">全部作用域</option>';
  }
}

async function loadMemoryList() {
  const box = document.getElementById('memoryList');
  const skill = document.getElementById('memSkill').value;
  const scope = document.getElementById('memScope').value;
  const q = document.getElementById('memQuery').value.trim();
  box.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text2)">加载中...</div>';
  try {
    const params = new URLSearchParams({ limit: '150' });
    if (skill) params.set('skill', skill);
    if (scope) params.set('scope', scope);
    if (q) params.set('q', q);
    const d = await (await safeFetch('/api/memory/list?' + params.toString())).json();
    const items = d.items || [];
    if (!d.ok) { box.innerHTML = `<div style="padding:20px;color:var(--coral)">${_esc(d.msg || '读取失败')}</div>`; return; }
    if (!items.length) { box.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text2)">没有匹配的记忆</div>'; return; }
    box.innerHTML = items.map(m => `
      <div style="padding:10px 8px;border-bottom:1px solid var(--border)">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:11px;color:var(--text2)">
          <b style="color:var(--text1)">#${m.id}</b>
          <span>${_esc(m.skill)}</span><span>·</span><span>${_esc(m.scope)}</span>
          <span class="pill" style="font-size:10px">${_esc(_MEM_KIND[m.kind] || m.kind)}</span>
          <span>${_esc(_memTime(m.created_at))}</span>
          <span style="flex:1"></span>
          <button class="btn btn-danger btn-xs" onclick="forgetMemory(${m.id})">删除</button>
        </div>
        <div style="margin-top:4px">${_esc(m.text)}</div>
        ${m.source_quote ? `<div style="margin-top:2px;font-size:11px;color:var(--text2)">引用：${_esc(m.source_quote)}</div>` : ''}
      </div>`).join('');
  } catch (e) {
    box.innerHTML = `<div style="padding:20px;color:var(--coral)">读取失败: ${_esc(e.message)}</div>`;
  }
}

async function forgetMemory(id) {
  if (!confirm('删除这条记忆？')) return;
  try {
    const d = await (await safeFetch('/api/memory/delete', {
      method: 'POST', body: JSON.stringify({ ids: [id] }),
    })).json();
    toast(d.ok ? `已删除 ${d.deleted} 条` : ('删除失败: ' + (d.msg || '')), !!d.ok);
    if (d.ok) loadMemoryList();
  } catch (e) { toast('删除失败: ' + e.message, false); }
}

async function forgetMemoryScope() {
  const skill = document.getElementById('memSkill').value;
  const scope = document.getElementById('memScope').value;
  if (!skill || !scope) { toast('先选具体角色和作用域', false); return; }
  if (!confirm(`清空 ${skill} / ${scope} 的全部记忆？`)) return;
  try {
    const d = await (await safeFetch('/api/memory/delete', {
      method: 'POST', body: JSON.stringify({ skill, scope, all: true }),
    })).json();
    toast(d.ok ? `已清空 ${d.deleted} 条` : ('清空失败: ' + (d.msg || '')), !!d.ok);
    if (d.ok) loadMemory(false);
  } catch (e) { toast('清空失败: ' + e.message, false); }
}

async function loadMemoryProactive() {
  const box = document.getElementById('memoryProactive');
  const skill = document.getElementById('memSkill').value;
  try {
    const url = '/api/memory/proactive?limit=80' + (skill ? '&skill=' + encodeURIComponent(skill) : '');
    const d = await (await safeFetch(url)).json();
    const items = d.items || [];
    if (!items.length) { box.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)">还没有主动消息</div>'; return; }
    box.innerHTML = items.map(x => `
      <div style="padding:8px 6px;border-bottom:1px solid var(--border);font-size:12px">
        <div style="display:flex;gap:8px;flex-wrap:wrap;color:var(--text2);font-size:11px">
          <b style="color:${x.ok ? 'var(--lime)' : 'var(--coral)'}">${x.ok ? '成功' : '失败'}</b>
          <span>${_esc(x.skill)}</span><span>${_esc(x.scope)}</span>
          <span>${_esc(_memTime(x.sent_at))}</span>
          ${x.memory_id ? `<span>记忆 #${x.memory_id}</span>` : '<span>随机</span>'}
        </div>
        <div style="margin-top:3px">${_esc(x.text)}</div>
        ${x.error ? `<div style="font-size:11px;color:var(--coral)">${_esc(x.error)}</div>` : ''}
      </div>`).join('');
  } catch (e) {
    box.innerHTML = `<div style="padding:20px;color:var(--coral)">读取失败: ${_esc(e.message)}</div>`;
  }
}

async function loadMemoryLearning() {
  const box = document.getElementById('memoryLearning');
  const skill = document.getElementById('memSkill').value;
  try {
    const url = '/api/memory/learning' + (skill ? '?skill=' + encodeURIComponent(skill) : '');
    const d = await (await safeFetch(url)).json();
    const rows = d.groups || [];
    if (!rows.length) { box.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)">还没有群学习记录</div>'; return; }
    box.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="color:var(--text2);text-align:left"><th style="padding:6px">角色</th><th>群号</th><th>上次摘要</th><th>待摘要</th></tr></thead>
      <tbody>${rows.map(r => `<tr style="border-top:1px solid var(--border)">
        <td style="padding:6px">${_esc(r.skill)}</td>
        <td>${_esc(r.group_id)}</td>
        <td>${r.last_summary_at ? _esc(_memTime(r.last_summary_at)) : '—'}</td>
        <td>${r.pending ?? '—'}</td>
      </tr>`).join('')}</tbody></table>`;
  } catch (e) {
    box.innerHTML = `<div style="padding:20px;color:var(--coral)">读取失败: ${_esc(e.message)}</div>`;
  }
}

async function loadUsage() {
  const totals = document.getElementById('usageTotals');
  const box = document.getElementById('usageRows');
  try {
    const d = await (await safeFetch('/api/usage/summary?days=7')).json();
    const t = d.totals || {};
    totals.innerHTML = `<span>调用：<b>${t.calls ?? 0}</b></span>
      <span>总 tokens：<b>${t.total_tokens ?? 0}</b></span>
      <span>输入：${t.prompt_tokens ?? 0}</span>
      <span>输出：${t.completion_tokens ?? 0}</span>
      <span style="color:var(--text2)">其中估算：${t.estimated_calls ?? 0} 次</span>`;
    const rows = d.rows || [];
    if (!rows.length) { box.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)">还没有用量记录</div>'; return; }
    box.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="color:var(--text2);text-align:left"><th style="padding:6px">类型</th><th>用途</th><th>角色</th><th>模型/来源</th><th>调用</th><th>tokens</th><th>估算</th></tr></thead>
      <tbody>${rows.map(r => `<tr style="border-top:1px solid var(--border)">
        <td style="padding:6px">${_esc(r.kind)}</td>
        <td>${_esc(_USAGE_PURPOSE[r.purpose] || r.purpose)}</td>
        <td>${_esc(r.skill || '—')}</td>
        <td>${_esc(r.model || r.provider)}</td>
        <td>${r.calls ?? 0}</td>
        <td>${r.total_tokens ?? 0}</td>
        <td>${r.estimated_calls ?? 0}</td>
      </tr>`).join('')}</tbody></table>`;
  } catch (e) {
    box.innerHTML = `<div style="padding:20px;color:var(--coral)">读取失败: ${_esc(e.message)}</div>`;
  }
}

async function loadLearningReport() {
  const box = document.getElementById('learningReport');
  try {
    const d = await (await safeFetch('/api/learning/report?days=7')).json();
    const t = d.totals || {};
    const recent = d.recent || [];
    let html = `<div style="display:flex;gap:14px;flex-wrap:wrap;font-size:13px;margin-bottom:8px">
      <span>摘要：<b>${t.runs ?? 0}</b> 次</span>
      <span>接收：<b>${t.accepted ?? 0}</b> 条</span>
      <span>丢弃：<b>${t.rejected ?? 0}</b> 条</span>
      <span>覆盖消息：${t.messages ?? 0} 条</span>
    </div>`;
    if (!recent.length) {
      html += '<div style="text-align:center;padding:16px;color:var(--text2)">近 7 天没有新入库的群体记忆</div>';
    } else {
      html += recent.map(m => `<div style="padding:6px 0;border-top:1px solid var(--border);font-size:12px">
        <b>${_esc(m.skill)}</b> · ${_esc(m.scope)} · ${_esc(_MEM_KIND[m.kind] || m.kind)}
        <div>${_esc(m.text)}</div>
        ${m.source_quote ? `<div style="color:var(--text2);font-size:11px">引用：${_esc(m.source_quote)}</div>` : ''}
      </div>`).join('');
    }
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<div style="padding:20px;color:var(--coral)">读取失败: ${_esc(e.message)}</div>`;
  }
}
