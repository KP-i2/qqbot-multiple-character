/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   初始化（SSR-first：先用 window._INIT_DATA 立即填首屏）
   ================================================================ */
_hydrateFromSSR();
loadRoster();
loadSkillCount();
connectWS();
// 立即首刷一次（SSR 已有首屏，这里拿最新），之后交给统一调度器
checkHealth(); loadDashboardStatus(); refreshWsHealth();
schedulePoll(checkHealth,30000,false);
schedulePoll(loadDashboardStatus,30000,false);
schedulePoll(refreshWsHealth,5000,false);
// 定期清理 toast 去重缓存（防止内存泄漏，纯本地无网络开销）
setInterval(()=>{const now=Date.now();for(const[k,v]of _toastDedup)if(now-v>10000)_toastDedup.delete(k)},60000);
document.getElementById('newSkillCharacter').addEventListener('change',function(){document.getElementById('researchProfileGroup').style.display=this.value==='celebrity'?'block':'none'});
document.getElementById('genSkillCharacter').addEventListener('change',function(){document.getElementById('genResearchProfileGroup').style.display=this.value==='celebrity'?'block':'none'});
