/* core.js sub-section: 语料列表（增强版：元信息 + 技能联动） -- auto-split */
// ── 语料列表（增强版：元信息 + 技能联动） ──
async function reloadBotSkills(){try{const d=await(await safeFetch('/api/bot/reload',{method:'POST'})).json();toast(d.msg,d.ok)}catch(e){toast('重载失败: '+e.message,false)}}
async function shutdownProject(){
  if(!confirm('确定关闭 NoneBot2？'))return;toast('正在关闭...',true);
  try{
    const r1=await safeFetch('/api/process/stop/nonebot2',{method:'POST'});const d1=await r1.json();
    if(!d1.ok){toast('NoneBot2 关闭失败: '+d1.msg,false)}
    else{toast('NoneBot2 已停止',true)}
  }
  catch(e){toast('关闭出错: '+e.message,false)}
}
async function startAll(){
  if(!confirm('启动 NoneBot2？'))return;toast('正在启动...',true);
  try{const d1=await(await safeFetch('/api/process/start/nonebot2',{method:'POST'})).json();toast(d1.msg,d1.ok)}
  catch(e){toast('启动出错: '+e.message,false)}
}
async function restartNonebot(btn){
  const msg='⚠️ 确认重启 NoneBot2\n\n此操作将：\n• 停止当前运行的 NoneBot2 进程\n• 等待 2 秒后重新启动\n• 等待端口就绪（最多 15 秒）\n\n重启期间 Bot 将暂时不可用';
  if(!confirm(msg))return;
  btnLoading(btn,true,'重启中...');toast('正在重启 NoneBot2...',true);
  try{const d=await(await safeFetch('/api/process/full-restart',{method:'POST'},90000)).json();toast(d.msg,d.ok)}
  catch(e){toast('重启出错: '+e.message,false)}finally{btnLoading(btn,false)}
}
async function toggleWatchdog(start){try{const url=start?'/api/watchdog/start':'/api/watchdog/stop';const d=await(await safeFetch(url,{method:'POST'})).json();toast(d.msg,d.ok)}catch(e){toast('出错: '+e.message,false)}}

// ── snowluma 启停 (2026-07-01 加进 dashboard 统一管理) ──
async function toggleSnowluma(action, btn){
  const verb = action === 'start' ? '启动' : '停止';
  if(!confirm(`确认${verb} snowluma？`)) return;
  btnLoading(btn, true, `${verb}中...`);
  try{
    const d = await(await safeFetch('/api/process/'+action+'/snowluma', {method:'POST'}, 30000)).json();
    toast(d.msg, d.ok);
    if(d.ok){
      // 操作成功后立即禁用对应按钮, 等待 WS push 刷新状态 (~2s 后启用另一个)
      const startBtn = document.getElementById('snowlumaStartBtn');
      const stopBtn  = document.getElementById('snowlumaStopBtn');
      if(action === 'start'){
        startBtn.disabled = true;
        stopBtn.disabled  = false;
        setTimeout(()=>{startBtn.disabled=false}, 3000);
      } else {
        stopBtn.disabled  = true;
        startBtn.disabled = false;
        setTimeout(()=>{stopBtn.disabled=false}, 3000);
      }
    }
  }catch(e){
    toast(verb+'出错: '+e.message, false);
  }finally{
    btnLoading(btn, false);
  }
}

