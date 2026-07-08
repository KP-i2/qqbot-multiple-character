/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   技能编辑面板
   ================================================================ */
let _editorSkill='',_editorFile='persona.md',_editorFiles={};

async function openEditor(name){
  _editorSkill=name;_editorFile='persona.md';_editorFiles={};
  document.getElementById('editorSkillName').textContent=name;
  document.getElementById('skillEditorPanel').classList.remove('hidden');
  document.getElementById('editorReloadBanner').classList.add('hidden');
  // 加载技能所有文件
  try{
    const r=await safeFetch('/api/skills/'+name);
    const d=await r.json();
    if(!d.ok){toast(d.msg,false);closeEditor();return}
    _editorFiles=d.files||{};
  }catch(e){toast('加载失败: '+e.message,false);closeEditor();return}
  // 激活 persona.md 标签
  document.querySelectorAll('.editor-tab').forEach(t=>t.classList.toggle('active',t.dataset.file==='persona.md'));
  document.getElementById('editorContent').value=_editorFiles['persona.md']||'';
  document.getElementById('editorStatus').textContent='已加载 '+Object.keys(_editorFiles).length+' 个文件';
  // 滚动到编辑面板
  document.getElementById('skillEditorPanel').scrollIntoView({behavior:'smooth',block:'start'});
}

function switchEditorTab(el,filename){
  _editorFile=filename;
  document.querySelectorAll('.editor-tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('editorContent').value=_editorFiles[filename]||'';
  document.getElementById('editorStatus').textContent=filename+(filename in _editorFiles?' ('+((_editorFiles[filename]||'').length)+' chars)':' (新建)');
}

function closeEditor(){
  document.getElementById('skillEditorPanel').classList.add('hidden');
  _editorSkill='';_editorFiles={};
}

async function saveEditorContent(){
  if(!_editorSkill)return;
  const content=document.getElementById('editorContent').value;
  const btn=document.getElementById('editorSaveBtn');
  btnLoading(btn,true,'保存中...');
  try{
    const r=await safeFetch('/api/skills/'+_editorSkill+'/'+_editorFile,{
      method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content})
    });
    const d=await r.json();
    toast(d.msg,d.ok);
    if(d.ok){
      _editorFiles[_editorFile]=content;
      document.getElementById('editorStatus').textContent='已保存 '+_editorFile;
      if(d.reloaded){
        const banner=document.getElementById('editorReloadBanner');
        banner.classList.remove('hidden');
        setTimeout(()=>banner.classList.add('hidden'),4000);
      }
    }
  }catch(e){toast('保存失败: '+e.message,false)}
  finally{btnLoading(btn,false)}
}

async function integrateTrait(){
  if(!_editorSkill){toast('请先打开一个技能进行编辑',false);return}
  const trait=document.getElementById('traitInput').value.trim();
  if(!trait){toast('请输入特征描述',false);return}
  const target=document.getElementById('traitTarget').value;
  const btn=document.getElementById('traitIntegrateBtn');
  const status=document.getElementById('traitStatus');
  btnLoading(btn,true,'AI 整合中...');
  status.textContent='正在调用 AI 分析特征...';status.style.color='var(--amber)';
  try{
    const r=await safeFetch('/api/skills/'+_editorSkill+'/integrate-trait',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({trait,target})
    },180000);
    const d=await r.json();
    if(d.ok){
      toast(d.msg,true);
      status.textContent='整合完成';status.style.color='var(--lime)';
      document.getElementById('traitInput').value='';
      // 更新本地缓存并刷新编辑器
      _editorFiles[target]=d.content;
      if(_editorFile===target){
        document.getElementById('editorContent').value=d.content;
        document.getElementById('editorStatus').textContent=target+' 已更新 (AI 整合)';
      }
      // 显示重载横幅
      const banner=document.getElementById('editorReloadBanner');
      banner.classList.remove('hidden');
      setTimeout(()=>banner.classList.add('hidden'),4000);
    }else{
      toast(d.msg,false);
      status.textContent='整合失败';status.style.color='var(--coral)';
    }
  }catch(e){
    toast('整合失败: '+e.message,false);
    status.textContent='错误';status.style.color='var(--coral)';
  }finally{btnLoading(btn,false)}
}

