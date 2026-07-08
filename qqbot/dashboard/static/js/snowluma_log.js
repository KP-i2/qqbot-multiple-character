/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   snowluma 实时日志面板（SSE 推流）
   ================================================================ */
let _snowLogSource=null;
let _snowLogBuffer=[];
const _SNOW_LOG_MAX_LINES=500;
function _classifySnowLine(line){
  if(/\[error\]|ERROR|Traceback/i.test(line))return 'error';
  if(/\[warn\]|WARN/i.test(line))return 'warn';
  if(/SUCCESS|\[success\]/i.test(line))return 'success';
  if(/\[Event\]|私聊|群聊|发送|接收|★.*★/i.test(line))return 'event';
  return 'info';
}
function _appendSnowLines(text){
  const body=document.getElementById('snowLogBody');
  if(!body)return;
  // 去掉 empty 提示
  const empty=body.querySelector('.log-panel-empty');
  if(empty)empty.remove();
  // 按行切分（保留最后一行可能不完整，先累积）
  const lines=text.split('\n');
  for(let i=0;i<lines.length;i++){
    const ln=lines[i];
    if(!ln)continue;
    const div=document.createElement('div');
    div.className='log-line '+_classifySnowLine(ln);
    div.textContent=ln;
    body.appendChild(div);
  }
  // 限制行数
  while(body.childElementCount>_SNOW_LOG_MAX_LINES){
    body.removeChild(body.firstChild);
  }
  // 自动滚动
  if(document.getElementById('snowLogAutoScroll')?.checked){
    body.scrollTop=body.scrollHeight;
  }
}
function clearSnowlumaLog(){
  const body=document.getElementById('snowLogBody');
  if(!body)return;
  body.innerHTML='<div class="log-panel-empty">已清屏</div>';
}
function openSnowlumaLogPanel(){
  if(_snowLogSource)return; // 已连接
  const body=document.getElementById('snowLogBody');
  if(body&&!body.innerHTML.trim())body.innerHTML='<div class="log-panel-empty">连接中...</div>';
  try{
    _snowLogSource=new EventSource('/api/snowluma-log/sse');
    _snowLogSource.onmessage=ev=>{if(ev.data)_appendSnowLines(ev.data)};
    _snowLogSource.onerror=()=>{
      // 浏览器会自动重连，这里只更新状态
      if(body){
        const empty=body.querySelector('.log-panel-empty');
        if(empty)empty.textContent='连接断开，自动重连中...';
      }
    };
  }catch(e){
    if(body)body.innerHTML=`<div class="log-line error">SSE 连接失败: ${e.message}</div>`;
  }
}
function closeSnowlumaLogPanel(){
  if(_snowLogSource){_snowLogSource.close();_snowLogSource=null}
  const body=document.getElementById('snowLogBody');
  if(body)body.innerHTML='';
}
function toggleSnowlumaLog(btn){
  const panel=document.getElementById('snowLogPanel');
  if(!panel)return;
  if(panel.classList.contains('hidden')){
    panel.classList.remove('hidden');
    btn.textContent='收起日志 ▴';
    openSnowlumaLogPanel();
  }else{
    panel.classList.add('hidden');
    btn.textContent='展开日志 ▾';
    closeSnowlumaLogPanel();
  }
}

