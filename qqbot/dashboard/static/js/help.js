/* core.js sub-section: 实时帮助文档 -- auto-split */
// ── 实时帮助文档 ──
let _activeDocName=null;
let _docsList=[];
async function loadDocsList(){
  const el=document.getElementById('docsSidebarList');
  el.innerHTML='<div class="docs-sidebar-empty">加载中…</div>';
  try{
    const list=await(await safeFetch('/api/docs')).json();
    _docsList=list;
    if(!list.length){
      el.innerHTML='<div class="docs-sidebar-empty">暂无文档<br><br>放一个 <code>README.md</code> 到项目根目录，或 <code>docs/*.md</code></div>';
      document.getElementById('docsContent').innerHTML='<div class="docs-empty"><p>暂无文档</p><p style="font-size:11px">在项目根放 <code>README.md</code>，或在 <code>docs/</code> 下放 <code>*.md</code>，刷新后会出现</p></div>';
      return;
    }
    let h='';
    for(const d of list){
      h+=`<div class="docs-sidebar-item${_activeDocName===d.name?' active':''}" onclick="loadDoc('${d.name}')" data-name="${d.name}">
        <div>${escapeHtml(d.title||d.name)}</div>
        <div class="meta">${d.size_kb} KB · ${d.modified||''}</div>
      </div>`;
    }
    el.innerHTML=h;
    if(_activeDocName){highlightDocItem(_activeDocName);}
  }catch(e){el.innerHTML='<div class="docs-sidebar-empty" style="color:var(--coral)">加载失败: '+escapeHtml(e.message)+'</div>';}
}
function highlightDocItem(name){
  document.querySelectorAll('.docs-sidebar-item').forEach(it=>{
    it.classList.toggle('active',it.dataset.name===name);
  });
}
async function loadDoc(name){
  if(!name)return;
  _activeDocName=name;
  highlightDocItem(name);
  const content=document.getElementById('docsContent');
  content.innerHTML='<div class="docs-loading">加载中…</div>';
  try{
    const d=await(await safeFetch('/api/docs/'+encodeURIComponent(name))).json();
    if(!d.ok){content.innerHTML='<div class="docs-error">'+escapeHtml(d.msg||'加载失败')+'</div>';return;}
    if(typeof marked!=='undefined'){
      marked.setOptions({breaks:false,gfm:true,headerIds:true});
      const html=marked.parse(d.content||'');
      content.innerHTML=`<div class="docs-content-header">
        <div class="docs-content-title"><span>📄</span> ${escapeHtml(d.title||d.name)}</div>
        <div class="docs-content-meta">${escapeHtml(d.name)} · ${d.size_kb} KB · ${d.modified||''}</div>
      </div><div class="docs-content-body">${html}</div>`;
      // 代码块高亮（轻量：不引入 hljs，浏览器默认就好）
      content.querySelectorAll('a[href^="http"]').forEach(a=>{a.target='_blank';a.rel='noopener';});
    }else{
      content.innerHTML='<div class="docs-content-header"><div class="docs-content-title">📄 '+escapeHtml(d.title||d.name)+'</div></div><pre style="white-space:pre-wrap;font-family:var(--font-mono);font-size:12px;color:var(--text);line-height:1.7">'+escapeHtml(d.content||'')+'</pre>';
    }
    content.scrollTop=0;
  }catch(e){content.innerHTML='<div class="docs-error">加载失败: '+escapeHtml(e.message)+'</div>';}
}


