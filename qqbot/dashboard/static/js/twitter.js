/* core.js sub-section: 推特抓取 -- auto-split */
// ── 推特抓取 ──
async function loadTwitterCookieStatus(){try{const d=await(await safeFetch('/api/twitter/cookie')).json();const el=document.getElementById('twCookieStatus');el.textContent=d.exists?'\u2713 已设置':'\u2717 未设置';el.className='value '+(d.exists?'ok':'err');document.getElementById('twCookieCount').textContent=d.count||'-';document.getElementById('twCookieModified').textContent=d.modified||'-'}catch(e){toast('读取失败',false)}}
async function uploadTwitterCookie(){const f=document.getElementById('twCookieFile').files[0];if(!f)return;const fd=new FormData();fd.append('file',f);try{const d=await(await safeFetch('/api/twitter/cookie/upload',{method:'POST',body:fd})).json();toast(d.msg,d.ok);if(d.ok)loadTwitterCookieStatus()}catch(e){toast('上传失败',false)}}
async function loadTwitterCorpora(){try{const list=await(await safeFetch('/api/twitter/corpora')).json();const el=document.getElementById('twCorporaList');if(!list.length){el.innerHTML='<div class="empty">暂无推特语料</div>';return}
  let h='<div class="card" style="margin-top:12px"><h3 style="font-size:12px;color:var(--text2);letter-spacing:1px;font-weight:600"><span class="icon pink">&#128230;</span> 已抓取推特语料</h3><div class="table-wrap"><table><thead><tr><th>Screen Name</th><th>目录</th><th>大小</th><th>corpus.txt</th><th>raw.json</th><th>更新时间</th></tr></thead><tbody>';
  for(const r of list){h+='<tr><td>@'+r.screen_name+'</td><td class="fs-10 fm">'+r.dir+'</td><td>'+r.size_kb+' KB</td><td>'+(r.corpus_exists?'\u2713':'\u2717')+'</td><td>'+(r.raw_exists?'\u2713':'\u2717')+'</td><td class="fs-10">'+(r.modified||'-')+'</td></tr>'}
  el.innerHTML=h+'</tbody></table></div></div>'}catch(e){toast('加载失败',false)}}

