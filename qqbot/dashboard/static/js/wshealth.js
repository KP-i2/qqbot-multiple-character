/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   WS 探活实时刷新（dashboard 后端每 3s 探测一次，前端 5s 拉一次）
   ================================================================ */
let _lastWsUp=false;  // 记录上次 ws 状态，用于断/连切换时弹 toast
let _wsDownSince=0;   // ws 断开起始时间（用于弹"持续 X 秒"toast）
async function refreshWsHealth(){
  try{
    const r=await fetch('/api/ws-health',{cache:'no-store'});
    if(!r.ok)return;
    const d=await r.json();
    const ok=!!(d.ws_upgrade_ok||d.tcp_alive);
    const el=document.getElementById('wsStatus');
    if(!el)return;
    // 顶栏 ws badge
    if(ok){
      el.className='ws-badge connected';
      el.textContent='已连接';
      el.title='最后握手: '+(d.last_ok_at?new Date(d.last_ok_at*1000).toLocaleTimeString():'-');
    } else {
      el.className='ws-badge disconnected';
      const sec=d.disconnected_for_sec||0;
      el.textContent=sec>0?('断 '+(sec<60?sec+'s':Math.floor(sec/60)+'m')):'未连接';
      el.title=(d.last_error||'no WebSocket')+'\n连续失败: '+(d.consecutive_fails||0);
    }
    // KPI 卡片 WS 项
    const kpiWs=document.getElementById('kpiNapcat');
    const kpiWsSub=document.getElementById('kpiNapcatSub');
    if(kpiWs){kpiWs.textContent=ok?'已连接':'未连接';kpiWs.style.color=ok?'var(--lime)':'var(--coral)'}
    if(kpiWsSub)kpiWsSub.textContent=ok?'WS 协议层握手成功':'WS 未响应 ('+(d.last_error||'连接失败')+')';
    // snowluma 卡片 WebSocket stat-item
    const wsItem=document.getElementById('wsConnStatus');
    if(wsItem){
      if(ok){wsItem.textContent='● 已连接';wsItem.className='value ok';wsItem.title='最后握手: '+(d.last_ok_at?new Date(d.last_ok_at*1000).toLocaleTimeString():'-')}
      else{const c=d.consecutive_fails||0;wsItem.textContent='● 断 '+(c<10?c+' 次':(Math.floor(c/10*30))+'s');wsItem.className='value err';wsItem.title=d.last_error||'no WebSocket'}
    }
    // 状态切换 toast
    if(ok && !_lastWsUp){
      if(_wsDownSince>0 && (Date.now()-_wsDownSince)>10000)toast('WebSocket 已恢复',true);
      _wsDownSince=0;
    } else if(!ok && _lastWsUp){
      _wsDownSince=Date.now();
      // 30s 持续断开才弹，避免启动期间抖动
      setTimeout(()=>{if(_wsDownSince>0 && Date.now()-_wsDownSince>=30000){toast('snowluma WebSocket 已断开 30s+，请检查 snowluma (http://localhost:5099)',false);_wsDownSince=0}},30000);
    }
    _lastWsUp=ok;
  }catch(e){/* 静默失败，下一次再试 */}
}

