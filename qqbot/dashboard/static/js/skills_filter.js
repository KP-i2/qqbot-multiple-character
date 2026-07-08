/* core.js sub-section: 技能排序和筛选 -- auto-split */
// ── 技能排序和筛选 ──
let _allSkills=[];
function sortSkills(list){
  const sortBy=document.getElementById('skillSortSelect')?.value||'name';
  return list.sort((a,b)=>{
    if(sortBy==='name')return (a.display_name||a.name).localeCompare(b.display_name||b.name);
    if(sortBy==='created')return (b.created||'').localeCompare(a.created||'');
    if(sortBy==='version')return (b.version||'0').localeCompare(a.version||'0','undefined',{numeric:true});
    if(sortBy==='size')return (b.prompt_size||0)-(a.prompt_size||0);
    return 0;
  });
}
function filterSkills(){
  const query=(document.getElementById('skillSearchInput')?.value||'').toLowerCase();
  const filtered=_allSkills.filter(s=>!query||s.name.toLowerCase().includes(query)||(s.display_name||'').toLowerCase().includes(query)||(s.description||'').toLowerCase().includes(query));
  renderSkills(sortSkills(filtered));
}
function renderSkills(list){
  const grid=document.getElementById('skillsGrid');
  if(!list.length){grid.innerHTML='<div class="empty" style="grid-column:1/-1">暂无技能</div>';return}
  let h='';
  for(const s of list){
    const photo=_PHOTOS[s.name];
    const t=Date.now();
    const initial=(s.display_name||s.name)[0].toUpperCase();
    const avatarHtml=photo
      ?'<img class="skill-avatar" src="'+photo+'?t='+t+'" onerror="_imgError(this,\'skill-fallback\',\''+escapeHtml(initial)+'\')" />'
      :'<div class="skill-fallback">'+escapeHtml(initial)+'</div>';
    const versionBadge=s.version?'<span class="skill-badge">v'+escapeHtml(s.version)+'</span>':'';
    const corpusBadge=s.corpus_ref
      ?'<span class="skill-badge linked" onclick="navigateToCorpus(\''+escapeHtml(s.corpus_ref)+'\')">&#128194; '+escapeHtml(s.corpus_ref)+'</span>'
      :'';
    const retrainBtn=s.corpus_ref
      ?'<button class="btn btn-outline btn-xs" onclick="openRetrainModal(\''+escapeHtml(s.name)+'\')">蒸馏</button>'
      :'';
    const checkboxHtml=_batchMode
      ?'<input type="checkbox" class="skill-checkbox" data-name="'+escapeHtml(s.name)+'" onchange="updateSelectedCount()" style="position:absolute;top:8px;right:8px">'
      :'';
    h+='<div class="skill-card" style="position:relative">'+checkboxHtml+avatarHtml+
      '<div class="skill-name">'+escapeHtml(s.display_name||s.name)+'</div>'+
      '<div class="skill-id">'+escapeHtml(s.name)+'</div>'+
      (s.description?'<div class="skill-desc">'+escapeHtml(s.description)+'</div>':'')+
      '<div class="skill-badges">'+versionBadge+corpusBadge+'</div>'+
      '<div class="skill-actions">'+
        '<button class="btn btn-primary btn-xs" onclick="openEditor(\''+escapeHtml(s.name)+'\')">编辑</button>'+
        retrainBtn+
        '<button class="btn btn-danger btn-xs" onclick="deleteSkill(\''+escapeHtml(s.name)+'\')">删除</button>'+
      '</div>'+
    '</div>';
  }
  grid.innerHTML=h;
}

async function loadSkills(){
  const grid=document.getElementById('skillsGrid');
  // SSR 优先：dashboard 后端 /api/skills 在 monitor 慢时可能 hang 15s+，先用首屏数据填
  if(!_allSkills.length){
    const init=window._INIT_DATA||{};
    if(Array.isArray(init.skills)&&init.skills.length>0){
      _allSkills=init.skills;
      // 顺便把 avatars 同步到 _PHOTOS
      if(init.avatars){for(const[k,v]of Object.entries(init.avatars))_PHOTOS[k]=v}
      filterSkills();
      return;
    }
    // 没 SSR 数据：显示骨架占位（避免空白闪烁）
    const skeletonCount=8;
    grid.innerHTML=Array.from({length:skeletonCount},()=>`
      <div class="skill-card" style="position:relative">
        <div class="skeleton skeleton-circle" style="margin:0 auto 8px"></div>
        <div class="skeleton skeleton-line" style="width:60%;margin:6px auto"></div>
        <div class="skeleton skeleton-line sm" style="width:40%;margin:0 auto"></div>
      </div>`).join('');
  }
  try{
    const fresh=await(await safeFetch('/api/skills',{},20000)).json();
    _allSkills=fresh;
    filterSkills();
  }catch(e){
    if(!_allSkills.length){
      grid.innerHTML='<div class="empty" style="grid-column:1/-1">技能加载失败：'+escapeHtml(e.message||String(e))+' <button class="btn btn-accent btn-sm" onclick="loadSkills()" style="margin-left:8px">重试</button></div>';
      toast('技能加载失败',false);
    }
    // 有 SSR 数据时不报错，让用户看到首屏内容
  }
}
