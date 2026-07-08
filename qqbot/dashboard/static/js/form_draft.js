/* core.js sub-section: 表单草稿保存 -- auto-split */
// ── 表单草稿保存 ──
const _SKILL_FORM_FIELDS=['newSkillName','newSkillDisplay','newSkillDesc','newSkillVer','newSkillPersona','newSkillWork'];
function saveSkillFormDraft(){
  try{
    const draft={};
    _SKILL_FORM_FIELDS.forEach(id=>{const el=document.getElementById(id);if(el)draft[id]=el.value});
    localStorage.setItem('skill_form_draft',JSON.stringify(draft));
  }catch(e){}
}
function loadSkillFormDraft(){
  try{
    const raw=localStorage.getItem('skill_form_draft');
    if(!raw)return;
    const draft=JSON.parse(raw);
    _SKILL_FORM_FIELDS.forEach(id=>{const el=document.getElementById(id);if(el&&draft[id]!==undefined)el.value=draft[id]});
  }catch(e){}
}
function clearSkillFormDraft(){
  try{localStorage.removeItem('skill_form_draft')}catch(e){}
}
// 监听表单输入，自动保存草稿
function initSkillFormAutoSave(){
  _SKILL_FORM_FIELDS.forEach(id=>{
    const el=document.getElementById(id);
    if(el)el.addEventListener('input',saveSkillFormDraft);
  });
}
document.addEventListener('DOMContentLoaded',initSkillFormAutoSave);

function showCreateSkill(){document.getElementById('createSkillCard').classList.remove('hidden');loadCorpusOptions();loadSkillFormDraft()}
function hideCreateSkill(){document.getElementById('createSkillCard').classList.add('hidden')}
async function loadCorpusOptions(){try{const list=await(await safeFetch('/api/corpus')).json();const sel=document.getElementById('newSkillCorpus');
  sel.innerHTML='<option value="">-- 手动创建 --</option>';
  for(const c of list)sel.innerHTML+='<option value="'+c.uid+'">'+c.uid+' ('+c.total_kb+'KB)'+(c.linked_skill?' ['+c.linked_skill+']':'')+'</option>'}catch(e){}}
function onCorpusSelect(){const uid=document.getElementById('newSkillCorpus').value;if(uid){const n=uid.split('_')[0];if(!document.getElementById('newSkillName').value)document.getElementById('newSkillName').value=n;if(!document.getElementById('newSkillDisplay').value)document.getElementById('newSkillDisplay').value=n}}
async function createSkill(ev){const fd=new FormData();fd.append('name',document.getElementById('newSkillName').value);fd.append('display_name',document.getElementById('newSkillDisplay').value);fd.append('description',document.getElementById('newSkillDesc').value);fd.append('version',document.getElementById('newSkillVer').value);fd.append('persona',document.getElementById('newSkillPersona').value);fd.append('work',document.getElementById('newSkillWork').value);
  const btn=ev;btnLoading(btn,true,'创建中...');try{const d=await(await safeFetch('/api/skills/create',{method:'POST',body:fd})).json();toast(d.msg,d.ok);if(d.ok){loadSkills();loadSkillCount();hideCreateSkill();clearSkillFormDraft()}}catch(e){toast('创建失败',false)}finally{btnLoading(btn,false)}}
async function createSkillFromCorpus(ev){
  const uid=document.getElementById('newSkillCorpus').value,name=document.getElementById('newSkillName').value.trim(),display=document.getElementById('newSkillDisplay').value.trim();
  const desc=document.getElementById('newSkillDesc').value.trim(),ver=document.getElementById('newSkillVer').value.trim();
  const character=document.getElementById('newSkillCharacter').value,researchProfile=document.getElementById('newSkillResearchProfile').value;
  if(!uid){toast('请选择语料',false);return}if(!name){toast('请输入技能名称',false);return}
  const btn=ev;btnLoading(btn,true,'生成中...');
  try{const fd=new FormData();fd.append('uid',uid);fd.append('skill_name',name);fd.append('display_name',display||name);fd.append('description',desc);fd.append('version',ver||'1.0.0');fd.append('character',character);
    const d1=await(await safeFetch('/api/corpus/generate-skill',{method:'POST',body:fd},180000)).json();if(!d1.ok){toast(d1.msg,false);return}
    const d2=await(await safeFetch('/api/skills/'+name+'/retrain?character='+character+'&research_profile='+researchProfile,{method:'POST'})).json();if(!d2.ok){toast('技能已创建但蒸馏失败: '+d2.msg,false);loadSkills();loadSkillCount();return}
    toast('蒸馏已开始',true);startProgressPoll(name);loadSkills();loadSkillCount()}
  catch(e){toast('生成失败: '+e.message,false)}finally{btnLoading(btn,false)}}
function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
function _imgError(img,fallbackClass,letter){var d=document.createElement('div');d.className=fallbackClass;d.textContent=letter;img.replaceWith(d)}
async function deleteSkill(name){
  const msg='确定删除技能 "'+name+'"？\n\n⚠️ 此操作将删除：\n• 技能目录及所有文件（persona.md, work.md, SKILL.md）\n• 关联的头像图片\n• 操作不可撤销';
  if(!confirm(msg))return;
  try{const d=await(await safeFetch('/api/skills/'+name,{method:'DELETE'})).json();toast(d.msg,d.ok);if(d.ok){loadSkills();loadSkillCount();loadRoster()}}catch(e){toast('删除失败',false)}
}

