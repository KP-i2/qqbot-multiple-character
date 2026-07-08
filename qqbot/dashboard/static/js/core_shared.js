/* core.js head: 跨 tab 共享工具（toast/safeFetch/btnLoading/switchTab/connectWS/...） */
/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   核心功能（保留）
   ================================================================ */
let ws,modalData={};
let wsRetryCount=0,wsRetryDelay=1000;
let _lastHealthStatus='';const _activePolls={};const _toastDedup=new Map();

// 恢复上次访问的标签页
function restoreLastTab(){
  try{
    const lastTab=localStorage.getItem('dashboard_active_tab');
    if(lastTab){
      const tabEl=document.querySelector('.tab[onclick*="'+lastTab+'"]');
      if(tabEl)switchTab(tabEl,lastTab);
    }
  }catch(e){}
}
// 页面加载后恢复标签页
document.addEventListener('DOMContentLoaded',restoreLastTab);

function switchImportTab(el,tabId){
  document.querySelectorAll('#tab-corpus .editor-tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  ['import-weibo','import-qq','import-text','import-twitter'].forEach(id=>{document.getElementById(id).classList.toggle('hidden',id!==tabId)});
  if(tabId==='import-twitter'){loadTwitterCookieStatus();loadTwitterCorpora();}
}


const _settingKeys=['active_hours_start','active_hours_end','web_search_enabled','multi_turn_enabled','test_mode_enabled','active_llm_provider','minimax_use_high_speed','minimax_temperature','minimax_top_p','minimax_max_tokens','minimax_reasoning_effort_with_tools','minimax_reasoning_effort_no_tools','stream_enabled','stream_flush_chars','stream_flush_interval','stream_flush_min_chars','stream_max_flush_size','max_history_rounds','history_ttl_hours','history_save_interval','thinking_timer_seconds'];

async function loadSettings(){
  try{
    const d=await(await safeFetch('/api/settings')).json();
    if(!d.ok){toast('加载设置失败',false);return}
    for(const key of _settingKeys){
      const el=document.getElementById('set-'+key);
      if(!el||!d.settings[key])continue;
      const val=d.settings[key].value;
      if(el.tagName==='SELECT'){el.value=String(val)}
      else{el.value=val}
    }
  }catch(e){toast('加载设置出错: '+e.message,false)}
}

async function saveSettings(btn){
  btnLoading(btn,true,'保存中...');
  const body={};
  for(const key of _settingKeys){
    const el=document.getElementById('set-'+key);
    if(!el)continue;
    let v=el.value;
    if(el.type==='number')v=Number(v);
    else if(el.tagName==='SELECT')v=(v==='true');
    body[key]=v;
  }
  try{
    const d=await(await safeFetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    toast(d.msg,d.ok);
  }catch(e){toast('保存失败: '+e.message,false)}
  finally{btnLoading(btn,false)}
}

function toast(msg,ok=true,type=null){
  const dk=msg+(type||(ok?'ok':'err')),now=Date.now();
  if(_toastDedup.has(dk)&&now-_toastDedup.get(dk)<5000)return;_toastDedup.set(dk,now);
  const cls=type==='warn'?'toast-warn':(ok?'toast-ok':'toast-err'),el=document.createElement('div');el.className='toast '+cls;el.setAttribute('role','alert');el.setAttribute('aria-live','polite');
  const ex=document.querySelectorAll('.toast');let top=16;ex.forEach(t=>{top=Math.max(top,t.offsetTop+t.offsetHeight+6)});el.style.top=top+'px';
  const sp=document.createElement('span');sp.textContent=msg;sp.style.flex='1';el.appendChild(sp);
  const cb=document.createElement('button');cb.className='toast-close';cb.textContent='\u2715';cb.onclick=()=>el.remove();el.appendChild(cb);
  document.body.appendChild(el);setTimeout(()=>{if(el.parentNode)el.remove()},6000);
}

async function safeFetch(url,opts={},timeoutMs=30000){
  const c=new AbortController(),t=setTimeout(()=>c.abort(),timeoutMs);
  const hdrs={'X-Dashboard-Token':window._DASHBOARD_TOKEN||''};
  // 仅当 body 非 FormData 时加 Content-Type（FormData 自动处理 boundary）
  if(opts.body&&!(opts.body instanceof FormData))hdrs['Content-Type']='application/json';
  try{const r=await fetch(url,{...opts,headers:{...hdrs,...(opts.headers||{})},signal:c.signal});clearTimeout(t);
    if(!r.ok){let msg=r.statusText;try{const j=await r.json();if(j.msg)msg=j.msg}catch(_e){}throw new Error('HTTP '+r.status+': '+msg)}
    return r}
  catch(e){clearTimeout(t);throw e.name==='AbortError'?new Error('请求超时'):e}
}
function btnLoading(btn,loading,text=''){
  if(loading){btn._origText=btn.textContent;btn._origDisabled=btn.disabled;btn.disabled=true;btn.classList.add('loading');if(text)btn.textContent=text}
  else{btn.disabled=btn._origDisabled||false;btn.classList.remove('loading');btn.textContent=btn._origText||text}
}

function connectWS(){
  const p=location.protocol==='https:'?'wss:':'ws:';
  const token=encodeURIComponent(window._DASHBOARD_TOKEN||'');
  try{ws=new WebSocket(p+'//'+location.host+'/ws/status?token='+token)}catch(e){setTimeout(connectWS,wsRetryDelay);return}
  ws.onopen=()=>{const el=document.getElementById('wsStatus');el.textContent='已连接';el.className='ws-badge connected';wsRetryCount=0;wsRetryDelay=1000};
  ws.onclose=()=>{const el=document.getElementById('wsStatus');el.className='ws-badge disconnected';
    wsRetryCount++;wsRetryDelay=Math.min(wsRetryDelay*1.5,30000);el.textContent='重连('+wsRetryCount+')';setTimeout(connectWS,wsRetryDelay)};
  ws.onerror=()=>{};ws.onmessage=(e)=>{try{const d=JSON.parse(e.data);if(d&&typeof d==='object'&&d.nonebot2)updateStatus(d)}catch(err){}};
}

function updateStatus(s){
  if(!s)return;
  const nb=s.nonebot2,nc=s.napcat;
  if(!nb||!nc)return;
  const nb2El=document.getElementById('nb2Status');nb2El.textContent=nb.running?'\u25cf 运行':'\u25cb 停止';nb2El.className='value '+(nb.running?'ok':'err');
  document.getElementById('nb2Port').textContent=nb.port||8080;
  if(nb.processes&&nb.processes.length>0){document.getElementById('nb2Pid').textContent=nb.processes[0].pid;document.getElementById('nb2Mem').textContent=nb.processes[0].memory_mb+' MB';document.getElementById('nb2Uptime').textContent=nb.processes[0].uptime||'-'}
  else{['nb2Pid','nb2Mem','nb2Uptime'].forEach(id=>document.getElementById(id).textContent='-')}
  // snowluma 状态（只读）
  const ncEl=document.getElementById('ncStatus');ncEl.textContent=nc.running?'\u25cf 运行':'\u25cb 停止';ncEl.className='value '+(nc.running?'ok':'err');
  const qqEl=document.getElementById('qqStatus');qqEl.textContent=nc.qq_running?'\u25cf 运行':'\u25cb 停止';qqEl.className='value '+(nc.qq_running?'ok':'err');
  const wsEl=document.getElementById('wsConnStatus');wsEl.textContent=nc.ws_connected?'\u25cf 已连接':'\u25cb 未连接';wsEl.className='value '+(nc.ws_connected?'ok':'err');
  if(nc.processes&&nc.processes.length>0){document.getElementById('ncPid').textContent=nc.processes[0].pid;document.getElementById('ncMem').textContent=nc.processes[0].memory_mb+' MB'}
  else{['ncPid','ncMem'].forEach(id=>document.getElementById(id).textContent='-')}
  // snowluma 启停按钮的启用/禁用 (与 nc.running 状态联动)
  const slStart = document.getElementById('snowlumaStartBtn');
  const slStop  = document.getElementById('snowlumaStopBtn');
  if(slStart && slStop){
    slStart.disabled = !!nc.running;
    slStop.disabled  = !nc.running;
  }
  if(s.watchdog){const wdEl=document.getElementById('wdStatus');
    if(s.watchdog.running){wdEl.className='wd-indicator on';document.getElementById('wdDot').textContent='\u25cf';document.getElementById('wdText').textContent='看门狗 开启'}
    else{wdEl.className='wd-indicator off';document.getElementById('wdDot').textContent='\u25cf';document.getElementById('wdText').textContent='看门狗 关闭'}}
}


