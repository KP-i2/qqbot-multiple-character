/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   角色花名册 & 头像上传
   ================================================================ */
const _PHOTOS={};

async function fetchAvatarPaths(){
  try{const d=await(await safeFetch('/api/avatars')).json();for(const[k,v]of Object.entries(d))_PHOTOS[k]=v}catch(e){}
}

async function loadRoster(){
  const grid=document.getElementById('rosterGrid');
  // SSR 首屏数据：直接渲染，不等 fetch
  const init=window._INIT_DATA||{};
  if(init.avatars){for(const[k,v]of Object.entries(init.avatars))_PHOTOS[k]=v}
  const initSkills=Array.isArray(init.skills)?init.skills:[];
  const nameMap={};
  initSkills.forEach(s=>{nameMap[s.name]=s.display_name||s.name});
  // 有 SSR 数据就先渲染
  const chars=Object.keys(nameMap).length>0?Object.keys(nameMap):Object.keys(_PHOTOS);
  if(chars.length>0){
    _renderRoster(grid, nameMap);
  }
  // 后台 refresh 一次拿到最新（不阻塞首屏）
  Promise.all([fetchAvatarPaths(),(async()=>{
    try{
      const skills=await(await safeFetch('/api/skills')).json();
      const map={};
      skills.forEach(s=>{map[s.name]=s.display_name||s.name});
      // 只有当数据**真正变化**时才重渲染（避免闪烁）
      const cur=Array.from(grid.querySelectorAll('.roster-card')).map(el=>el.querySelector('.roster-name')?.textContent);
      const next=Object.keys(map).sort();
      const changed=cur.length!==next.length||cur.some((v,i)=>v!==next[i]&&v!==(map[next[i]]||next[i]));
      if(changed)_renderRoster(grid, map);
    }catch(e){}
  })()]).catch(()=>{});
}

function _renderRoster(grid, nameMap){
  const t=Date.now();let html='';
  const allNames=new Set([...Object.keys(nameMap),...Object.keys(_PHOTOS)]);
  for(const id of allNames){
    const display=nameMap[id]||id;
    const photo=_PHOTOS[id];
    const initial=(display||id)[0].toUpperCase();
    const avatarHtml=photo
      ?'<img class="roster-avatar" loading="lazy" decoding="async" src="'+photo+'?t='+t+'" onerror="_imgError(this,\'roster-fallback\',\''+escapeHtml(initial)+'\')" />'
      :'<div class="roster-fallback">'+escapeHtml(initial)+'</div>';
    html+='<div class="roster-card">'+avatarHtml+
      '<div class="roster-name">'+escapeHtml(display)+'</div>'+
      '<label class="roster-upload">&#128247; 上传头像<input type="file" accept=".jpg,.jpeg,.png" style="display:none" onchange="uploadAvatar(\''+escapeHtml(id)+'\',this)"></label>'+
      '</div>';
  }
  grid.innerHTML=html;
}

async function uploadAvatar(skillName,input){
  const f=input.files[0];if(!f)return;
  if(!/\.(jpg|jpeg|png)$/i.test(f.name)){toast('仅支持 JPG / PNG 格式',false);return}
  if(f.size>5*1024*1024){toast('文件大小不能超过 5MB',false);input.value='';return}
  const fd=new FormData();fd.append('file',f);
  try{
    const d=await(await safeFetch('/api/skills/'+skillName+'/avatar',{method:'POST',body:fd})).json();
    toast(d.msg,d.ok);if(d.ok)loadRoster();
  }catch(e){toast('上传失败: '+e.message,false)}
  input.value='';
}

