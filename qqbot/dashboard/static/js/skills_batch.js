/* core.js sub-section: 批量操作 -- auto-split */
// ── 批量操作 ──
let _batchMode=false;
function toggleBatchMode(){
  _batchMode=!_batchMode;
  document.getElementById('batchToolbar').classList.toggle('hidden',!_batchMode);
  if(!_batchMode){document.getElementById('selectAllSkills').checked=false}
  loadSkills();
}
function toggleSelectAll(checked){
  document.querySelectorAll('.skill-checkbox').forEach(cb=>{cb.checked=checked});
  updateSelectedCount();
}
function updateSelectedCount(){
  const count=document.querySelectorAll('.skill-checkbox:checked').length;
  document.getElementById('selectedCount').textContent='已选 '+count+' 项';
}
async function batchDeleteSkills(){
  const checked=document.querySelectorAll('.skill-checkbox:checked');
  if(!checked.length){toast('请先选择要删除的技能',false);return}
  const names=Array.from(checked).map(cb=>cb.dataset.name);
  const msg='⚠️ 批量删除确认\n\n即将删除以下 '+names.length+' 个技能：\n\n'+names.map(n=>'• '+n).join('\n')+'\n\n此操作将删除：\n• 每个技能的目录及所有文件\n• 关联的头像图片\n• 操作不可撤销';
  if(!confirm(msg))return;
  const results=await Promise.allSettled(names.map(name=>safeFetch('/api/skills/'+name,{method:'DELETE'}).then(r=>r.json())));
  let success=0,fail=0;results.forEach(r=>{if(r.status==='fulfilled'&&r.value.ok)success++;else fail++});
  toast('删除完成：成功 '+success+' 个，失败 '+fail+' 个',success>0);
  loadSkills();loadSkillCount();loadRoster();
}

let _retrainTarget='';
let _retrainLastFocus=null;
function openRetrainModal(name){
  _retrainTarget=name;
  document.getElementById('retrainModalTitle').textContent='蒸馏配置：'+name;
  const m=document.getElementById('retrainModal');
  m.style.display='flex';
  _retrainLastFocus=document.activeElement;
  const first=m.querySelector('button,select,input,[tabindex]:not([tabindex="-1"])');
  if(first&&first.focus)first.focus();
}
function _closeRetrainModal(){
  const m=document.getElementById('retrainModal');
  if(!m||m.style.display==='none')return;
  m.style.display='none';
  if(_retrainLastFocus&&_retrainLastFocus.focus)_retrainLastFocus.focus();
  _retrainLastFocus=null;
}
// 弹窗键盘：Escape 关闭 + Tab 焦点圈定在弹窗内
document.addEventListener('keydown',function(e){
  const m=document.getElementById('retrainModal');
  if(!m||m.style.display==='none')return;
  if(e.key==='Escape'){e.preventDefault();_closeRetrainModal();return}
  if(e.key==='Tab'){
    const focusables=m.querySelectorAll('button,select,input,[tabindex]:not([tabindex="-1"])');
    if(!focusables.length)return;
    const first=focusables[0],last=focusables[focusables.length-1];
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
  }
});
document.getElementById('retrainCharacter').addEventListener('change',function(){document.getElementById('retrainResearchGroup').style.display=this.value==='celebrity'?'block':'none'});
async function confirmRetrain(){
  const name=_retrainTarget,character=document.getElementById('retrainCharacter').value,rp=document.getElementById('retrainResearchProfile').value;
  _closeRetrainModal();const btn=document.getElementById('retrainConfirmBtn');btnLoading(btn,true,'启动中...');
  try{const d=await(await safeFetch('/api/skills/'+name+'/retrain?character='+character+'&research_profile='+rp,{method:'POST'})).json();if(!d.ok){toast(d.msg,false);return}toast(d.msg,true);startProgressPoll(name)}
  catch(e){toast('启动失败: '+e.message,false)}finally{btnLoading(btn,false)}}

function startProgressPoll(name){
  if(_activePolls[name])clearInterval(_activePolls[name]);
  const container=document.getElementById('retrainProgressPanels');let panel=document.getElementById('pp-'+name);
  if(!panel){panel=document.createElement('div');panel.id='pp-'+name;panel.className='progress-panel';container.prepend(panel)}
  panel.innerHTML='<div class="pp-header"><span class="pp-title">'+name+'</span><span class="pp-time" id="pp-time-'+name+'">--:--</span></div><div class="pp-stage" id="pp-stage-'+name+'">初始化中...</div><div class="pp-msg" id="pp-msg-'+name+'">正在启动流水线</div><div class="pp-bar"><div class="pp-fill" id="pp-fill-'+name+'" style="width:5%"></div></div>';
  let pc=0,errCount=0;const mp=240;
  const sm={'\u5206\u6790':30,'\u7814\u7a76\u5206\u6790':20,'\u7814\u7a76\u5ba1\u8ba1':40,'\u5fc3\u667a\u6a21\u578b\u63d0\u70bc':60,'\u751f\u6210':80,'\u5b8c\u6210':100,'error':0};
  _activePolls[name]=setInterval(async()=>{
    pc++;if(pc>mp){clearInterval(_activePolls[name]);delete _activePolls[name];return}
    try{const r=await safeFetch('/api/skills/'+name+'/retrain-progress');const d=await r.json();
      const se=document.getElementById('pp-stage-'+name),me=document.getElementById('pp-msg-'+name),fe=document.getElementById('pp-fill-'+name),te=document.getElementById('pp-time-'+name);
      if(!se)return;
      if(d.stage&&d.stage!=='idle'){se.textContent=d.is_running?'\u23f3 '+d.stage:d.stage;me.textContent=d.message||'';te.textContent=d.timestamp||'';fe.style.width=(sm[d.stage]||50)+'%'}
      if(d.done){clearInterval(_activePolls[name]);delete _activePolls[name];
        if(d.ok){fe.style.width='100%';se.textContent='\u2713 完成';se.style.color='var(--lime)';me.textContent=d.message||'';toast(name+' 蒸馏完成',true);loadSkills()}
        else{fe.style.width='100%';fe.style.background='var(--coral)';se.textContent='\u2717 失败';se.style.color='var(--coral)';me.textContent=d.message||'';toast(name+' 蒸馏失败: '+d.message,false)}
        setTimeout(()=>{if(panel.parentNode)panel.style.opacity='0.4'},5000)}}
    catch(e){errCount++;const me=document.getElementById('pp-msg-'+name);if(me)me.textContent='轮询异常 ('+errCount+')，自动重试中...';if(errCount>=12){clearInterval(_activePolls[name]);delete _activePolls[name];const se=document.getElementById('pp-stage-'+name);if(se){se.textContent='⚠ 轮询中断';se.style.color='var(--coral)'}if(me)me.textContent='无法连接服务器，请刷新页面重试';toast(name+' 蒸馏进度丢失',false)}}
  },5000);
}

async function checkHealth(){
  // 用 SSR 数据兜底，避免后端慢导致 pill 全 off + 视觉错位
  const fallback=()=>{
    const init=window._INIT_DATA||{};
    const s=init.status||{};
    const nb=s.nonebot2||{};const nc=s.napcat||{};
    const bot=!!(nb.running),napcat=!!(nc.running),wsConnected=!!(nc.ws_connected);
    return {bot,napcat,ws_connected:wsConnected,timestamp:init.server_time?new Date(init.server_time*1000).toLocaleTimeString():'--:--',status:(bot&&napcat&&wsConnected)?'ok':(bot?'warn':'error')};
  };
  try{
    const r=await safeFetch('/api/health',{},15000);const d=await r.json();
    document.getElementById('healthBot').className='health-pill '+(d.bot?'on':'off');
    document.getElementById('healthNc').className='health-pill '+(d.napcat?'on':'off');
    document.getElementById('healthWs').className='health-pill '+(d.ws_connected?'on':'off');
    document.getElementById('healthTime').textContent=d.timestamp;
    updateKPIs(d);
    if(d.status!==_lastHealthStatus){_lastHealthStatus=d.status;if(d.status==='error')toast('服务异常',false,'warn');else if(d.status==='warn')toast('部分服务离线',false,'warn')}
  }catch(e){
    // 后端 /api/health 慢/超时：用 SSR 首屏数据兜底（保证 pill 不全 off）
    const d=fallback();
    document.getElementById('healthBot').className='health-pill '+(d.bot?'on':'off');
    document.getElementById('healthNc').className='health-pill '+(d.napcat?'on':'off');
    document.getElementById('healthWs').className='health-pill '+(d.ws_connected?'on':'off');
    document.getElementById('healthTime').textContent=d.timestamp;
    // SSR 数据不能调 updateKPIs（需要 status.nonebot2/napcat 完整对象）— KPI 留给 WS push 处理
    console.warn('[checkHealth] /api/health timeout, using SSR fallback', e?.message||e);
  }
}

