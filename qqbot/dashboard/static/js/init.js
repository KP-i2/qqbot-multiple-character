/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   初始化（SSR-first：先用 window._INIT_DATA 立即填首屏）
   ================================================================ */
_hydrateFromSSR();
loadRoster();
loadSkillCount();
connectWS();checkHealth();setInterval(checkHealth,30000);
loadDashboardStatus();setInterval(loadDashboardStatus,30000);
// WS 实时探活（dashboard 后端每 3s 探测，前端 5s 拉结果）
refreshWsHealth();setInterval(refreshWsHealth,5000);
// 定期清理 toast 去重缓存（防止内存泄漏）
setInterval(()=>{const now=Date.now();for(const[k,v]of _toastDedup)if(now-v>10000)_toastDedup.delete(k)},60000);
document.getElementById('newSkillCharacter').addEventListener('change',function(){document.getElementById('researchProfileGroup').style.display=this.value==='celebrity'?'block':'none'});
document.getElementById('genSkillCharacter').addEventListener('change',function(){document.getElementById('genResearchProfileGroup').style.display=this.value==='celebrity'?'block':'none'});
