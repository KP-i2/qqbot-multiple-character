/* Auto-split from index.html — do not edit here, edit the source. */
/* ================================================================
   主题系统
   ================================================================ */
const _THEMES = {'sakura-biyori':'桜日和','peach-soda':'蜜桃气泡','neon-terminal':'霓虹终端','midnight-galaxy':'午夜银河','arctic-frost':'极地霜白','scarlet-mecha':'绯红机甲','amber-black':'琥珀暗夜','arctic-green':'翡翠霜白'};
function applyTheme(name){if(!_THEMES[name])return;document.documentElement.setAttribute('data-theme',name);localStorage.setItem('qqbot-theme',name);document.querySelectorAll('.theme-card').forEach(b=>b.classList.toggle('active',b.dataset.theme===name));const dd=document.getElementById('themeDropdown');if(dd)dd.classList.add('hidden')}
function toggleThemeMenu(e){e.stopPropagation();document.getElementById('themeDropdown').classList.toggle('hidden')}
document.addEventListener('click',function(e){const dd=document.getElementById('themeDropdown');if(dd&&!dd.classList.contains('hidden')&&!e.target.closest('.theme-wrap'))dd.classList.add('hidden')});
(function(){applyTheme(localStorage.getItem('qqbot-theme')||'sakura-biyori')})();

