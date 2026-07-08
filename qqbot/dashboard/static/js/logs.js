/* core.js sub-section: 日志查看 -- auto-split */
// ── 日志查看 ──
async function loadLogFiles(){
  try{
    const d=await(await safeFetch('/api/logs')).json();
    const sel=document.getElementById('logFileSelect');
    sel.innerHTML='<option value="">-- 选择日志文件 --</option>';
    for(const log of d.logs){
      sel.innerHTML+='<option value="'+log.name+'">'+log.name+' ('+log.size_kb+'KB, '+log.modified+')</option>';
    }
  }catch(e){toast('加载日志列表失败',false)}
}

async function loadLogFile(){
  const name=document.getElementById('logFileSelect').value;
  if(!name){document.getElementById('logContent').textContent='请选择日志文件...';return}
  try{
    const d=await(await safeFetch('/api/logs/'+name+'?tail=300')).json();
    if(!d.ok){toast(d.msg,false);return}
    const el=document.getElementById('logContent');
    // 清理 ANSI 颜色代码
    const cleanLines=d.lines.map(l=>l.replace(/\x1b\[[0-9;]*m/g,''));
    el.textContent=cleanLines.join('\n');
    document.getElementById('logLineCount').textContent='共 '+d.total+' 行，显示最后 '+cleanLines.length+' 行';
    // 自动滚动
    if(document.getElementById('logAutoScroll').checked){
      el.scrollTop=el.scrollHeight;
    }
  }catch(e){toast('加载日志失败',false)}
}

// 切换到日志标签页时自动加载
function switchTab(el,name){
  document.querySelectorAll('.tab-content').forEach(e=>e.classList.add('hidden'));
  document.querySelectorAll('[role="tab"]').forEach(t=>{t.classList.remove('active');t.setAttribute('aria-selected','false');t.tabIndex=-1});
  document.getElementById('tab-'+name).classList.remove('hidden');el.classList.add('active');el.setAttribute('aria-selected','true');el.tabIndex=0;el.focus();
  if(name==='cookie'){loadCookieStatus();loadTwitterCookieStatus();}if(name==='corpus')loadCorpus();
  if(name==='skills')loadSkills();
  if(name==='process')loadDashboardStatus();
  if(name==='settings')loadSettings();
  if(name==='logs'){loadLogFiles();loadLogFile()}
  if(name==='help'){loadDocsList();if(_activeDocName){loadDoc(_activeDocName);}else if(_docsList.length){const first=_docsList.find(d=>d.name==='README.md')||_docsList[0];loadDoc(first.name);}}
  if(name==='turns'){loadTurnsHistory();connectTurnsStream();}
  try{localStorage.setItem('dashboard_active_tab',name)}catch(e){}
}
// 键盘导航：← → 切换标签页，Home/End 跳首尾
document.querySelector('[role="tablist"]').addEventListener('keydown',function(e){
  const tabs=[...this.querySelectorAll('[role="tab"]')];const i=tabs.indexOf(document.activeElement);if(i<0)return;
  let next=-1;
  if(e.key==='ArrowRight')next=(i+1)%tabs.length;
  else if(e.key==='ArrowLeft')next=(i-1+tabs.length)%tabs.length;
  else if(e.key==='Home')next=0;
  else if(e.key==='End')next=tabs.length-1;
  if(next>=0){e.preventDefault();tabs[next].click()}
});

