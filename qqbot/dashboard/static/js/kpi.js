/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   KPI 总览更新
   ================================================================ */
function updateKPIs(d){
  const sysEl=document.getElementById('kpiSystem'),sysSub=document.getElementById('kpiSystemSub');
  const botEl=document.getElementById('kpiBot'),botSub=document.getElementById('kpiBotSub');
  const ncEl=document.getElementById('kpiNapcat'),ncSub=document.getElementById('kpiNapcatSub');
  if(d.status==='ok'){sysEl.textContent='正常';sysEl.style.color='var(--lime)';sysSub.textContent='所有服务运行中'}
  else if(d.status==='warn'){sysEl.textContent='警告';sysEl.style.color='var(--amber)';sysSub.textContent='部分服务离线'}
  else{sysEl.textContent='异常';sysEl.style.color='var(--coral)';sysSub.textContent='服务不可用'}
  botEl.textContent=d.bot?'运行':'停止';botEl.style.color=d.bot?'var(--lime)':'var(--coral)';
  botSub.textContent=d.bot?'端口 8080 活跃':'端口 8080 空闲';
  const ncOk=d.napcat&&d.ws_connected;ncEl.textContent=ncOk?'已连接':'未连接';ncEl.style.color=ncOk?'var(--lime)':'var(--coral)';
  ncSub.textContent=d.napcat?(d.ws_connected?'WS 已连接':'snowluma 运行, WS 未连接'):'snowluma 未运行';
  // 2026-06-28 双轨 LLM 状态（异步加载,失败也不影响主 KPI）
  updateLLMProviderKPI();
}

// 双轨 LLM 状态卡 (主对话 + 图片描述)
async function updateLLMProviderKPI(){
  const chatEl=document.getElementById('kpiChatLLM');
  const chatSubEl=document.getElementById('kpiChatLLMSub');
  const visEl=document.getElementById('kpiVisionLLM');
  const visSubEl=document.getElementById('kpiVisionLLMSub');
  if(!chatEl || !visEl) return;
  try{
    const r=await fetch('/api/llm-providers');
    if(!r.ok) throw new Error('http '+r.status);
    const d=await r.json();
    const cp=d.chat_provider||{};
    const vp=d.vision_provider||{};
    // 主对话
    chatEl.textContent=cp.available?(cp.name||'-'):'未配置';
    chatEl.style.color=cp.available?'var(--lime)':'var(--coral)';
    chatSubEl.textContent=cp.model||'';
    // 图片描述
    visEl.textContent=vp.available?(vp.name||'-'):'不可用';
    visEl.style.color=vp.available?'var(--lime)':'var(--amber)';
    visSubEl.textContent=vp.model?(vp.model+' (vision)'):'图片描述不可用';
  }catch(e){
    chatEl.textContent='-';chatEl.style.color='var(--text-dim)';
    visEl.textContent='-';visEl.style.color='var(--text-dim)';
  }
}

// SSR-first：把 dashboard 端的 status 翻译成 updateKPIs 期望的扁平格式，立即渲染
function _hydrateFromSSR(){
  const init=window._INIT_DATA||{};
  const s=init.status||{};
  const nb=s.nonebot2||{};
  const nc=s.napcat||{};
  const bot=!!(nb.running);
  const napcat=!!(nc.running);
  const wsConnected=!!(nc.ws_connected);
  // 系统状态
  let sysStatus='err',sysSub='服务不可用';
  if(bot&&napcat&&wsConnected){sysStatus='ok';sysSub='所有服务运行中'}
  else if(bot&&!napcat){sysStatus='warn';sysSub='Bot 正常, snowluma 未运行'}
  else if(bot&&napcat&&!wsConnected){sysStatus='warn';sysSub='Bot+snowluma 在, WS 未连'}
  else if(!bot){sysStatus='err';sysSub='Bot 未运行'}
  updateKPIs({status:sysStatus,bot,napcat,ws_connected:wsConnected});
  // 修正 updateKPis 的 sub 字段
  const sysSubEl=document.getElementById('kpiSystemSub');
  if(sysSubEl)sysSubEl.textContent=sysSub;
  // 顶栏 pill 立即用 SSR 填（不等 /api/health，绕过 15s+ 超时）
  const botEl=document.getElementById('healthBot');
  const ncEl=document.getElementById('healthNc');
  const wsEl=document.getElementById('healthWs');
  const timeEl=document.getElementById('healthTime');
  if(botEl)botEl.className='health-pill '+(bot?'on':'off');
  if(ncEl)ncEl.className='health-pill '+(napcat?'on':'off');
  if(wsEl)wsEl.className='health-pill '+(wsConnected?'on':'off');
  if(timeEl)timeEl.textContent=init.server_time?new Date(init.server_time*1000).toLocaleTimeString('en-GB',{hour12:false}):'--:--';
  // 技能数
  const skills=Array.isArray(init.skills)?init.skills:[];
  if(skills.length>0){
    const el=document.getElementById('kpiSkills');
    el.textContent=skills.length;el.style.color='var(--magenta)';
    document.getElementById('kpiSkillsSub').textContent=skills.length+' 个角色';
  }
  // bot 进程信息（PID/内存/时间）也直接补
  const procs=nb.processes||[];
  if(procs.length>0){
    const p=procs[0];
    if(document.getElementById('nb2Status')){document.getElementById('nb2Status').textContent='● 运行';document.getElementById('nb2Status').className='value ok'}
    if(document.getElementById('nb2Pid'))document.getElementById('nb2Pid').textContent=p.pid;
    if(document.getElementById('nb2Mem'))document.getElementById('nb2Mem').textContent=(p.memory_mb||0)+' MB';
    if(document.getElementById('nb2Uptime'))document.getElementById('nb2Uptime').textContent=p.uptime||'-';
  }
  // snowluma 进程信息
  if(nc.processes&&nc.processes.length>0){
    const p=nc.processes[0];
    if(document.getElementById('ncStatus')){document.getElementById('ncStatus').textContent='● 运行';document.getElementById('ncStatus').className='value ok'}
    if(document.getElementById('ncPid'))document.getElementById('ncPid').textContent=p.pid;
    if(document.getElementById('ncMem'))document.getElementById('ncMem').textContent=(p.memory_mb||0)+' MB';
  } else if(document.getElementById('ncStatus')){
    document.getElementById('ncStatus').textContent='未运行';document.getElementById('ncStatus').className='value err';
  }
  // QQ 客户端
  if(document.getElementById('qqStatus')){
    if(nc.qq_running){
      const qqs=nc.qq_processes||[];
      const qq=qqs[0]||{};
      document.getElementById('qqStatus').textContent='● '+(qq.name||'运行');
      document.getElementById('qqStatus').className='value ok';
    } else {
      document.getElementById('qqStatus').textContent='未运行';
      document.getElementById('qqStatus').className='value err';
    }
  }
  // WS（先用 psutil 的粗结果，等 5s 后 ws-health 接管）
  if(document.getElementById('wsConnStatus')){
    if(wsConnected){document.getElementById('wsConnStatus').textContent='● 已连接';document.getElementById('wsConnStatus').className='value ok'}
    else if(napcat){document.getElementById('wsConnStatus').textContent='未连接';document.getElementById('wsConnStatus').className='value warn'}
    else{document.getElementById('wsConnStatus').textContent='-';document.getElementById('wsConnStatus').className='value neutral'}
  }
}

async function loadSkillCount(){
  // SSR 优先
  const init=window._INIT_DATA||{};
  if(Array.isArray(init.skills)&&init.skills.length>0)return; // 已被 SSR 填好
  try{const s=await(await safeFetch('/api/skills')).json();
    document.getElementById('kpiSkills').textContent=s.length;document.getElementById('kpiSkills').style.color='var(--magenta)';
    document.getElementById('kpiSkillsSub').textContent=s.length+' 个角色';
  }catch(e){document.getElementById('kpiSkills').textContent='-'}
}

