/* core.js sub-section: Dashboard 自身管理 -- auto-split */
// ── Dashboard 自身管理 ──
async function loadDashboardStatus(){
  try{
    const d=await(await safeFetch('/api/dashboard/status')).json();
    const sEl=document.getElementById('dashStatus');
    if(d.running){sEl.textContent='\u25cf 运行';sEl.className='value ok'}
    else{sEl.textContent='\u25cb 停止';sEl.className='value err'}
    document.getElementById('dashPort').textContent=d.port||8501;
    document.getElementById('dashPid').textContent=d.pid||'-';
    document.getElementById('dashMem').textContent=d.memory_mb?d.memory_mb+' MB':'-';
    document.getElementById('dashUptime').textContent=d.uptime||'-';
  }catch(e){}
}
async function restartDashboard(btn){
  const msg='\u26a0\ufe0f 确认重启 Dashboard？\n\nDashboard 将在几秒后自动重启，页面需要手动刷新。';
  if(!confirm(msg))return;
  btnLoading(btn,true,'重启中...');toast('正在重启 Dashboard...',true);
  try{
    const d=await(await safeFetch('/api/dashboard/restart',{method:'POST'},10000)).json();
    toast(d.msg,d.ok);
    if(d.ok){
      // 等待几秒后尝试刷新页面
      setTimeout(()=>{
        toast('Dashboard 重启中，5 秒后刷新页面...',true,'warn');
        setTimeout(()=>location.reload(),5000);
      },2000);
    }
  }catch(e){
    toast('重启请求已发送，请稍后刷新页面',true,'warn');
    setTimeout(()=>location.reload(),8000);
  }finally{btnLoading(btn,false)}
}

async function loadCookieStatus(){try{const d=await(await safeFetch('/api/cookies')).json();const el=document.getElementById('cookieStatus');el.textContent=d.exists?'\u2713 已设置':'\u2717 未设置';el.className='value '+(d.exists?'ok':'err');document.getElementById('cookieCount').textContent=d.count||'-';document.getElementById('cookieSize').textContent=d.size_kb?d.size_kb+' KB':'-';document.getElementById('cookieModified').textContent=d.modified||'-'}catch(e){}}
async function uploadCookie(){const f=document.getElementById('cookieFile').files[0];if(!f)return;const fd=new FormData();fd.append('file',f);try{const d=await(await safeFetch('/api/cookies/upload',{method:'POST',body:fd})).json();toast(d.msg,d.ok);if(d.ok)loadCookieStatus()}catch(e){toast('上传失败',false)}}

async function loadCorpus(){try{
  const list=await(await safeFetch('/api/corpus')).json();
  // 同时加载技能列表以建立关联映射
  const skills=await(await safeFetch('/api/skills')).json();
  const el=document.getElementById('corpusList');
  if(!list.length){el.innerHTML='<div class="empty">暂无语料数据</div>';return}
  let h='<table><thead><tr><th>UID</th><th>文件</th><th>大小</th><th>关联技能</th><th></th></tr></thead><tbody>';
  for(const c of list){
    const fn=Object.keys(c.files).join(', ');
    // 语料-技能联动：查找关联技能
    let linkedSkill=null;
    if(c.linked_skill){linkedSkill=c.linked_skill}
    else{
      // 尝试通过 UID 匹配技能名称
      const match=skills.find(s=>s.name===c.uid||c.uid.startsWith(s.name+'_'));
      if(match)linkedSkill=match.name;
    }
    const lk=linkedSkill
      ?'<span class="c-lime">&#10003; <a style="color:var(--lime);cursor:pointer;text-decoration:none" onclick="navigateToSkill(\''+linkedSkill+'\')">'+linkedSkill+'</a></span>'
      :'未关联 <button class="btn btn-accent btn-sm" onclick="showGenerateSkill(\''+c.uid+'\')">生成技能</button>';
    h+='<tr><td><strong class="c-magenta">'+c.uid+'</strong></td><td class="fs-11 c-text2">'+fn+'</td><td class="c-text3 fm">'+c.total_kb+'KB</td><td>'+lk+'</td><td><button class="btn btn-outline btn-sm" onclick="viewCorpus(\''+c.uid+'\')">查看</button></td></tr>'
  }
  el.innerHTML=h+'</tbody></table>'}catch(e){toast('语料加载失败',false)}}

function navigateToSkill(skillName){
  // 跳转到技能管理标签页并打开编辑器
  const tab=document.querySelectorAll('.tab')[3]; // 技能管理是第4个tab (index 3)
  switchTab(tab,'skills');
  setTimeout(()=>openEditor(skillName),300);
}
function navigateToCorpus(corpusRef){
  // 跳转到语料库标签页
  const tab=document.querySelectorAll('.tab')[2]; // 语料库是第3个tab
  switchTab(tab,'corpus');
}
function showGenerateSkill(uid){document.getElementById('genSkillUid').value=uid;document.getElementById('genSkillCard').classList.remove('hidden');document.getElementById('genSkillName').focus()}
function hideGenerateSkill(){document.getElementById('genSkillCard').classList.add('hidden')}
async function generateSkill(ev){const fd=new FormData();fd.append('uid',document.getElementById('genSkillUid').value);fd.append('skill_name',document.getElementById('genSkillName').value);fd.append('display_name',document.getElementById('genSkillDisplay').value);fd.append('description',document.getElementById('genSkillDesc').value);fd.append('version',document.getElementById('genSkillVer').value);fd.append('character',document.getElementById('genSkillCharacter').value);
  const btn=ev;btnLoading(btn,true,'生成中...');try{const d=await(await safeFetch('/api/corpus/generate-skill',{method:'POST',body:fd},180000)).json();toast(d.msg,d.ok);if(d.ok){loadCorpus();hideGenerateSkill();loadSkills()}}catch(e){toast('生成失败',false)}finally{btnLoading(btn,false)}}
async function viewCorpus(uid){try{const d=await(await safeFetch('/api/corpus/'+uid)).json();if(!d.ok){toast(d.msg,false);return}
  let c=uid+'\n'+'='.repeat(50)+'\n\n';for(const f of d.files)c+='--- '+f.name+' ('+f.size_kb+'KB) ---\n'+f.preview+'\n...\n\n';
  const w=window.open('','_blank');if(!w){toast('弹窗被阻止',false);return}
  const cs=getComputedStyle(document.documentElement);const bg=cs.getPropertyValue('--void').trim()||'#0a0a1a';const fg=cs.getPropertyValue('--text').trim()||'#c8d6e5';const mono=cs.getPropertyValue('--font-mono').trim()||'monospace';
  w.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>'+escapeHtml(uid)+'</title></head><body style="margin:0;background:'+bg+';color:'+fg+'"><pre style="font-family:'+mono+';font-size:13px;padding:20px;white-space:pre-wrap">'+escapeHtml(c)+'</pre></body></html>')}catch(e){toast('查看失败',false)}}
async function fetchWeibo(ev){const uid=document.getElementById('weiboUid').value.trim();if(!uid){toast('请输入 UID',false);return}
  const btn=ev;btnLoading(btn,true,'抓取中...');document.getElementById('weiboLog').style.display='block';
  document.getElementById('weiboLog').textContent='> 正在抓取...';
  try{const fd=new FormData();fd.append('uid',uid);const d=await(await safeFetch('/api/corpus/fetch',{method:'POST',body:fd},180000)).json();
    document.getElementById('weiboLog').textContent+='\n'+d.msg;toast(d.msg,d.ok);if(d.ok)loadCorpus()}
  catch(e){document.getElementById('weiboLog').textContent+='\n错误: '+e.message;toast('抓取失败',false)}finally{btnLoading(btn,false)}}
async function previewSenders(){const f=document.getElementById('qqJsonFile').files[0];if(!f)return;const fd=new FormData();fd.append('file',f);
  try{const d=await(await safeFetch('/api/corpus/qq-senders',{method:'POST',body:fd})).json();const el=document.getElementById('sendersList');
    if(!d.ok){el.innerHTML='<div class="c-coral">'+d.msg+'</div>';return}
    let h='<table><thead><tr><th>UID</th><th>名称</th><th>消息数</th><th></th></tr></thead><tbody>';
    for(const[uid,info]of Object.entries(d.senders))h+='<tr><td class="fs-10 fm">'+uid+'</td><td>'+info.name+'</td><td>'+info.count+'</td><td><button class="btn btn-outline btn-sm" onclick="selectSender(\''+uid+'\')">选择</button></td></tr>';
    el.innerHTML=h+'</tbody></table>'}catch(e){toast('解析失败',false)}}
function selectSender(qqUid){document.getElementById('qqTargetUid').value=qqUid}
async function importQQ(ev){const f=document.getElementById('qqJsonFile').files[0];const qqUid=document.getElementById('qqTargetUid').value.trim();const dirUid=document.getElementById('qqDirUid').value.trim()||qqUid;
  if(!f||!qqUid){toast('请选择文件并填写发送者',false);return}
  const btn=ev;btnLoading(btn,true,'导入中...');try{const fd=new FormData();fd.append('file',f);fd.append('qq_uid',qqUid);fd.append('dir_uid',dirUid);
    const d=await(await safeFetch('/api/corpus/qq-import',{method:'POST',body:fd})).json();toast(d.msg,d.ok);if(d.ok)loadCorpus()}catch(e){toast('导入失败',false)}finally{btnLoading(btn,false)}}
async function importTextFile(ev){const f=document.getElementById('textImportFile').files[0];if(!f){toast('请选择文件',false);return}
  const btn=ev;btnLoading(btn,true,'导入中...');try{const fd=new FormData();fd.append('file',f);fd.append('uid',document.getElementById('textImportUid').value.trim());
    const d=await(await safeFetch('/api/corpus/import-text',{method:'POST',body:fd})).json();toast(d.msg,d.ok);if(d.ok)loadCorpus()}catch(e){toast('导入失败',false)}finally{btnLoading(btn,false)}}

