/* ── RSS 管理面板前端 ─────────────────────────────────── */

const API = '';  // 同源
let currentPage = 'dashboard';
let searchPage = 1;
let mediaChart = null;

let selectedArticles = new Map(); // id -> {title, platform}

let myGlobe = null;
let geocodeCache = {}; // city -> {lat, lng}
let feedLocations = []; // Array of feeds with lat/lng for plotting

document.addEventListener('DOMContentLoaded', () => {
    initNav();
    loadDashboard();
    pollSchedulerStatus();
});

// 全局捕捉 JS 错误并弹窗提醒
window.onerror = function(msg, url, line) {
    alert(`系统脚本错误: ${msg}\n位置: ${line}行\n请尝试刷新页面。`);
    return false;
};

// ── 核心交互函数 (移至顶部确保可用) ──────────────────────────────

window.selectWholeHotEvent = function(idx) {
    if (!window.currentHotEvents) return;
    const event = window.currentHotEvents[idx];
    if (!event) return;

    // 一键全选，但跳过无效 ID
    let addedCount = 0;
    event.items.forEach(item => {
        // 关键修复：跳过 null/undefined/非数字的 id
        if (item.id == null || item.id === undefined || String(item.id) === 'null') {
            console.warn('[WARN] 跳过无效 ID 的文章:', item.title);
            return;
        }
        selectedArticles.set(String(item.id), {
            title: item.title,
            platform: item.platform || '来源'
        });
        addedCount++;
    });

    if (addedCount === 0) {
        alert('该热点事件中的文章缺少有效 ID，无法选择。请尝试刷新热点分析。');
        return;
    }
    
    // UI 状态刷新
    document.querySelectorAll('.hot-event-card').forEach(c => c.classList.remove('active'));
    const card = document.getElementById(`he-card-${idx}`);
    if (card) {
        card.classList.add('active');
        card.querySelectorAll('.he-item-check').forEach(cb => cb.checked = true);
    }
    
    renderWorkbench();
    updateFloatingBar();
};

window.toggleHotItem = function(eventIdx, articleId, checked) {
    // 关键修复：验证 articleId 是否有效
    if (articleId == null || articleId === undefined || String(articleId) === 'null' || String(articleId) === 'undefined') {
        console.warn('[WARN] 尝试选择无效 ID 的文章，已拦截');
        if (checked) {
            alert('该文章缺少有效 ID，无法选择。请尝试刷新热点分析。');
        }
        return;
    }

    const sid = String(articleId);
    if (checked) {
        const event = window.currentHotEvents[eventIdx];
        if (event && event.items) {
            const item = event.items.find(i => String(i.id) === sid);
            if (item) {
                selectedArticles.set(sid, {
                    title: item.title,
                    platform: item.platform || '来源'
                });
            }
        }
    } else {
        selectedArticles.delete(sid);
    }
    renderWorkbench();
    updateFloatingBar();
};

window.removeFromWorkbench = function(id) {
    const sid = String(id);
    selectedArticles.delete(sid);
    
    // 同步取消左侧卡片内的勾选状态
    document.querySelectorAll(`.he-item-check`).forEach(cb => {
        if (cb.getAttribute('onclick') && cb.getAttribute('onclick').includes(`'${sid}'`)) {
            cb.checked = false;
        }
    });
    
    renderWorkbench();
    updateFloatingBar();
};

function initNav() {
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', (e) => {
            const page = link.dataset.page;
            if (page) {
                e.preventDefault();
                switchPage(page);
            }
            // If no data-page, allow default <a> navigation (e.g., to /foreign_media.html)
        });
    });
}

function switchPage(page) {
    if (!page) return;
    currentPage = page;
    
    // 1. 更新导航栏状态
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    const navLink = document.querySelector(`[data-page="${page}"]`);
    if (navLink) navLink.classList.add('active');

    // 2. 切换页面正文显示
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) {
        pageEl.classList.add('active');
    } else {
        console.warn(`[WARN] 页面元素 page-${page} 不存在`);
    }

    if (page === 'dashboard') loadDashboard();
    else if (page === 'feeds') loadFeeds();
    else if (page === 'scifi') {
        if (!myGlobe) renderGlobeMap();
    }
    else if (page === 'explorer') { loadMediaGroups(); loadPlatforms(); loadCountries(); doSearch(); }
    else if (page === 'logs') loadLogs();
    else if (page === 'database') loadDbStats();
    else if (page === 'intelligence') loadIntelligence();
    else if (page === 'timeline') loadTimelinePage();
}

// ── API 工具 ─────────────────────────────────────────────
async function api(path, opts = {}) {
    const res = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json();
    if (!res.ok) {
        if (data.error) alert(data.error);
        throw new Error(data.error || 'Server Error');
    }
    return data;
}

function fmtTime(iso) {
    if (!iso) return '—';
    try {
        const d = new Date(iso);
        const pad = n => String(n).padStart(2, '0');
        return `${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch { return iso; }
}

function fmtDuration(sec) {
    if (!sec && sec !== 0) return '—';
    if (sec < 60) return `${Math.round(sec)}s`;
    return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
}

function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

function getCountryFlag(country) {
    const flags = {
        '美国': '🇺🇸', '英国': '🇬🇧', '日本': '🇯🇵', '韩国': '🇰🇷', '新加坡': '🇸🇬',
        '俄罗斯': '🇷🇺', '中国': '🇨🇳', '台湾': '🇹🇼', '香港': '🇭🇰', '中国香港': '🇭🇰',
        '中国台湾': '🇹🇼', '法国': '🇫🇷', '德国': '🇩🇪', '加拿大': '🇨🇦', '澳大利亚': '🇦🇺',
        '印度': '🇮🇳', '瑞士': '🇨🇭', '以色列': '🇮🇱', '乌克兰': '🇺🇦', '中东': '☪️'
    };
    if (!country) return '🌐';
    return flags[country] || '🏳️';
}

// 获取媒体缩略图URL（支持图片和YouTube视频）
function getMediaThumbUrl(image, video) {
    // 优先使用视频缩略图
    if (video && (video.includes('youtube.com') || video.includes('youtu.be'))) {
        let videoId = '';
        if (video.includes('youtube.com/watch?v=')) {
            const match = video.match(/[?&]v=([^&]+)/);
            if (match) videoId = match[1];
        } else if (video.includes('youtu.be/')) {
            const match = video.match(/youtu\.be\/([^?]+)/);
            if (match) videoId = match[1];
        } else if (video.includes('youtube.com/embed/')) {
            const match = video.match(/embed\/([^?]+)/);
            if (match) videoId = match[1];
        }
        if (videoId) {
            return `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
        }
    }
    return image || '';
}

// ── 仪表盘 ───────────────────────────────────────────────
async function loadDashboard() {
    const [stats, fetchStatus, logs] = await Promise.all([
        api('/api/stats'),
        api('/api/fetch/status'),
        api('/api/fetch/logs?limit=5'),
    ]);

    document.getElementById('statTotal').textContent = stats.total?.toLocaleString() || '0';
    document.getElementById('statToday').textContent = stats.today?.toLocaleString() || '0';
    document.getElementById('statWeek').textContent = stats.this_week?.toLocaleString() || '0';
    document.getElementById('statLastFetch').textContent =
        fmtTime(fetchStatus.scheduler?.last_run);
    
    // 下次运行显示
    const nextRunDt = fetchStatus.scheduler?.next_run;
    const nextReason = fetchStatus.scheduler?.next_run_reason || '常规间隔';
    const nextEl = document.getElementById('statNextFetch');
    if (nextEl) {
        nextEl.innerHTML = fmtTime(nextRunDt) + 
            (nextReason.includes('补位') ? ` <span class="badge-reason badge-padding">小时补位</span>` : '');
    }

    // 进度处理
    updateProgressUI(fetchStatus.scheduler);
    
    // 媒体图表
    renderMediaChart(stats.by_media || []);

    // 最近抓取
    const list = document.getElementById('recentFetchList');
    const fetchLogs = logs.logs || [];
    if (!fetchLogs.length) {
        list.innerHTML = '<div class="empty-state"><div class="icon">📭</div>暂无抓取记录</div>';
    } else {
        list.innerHTML = fetchLogs.map(l => `
            <div class="fetch-item">
                <span class="time">${fmtTime(l.started_at)}</span>
                <span class="status-tag ${l.status}">${l.status}</span>
                <span class="count">+${l.articles_new || 0} 条</span>
                <span class="time">${fmtDuration(l.duration_sec)}</span>
            </div>
        `).join('');
    }

    // 渲染地图数据缓存（后台）
    if (feedLocations.length === 0) {
        refreshGlobeFeeds();
    }

    // ── 抓取控制中心 ──────────────────────────────────────
    const manualBtn = document.getElementById('btnManualFetch');
    const inputSince = document.getElementById('inputFetchSince');
    const btnReset = document.getElementById('btnResetSince');
    const lastAtStr = fetchStatus.scheduler?.last_fetch_at;

    // 辅助函数：转换为 datetime-local 格式
    const toInputVal = (iso) => {
        if (!iso) return "";
        const d = new Date(iso);
        const pad = n => n.toString().padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    };

    if (lastAtStr && inputSince) {
        // 只有在输入框为空时才初始化，避免覆盖用户正在输入的内容
        if (!inputSince.value) {
            inputSince.value = toInputVal(lastAtStr);
        }
    }

    if (btnReset) {
        btnReset.onclick = () => {
            if (lastAtStr) inputSince.value = toInputVal(lastAtStr);
        };
    }

    if (manualBtn) {
        manualBtn.onclick = async () => {
            manualBtn.disabled = true;
            manualBtn.classList.add('btn-loading');
            
            // 获取用户选择的时间，并转回 ISO 格式
            let sinceParam = null;
            if (inputSince && inputSince.value) {
                sinceParam = new Date(inputSince.value).toISOString();
            }

            try {
                await api('/api/fetch', { 
                    method: 'POST', 
                    body: { since: sinceParam } 
                });
                pollFetchProgress();
            } catch(e) {
                manualBtn.disabled = false;
                manualBtn.classList.remove('btn-loading');
            }
        };
    }

    // 手动抓取 (科幻舱)
    const btnScifi = document.getElementById('btnScifiFetch');
    if (btnScifi) {
        btnScifi.onclick = async () => {
            btnScifi.disabled = true;
            btnScifi.classList.add('btn-loading');
            try {
                await api('/api/fetch', { method: 'POST', body: {} }); 
                pollFetchProgress();
            } catch(e) {
                btnScifi.disabled = false;
                btnScifi.classList.remove('btn-loading');
            }
        };
    }
}

let progressInterval = null;
let recentArticles = []; // 用于侧边栏滚动条
let articleIds = new Set();

// 流星精准弹射机制 + 发射卡片动画
function spawnMeteor(feed, item) {
    if (!myGlobe) return;
    const coords = myGlobe.getScreenCoords(feed.lat, feed.lng);
    if (!coords) return;

    const key = item.article_title + item.article_time;
    if (articleIds.has(key)) return;
    articleIds.add(key);

    const scifiRect = document.getElementById('shootingStarsLayer').getBoundingClientRect();
    const list = document.getElementById('liveFeedList');

    // 1. 创建发射卡片（先放大展示）
    const launchCard = document.createElement('div');
    launchCard.className = 'launch-card';

    const imageUrl = item.image || '';
    const hasImage = imageUrl && imageUrl.length > 0;
    const articleUrl = item.article_url || '';

    launchCard.innerHTML = `
        ${hasImage
            ? `<img class="launch-card-thumb" src="${escHtml(imageUrl)}" onerror="this.outerHTML='<div class=\\'launch-card-thumb-placeholder\\'>📰</div>'" />`
            : `<div class="launch-card-thumb-placeholder">📰</div>`
        }
        <div class="launch-card-content">
            <div class="launch-card-source">${escHtml(item.feed_name || feed.name || '未知频段')}</div>
            <div class="launch-card-title">${articleUrl ? `<a href="${escHtml(articleUrl)}" target="_blank" style="color:inherit;text-decoration:none;">${escHtml(item.article_title)}</a>` : escHtml(item.article_title)}</div>
            <div class="launch-card-time">${item.article_time || ''}</div>
        </div>
    `;

    // 定位在球体附近（略微偏移避免遮挡）
    launchCard.style.left = (coords.x - 140) + 'px';
    launchCard.style.top = (coords.y - 100) + 'px';

    document.getElementById('shootingStarsLayer').appendChild(launchCard);

    // 2. 创建右侧列表项（带缩略图）
    const div = document.createElement('div');
    div.className = 'feed-item';
    div.style.opacity = '0'; // 隐形占位，等待动画
    div.innerHTML = `
        ${hasImage
            ? `<img class="feed-item-thumb" src="${escHtml(imageUrl)}" onerror="this.outerHTML='<div class=\\'feed-item-thumb-placeholder\\'>📰</div>'" />`
            : `<div class="feed-item-thumb-placeholder">📰</div>`
        }
        <div class="feed-item-content">
            <div class="fi-meta"><span class="fi-source">${escHtml(item.feed_name || feed.name || '未知频段')}</span> <span class="fi-time">${item.article_time || ''}</span></div>
            <div class="fi-title">${articleUrl ? `<a href="${escHtml(articleUrl)}" target="_blank" style="color:inherit;text-decoration:none;">${escHtml(item.article_title)}</a>` : escHtml(item.article_title)}</div>
        </div>
    `;
    list.prepend(div);

    // 计算右侧目标位置
    const rect = div.getBoundingClientRect();
    const targetX = rect.left - scifiRect.left;
    const targetY = rect.top - scifiRect.top + rect.height / 2;

    // 3. 延迟发射流星射线（在卡片展示后）
    setTimeout(() => {
        const star = document.createElement('div');
        star.className = 'meteor-star';
        star.style.left = coords.x + 'px';
        star.style.top = coords.y + 'px';

        document.getElementById('shootingStarsLayer').appendChild(star);

        // 强制重绘
        star.getBoundingClientRect();

        // 计算射线夹角与距离
        const dx = targetX - coords.x;
        const dy = targetY - coords.y;
        const angle = Math.atan2(dy, dx) * 180 / Math.PI;
        const dist = Math.sqrt(dx * dx + dy * dy);

        star.style.width = '60px';
        star.style.transform = `rotate(${angle}deg) translateX(${dist}px)`;
        star.style.opacity = '0';

        setTimeout(() => {
            star.remove();
            div.classList.add('new-item');
            div.style.opacity = '';

            recentArticles.unshift({ ...item, source: feed.feed_name || feed.platform });
            if (list.children.length > 50) {
                const removed = recentArticles.pop();
                if (removed) articleIds.delete(removed.article_title + removed.article_time);
                list.removeChild(list.lastChild);
            }
        }, 600);
    }, 1300); // 等待launchCard动画完成（1.2s动画 + 0.1s缓冲）

    // 4. 清理发射卡片
    setTimeout(() => {
        launchCard.remove();
    }, 1700);
}

// --- 补充坐标库与翻译库 ---
// 全球主流媒体城市坐标库（精确到城市级别）
const CITY_COORDS = {
    // === 中国大陆 ===
    '北京': {lat: 39.9042, lng: 116.4074},
    '上海': {lat: 31.2304, lng: 121.4737},
    '广州': {lat: 23.1291, lng: 113.2644},
    '深圳': {lat: 22.5431, lng: 114.0579},
    '成都': {lat: 30.5728, lng: 104.0668},
    '杭州': {lat: 30.2741, lng: 120.1551},
    '南京': {lat: 32.0603, lng: 118.7969},
    '武汉': {lat: 30.5928, lng: 114.3055},
    '西安': {lat: 34.3416, lng: 108.9398},
    '重庆': {lat: 29.4316, lng: 106.9123},

    // === 港澳台 ===
    '香港': {lat: 22.3193, lng: 114.1694},
    '中国香港': {lat: 22.3193, lng: 114.1694},
    '澳门': {lat: 22.1987, lng: 113.5439},
    '台湾': {lat: 25.0330, lng: 121.5654},
    '中国台湾': {lat: 25.0330, lng: 121.5654},
    '台北': {lat: 25.0330, lng: 121.5654},

    // === 新加坡 ===
    '新加坡': {lat: 1.3521, lng: 103.8198},
    '联合早报': {lat: 1.3521, lng: 103.8198},

    // === 日本 ===
    '日本': {lat: 35.6762, lng: 139.6503},
    '东京': {lat: 35.6762, lng: 139.6503},
    'NHK': {lat: 35.6762, lng: 139.6503},
    '读卖新闻': {lat: 35.6762, lng: 139.6503},
    '朝日新闻': {lat: 35.6762, lng: 139.6503},
    '产经新闻': {lat: 35.6762, lng: 139.6503},
    '日本经济新闻': {lat: 35.6762, lng: 139.6503},

    // === 韩国 ===
    '韩国': {lat: 37.5665, lng: 126.9780},
    '首尔': {lat: 37.5665, lng: 126.9780},
    '朝鲜': {lat: 39.0392, lng: 125.7625},

    // === 美国 ===
    '美国': {lat: 38.9072, lng: -77.0369},
    '华盛顿': {lat: 38.9072, lng: -77.0369},
    '华盛顿邮报': {lat: 38.9072, lng: -77.0369},
    '美国政治': {lat: 38.9072, lng: -77.0369},
    '纽约': {lat: 40.7128, lng: -74.0060},
    '纽约时报': {lat: 40.7128, lng: -74.0060},
    '华尔街日报': {lat: 40.7128, lng: -74.0060},
    '美联社': {lat: 40.7128, lng: -74.0060},
    '洛杉矶': {lat: 34.0522, lng: -118.2437},
    '旧金山': {lat: 37.7749, lng: -122.4194},
    'CNN': {lat: 33.7490, lng: -84.3880},
    '亚特兰大': {lat: 33.7490, lng: -84.3880},
    '芝加哥': {lat: 41.8781, lng: -87.6298},
    '波士顿': {lat: 42.3601, lng: -71.0589},
    '西雅图': {lat: 47.6062, lng: -122.3321},
    '硅谷': {lat: 37.3861, lng: -122.0839},

    // === 英国 ===
    '英国': {lat: 51.5074, lng: -0.1278},
    '伦敦': {lat: 51.5074, lng: -0.1278},
    'BBC': {lat: 51.5074, lng: -0.1278},
    '路透社': {lat: 51.5074, lng: -0.1278},
    '金融时报': {lat: 51.5074, lng: -0.1278},
    '卫报': {lat: 51.5074, lng: -0.1278},
    '经济学人': {lat: 51.5074, lng: -0.1278},
    '泰晤士报': {lat: 51.5074, lng: -0.1278},

    // === 法国 ===
    '法国': {lat: 48.8566, lng: 2.3522},
    '巴黎': {lat: 48.8566, lng: 2.3522},
    '法新社': {lat: 48.8566, lng: 2.3522},

    // === 德国 ===
    '德国': {lat: 52.5200, lng: 13.4050},
    '柏林': {lat: 52.5200, lng: 13.4050},
    '法兰克福': {lat: 50.1109, lng: 8.6821},
    '德国之声': {lat: 50.7374, lng: 7.0982},

    // === 俄罗斯 ===
    '俄罗斯': {lat: 55.7558, lng: 37.6173},
    '莫斯科': {lat: 55.7558, lng: 37.6173},
    '塔斯社': {lat: 55.7558, lng: 37.6173},
    '今日俄罗斯': {lat: 55.7558, lng: 37.6173},
    '卫星通讯社': {lat: 55.7558, lng: 37.6173},

    // === 中东 ===
    '卡塔尔': {lat: 25.2854, lng: 51.5310},
    '半岛电视台': {lat: 25.2854, lng: 51.5310},
    '以色列': {lat: 31.7683, lng: 35.2137},
    '耶路撒冷': {lat: 31.7683, lng: 35.2137},
    '伊朗': {lat: 35.6892, lng: 51.3890},
    '德黑兰': {lat: 35.6892, lng: 51.3890},
    '沙特': {lat: 24.7136, lng: 46.6753},
    '利雅得': {lat: 24.7136, lng: 46.6753},
    '阿联酋': {lat: 25.2048, lng: 55.2708},
    '迪拜': {lat: 25.2048, lng: 55.2708},

    // === 印度 ===
    '印度': {lat: 28.6139, lng: 77.2090},
    '新德里': {lat: 28.6139, lng: 77.2090},
    '孟买': {lat: 19.0760, lng: 72.8777},
    '印度时报': {lat: 19.0760, lng: 72.8777},

    // === 澳大利亚 ===
    '澳大利亚': {lat: -35.2809, lng: 149.1300},
    '悉尼': {lat: -33.8688, lng: 151.2093},
    '墨尔本': {lat: -37.8136, lng: 144.9631},
    '堪培拉': {lat: -35.2809, lng: 149.1300},
    '瑞典': {lat: 59.3293, lng: 18.0686},
    '挪威': {lat: 59.9139, lng: 10.7522},
    '奥斯陆': {lat: 59.9139, lng: 10.7522},
    '丹麦': {lat: 55.6761, lng: 12.5683},
    '哥本哈根': {lat: 55.6761, lng: 12.5683},
    '波兰': {lat: 52.2297, lng: 21.0122},
    '华沙': {lat: 52.2297, lng: 21.0122},
    '乌克兰': {lat: 50.4501, lng: 30.5234},
    '基辅': {lat: 50.4501, lng: 30.5234},

    // === 东南亚 ===
    '泰国': {lat: 13.7563, lng: 100.5018},
    '曼谷': {lat: 13.7563, lng: 100.5018},
    '马来西亚': {lat: 3.1390, lng: 101.6869},
    '吉隆坡': {lat: 3.1390, lng: 101.6869},
    '印度尼西亚': {lat: -6.2088, lng: 106.8456},
    '雅加达': {lat: -6.2088, lng: 106.8456},
    '越南': {lat: 21.0285, lng: 105.8542},
    '河内': {lat: 21.0285, lng: 105.8542},
    '胡志明市': {lat: 10.8231, lng: 106.6297},
    '菲律宾': {lat: 14.5995, lng: 120.9842},
    '马尼拉': {lat: 14.5995, lng: 120.9842},

    // === 南美 ===
    '巴西': {lat: -15.7801, lng: -47.9292},
    '巴西利亚': {lat: -15.7801, lng: -47.9292},
    '圣保罗': {lat: -23.5505, lng: -46.6333},
    '里约热内卢': {lat: -22.9068, lng: -43.1729},
    '阿根廷': {lat: -34.6037, lng: -58.3816},
    '布宜诺斯艾利斯': {lat: -34.6037, lng: -58.3816},

    // === 非洲 ===
    '南非': {lat: -25.7479, lng: 28.2293},
    '约翰内斯堡': {lat: -26.2041, lng: 28.0473},
    '开普敦': {lat: -33.9249, lng: 18.4241},
    '埃及': {lat: 30.0444, lng: 31.2357},
    '开罗': {lat: 30.0444, lng: 31.2357},
    '尼日利亚': {lat: 9.0820, lng: 8.6753},
    '拉各斯': {lat: 6.5244, lng: 3.3792},
    '肯尼亚': {lat: -1.2921, lng: 36.8219},
    '内罗毕': {lat: -1.2921, lng: 36.8219},

    // === 新西兰 ===
    '新西兰': {lat: -41.2865, lng: 174.7762},
    '惠灵顿': {lat: -41.2865, lng: 174.7762},
    '奥克兰': {lat: -36.8509, lng: 174.7645},

    // === 媒体品牌别名映射 ===
    '南华早报': {lat: 22.3193, lng: 114.1694},
    '端传媒': {lat: 22.3193, lng: 114.1694},
    '澎湃新闻': {lat: 31.2304, lng: 121.4737},
    '界面新闻': {lat: 31.2304, lng: 121.4737},
    '第一财经': {lat: 31.2304, lng: 121.4737},
    '财新': {lat: 31.2304, lng: 121.4737},
    '观察者网': {lat: 31.2304, lng: 121.4737},
    '环球时报': {lat: 39.9042, lng: 116.4074},
    '新华社': {lat: 39.9042, lng: 116.4074},
    '人民日报': {lat: 39.9042, lng: 116.4074},
    '央视新闻': {lat: 39.9042, lng: 116.4074},
    '中国日报': {lat: 39.9042, lng: 116.4074},
    '参考消息': {lat: 39.9042, lng: 116.4074},
    '海峡时报': {lat: 1.3521, lng: 103.8198},
    '彭博社': {lat: 40.7128, lng: -74.0060},
};

const COUNTRY_TRANS = {
    'United States of America': '美国',
    'China': '中国',
    'Japan': '日本',
    'United Kingdom': '英国',
    'Russia': '俄罗斯',
    'France': '法国',
    'Germany': '德国',
    'India': '印度',
    'Australia': '澳大利亚',
    'Canada': '加拿大',
    'South Korea': '韩国',
    'Singapore': '新加坡',
    'Taiwan': '台湾',
    'Hong Kong': '香港',
};

async function getGeocode(city, platform, media_group) {
    if (CITY_COORDS[city]) return CITY_COORDS[city];
    if (CITY_COORDS[platform]) return CITY_COORDS[platform];
    if (CITY_COORDS[media_group]) return CITY_COORDS[media_group];

    if (!city) return CITY_COORDS['北京']; // Fallback to China instead of Atlantic Ocean

    if (geocodeCache[city]) return geocodeCache[city];
    try {
        const res = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(city)}&format=json&limit=1`, {
            headers: { 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' }
        });
        const data = await res.json();
        if (data && data.length > 0) {
            const loc = { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon) };
            geocodeCache[city] = loc;
            return loc;
        }
    } catch {}
    
    // Default Fallback
    return CITY_COORDS['北京'];
}

async function refreshGlobeFeeds() {
    const data = await api('/api/feeds');
    const feeds = data.feeds || [];
    
    // 按经纬度坐标聚类，提取主媒体名称防止标签重叠堆砌
    const locationMap = {};
    for (const f of feeds) {
        const cityName = f.city || f.platform || '北京';
        const loc = await getGeocode(cityName, f.platform, f.media_group);
        if (loc) {
            const key = `${loc.lat.toFixed(4)},${loc.lng.toFixed(4)}`;
            if (!locationMap[key]) {
                locationMap[key] = { lat: loc.lat, lng: loc.lng, names: new Set(), rawPlatforms: new Set() };
            }
            if (f.platform) {
                const baseName = f.platform.split('|')[0].trim();
                locationMap[key].names.add(baseName);
                locationMap[key].rawPlatforms.add(f.platform);
            }
        }
    }
    
    const NAME_MAP = {
        '联合早报': 'Lianhe Zaobao',
        '路透社': 'Reuters',
        '纽约时报': 'NYT',
        '华盛顿邮报': 'Washington Post',
        'CNN': 'CNN',
        'BBC': 'BBC',
        'NHK': 'NHK',
        'SCMP': 'SCMP',
        '南华早报': 'SCMP',
        '韩联社': 'Yonhap',
        '俄罗斯卫星通讯社': 'Sputnik',
        '共同社': 'Kyodo News',
        '共同网': 'Kyodo News',
        '日经中文网': 'Nikkei',
        '美联社': 'AP',
        '德国之声': 'DW',
        '印度经济时报': 'Economic Times',
        '法国24电视台': 'France 24',
        '加拿大广播公司': 'CBC',
        '加拿大金融邮报': 'Financial Post',
        '彭博社': 'Bloomberg',
        '美国国防部': 'DoD'
    };

    feedLocations = Object.values(locationMap).map(d => {
        const bilingualNames = Array.from(d.names).map(n => {
            const en = NAME_MAP[n];
            return en ? `${n} | ${en}` : n;
        });
        return {
            lat: d.lat, lng: d.lng,
            name: bilingualNames.join(' <br> '),
            platforms: Array.from(d.rawPlatforms)
        };
    });

    if (myGlobe) {
        myGlobe.pointsData(feedLocations);
        myGlobe.ringsData(feedLocations);
        myGlobe.htmlElementsData(feedLocations);
    }
}

async function renderGlobeMap() {
    const container = document.getElementById('globeContainer');
    if (!container) return;

    if (feedLocations.length === 0) {
        await refreshGlobeFeeds();
    }

    console.log('[DEBUG] feedLocations:', feedLocations.length, feedLocations.slice(0,3));

    if (!myGlobe) {
        myGlobe = Globe()(container)
            // 夜间地球纹理（城市灯光效果）
            .globeImageUrl('//unpkg.com/three-globe/example/img/earth-night.jpg')
            .bumpImageUrl('//unpkg.com/three-globe/example/img/earth-topology.png')
            .backgroundImageUrl('//unpkg.com/three-globe/example/img/night-sky.png')
            // 大气层 - 青色科幻感
            .showAtmosphere(true)
            .atmosphereColor('#00f3ff')
            .atmosphereAltitude(0.12)
            // 国家边界 - 增强可见度
            .polygonCapColor(() => 'rgba(0, 243, 255, 0.02)')
            .polygonSideColor(() => 'rgba(0, 0, 0, 0)')
            .polygonStrokeColor(() => 'rgba(0, 243, 255, 0.4)')
            .polygonLabel(({ properties: d }) => `
                <div style="background: rgba(0,0,0,0.9); padding: 10px 16px; border: 1px solid #00f3ff; border-radius: 8px; color: white; box-shadow: 0 0 20px rgba(0,243,255,0.3);">
                    <span style="color:#00f3ff; font-weight: 700; font-size: 14px;">${COUNTRY_TRANS[d.ADMIN] || d.ADMIN}</span>
                </div>
            `)
            // 发光点标记
            .pointsData(feedLocations)
            .pointColor(() => '#00f3ff')
            .pointRadius(0.5)
            .pointAltitude(0.02)
            // 脉冲圆环效果
            .ringsData(feedLocations)
            .ringColor(() => 'rgba(0, 243, 255, 0.4)')
            .ringMaxRadius(3)
            .ringPropagationSpeed(2)
            .ringRepeatPeriod(1200)
            // 使用 HTML 元素替代原生 Label 以支持中文字符和更好的样式
            .htmlElementsData(feedLocations)
            .htmlElement(d => {
                const el = document.createElement('div');
                el.innerHTML = `<div class="globe-label">${d.name}</div>`;
                el.style.color = '#ffffff';
                el.style.width = 'fit-content';
                el.style['pointer-events'] = 'none';
                return el;
            });

        // 加载国家边界
        fetch('https://unpkg.com/globe.gl/example/datasets/ne_110m_admin_0_countries.geojson')
            .then(res => res.json())
            .then(countries => {
                myGlobe.polygonsData(countries.features);
            });

        // 初始视角：亚洲上空
        myGlobe.pointOfView({ lat: 25, lng: 110, altitude: 1.8 });
        myGlobe.controls().autoRotate = true;
        myGlobe.controls().autoRotateSpeed = 0.3;
    } else {
        myGlobe.pointsData(feedLocations);
        myGlobe.ringsData(feedLocations);
        myGlobe.labelsData(feedLocations);
    }
}

// 高亮指定媒体源（抓取时调用）
window.highlightGlobePoint = function(platformName, active) {
    if (!myGlobe || !feedLocations.length) return;

    // 找到匹配的位置
    const targetLoc = feedLocations.find(f =>
        f.platforms && f.platforms.some(p => p.includes(platformName))
    );

    if (targetLoc && active) {
        // 高亮为紫色
        myGlobe.pointColor(d => {
            if (d.platforms && d.platforms.some(p => p.includes(platformName))) {
                return '#a855f7';
            }
            return '#00f3ff';
        });
        myGlobe.ringColor(d => {
            if (d.platforms && d.platforms.some(p => p.includes(platformName))) {
                return 'rgba(168, 85, 247, 0.6)';
            }
            return 'rgba(0, 243, 255, 0.4)';
        });

        // 镜头转向该位置
        myGlobe.pointOfView({ lat: targetLoc.lat, lng: targetLoc.lng, altitude: 1.2 }, 1000);
    } else {
        // 恢复默认青色
        myGlobe.pointColor(() => '#00f3ff');
        myGlobe.ringColor(() => 'rgba(0, 243, 255, 0.4)');
    }
};

function updateProgressUI(scheduler) {
    const container = document.getElementById('fetchProgressContainer');
    const bar = document.getElementById('fetchProgressBar');
    const text = document.getElementById('fetchProgressText');
    const percent = document.getElementById('fetchProgressPercent');

    const sContainer = document.getElementById('scifiProgressContainer');
    const sBar = document.getElementById('scifiProgressBar');
    const sText = document.getElementById('scifiProgressText');
    const sPercent = document.getElementById('scifiProgressPercent');

// --- 渲染动态更新事件 ---
    if (scheduler?.is_fetching) {
        if(container) container.style.display = 'block';
        if(sContainer) sContainer.style.display = 'block';
        const prog = scheduler.fetch_progress;

        // 使用新的高亮函数
        if (prog && prog.feed_name && typeof window.highlightGlobePoint === 'function') {
            window.highlightGlobePoint(prog.feed_name, true);
        }

        if (typeof prog === 'object' && prog !== null) {
            if (prog.type === 'feed') {
                const txt = `准备解析: ${prog.feed_name || ''} (${prog.status || ''})`;
                if(text) text.textContent = txt;
                if(sText) sText.textContent = txt;
                
                const p = Math.round(((prog.feed_idx - 1) / prog.feed_total) * 100) || 5;
                if(bar) bar.style.width = p + '%';
                if(percent) percent.textContent = `${prog.feed_idx}/${prog.feed_total}`;
                
                if(sBar) sBar.style.width = p + '%';
                if(sPercent) sPercent.textContent = `${prog.feed_idx}/${prog.feed_total}`;

                // --- 自动追焦摄像机地图转向 ---
                const targetFeed = feedLocations.find(f => f.platforms && f.platforms.includes(prog.feed_name));
                if (targetFeed && myGlobe) {
                    myGlobe.pointOfView({ lat: targetFeed.lat, lng: targetFeed.lng, altitude: 1.6 }, 800);
                }

            } else if (prog.type === 'article') {
                const feedP = (prog.feed_idx - 1) / prog.feed_total;
                const artP = (prog.article_idx / prog.article_total) * (1 / prog.feed_total);
                const p = Math.round((feedP + artP) * 100) || 0;
                
                if(bar) bar.style.width = p + '%';
                if(percent) percent.textContent = `${p}%`;
                
                if(sBar) sBar.style.width = p + '%';
                if(sPercent) sPercent.textContent = `${p}%`;
                
                // --- Sci-Fi Meteor Animation Trigger ---
                if (prog.is_new) {
                    let targetFeed = feedLocations.find(f => f.platforms && f.platforms.includes(prog.feed_name));
                    if(!targetFeed) targetFeed = { lat: 0, lng: 0, feed_name: prog.feed_name };
                    spawnMeteor(targetFeed, prog);
                }

                const statusTag = prog.is_new ? 
                    `<span style="color:var(--accent-1); font-size:12px; margin-right:6px;">✨ 新</span>` : 
                    `<span style="color:var(--text-secondary); font-size:12px; margin-right:6px; text-decoration:line-through;">🚫 旧</span>`;

                if(text) text.innerHTML = `
                    <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 6px;">
                        正在解析源 <span style="color:var(--accent-2)">[${prog.feed_idx}/${prog.feed_total}]</span>: <span style="color:var(--text-primary); font-weight:600;">${prog.feed_name}</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center; gap: 12px; width: 100%;">
                        <span style="flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${prog.article_title || ''}">
                            <span style="color:var(--accent-2); margin-right: 6px;">${prog.article_idx}/${prog.article_total}</span> 
                            ${statusTag}<span style="${prog.is_new ? '' : 'color:var(--text-secondary);'}">${prog.article_title || ''}</span>
                        </span>
                        ${prog.article_time ? `<span style="font-size:12px; padding:3px 8px; background:rgba(99,102,241,0.15); color:var(--accent-2); border-radius:6px; flex-shrink:0; border: 1px solid rgba(99,102,241,0.3); box-shadow: 0 0 10px rgba(99,102,241,0.1);">🕒 ${prog.article_time}</span>` : ''}
                    </div>
                `;
                
                const sStatusTag = prog.is_new ? 
                    `<span style="color:#00f3ff; margin-right:6px;">[NEW]</span>` : 
                    `<span style="color:rgba(255,255,255,0.3); margin-right:6px; text-decoration:line-through;">[OLD]</span>`;

                if(sText) sText.innerHTML = `
                    <div style="font-size: 13px; color: rgba(255,255,255,0.7); margin-bottom: 6px;">
                        正在解析源 <span style="color:#00f3ff">[${prog.feed_idx}/${prog.feed_total}]</span>: <span style="font-weight:600;">${prog.feed_name}</span>
                    </div>
                    <div style="font-size: 12px; color: ${prog.is_new ? '#a855f7' : 'rgba(255,255,255,0.3)'}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 400px; display:flex; gap: 8px;">
                        <span style="color: rgba(255,255,255,0.4)">${prog.article_time || ''}</span>
                        <span>${sStatusTag}${prog.article_title || ''}</span>
                    </div>
                `;
            } else if (prog.type === 'jina_start') {
                const str = `准备抓取正文 (${prog.total} 篇) ...`;
                if(text) text.textContent = str;
                if(sText) sText.textContent = str;
                if(bar) bar.style.width = '95%';
                if(percent) percent.textContent = '95%';
                if(sBar) sBar.style.width = '95%';
                if(sPercent) sPercent.textContent = '95%';
            } else if (prog.type === 'jina_article') {
                const htmlStr = `
                    <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 6px;">
                        正在提取正文 (Jina AI)
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                            <span style="color:var(--accent-2)">${prog.jina_idx}/${prog.jina_total}</span> 
                            ${prog.article_title || ''}
                        </span>
                    </div>
                `;
                if(text) text.innerHTML = htmlStr;
                if(sText) sText.innerHTML = htmlStr;
                
                const p = 95 + Math.round((prog.jina_idx / prog.jina_total) * 2);
                if(bar) bar.style.width = p + '%';
                if(percent) percent.textContent = p + '%';
                if(sBar) sBar.style.width = p + '%';
                if(sPercent) sPercent.textContent = p + '%';
            } else if (prog.type === 'llm_start' || prog.type === 'llm_batch') {
                const isBatch = prog.type === 'llm_batch';
                const htmlStr = `
                    <div style="font-size: 13px; color: var(--accent-1); margin-bottom: 6px;">
                        正在进行 AI 语义分类 (DeepSeek)
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                            ${isBatch ? `分析进度: <span style="color:var(--accent-2)">${prog.batch_end}/${prog.total}</span>` : '准备调用 LLM 模型...'}
                        </span>
                    </div>
                `;
                if(text) text.innerHTML = htmlStr;
                if(sText) sText.innerHTML = htmlStr;
                
                const p = 97 + (isBatch ? Math.round((prog.batch_end / prog.total) * 2) : 0);
                if(bar) bar.style.width = p + '%';
                if(percent) percent.textContent = p + '%';
                if(sBar) sBar.style.width = p + '%';
                if(sPercent) sPercent.textContent = p + '%';
            } else if (prog.type === 'llm_done') {
                const str = `AI 分类完成 (${prog.tagged} 篇命中标签)`;
                if(text) text.textContent = str;
                if(sText) sText.textContent = str;
                if(bar) bar.style.width = '100%';
                if(percent) percent.textContent = '100%';
                if(sBar) sBar.style.width = '100%';
                if(sPercent) sPercent.textContent = '100%';
            } else {
                if(text) text.textContent = '正在执行后续处理...';
                if(sText) sText.textContent = '正在执行后续处理...';
                if(bar) bar.style.width = '99%';
                if(percent) percent.textContent = '...';
                if(sBar) sBar.style.width = '99%';
                if(sPercent) sPercent.textContent = '...';
            }
        } else {
            const strProg = typeof prog === 'string' ? prog : '正在启动...';
            if(text) text.textContent = strProg;
            if(sText) sText.textContent = strProg;
            
            const match = strProg.match(/\((\d+)\/(\d+)\)/);
            if (match) {
                const p = Math.round((parseInt(match[1]) / parseInt(match[2])) * 100);
                if(bar) bar.style.width = p + '%';
                if(percent) percent.textContent = p + '%';
                if(sBar) sBar.style.width = p + '%';
                if(sPercent) sPercent.textContent = p + '%';
            } else {
                if(bar) bar.style.width = '5%';
                if(percent) percent.textContent = '...';
                if(sBar) sBar.style.width = '5%';
                if(sPercent) sPercent.textContent = '...';
            }
        }
        
        if (!progressInterval) {
            progressInterval = setInterval(async () => {
                const status = await api('/api/fetch/status');
                updateProgressUI(status.scheduler);
                if (!status.scheduler?.is_fetching) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                    setTimeout(loadDashboard, 1000); // 抓取完刷新一次
                }
            }, 300);
        }
    } else {
        if(container) container.style.display = 'none';
        if(sContainer) sContainer.style.display = 'none';

        // 重置地球标记高亮
        if (typeof window.highlightGlobePoint === 'function') {
            window.highlightGlobePoint('', false);
        }

        const btn = document.getElementById('btnManualFetch');
        if (btn) { btn.disabled = false; btn.classList.remove('btn-loading'); }

        const btnScifi = document.getElementById('btnScifiFetch');
        if (btnScifi) { btnScifi.disabled = false; btnScifi.classList.remove('btn-loading'); }
    }
}

async function pollFetchProgress() {
    const status = await api('/api/fetch/status');
    updateProgressUI(status.scheduler);
}

function renderMediaChart(data) {
    const ctx = document.getElementById('mediaChart');
    if (mediaChart) mediaChart.destroy();

    const colors = [
        '#6366f1','#818cf8','#a5b4fc','#22c55e','#4ade80',
        '#f59e0b','#fbbf24','#ef4444','#f87171','#06b6d4',
        '#14b8a6','#8b5cf6','#d946ef','#ec4899','#f97316'
    ];

    mediaChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.media_group || '未分类'),
            datasets: [{
                data: data.map(d => d.cnt),
                backgroundColor: data.map((_, i) => colors[i % colors.length]),
                borderRadius: 6,
                borderSkipped: false,
                maxBarThickness: 40,  // 防止只有 1 个源时柱子太粗
            }]
        },
        options: {
            indexAxis: 'x',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(22, 24, 34, 0.9)',
                    titleColor: '#e4e6ee',
                    bodyColor: '#8b8fa3',
                    borderColor: '#2a2d3e',
                    borderWidth: 1,
                    padding: 10,
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#8b8fa3',
                        font: { size: 11 },
                        maxRotation: 45,
                        minRotation: 0,
                        autoSkip: true
                    },
                    grid: { display: false }
                },
                y: {
                    beginAtZero: true,
                    ticks: { color: '#8b8fa3', stepSize: 5 },
                    grid: { color: 'rgba(42,45,62,0.5)' }
                }
            }
        }
    });
}

// ── 调度器状态轮询 ────────────────────────────────────────
async function pollSchedulerStatus() {
    try {
        const data = await api('/api/fetch/status');
        const badge = document.getElementById('schedulerBadge');
        const dot = badge.querySelector('.pulse-dot');
        if (data.scheduler?.running) {
            dot.classList.remove('off');
            badge.querySelector('span:last-child').textContent = '调度器运行中';
        } else {
            dot.classList.add('off');
            badge.querySelector('span:last-child').textContent = '调度器已停止';
        }
    } catch {}
    setTimeout(pollSchedulerStatus, 30000);
}

// ── 源管理 ───────────────────────────────────────────────

// 折叠/展开状态
const categoryState = { rss: true, external: false };

// 挂载到 window 以便 HTML onclick 调用
window.toggleSourceCategory = function(type) {
    categoryState[type] = !categoryState[type];
    const container = document.getElementById(type === 'rss' ? 'rssFeedsContainer' : 'externalFeedsContainer');
    const icon = document.getElementById(type === 'rss' ? 'rssToggleIcon' : 'externalToggleIcon');

    if (container) {
        container.style.display = categoryState[type] ? 'block' : 'none';
    }
    if (icon) {
        icon.style.transform = categoryState[type] ? 'rotate(0deg)' : 'rotate(-90deg)';
    }
}

async function loadFeeds() {
    const data = await api('/api/feeds');
    const feeds = data.feeds || [];

    // 设置间隔 input 值及开启状态
    document.getElementById('inputInterval').value = data.settings?.interval_min || 60;
    const checkAutoFetch = document.getElementById('checkAutoFetch');
    if (checkAutoFetch) {
        checkAutoFetch.checked = !!data.settings?.auto_fetch;
    }

    // 更新计数
    document.getElementById('rssCount').textContent = `${feeds.length} 个RSS源`;

    // 渲染 RSS 源
    const container = document.getElementById('rssFeedsContainer');

    if (!feeds.length) {
        container.innerHTML = '<div class="card"><div class="empty-state"><div class="icon">📋</div>暂无配置源，点击上方"添加RSS源"按钮添加</div></div>';
    } else {
        // 按国家分组
        const grouped = {};
        feeds.forEach((f, i) => {
            const c = f.country || '未分类';
            if (!grouped[c]) grouped[c] = [];
            grouped[c].push({...f, _idx: i});
        });

        const countries = Object.keys(grouped).sort();

        // 紧凑的单容器布局
        container.innerHTML = `
            <div class="feeds-panel">
                <div class="feeds-header">
                    ${countries.map(country => `
                        <div class="country-section">
                            <div class="country-label">
                                <span class="flag">${getCountryFlag(country)}</span>
                                <span class="name">${escHtml(country)}</span>
                                <span class="count">${grouped[country].length}</span>
                            </div>
                            <div class="feed-items">
                                ${grouped[country].map(f => `
                                    <div class="feed-item" onclick="editFeed(${f._idx})">
                                        <span class="feed-platform">${escHtml(f.platform)}</span>
                                        <span class="feed-group">${escHtml(f.media_group || '-')}</span>
                                        <button class="feed-delete" onclick="event.stopPropagation(); deleteFeed(${f._idx})">×</button>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // 设置间隔按钮
    document.getElementById('btnSetInterval').onclick = async () => {
        const val = document.getElementById('inputInterval').value;
        const auto = document.getElementById('checkAutoFetch') ? document.getElementById('checkAutoFetch').checked : false;
        await api('/api/scheduler/interval', { method: 'POST', body: { interval: val, auto_fetch: auto } });
        alert('配置已保存并生效');
        pollSchedulerStatus();
    };

    // 加载外部搜索源
    loadExternalSources();

    // 应用折叠状态（初始状态已在 CSS 中设置）
    // RSS 默认展开，外部源默认折叠
}

// 加载并显示外部搜索源
async function loadExternalSources() {
    try {
        const data = await api('/api/external-sources');
        const sources = data.sources || [];
        const grouped = data.grouped || {};

        // 更新计数
        const enabledCount = sources.filter(s => s.enabled).length;
        document.getElementById('externalCount').textContent = `${sources.length} 个源 (${enabledCount} 个已启用)`;

        const container = document.getElementById('externalFeedsContainer');

        if (!sources.length) {
            container.innerHTML = '<div class="card"><div class="empty-state"><div class="icon">🌐</div>暂无外部搜索源配置</div></div>';
            return;
        }

        // 分类图标映射
        const categoryIcons = {
            '社交媒体': '💬',
            '搜索引擎': '🔍',
            '新闻聚合': '📰',
            'AI搜索引擎': '🤖',
            '其他': '📡'
        };

        let html = '';
        for (const [category, items] of Object.entries(grouped)) {
            html += `
                <div class="feeds-panel" style="margin-bottom: 12px;">
                    <div style="display:flex; align-items:center; gap:10px; padding: 12px 16px; background: rgba(99,102,241,0.08); border-bottom: 1px solid rgba(99,102,241,0.15);">
                        <span style="font-size: 16px;">${categoryIcons[category] || '📡'}</span>
                        <span style="font-size: 13px; font-weight:600; color: #6366f1;">${category}</span>
                        <span style="font-size: 10px; color: var(--text-secondary); margin-left: auto;">${items.length} 个源</span>
                    </div>
                    <div class="external-grid" style="padding: 12px;">
                        ${items.map(src => `
                            <div class="external-card">
                                <span class="icon">${src.enabled ? '✅' : '⚪'}</span>
                                <span class="name">${escHtml(src.name)}</span>
                                <label class="toggle toggle-wrap">
                                    <input type="checkbox" ${src.enabled ? 'checked' : ''} onchange="toggleExternalSource('${escHtml(src.name)}', this.checked)">
                                    <span class="toggle-slider"></span>
                                </label>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        container.innerHTML = html;
    } catch (e) {
        console.error('加载外部搜索源失败:', e);
    }
}

// 切换外部源启用状态 - 挂载到 window
window.toggleExternalSource = async function(name, enabled) {
    try {
        await api(`/api/external-sources/${encodeURIComponent(name)}`, {
            method: 'PUT',
            body: { enabled }
        });
        // 刷新计数
        loadExternalSources();
    } catch (e) {
        alert('更新失败: ' + e.message);
    }
}

// 编辑外部源 - 挂载到 window
window.editExternalSource = function(name) {
    // 获取当前源的数据
    api('/api/external-sources').then(data => {
        const source = data.sources.find(s => s.name === name);
        if (!source) {
            alert('未找到源: ' + name);
            return;
        }

        // 填充表单
        document.getElementById('externalSourceName').value = source.name;
        document.getElementById('externalSourceNameDisplay').value = source.name;
        document.getElementById('externalSourceType').value = source.type;
        document.getElementById('externalSourceCategory').value = source.category;
        document.getElementById('externalSourceDesc').value = source.description || '';
        document.getElementById('externalSourceApiKeyRef').value = source.config?.api_key_ref || '';
        document.getElementById('externalSourceMaxResults').value = source.config?.max_results || 20;
        document.getElementById('externalSourceEnabled').checked = source.enabled;

        // 显示弹窗
        document.getElementById('externalSourceModal').classList.add('active');
    });
}

// 保存外部源
window.saveExternalSource = async function() {
    const name = document.getElementById('externalSourceName').value;
    const description = document.getElementById('externalSourceDesc').value;
    const apiKeyRef = document.getElementById('externalSourceApiKeyRef').value;
    const maxResults = parseInt(document.getElementById('externalSourceMaxResults').value);
    const enabled = document.getElementById('externalSourceEnabled').checked;

    try {
        await api(`/api/external-sources/${encodeURIComponent(name)}`, {
            method: 'PUT',
            body: {
                enabled,
                description,
                config: {
                    api_key_ref: apiKeyRef,
                    max_results: maxResults
                }
            }
        });

        document.getElementById('externalSourceModal').classList.remove('active');
        loadExternalSources();
        alert('保存成功');
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

function openFeedModal(idx) {
    const modal = document.getElementById('modalOverlay');
    document.getElementById('feedEditIdx').value = idx;
    document.getElementById('modalTitle').textContent = idx >= 0 ? '编辑源' : '添加源';

    if (idx >= 0) {
        api('/api/feeds').then(data => {
            const f = data.feeds[idx];
            document.getElementById('feedUrl').value = f.url || '';
            document.getElementById('feedPlatform').value = f.platform || '';
            document.getElementById('feedMediaGroup').value = f.media_group || '';
            document.getElementById('feedCountry').value = f.country || '';
            document.getElementById('feedCity').value = f.city || '';
            document.getElementById('feedScrapeUrl').value = f.scrape_url || '';
            document.getElementById('feedTimeout').value = f.timeout || 30;
            document.getElementById('feedTimeOnly').checked = !!f.time_only;
            document.getElementById('feedFetchJina').checked = !!f.fetch_jina;
        });
    } else {
        document.getElementById('feedUrl').value = '';
        document.getElementById('feedPlatform').value = '';
        document.getElementById('feedMediaGroup').value = '';
        document.getElementById('feedCountry').value = '';
        document.getElementById('feedCity').value = '';
        document.getElementById('feedScrapeUrl').value = '';
        document.getElementById('feedTimeout').value = 30;
        document.getElementById('feedTimeOnly').checked = false;
        document.getElementById('feedFetchJina').checked = false;
    }
    modal.classList.add('active');
}

document.getElementById('modalClose').onclick = () =>
    document.getElementById('modalOverlay').classList.remove('active');
document.getElementById('modalCancel').onclick = () =>
    document.getElementById('modalOverlay').classList.remove('active');

document.getElementById('modalSave').onclick = async () => {
    const idx = parseInt(document.getElementById('feedEditIdx').value);
    const payload = {
        url: document.getElementById('feedUrl').value,
        platform: document.getElementById('feedPlatform').value,
        media_group: document.getElementById('feedMediaGroup').value,
        country: document.getElementById('feedCountry').value,
        city: document.getElementById('feedCity').value,
        scrape_url: document.getElementById('feedScrapeUrl').value,
        timeout: parseInt(document.getElementById('feedTimeout').value) || 30,
        time_only: document.getElementById('feedTimeOnly').checked,
        fetch_jina: document.getElementById('feedFetchJina').checked,
    };

    if (idx >= 0) {
        await api(`/api/feeds/${idx}`, { method: 'PUT', body: payload });
    } else {
        await api('/api/feeds', { method: 'POST', body: payload });
    }
    document.getElementById('modalOverlay').classList.remove('active');
    loadFeeds();
    if (typeof refreshGlobeFeeds === 'function') refreshGlobeFeeds();
};

async function editFeed(idx) { openFeedModal(idx); }

async function deleteFeed(idx) {
    if (!confirm('确认删除此源？')) return;
    await api(`/api/feeds/${idx}`, { method: 'DELETE' });
    loadFeeds();
    refreshGlobeFeeds();
}

async function toggleTimeOnly(idx, val) {
    await api(`/api/feeds/${idx}`, { method: 'PUT', body: { time_only: val } });
}

async function loadMediaGroups() {
    const data = await api('/api/media-groups');
    const sel = document.getElementById('searchMedia');
    const current = sel.value;
    sel.innerHTML = '<option value="">全部媒体</option>' +
        (data.groups || []).map(g => `<option value="${escHtml(g)}">${escHtml(g)}</option>`).join('');
    sel.value = current;
}

async function loadCountries() {
    try {
        const data = await api('/api/countries');
        const sel = document.getElementById('searchCountry');
        const current = sel.value;
        sel.innerHTML = '<option value="">全部国家/地区</option>' +
            data.countries.map(c => `<option value="${escHtml(c)}" ${c===current?'selected':''}>${escHtml(c)}</option>`).join('');
    } catch {}
}

async function loadPlatforms() {
    const data = await api('/api/feeds');
    const sel = document.getElementById('searchPlatform');
    const current = sel.value;
    const platforms = Array.from(new Set((data.feeds || []).map(f => f.platform).filter(Boolean))).sort();
    sel.innerHTML = '<option value="">全部数据源</option>' +
        platforms.map(p => `<option value="${escHtml(p)}">${escHtml(p)}</option>`).join('');
    sel.value = current;
}

window.gotoFiltered = function(type) {
    document.querySelector('.nav-link[data-page="explorer"]').click();
    document.getElementById('searchKeyword').value = '';
    document.getElementById('searchMedia').value = '';
    document.getElementById('searchPlatform').value = '';
    document.getElementById('searchHours').value = '';

    if (type === 'total') {
        document.getElementById('searchPeriod').value = '';
    } else if (type === 'today') {
        document.getElementById('searchPeriod').value = 'day';
    } else if (type === 'week') {
        document.getElementById('searchPeriod').value = 'week';
    } else if (type === 'last_fetch') {
        document.getElementById('searchPeriod').value = '';
        document.getElementById('searchHours').value = '2'; // 代表近2小时
    }
    
    searchPage = 1;
    doSearch();
};

document.getElementById('btnSearch').addEventListener('click', () => { searchPage = 1; doSearch(); });
document.getElementById('btnDeepResearch').addEventListener('click', () => { doDeepResearch(); });
document.getElementById('searchKeyword').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') { 
        if (e.ctrlKey) doDeepResearch();
        else { searchPage = 1; doSearch(); }
    }
});

async function doDeepResearch() {
    const keyword = document.getElementById('searchKeyword').value;
    if (!keyword) {
        alert('请先输入研究课题关键词');
        document.getElementById('searchKeyword').focus();
        return;
    }

    const btn = document.getElementById('btnDeepResearch');
    const oldText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span>⌛</span> 正在深度研究中...';

    // 显示一个临时的加载提示在结果区
    const list = document.getElementById('articlesList');
    list.innerHTML = `
        <div class="empty-state" style="padding: 60px;">
            <div class="loading" style="width:40px; height:40px; margin-bottom:15px; border-top-color:var(--accent);"></div>
            <div style="font-weight:600; color:var(--accent-2); font-size:18px;">正在调动 AI 搜索引擎进行全球扫描...</div>
            <div style="font-size:13px; margin-top:12px; opacity:0.8; max-width:400px; line-height:1.6;">
                正在通过 Tavily, Perplexity 等引擎获取实时资讯，<br>
                并调动智库专家构建事件时间线。请耐心等待约 30 秒。
            </div>
        </div>
    `;

    try {
        const data = await api('/api/v2/research', {
            method: 'POST',
            body: { 
                keyword: keyword,
                mode: 'deep_research'
            }
        });

        if (data.error) throw new Error(data.error);

        // 研究成功后：
        // 1. 获取新生成的时间线 ID
        const timelineId = data.timeline?.id;
        
        if (timelineId) {
            // 2. 切换到时间线页面
            switchPage('timeline');
            // 3. 加载并显示该时间线
            currentTimelineId = timelineId;
            await loadTimelineList(); // 刷新列表
            await showTimelineDetail(timelineId); // 显示详情
        } else {
            console.error('API 返回成功但缺少 timeline ID', data);
            alert('深度研究完成，但无法定位生成的时间线。请前往“事件时间线”查看。');
        }

    } catch (e) {
        console.error('Deep Research Failed:', e);
        list.innerHTML = `<div class="status-tag error" style="margin:20px;">研究任务失败: ${e.message}</div>`;
        alert('研究任务失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = oldText;
    }
}

async function doSearch() {
    const keyword = document.getElementById('searchKeyword').value;
    const media = document.getElementById('searchMedia').value;
    const platform = document.getElementById('searchPlatform').value;
    const country = document.getElementById('searchCountry').value;
    const period = document.getElementById('searchPeriod').value;
    const hours = document.getElementById('searchHours').value;
    const limit = 30;

    let params = `limit=${limit}&page=${searchPage}`;
    if (keyword) params += `&keyword=${encodeURIComponent(keyword)}`;
    if (media) params += `&media=${encodeURIComponent(media)}`;
    if (platform) params += `&platform=${encodeURIComponent(platform)}`;
    if (country) params += `&country=${encodeURIComponent(country)}`;
    if (period) params += `&period=${period}`;
    if (hours) params += `&hours=${hours}`;

    const data = await api(`/api/articles?${params}`);
    const list = document.getElementById('articlesList');
    const items = data.items || [];

    document.getElementById('resultCount').textContent =
        `共 ${data.total || 0} 条结果，当前第 ${data.page || 1} 页`;

    // 存入全局变量以便模态框读取
    window.currentArticles = items;

    if (!items.length) {
        list.innerHTML = '<div class="empty-state"><div class="icon">🔍</div>没有找到匹配的新闻</div>';
    } else {
        list.innerHTML = items.map((i, idx) => {
            const thumbUrl = getMediaThumbUrl(i.image, i.video);
            const hasVideo = i.video && i.video.length > 0;
            const hasImage = i.image && i.image.length > 0;
            const hasMedia = hasVideo || hasImage;

            return `
            <div class="article-item" data-id="${i.id}">
                <div class="article-check-wrapper">
                    <input type="checkbox" class="article-check" ${selectedArticles && selectedArticles.has(i.id) ? 'checked' : ''}
                           onchange="toggleArticleSelection(${i.id}, '${escHtml(i.title)}', '${escHtml(i.platform)}', this.checked)">
                </div>
                ${hasMedia ? `
                    <div class="article-thumb-wrapper">
                        ${hasVideo ? `<a href="${escHtml(i.video)}" target="_blank" title="点击播放视频">` : `<a href="${escHtml(i.url)}" target="_blank">`}
                        <img class="article-thumb" src="${escHtml(thumbUrl)}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 120 80%22><rect fill=%22%231c1f2e%22 width=%22120%22 height=%2280%22/><text x=%2260%22 y=%2245%22 text-anchor=%22middle%22 fill=%22%23666%22 font-size=%2216%22>📰</text></svg>'" />
                        ${hasVideo ? '<div class="video-play-icon">▶</div>' : ''}
                        </a>
                    </div>
                ` : ''}
                <div class="article-body">
                    <div class="article-title"><a href="${escHtml(i.url)}" target="_blank">${escHtml(i.title)}</a></div>
                    <div class="article-meta">
                        <span class="country-badge" style="padding: 1px 6px; font-size: 11px; margin-right: 8px; font-weight: 500;">
                            ${getCountryFlag(i.country)} ${escHtml(i.country || '国际')}
                        </span>
                        <span>📰 ${escHtml(i.platform || '')}</span>
                        <span>🕐 ${fmtTime(i.published)}</span>
                        <span>📁 ${escHtml(i.media_group || '')}</span>
                        ${hasVideo ? '<span style="color:#ef4444;">🎬 视频</span>' : ''}
                    </div>
                    <div class="article-summary">
                        ${escHtml(i.summary || '')}
                        ${i.content ? `<div style="margin-top:8px"><button class="btn btn-sm btn-secondary" onclick="showContent(${idx})">📖 阅读正文</button></div>` : ''}
                    </div>
                </div>
            </div>
        `}).join('');
    }

window.showContent = function(idx) {
    const item = window.currentArticles[idx];
    if (!item || !item.content) return;
    document.getElementById('contentTitle').textContent = item.title;
    document.getElementById('contentMarkdown').textContent = item.content;
    document.getElementById('contentModal').classList.add('active');
};

    // 分页
    const total = data.total || 0;
    const totalPages = Math.ceil(total / limit);
    const pag = document.getElementById('pagination');
    if (totalPages <= 1) { pag.innerHTML = ''; return; }

    let html = '';
    
    // 上一页
    html += `<button class="page-btn nav-btn" onclick="gotoPage(${searchPage - 1})" ${searchPage === 1 ? 'disabled' : ''}>上一页</button>`;

    const start = Math.max(1, searchPage - 2);
    const end = Math.min(totalPages, searchPage + 2);
    
    if (start > 1) {
        html += `<button class="page-btn" onclick="gotoPage(1)">1</button>`;
        if (start > 2) html += `<span class="page-ellipsis">...</span>`;
    }
    
    for (let p = start; p <= end; p++) {
        html += `<button class="page-btn ${p === searchPage ? 'active' : ''}" onclick="gotoPage(${p})">${p}</button>`;
    }
    
    if (end < totalPages) {
        if (end < totalPages - 1) html += `<span class="page-ellipsis">...</span>`;
        html += `<button class="page-btn" onclick="gotoPage(${totalPages})">${totalPages}</button>`;
    }

    // 下一页
    html += `<button class="page-btn nav-btn" onclick="gotoPage(${searchPage + 1})" ${searchPage === totalPages ? 'disabled' : ''}>下一页</button>`;
    
    pag.innerHTML = html;
}

function gotoPage(p) {
    searchPage = p;
    doSearch();
    document.getElementById('page-explorer').scrollTo({ top: 0, behavior: 'smooth' });
}

// ── 抓取日志 ─────────────────────────────────────────────
async function loadLogs() {
    const [status, logsData] = await Promise.all([
        api('/api/fetch/status'),
        api('/api/fetch/logs?limit=50'),
    ]);

    const s = status.scheduler || {};
    document.getElementById('logSchedulerStatus').innerHTML = s.running
        ? '<span class="status-tag running">● 运行中</span>'
        : '<span class="status-tag error">● 已停止</span>';
    document.getElementById('logInterval').textContent = `${s.interval_min || '—'} 分钟`;
    document.getElementById('logLastRun').textContent = fmtTime(s.last_run);
    
    const nextReasonStr = s.next_run_reason || '';
    document.getElementById('logNextRun').innerHTML = `
        ${fmtTime(s.next_run)} 
        ${nextReasonStr ? `<span class="badge-reason ${nextReasonStr.includes('补位')?'badge-padding':''}">${nextReasonStr}</span>` : ''}
    `;
    document.getElementById('logFetching').innerHTML = s.is_fetching
        ? '<span class="status-tag running"><span class="loading"></span> 抓取中</span>'
        : '<span class="status-tag done">空闲</span>';

    const tbody = document.querySelector('#logsTable tbody');
    const logs = logsData.logs || [];
    if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty-state"><div class="icon">📭</div>暂无日志</div></td></tr>';
        return;
    }

    tbody.innerHTML = logs.map(l => `
        <tr>
            <td>${fmtTime(l.started_at)}</td>
            <td><span class="status-tag ${l.status}">${l.status}</span></td>
            <td>${fmtDuration(l.duration_sec)}</td>
            <td>${l.feeds_total || '—'}</td>
            <td style="font-weight:700;color:var(--accent-2)">+${l.articles_new || 0}</td>
            <td>${l.articles_total || '—'}</td>
            <td><button class="btn btn-sm btn-secondary" onclick='showLogDetail(${JSON.stringify(JSON.stringify(l))})'>详情</button></td>
        </tr>
    `).join('');
}

function showLogDetail(jsonStr) {
    const detail = JSON.parse(jsonStr);
    document.getElementById('detailTitle').textContent = `抓取详情 — ${fmtTime(detail.started_at)}`;
    document.getElementById('detailContent').textContent = JSON.stringify(detail, null, 2);
    document.getElementById('detailModal').classList.add('active');
}

document.getElementById('btnRefreshLogs').onclick = loadLogs;

// ── 数据维护 ─────────────────────────────────────────────
async function loadDbStats() {
    const stats = await api('/api/stats');
    document.getElementById('dbTotal').textContent = stats.total?.toLocaleString() || '0';
    document.getElementById('dbEarliest').textContent = fmtTime(stats.earliest);
    document.getElementById('dbLatest').textContent = fmtTime(stats.latest);

    const bd = document.getElementById('mediaBreakdown');
    const media = stats.by_media || [];
    if (!media.length) {
        bd.innerHTML = '<div class="empty-state">暂无数据</div>';
    } else {
        bd.innerHTML = media.map(m => `
            <div class="media-row">
                <span class="name">${escHtml(m.media_group || '未分类')}</span>
                <span class="cnt">${m.cnt}</span>
            </div>
        `).join('');
    }

    document.getElementById('btnCleanup').onclick = async () => {
        const date = document.getElementById('cleanupDate').value;
        if (!date) { alert('请选择日期'); return; }
        if (!confirm(`确认删除 ${date} 之前的所有数据？此操作不可撤销！`)) return;
        const result = await api(`/api/articles/cleanup?before=${date}T00:00:00+08:00`, { method: 'DELETE' });
        document.getElementById('cleanupResult').textContent =
            `已删除 ${result.deleted || 0} 条记录`;
        loadDbStats();
    };

    const btnClearAll = document.getElementById('btnClearAll');
    if (btnClearAll) {
        btnClearAll.onclick = async () => {
            if (!confirm(`🚨 警告：此操作将永久清空数据库中所有历史文章和日志，且无法恢复！\n\n清空后将立即触发一次全新抓取任务，确定要继续吗？`)) return;
            try {
                // Disable the button to prevent multiple clicks
                btnClearAll.disabled = true;
                btnClearAll.textContent = '正在清空...';
                
                await api(`/api/articles/clear-all`, { method: 'POST' });
                alert('数据库与缓存已清空！将立即开始全量重新抓取...');
                // Trigger full sync
                await api('/api/fetch', { method: 'POST', body: { full: true } }); 
                
                loadDbStats();
                pollFetchProgress();
                
                // Switch to dashboard or scifi page optionally, or stay and watch progression
                document.querySelector('.nav-link[data-page="dashboard"]').click();
            } catch (e) {
                alert('清空失败: ' + e);
            } finally {
                btnClearAll.disabled = false;
                btnClearAll.innerHTML = '🚨 一键清空全库内容并重新抓取';
            }
        };
    }
}

// ── 多选与情报工作台逻辑 ───────────────────────────────
window.toggleArticleSelection = function(id, title, platform, checked) {
    if (checked) {
        selectedArticles.set(id, { title, platform });
    } else {
        selectedArticles.delete(id);
    }
    updateFloatingBar();
};

function updateFloatingBar() {
    const bar = document.getElementById('floatingActionBar');
    if (!bar) return;
    const count = document.getElementById('selectedCount');
    if (selectedArticles.size > 0) {
        bar.style.display = 'flex';
        if (count) count.textContent = selectedArticles.size;
    } else {
        bar.style.display = 'none';
    }
}

function renderWorkbench() {
    const list = document.getElementById('workbenchList');
    if (!list) return;
    
    if (selectedArticles.size === 0) {
        list.innerHTML = '<div class="empty-state" style="padding:10px; font-size:13px; color:var(--text-secondary);">尚未选择素材，请前往“新闻查询”勾选</div>';
        return;
    }
    
    list.innerHTML = Array.from(selectedArticles.entries()).map(([id, info]) => `
        <div class="he-details-item" style="border-bottom:1px solid rgba(255,255,255,0.05); padding:8px 0;">
            <div style="display:flex; align-items:flex-start; gap:8px;">
                <button style="color:var(--error); background:none; border:none; cursor:pointer;" 
                        onclick="removeFromWorkbench('${id}')">✕</button>
                <div style="font-size:12px; font-weight:600; line-height:1.4;">
                    [${escHtml(info.platform)}] ${escHtml(info.title)}
                </div>
            </div>
        </div>
    `).join('');
}

document.getElementById('btnCancelSelect').onclick = () => {
    selectedArticles.clear();
    updateFloatingBar();
    if (currentPage === 'explorer') doSearch();
};

document.getElementById('btnSubmitToIntel').onclick = () => {
    switchPage('intelligence');
};

// ── 新闻研究中心 (原情报中心) ──────────────────────────
async function loadIntelligence() {
    const container = document.getElementById('hotEventsContainer');
    const reportContainer = document.getElementById('reportContainer');
    
    // 强制重置页面状态：清空旧结果并隐藏报告区域，确保必须手动点击启动分析
    if (reportContainer) reportContainer.style.display = 'none';
    
    container.innerHTML = `
        <div class="empty-state" style="padding: 40px 20px;">
            <div class="icon">🔍</div>
            <div style="font-weight:600; font-size:15px; margin-bottom:10px;">准备开始研究分析</div>
            <p style="font-size:13px; color:var(--text-secondary); line-height:1.6;">
                由于 AI 全文处理的高性能消耗，该中心已切换至手动触发模式。<br>
                请在上方选择 <b>AI 研究员模型</b> 并点击“<b>启动热点识别</b>”。
            </p>
        </div>
    `;

    // 初始化时间选择器 (默认当天 00:00 - 23:59)
    try {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const dayStr = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`;
        const startInput = document.getElementById('intelStart');
        const endInput = document.getElementById('intelEnd');
        if (startInput && !startInput.value) startInput.value = `${dayStr}T00:00`;
        if (endInput && !endInput.value) endInput.value = `${dayStr}T23:59`;

        const periodSel = document.getElementById('intelPeriod');
        const customDiv = document.getElementById('intelCustomRange');
        if (periodSel && customDiv) {
            periodSel.onchange = () => {
                customDiv.style.display = (periodSel.value === 'custom') ? 'flex' : 'none';
            };
            // 初始状态同步
            customDiv.style.display = (periodSel.value === 'custom') ? 'flex' : 'none';
        }
    } catch(e) { console.error("Intel Date Init Error:", e); }

    renderWorkbench();
    updateFloatingBar();
    
    // 绑定交互按钮
    document.getElementById('btnStartAnalyze').onclick = loadHotEvents;
    document.getElementById('btnWriteReport').onclick = handleWriteReport;
    document.getElementById('btnClearWorkbench').onclick = () => {
        selectedArticles.clear();
        document.querySelectorAll('.he-item-check').forEach(cb => cb.checked = false);
        renderWorkbench();
        updateFloatingBar();
    };
}

async function loadHotEvents() {
    const period = document.getElementById('intelPeriod').value;
    const provider = document.getElementById('intelProvider').value;
    const container = document.getElementById('hotEventsContainer');

    // 默认使用快速本地算法（毫秒级响应），只有用户明确选择 LLM 时才用慢模式
    const useFast = provider !== 'deepseek-llm' && provider !== 'volcengine-llm';

    if (useFast) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="loading" style="width:40px; height:40px; margin-bottom:15px; border-top-color:var(--accent);"></div>
                <div style="font-weight:600; color:var(--accent-2);">正在分析热点事件...</div>
                <div style="font-size:12px; margin-top:8px; opacity:0.7;">(本地智能算法，秒级响应)</div>
            </div>
        `;
    } else {
        container.innerHTML = `
            <div class="empty-state">
                <div class="loading" style="width:40px; height:40px; margin-bottom:15px; border-top-color:var(--accent);"></div>
                <div style="font-weight:600; color:var(--accent-2);">正在调动 ${provider === 'volcengine' ? '豆包 (ARK)' : 'DeepSeek'} 深度分析...</div>
                <div style="font-size:12px; margin-top:8px; opacity:0.7;">(LLM 聚类分析，耗时较长)</div>
            </div>
        `;
    }

    try {
        let url = `/api/intelligence/hot?period=${period}&fast=true`;
        
        // 如果是自定义时间段，构造 start/end 参数
        if (period === 'custom') {
            const startVal = document.getElementById('intelStart').value;
            const endVal = document.getElementById('intelEnd').value;
            if (!startVal) {
                alert('请选择起始时间');
                return;
            }
            // 转换为本地 ISO 格式 (通常用于后端解析)
            const toISO = (val) => val ? new Date(val).toISOString() : "";
            url = `/api/intelligence/hot?start=${toISO(startVal)}&end=${toISO(endVal)}&fast=true`;
        }

        // 使用快速模式（fast=true），秒级响应
        const data = await api(url);
        if (data.error) throw new Error(data.error);
        const events = data.events || [];
        
        if (!events.length) {
            container.innerHTML = '<div class="empty-state">暂无热点事件聚合</div>';
            return;
        }
        
        container.innerHTML = events.map((e, idx) => {
            const itemsHtml = e.items.slice(0, 8).map(i => {
                const isChecked = selectedArticles.has(String(i.id));
                const itemUrl = i.url || '';
                const titleText = `[${escHtml(i.platform)}] ${escHtml(i.title)}`;
                return `
                    <div class="he-details-item" title="${escHtml(i.title)}">
                        <input type="checkbox" class="he-item-check"
                               ${isChecked ? 'checked' : ''}
                               onclick="event.stopPropagation(); toggleHotItem('${idx}', '${i.id}', this.checked)">
                        <span class="he-item-title">${itemUrl ? `<a href="${escHtml(itemUrl)}" target="_blank" style="color:inherit;text-decoration:none;" onclick="event.stopPropagation();">${titleText}</a>` : titleText}</span>
                    </div>
                `;
            }).join('');

            return `
                <div class="hot-event-card" id="he-card-${idx}" onclick="selectWholeHotEvent(${idx})">
                    <div class="he-header" style="display:flex; justify-content:space-between; align-items:flex-start;">
                        <div class="he-score">🔥 指数 ${Math.round(e.score)}</div>
                        ${e.is_china_related ? '<span class="badge-reason badge-padding" style="margin:0;">中国相关</span>' : ''}
                    </div>
                    <div class="he-title">${escHtml(e.title)}</div>
                    <div class="he-meta">
                        <span>📰 ${e.platforms.length} 媒体 / ${e.count} 篇</span>
                    </div>
                    <div class="he-sources" style="font-size:11px; color:var(--text-secondary); margin-top:6px; line-height:1.4;">
                        ${escHtml((e.all_platforms || e.platforms).slice(0, 6).join('、'))}
                    </div>
                    <div class="he-details-list">
                        ${itemsHtml}
                        ${e.count > 8 ? `<div style="font-size:10px; color:var(--accent-2); padding-left:22px;">... 更多共 ${e.count} 篇</div>` : ''}
                    </div>
                    <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:4px;">
                        ${e.tags.slice(0, 3).map(t => `<span class="badge" style="font-size:9px;">${escHtml(t)}</span>`).join('')}
                    </div>
                </div>
            `;
        }).join('');
        
        window.currentHotEvents = events;
        
    } catch (e) {
        container.innerHTML = `<div class="status-tag error">加载失败: ${e.message}</div>`;
    }
}

window.handleWriteReport = async function() {
    if (selectedArticles.size === 0) {
        alert('请先选择至少一篇新闻作为研究素材。');
        return;
    }

    // 关键修复：过滤掉无效 ID (null/undefined/'null'/'undefined')
    const rawIds = Array.from(selectedArticles.keys());
    const ids = rawIds.filter(id => {
        const sid = String(id);
        return sid !== 'null' && sid !== 'undefined' && id != null && id !== undefined;
    });

    if (ids.length === 0) {
        alert('所选文章均缺少有效 ID。请刷新热点分析后重新选择。');
        return;
    }

    if (ids.length < rawIds.length) {
        console.warn(`[WARN] 已过滤 ${rawIds.length - ids.length} 个无效 ID`);
    }
    
    const btn = document.getElementById('btnWriteReport');
    const container = document.getElementById('reportContainer');
    const prompt = document.getElementById('intelCustomPrompt').value;
    const provider = document.getElementById('intelProvider').value;
    
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = '⌛ 正在获取全文并深度分析中...';
    container.style.display = 'block';
    container.innerHTML = '<div class="empty-state"><div class="loading"></div>正在获取原文并调动智库专家进行分析...</div>';
    
    try {
        // 发送已过滤的有效 IDs 给后端处理
        const data = await api('/api/intelligence/write', {
            method: 'POST',
            body: { ids, prompt, provider }
        });
        
        // 缓存报告以便下载
        window.lastGeneratedReport = data.report;
        
        container.innerHTML = `
            <div style="border-left: 4px solid var(--accent); padding-left: 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: flex-start; gap: 20px;">
                <div>
                    <h2 style="margin-top:0; font-size: 20px; margin-bottom: 8px;">新闻事件深度分析报告</h2>
                    <div style="font-size:12px; color:var(--text-secondary); line-height: 1.5;">
                        生成时间: ${new Date().toLocaleString()} | 
                        研究素材: ${selectedArticles.size} 篇 | 
                        分析专家: ${provider === 'volcengine-llm' ? '豆包 (ARK)' : 'DeepSeek'}
                    </div>
                </div>
                <button class="btn btn-secondary btn-sm" onclick="downloadReport()" style="flex-shrink:0; white-space:nowrap;">
                    📥 下载报告 (.md)
                </button>
            </div>
            <div class="markdown-body">
                ${formatReportMarkdown(data.report)}
            </div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="status-tag error">分析生成失败: ${e.message}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = oldText;
    }
};

/**
 * 将当前生成的报告下载为本地 Markdown 文件
 */
window.downloadReport = function() {
    const content = window.lastGeneratedReport;
    if (!content) {
        alert('没有可下载的报告内容');
        return;
    }

    try {
        const now = new Date();
        const timestamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`;
        
        // 尝试从报告第一行提取标题
        let filename = `情报分析报告_${timestamp}.md`;
        const firstLine = content.split('\n')[0];
        if (firstLine && firstLine.startsWith('# ')) {
            const cleanTitle = firstLine.replace('# ', '').trim().substring(0, 30).replace(/[\\/:*?"<>|]/g, '_');
            if (cleanTitle) filename = `${cleanTitle}_${timestamp}.md`;
        }

        const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    } catch (e) {
        console.error('下载失败:', e);
        alert('文件下载失败: ' + e.message);
    }
};

function formatReportMarkdown(text) {
    if (!text) return '';

    let html = text;

    // 1. 处理 GitHub 风格 Alerts: > [!NOTE], > [!WARNING], > [!IMPORTANT]
    // 匹配方案: 匹配以 "> [!" 开头的行组
    html = html.replace(/^> \[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*\n?((?:^> .*\n?)*)/gim, (match, type, content) => {
        const iconMap = { NOTE: 'ℹ️', TIP: '💡', IMPORTANT: '📢', WARNING: '⚠️', CAUTION: '🚫' };
        const cleanContent = content.replace(/^> /gm, '').trim();
        return `<div class="markdown-alert markdown-alert-${type.toLowerCase()}">
            <div class="markdown-alert-title">${iconMap[type] || ''} ${type}</div>
            <div>${cleanContent}</div>
        </div>`;
    });

    // 2. 处理标准块引用 (Blockquotes)
    html = html.replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>');

    // 3. 处理标题 (Headers) - 从高到低以防冲突
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
    html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
    html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');

    // 4. 处理列表 (Unordered Lists)
    // 简单的单行转换，暂不支持嵌套
    html = html.replace(/^\s*[-*+]\s+(.*$)/gim, '<li>$1</li>');
    // 包装 <li> 标签到 <ul> 中 (粗略实现：连续的 <li> 包裹)
    html = html.replace(/(<li>.*<\/li>)+/gim, '<ul>$&</ul>');

    // 5. 处理粗体和斜体
    html = html.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

    // 6. 处理换行 (Paragraphs)
    // 非标签包围的行转换为 <p>
    const lines = html.split('\n');
    const processedLines = lines.map(line => {
        const trimmed = line.trim();
        if (!trimmed) return '<br/>';
        // 如果行已经包含 HTML 块标签，则不包裹 p
        if (/^<(h\d|div|blockquote|ul|li|hr)/i.test(trimmed)) return trimmed;
        return `<p>${trimmed}</p>`;
    });

    return processedLines.join('\n');
}

// ── 时间线页面 ─────────────────────────────────────────────

let currentTimelineId = null;

async function loadTimelinePage() {
    await loadTimelineList();
    updateTimelineButtons();
}

function updateTimelineButtons() {
    const btn = document.getElementById('btnCreateTimeline');
    if (selectedArticles.size === 0) {
        btn.disabled = true;
        btn.textContent = '📜 请先选择文章';
    } else {
        btn.disabled = false;
        btn.textContent = `📜 从 ${selectedArticles.size} 篇文章创建时间线`;
    }
}

async function loadTimelineList() {
    const container = document.getElementById('timelineList');
    try {
        const data = await api('/api/timeline/list');
        const timelines = data.timelines || [];

        if (timelines.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="padding: 20px;">
                    <div class="icon">📜</div>
                    暂无时间线，请先选择相关新闻创建
                </div>
            `;
            return;
        }

        container.innerHTML = timelines.map(tl => `
            <div class="timeline-item ${tl.id === currentTimelineId ? 'active' : ''}"
                 onclick="showTimelineDetail(${tl.id})">
                <div class="tl-item-title">${escHtml(tl.title)}</div>
                <div class="tl-item-meta">
                    <span>${tl.event_count || 0} 个事件</span>
                    <span>${fmtTime(tl.updated_at)}</span>
                </div>
                <div class="tl-item-status status-tag ${tl.status === 'active' ? 'success' : tl.status === 'archived' ? '' : 'warning'}">${tl.status}</div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<div class="status-tag error">加载失败: ${e.message}</div>`;
    }
}

async function showTimelineDetail(timelineId) {
    currentTimelineId = timelineId;
    document.getElementById('timelineEmpty').style.display = 'none';
    document.getElementById('timelineDetail').style.display = 'block';

    // 高亮列表项
    document.querySelectorAll('.timeline-item').forEach(el => el.classList.remove('active'));
    document.querySelector(`.timeline-item[onclick="showTimelineDetail(${timelineId})"]`)?.classList.add('active');

    try {
        const tl = await api(`/api/timeline/${timelineId}`);

        document.getElementById('tlTitle').textContent = tl.title;
        document.getElementById('tlKeywords').textContent = (tl.keywords || []).join(', ');
        document.getElementById('tlCreatedAt').textContent = `创建: ${fmtTime(tl.created_at)}`;
        document.getElementById('tlStatus').textContent = tl.status;
        document.getElementById('tlSummary').textContent = tl.summary || '';

        renderTimelineAxis(tl.events || []);
    } catch (e) {
        document.getElementById('timelineAxis').innerHTML = `<div class="status-tag error">加载失败: ${e.message}</div>`;
    }
}

function renderTimelineAxis(events) {
    const container = document.getElementById('timelineAxis');

    if (!events || events.length === 0) {
        container.innerHTML = `<div class="empty-state" style="padding: 20px;">暂无事件节点</div>`;
        return;
    }

    // 按时间排序（从早到晚）
    const sorted = events.sort((a, b) => new Date(a.event_time) - new Date(b.event_time));

    container.innerHTML = sorted.map(e => {
        const dt = new Date(e.event_time);
        const dateDisplay = `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
        const timeDisplay = `${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
        const isKey = e.is_key_event;

        return `
            <div class="timeline-event ${isKey ? 'key-event' : ''}">
                <div class="event-marker">
                    <span class="event-dot ${isKey ? 'key-dot' : ''}"></span>
                    <span class="event-date">${dateDisplay}</span>
                    <span class="event-time">${timeDisplay}</span>
                </div>
                <div class="event-content">
                    <div class="event-title">${escHtml(e.title)}</div>
                    <div class="event-desc">${escHtml(e.description || '')}</div>
                    <div class="event-source">
                        ${e.source_platform ? `<span class="source-tag">[${escHtml(e.source_platform)}]</span>` : ''}
                        ${e.source_url ? `<a href="${e.source_url}" target="_blank" class="source-link">原文</a>` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// 创建时间线按钮
document.getElementById('btnCreateTimeline')?.addEventListener('click', async () => {
    if (selectedArticles.size === 0) {
        alert('请先在"新闻查询"中选择相关文章');
        return;
    }

    const ids = Array.from(selectedArticles.keys());
    const provider = document.getElementById('intelProvider')?.value || 'volcengine';
    const btn = document.getElementById('btnCreateTimeline');

    btn.disabled = true;
    btn.textContent = '⌛ 正在生成时间线...';

    try {
        const data = await api('/api/timeline/create', {
            method: 'POST',
            body: { article_ids: ids, provider }
        });

        if (data.timeline_id) {
            alert(`时间线创建成功！共 ${data.timeline?.events?.length || 0} 个事件节点`);
            // 清空选择
            selectedArticles.clear();
            renderWorkbench();
            updateFloatingBar();
            // 切换到时间线页面并显示详情
            switchPage('timeline');
            await showTimelineDetail(data.timeline_id);
        }
    } catch (e) {
        alert('创建失败: ' + e.message);
    } finally {
        btn.disabled = false;
        updateTimelineButtons();
    }
});

// 检查更新按钮
document.getElementById('btnTrackUpdate')?.addEventListener('click', async () => {
    if (!currentTimelineId) return;

    const btn = document.getElementById('btnTrackUpdate');
    btn.disabled = true;
    btn.textContent = '⌛ 正在检查...';

    try {
        const data = await api(`/api/timeline/${currentTimelineId}/track`, { method: 'POST' });
        if (data.updated) {
            alert(`发现 ${data.new_count} 条新进展，已更新时间线`);
            await showTimelineDetail(currentTimelineId);
            await loadTimelineList();
        } else {
            alert(data.message || '暂无新的进展');
        }
    } catch (e) {
        alert('跟踪失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '🔄 检查最新进展';
    }
});

// 导出时间线
document.getElementById('btnExportTimeline')?.addEventListener('click', async () => {
    if (!currentTimelineId) return;

    try {
        const data = await api(`/api/timeline/${currentTimelineId}/export`);
        const md = data.markdown;

        const now = new Date();
        const timestamp = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}`;

        const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `事件时间线_${timestamp}.md`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    } catch (e) {
        alert('导出失败: ' + e.message);
    }
});

// 归档时间线
document.getElementById('btnArchiveTimeline')?.addEventListener('click', async () => {
    if (!currentTimelineId) return;
    if (!confirm('确认归档此时间线？')) return;

    try {
        await api(`/api/timeline/${currentTimelineId}/status`, {
            method: 'PUT',
            body: { status: 'archived' }
        });
        alert('已归档');
        await loadTimelineList();
        await showTimelineDetail(currentTimelineId);
    } catch (e) {
        alert('归档失败: ' + e.message);
    }
});

// 删除时间线
document.getElementById('btnDeleteTimeline')?.addEventListener('click', async () => {
    if (!currentTimelineId) return;
    if (!confirm('确认删除此时间线？此操作不可恢复！')) return;

    try {
        await api(`/api/timeline/${currentTimelineId}`, { method: 'DELETE' });
        alert('已删除');
        currentTimelineId = null;
        document.getElementById('timelineDetail').style.display = 'none';
        document.getElementById('timelineEmpty').style.display = 'block';
        await loadTimelineList();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
});

function escHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

