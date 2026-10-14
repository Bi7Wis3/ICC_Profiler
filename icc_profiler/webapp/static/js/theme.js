const THEMES = [
    { n:'Arctic Frost',   d:false, v:{'--primary':'#0ea5e9','--primary-dark':'#0284c7','--card-bg-dark':'rgba(255,255,255,0.95)','--text':'#0f172a','--text-light':'#475569','--border':'#cbd5e1','--shadow':'rgba(14,165,233,0.18)','--dropdown-bg':'#f0f9ff'}, bg:'linear-gradient(135deg,#f0f9ff 0%,#bae6fd 100%)'},
    { n:'Sandstone',      d:false, v:{'--primary':'#d97706','--primary-dark':'#b45309','--card-bg-dark':'rgba(255,251,235,0.95)','--text':'#451a03','--text-light':'#78350f','--border':'#fde68a','--shadow':'rgba(180,83,9,0.20)','--dropdown-bg':'#fef3c7'}, bg:'linear-gradient(135deg,#fef3c7 0%,#fed7aa 100%)'},
    { n:'Mint Fresh',     d:false, v:{'--primary':'#10b981','--primary-dark':'#059669','--card-bg-dark':'rgba(255,255,255,0.95)','--text':'#064e3b','--text-light':'#065f46','--border':'#a7f3d0','--shadow':'rgba(16,185,129,0.20)','--dropdown-bg':'#ecfdf5'}, bg:'linear-gradient(135deg,#ecfdf5 0%,#a7f3d0 100%)'},
    { n:'Lavender Dream', d:false, v:{'--primary':'#8b5cf6','--primary-dark':'#7c3aed','--card-bg-dark':'rgba(255,255,255,0.95)','--text':'#2e1065','--text-light':'#5b21b6','--border':'#ddd6fe','--shadow':'rgba(139,92,246,0.20)','--dropdown-bg':'#faf5ff'}, bg:'linear-gradient(135deg,#faf5ff 0%,#ddd6fe 100%)'},
    { n:'Solarized Light',d:false, v:{'--primary':'#268bd2','--primary-dark':'#1e6fa8','--card-bg-dark':'rgba(253,246,227,0.95)','--text':'#002b36','--text-light':'#586e75','--border':'#eee8d5','--shadow':'rgba(7,54,66,0.18)','--dropdown-bg':'#fdf6e3'}, bg:'linear-gradient(135deg,#fdf6e3 0%,#eee8d5 100%)'},
    { n:'Rose Gold',      d:false, v:{'--primary':'#e11d48','--primary-dark':'#be123c','--card-bg-dark':'rgba(255,255,255,0.95)','--text':'#4c0519','--text-light':'#881337','--border':'#fecdd3','--shadow':'rgba(225,29,72,0.20)','--dropdown-bg':'#fff1f2'}, bg:'linear-gradient(135deg,#fff1f2 0%,#fecdd3 100%)'},
    { n:'Sky Blue',       d:false, v:{'--primary':'#2563eb','--primary-dark':'#1d4ed8','--card-bg-dark':'rgba(255,255,255,0.95)','--text':'#1e3a8a','--text-light':'#1e40af','--border':'#bfdbfe','--shadow':'rgba(37,99,235,0.20)','--dropdown-bg':'#eff6ff'}, bg:'linear-gradient(135deg,#eff6ff 0%,#bfdbfe 100%)'},
    { n:'Midnight Blue',  d:true,  v:{'--primary':'#667eea','--primary-dark':'#5a6fd8','--card-bg-dark':'rgba(255,255,255,0.04)','--text':'#e0e6ff','--text-light':'#a0a8d0','--border':'#33334d','--shadow':'rgba(0,0,0,0.5)','--dropdown-bg':'#0f0f1e'}, bg:'linear-gradient(135deg,#0f0f1e 0%,#16213e 100%)'},
    { n:'Nord',           d:true,  v:{'--primary':'#88c0d0','--primary-dark':'#81a1c1','--card-bg-dark':'rgba(76,86,106,0.3)','--text':'#eceff4','--text-light':'#d8dee9','--border':'#4c566a','--shadow':'rgba(46,52,64,0.6)','--dropdown-bg':'#2e3440'}, bg:'linear-gradient(135deg,#2e3440 0%,#3b4252 100%)'},
    { n:'Forest Night',   d:true,  v:{'--primary':'#34d399','--primary-dark':'#10b981','--card-bg-dark':'rgba(6,78,59,0.3)','--text':'#d1fae5','--text-light':'#6ee7b7','--border':'#065f46','--shadow':'rgba(0,0,0,0.6)','--dropdown-bg':'#022c22'}, bg:'linear-gradient(135deg,#022c22 0%,#064e3b 100%)'},
    { n:'Dracula',        d:true,  v:{'--primary':'#bd93f9','--primary-dark':'#9580d6','--card-bg-dark':'rgba(68,71,90,0.5)','--text':'#f8f8f2','--text-light':'#bfbfbf','--border':'#44475a','--shadow':'rgba(0,0,0,0.6)','--dropdown-bg':'#282a36'}, bg:'linear-gradient(135deg,#282a36 0%,#21222c 100%)'},
    { n:'Tokyo Night',    d:true,  v:{'--primary':'#7aa2f7','--primary-dark':'#5d7cd8','--card-bg-dark':'rgba(26,27,38,0.65)','--text':'#c0caf5','--text-light':'#9aa5ce','--border':'#414868','--shadow':'rgba(0,0,0,0.7)','--dropdown-bg':'#1a1b26'}, bg:'linear-gradient(135deg,#1a1b26 0%,#24283b 100%)'},
    { n:'Ember',          d:true,  v:{'--primary':'#fb923c','--primary-dark':'#f97316','--card-bg-dark':'rgba(124,45,18,0.30)','--text':'#fed7aa','--text-light':'#fdba74','--border':'#7c2d12','--shadow':'rgba(0,0,0,0.6)','--dropdown-bg':'#1c0a00'}, bg:'linear-gradient(135deg,#1c0a00 0%,#431407 100%)'},
    { n:'Monokai',        d:true,  v:{'--primary':'#a6e22e','--primary-dark':'#75a828','--card-bg-dark':'rgba(73,72,62,0.55)','--text':'#f8f8f2','--text-light':'#cfcfc2','--border':'#49483e','--shadow':'rgba(0,0,0,0.6)','--dropdown-bg':'#272822'}, bg:'linear-gradient(135deg,#272822 0%,#1e1f1c 100%)'},
];

function applyTheme(i) {
    if (typeof i !== 'number' || isNaN(i) || i < 0 || i >= THEMES.length) i = 7;
    const t = THEMES[i];
    for (const k in t.v) document.documentElement.style.setProperty(k, t.v[k]);
    document.body.style.background = t.bg;
    document.body.setAttribute('data-theme', t.d ? 'dark' : 'light');
    document.documentElement.style.colorScheme = t.d ? 'dark' : 'light';
    const label = document.getElementById('theme-name');
    if (label) label.textContent = t.n;
    localStorage.setItem('icc_themeIdx', i);
}

function toggleTheme() {
    let i = parseInt(localStorage.getItem('icc_themeIdx') || '7', 10);
    if (isNaN(i)) i = 7;
    applyTheme((i + 1) % THEMES.length);
}

applyTheme(parseInt(localStorage.getItem('icc_themeIdx') || '7', 10));

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('theme-btn');
    if (btn) btn.addEventListener('click', toggleTheme);
});
