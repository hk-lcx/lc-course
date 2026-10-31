/**
 * 静态前端（已编译的 Next.js 导出）的登录门禁与账号区。
 *
 * 仓库里没有前端源码，无法重新构建，所以用这个脚本在运行时补齐：
 *   1. 未登录时跳转到 /login.html；
 *   2. 拦截 fetch，给所有 /api/v1/ 请求自动带 Authorization: Bearer <token>；
 *   3. 收到 401 时清除本地凭证并回到登录页；
 *   4. 在页面右上角显示「当前用户：xxx」和「退出登录」按钮。
 */
(function () {
  'use strict';

  var TOKEN_KEY = 'auth_token';
  var USER_KEY = 'auth_user';
  var CUISINE_KEY = 'cuisine';
  var CUISINE_PENDING_KEY = 'cuisine_pending';
  var LOGIN_PAGE = '/login.html';

  var CUISINES = [
    { name: '川菜', desc: '麻辣鲜香' },
    { name: '鲁菜', desc: '咸鲜醇厚' },
    { name: '粤菜', desc: '清鲜本味' },
    { name: '苏菜', desc: '精细雅致' },
    { name: '浙菜', desc: '清爽细嫩' },
    { name: '闽菜', desc: '鲜香清醇' },
    { name: '湘菜', desc: '香辣浓郁' },
    { name: '徽菜', desc: '重火功' }
  ];

  var nativeFetch = window.fetch;

  function getCuisine() {
    try { return localStorage.getItem(CUISINE_KEY) || ''; } catch (e) { return ''; }
  }

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }

  function getCachedUser() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { return null; }
  }

  function clearSession() {
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      localStorage.removeItem(CUISINE_KEY);
      localStorage.removeItem(CUISINE_PENDING_KEY);
    } catch (e) { /* ignore */ }
  }

  function redirectToLogin() {
    if (location.pathname === LOGIN_PAGE) return;
    var next = location.pathname + location.search;
    location.replace(LOGIN_PAGE + '?next=' + encodeURIComponent(next));
  }

  // 1. 未登录直接去登录页（本脚本在 <head> 中同步执行，避免页面先闪一下）
  if (!getToken()) {
    redirectToLogin();
    return;
  }

  // 2. 拦截 fetch，自动附加 token
  if (typeof nativeFetch === 'function') {
    window.fetch = function (input, init) {
      init = init || {};
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var isApi = url.indexOf('/api/v1/') !== -1;

      if (isApi) {
        var token = getToken();
        if (token) {
          var headers = new Headers(init.headers || (input && input.headers) || undefined);
          if (!headers.has('Authorization')) {
            headers.set('Authorization', 'Bearer ' + token);
          }
          init.headers = headers;
        }

        // 已选菜系随聊天请求一起提交（前端源码不可改，这里改请求体）
        if (url.indexOf('/api/v1/chat/stream') !== -1 && typeof init.body === 'string') {
          try {
            var payload = JSON.parse(init.body);
            var cuisine = getCuisine();
            if (cuisine && payload && typeof payload === 'object') {
              payload.cuisine = cuisine;
              init.body = JSON.stringify(payload);
              var chatHeaders = new Headers(init.headers || undefined);
              if (!chatHeaders.has('Content-Type')) {
                chatHeaders.set('Content-Type', 'application/json');
              }
              init.headers = chatHeaders;
            }
          } catch (e) { /* 非 JSON 请求体，保持原样 */ }
        }
      }

      return nativeFetch.call(this, input, init).then(function (response) {
        if (isApi && response.status === 401 && url.indexOf('/api/v1/auth/') === -1) {
          clearSession();
          redirectToLogin();
        }
        return response;
      });
    };
  }

  function logout() {
    var token = getToken();
    var finish = function () {
      clearSession();
      location.replace(LOGIN_PAGE);
    };
    if (!token) {
      finish();
      return;
    }
    // 通知服务端作废该账号已签发的 token；失败也照样清本地并跳转
    nativeFetch('/api/v1/auth/logout', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token }
    }).catch(function () { /* ignore */ }).then(finish);
  }

  function updateCuisineLabels() {
    var current = getCuisine() || '未选择';
    var nodes = document.querySelectorAll('.auth-cuisine-name');
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = current;
  }

  function hideCuisineModal() {
    var overlay = document.getElementById('cuisine-modal');
    if (overlay) overlay.style.display = 'none';
  }

  function setCuisine(name) {
    try {
      localStorage.setItem(CUISINE_KEY, name);
      localStorage.removeItem(CUISINE_PENDING_KEY);
    } catch (e) { /* ignore */ }
    updateCuisineLabels();
    hideCuisineModal();
  }

  function markActiveCuisine() {
    var current = getCuisine();
    var options = document.querySelectorAll('.cuisine-option');
    for (var i = 0; i < options.length; i++) {
      var active = options[i].getAttribute('data-cuisine') === current;
      options[i].setAttribute('data-active', active ? '1' : '0');
      options[i].style.borderColor = active ? '#f97316' : '#f3e0cf';
      options[i].style.background = active ? '#ffedd5' : '#fffaf5';
      options[i].style.boxShadow = active ? '0 0 0 2px rgba(249,115,22,.18)' : 'none';
    }
  }

  function ensureCuisineModal() {
    if (document.getElementById('cuisine-modal')) return;

    var overlay = document.createElement('div');
    overlay.id = 'cuisine-modal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:100', 'display:none',
      'align-items:center', 'justify-content:center', 'padding:16px',
      'background:rgba(80,40,10,.35)', 'backdrop-filter:blur(3px)'
    ].join(';');

    var card = document.createElement('div');
    card.style.cssText = [
      'width:100%', 'max-width:520px', 'background:#fff', 'border-radius:20px',
      'padding:26px 24px', 'box-shadow:0 24px 60px rgba(120,60,20,.25)',
      'font:14px/1.5 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif',
      'color:#3f2d1e', 'max-height:calc(100vh - 32px)', 'overflow:auto'
    ].join(';');

    var title = document.createElement('h2');
    title.textContent = '选择菜系';
    title.style.cssText = 'margin:0;font-size:20px;font-weight:700;color:#c2410c';

    var subtitle = document.createElement('p');
    subtitle.textContent = '登录成功！请先选择本次想制作的菜系，之后会根据它为你推荐菜谱。';
    subtitle.style.cssText = 'margin:8px 0 0;font-size:13px;color:#9a8a7b';

    var grid = document.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:10px;margin-top:20px';

    CUISINES.forEach(function (item) {
      var option = document.createElement('button');
      option.type = 'button';
      option.className = 'cuisine-option';
      option.setAttribute('data-cuisine', item.name);
      option.style.cssText = [
        'display:flex', 'flex-direction:column', 'align-items:center', 'gap:3px',
        'padding:12px 6px', 'border:1px solid #f3e0cf', 'border-radius:12px',
        'background:#fffaf5', 'cursor:pointer', 'font:inherit', 'color:#7c2d12',
        'transition:border-color .15s, background .15s'
      ].join(';');

      var name = document.createElement('span');
      name.textContent = item.name;
      name.style.cssText = 'font-size:15px;font-weight:700';

      var desc = document.createElement('span');
      desc.textContent = item.desc;
      desc.style.cssText = 'font-size:11px;color:#a08b78';

      option.appendChild(name);
      option.appendChild(desc);

      option.addEventListener('mouseenter', function () {
        if (option.getAttribute('data-active') !== '1') {
          option.style.borderColor = '#fb923c';
          option.style.background = '#fff7ed';
        }
      });
      option.addEventListener('mouseleave', function () {
        if (option.getAttribute('data-active') !== '1') {
          option.style.borderColor = '#f3e0cf';
          option.style.background = '#fffaf5';
        }
      });
      option.addEventListener('click', function () { setCuisine(item.name); });
      grid.appendChild(option);
    });

    var hint = document.createElement('p');
    hint.textContent = '选好后可通过左侧「切换」随时更改菜系。';
    hint.style.cssText = 'margin:16px 0 0;font-size:12px;color:#b09a86;text-align:center';

    card.appendChild(title);
    card.appendChild(subtitle);
    card.appendChild(grid);
    card.appendChild(hint);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function showCuisineModal() {
    ensureCuisineModal();
    markActiveCuisine();
    var overlay = document.getElementById('cuisine-modal');
    overlay.style.display = 'flex';
  }

  function maybeShowCuisineModal() {
    var pending = false;
    try { pending = localStorage.getItem(CUISINE_PENDING_KEY) === '1'; } catch (e) { /* ignore */ }
    if (pending || !getCuisine()) showCuisineModal();
  }

  // 侧边栏 + 聊天区两栏布局
  function injectLayoutStyle() {
    if (document.getElementById('auth-layout-style')) return;
    var style = document.createElement('style');
    style.id = 'auth-layout-style';
    style.textContent = [
      // 通用
      '.auth-divider{height:1px;background:#f3e7da;flex-shrink:0}',
      '.auth-user{min-width:0}',
      '.auth-user-row{display:flex;align-items:center;justify-content:flex-start;gap:6px;min-width:0}',
      '.auth-user-icon{display:flex;align-items:center;color:#c2410c;flex-shrink:0}',
      '.auth-user-name{font-size:16px;font-weight:700;color:#c2410c;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}',
      '.auth-logout{border:1px solid #fed7aa;background:#fff7ed;color:#c2410c;border-radius:999px;padding:4px 10px;font-size:12px;font-weight:600;line-height:1.3;cursor:pointer;white-space:nowrap;flex-shrink:0}',
      '.auth-cuisine{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;font-size:12px;color:#8a6a52}',
      '.auth-cuisine strong{color:#c2410c}',
      '.auth-cuisine-change{border:0;background:none;color:#c2410c;font-size:12px;cursor:pointer;padding:0;text-decoration:underline}',
      '.auth-newchat-wrap{flex-shrink:0}',
      '.auth-newchat-wrap>button{width:100%;justify-content:center}',
      // 桌面端：左侧边栏 + 右侧聊天区
      '@media (min-width:900px){',
      'header{display:none!important}',
      '#auth-sidebar{position:fixed;top:24px;left:16px;width:260px;z-index:60;display:flex;flex-direction:column;gap:18px;padding:20px;border-radius:16px;background:rgba(255,255,255,.8);backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,.5);box-shadow:0 10px 15px -3px rgba(0,0,0,.1),0 4px 6px -4px rgba(0,0,0,.1);font:13px/1.4 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}',
      '#auth-sidebar .auth-brand{flex-shrink:0}',
      '#auth-sidebar .auth-user{display:flex;flex-direction:column}',
      '.app-chat-pane{margin-left:calc(292px + max(0px,(100% - 292px - 16px - 56rem)/2))!important;margin-right:calc(16px + max(0px,(100% - 292px - 16px - 56rem)/2))!important;padding-top:24px!important}',
      'div.fixed.bottom-0{left:calc(292px + max(0px,(100% - 292px - 16px - 56rem)/2))!important;right:calc(16px + max(0px,(100% - 292px - 16px - 56rem)/2))!important}',
      '}',
      // 移动端：侧边栏变成顶部横条
      '@media (max-width:899px){',
      'header{display:none!important}',
      '#auth-sidebar{position:fixed;top:8px;left:8px;right:8px;z-index:60;display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:10px 12px;border-radius:16px;background:rgba(255,255,255,.85);backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,.5);box-shadow:0 10px 15px -3px rgba(0,0,0,.1),0 4px 6px -4px rgba(0,0,0,.1);font:13px/1.4 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}',
      '#auth-sidebar .auth-divider{display:none}',
      '#auth-sidebar .auth-brand p{display:none}',
      '#auth-sidebar .auth-brand h1{font-size:16px}',
      '#auth-sidebar .auth-user{display:flex;flex-direction:column}',
      '#auth-sidebar .auth-user-row{justify-content:flex-start;gap:6px}',
      '#auth-sidebar .auth-user-name{font-size:13px;max-width:92px}',
      '#auth-sidebar .auth-logout{padding:3px 8px;font-size:11px}',
      '#auth-sidebar .auth-cuisine{display:none}',
      '#auth-sidebar .auth-newchat-wrap{margin-left:auto}',
      '#auth-sidebar .auth-newchat-wrap>button{width:auto;padding:7px 12px}',
      '}'
    ].join('');
    document.head.appendChild(style);
  }

  function buildSidebar(username, brandNode, newChatButton) {
    var sidebar = document.createElement('div');
    sidebar.id = 'auth-sidebar';

    // 1. 私人厨师（品牌）
    var brand = brandNode.cloneNode(true);
    brand.classList.add('auth-brand');
    sidebar.appendChild(brand);

    var divider1 = document.createElement('div');
    divider1.className = 'auth-divider';
    sidebar.appendChild(divider1);

    // 2. 用户信息
    var user = document.createElement('div');
    user.className = 'auth-user';

    var icon = document.createElement('span');
    icon.className = 'auth-user-icon';
    icon.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>';

    var name = document.createElement('div');
    name.className = 'auth-user-name';
    name.textContent = username;
    name.title = '点击切换菜系';
    name.style.cursor = 'pointer';
    name.addEventListener('click', showCuisineModal);

    var logoutBtn = document.createElement('button');
    logoutBtn.type = 'button';
    logoutBtn.className = 'auth-logout';
    logoutBtn.textContent = '退出当前账号';
    logoutBtn.addEventListener('click', logout);

    var userRow = document.createElement('div');
    userRow.className = 'auth-user-row';
    userRow.appendChild(icon);
    userRow.appendChild(name);
    userRow.appendChild(logoutBtn);

    var cuisineLine = document.createElement('div');
    cuisineLine.className = 'auth-cuisine';
    var cuisineText = document.createElement('span');
    cuisineText.innerHTML = '菜系：<strong class="auth-cuisine-name"></strong>';
    var cuisineChange = document.createElement('button');
    cuisineChange.type = 'button';
    cuisineChange.className = 'auth-cuisine-change';
    cuisineChange.textContent = '切换';
    cuisineChange.addEventListener('click', showCuisineModal);
    cuisineLine.appendChild(cuisineText);
    cuisineLine.appendChild(cuisineChange);

    user.appendChild(userRow);
    user.appendChild(cuisineLine);
    sidebar.appendChild(user);

    var divider2 = document.createElement('div');
    divider2.className = 'auth-divider';
    sidebar.appendChild(divider2);

    // 3. 新建对话
    var wrap = document.createElement('div');
    wrap.className = 'auth-newchat-wrap';
    var newChat = newChatButton.cloneNode(true);
    newChat.style.display = 'flex';
    newChat.addEventListener('click', function (event) {
      event.preventDefault();
      newChatButton.click();
    });
    wrap.appendChild(newChat);
    sidebar.appendChild(wrap);

    return sidebar;
  }

  // 兜底：找不到 header 时，只显示账号和退出
  function mountFloating(username) {
    var chip = document.createElement('div');
    chip.id = 'auth-account-chip';
    chip.style.cssText = [
      'position:fixed', 'top:16px', 'right:16px', 'z-index:60',
      'display:flex', 'align-items:center', 'gap:8px',
      'padding:6px 8px 6px 14px', 'border-radius:999px',
      'background:rgba(255,255,255,0.92)', 'backdrop-filter:blur(8px)',
      'border:1px solid rgba(251,146,60,0.45)',
      'box-shadow:0 6px 18px rgba(120,60,20,0.12)',
      'font:12px/1.4 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif',
      'color:#7c2d12', 'white-space:nowrap'
    ].join(';');

    var name = document.createElement('strong');
    name.textContent = username;
    name.style.cssText = 'max-width:140px;overflow:hidden;text-overflow:ellipsis;color:#c2410c;font-weight:700';

    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = '退出当前账号';
    button.style.cssText = 'border:1px solid #fed7aa;cursor:pointer;border-radius:999px;padding:4px 10px;font-size:12px;font-weight:600;background:#fff7ed;color:#c2410c';
    button.addEventListener('click', logout);

    chip.appendChild(name);
    chip.appendChild(button);
    document.body.appendChild(chip);
  }

  function mountAccount(username) {
    if (!document.body || document.getElementById('auth-sidebar')) return;

    var header = document.querySelector('header');
    var bar = header && header.firstElementChild;
    var brandNode = bar && bar.firstElementChild;
    var originalNewChat = bar && bar.querySelector('button');
    if (!bar || !brandNode || !originalNewChat) {
      mountFloating(username);
      return;
    }

    injectLayoutStyle();

    var sidebar = buildSidebar(username, brandNode, originalNewChat);
    document.body.appendChild(sidebar);

    var chat = document.querySelector('div.min-h-screen.relative > div.max-w-4xl');
    if (chat) chat.classList.add('app-chat-pane');

    function place() {
      if (!document.body.contains(sidebar)) return;
      if (window.innerWidth < 900) {
        // 手机端：内容需要避开顶部横条
        if (chat) chat.style.paddingTop = (sidebar.getBoundingClientRect().bottom + 12) + 'px';
      } else if (chat) {
        chat.style.paddingTop = '';
      }
    }

    place();
    setTimeout(place, 300);
    setTimeout(place, 1200);
    window.addEventListener('resize', place);
  }

  function loadUser() {
    var cached = getCachedUser();
    if (cached && cached.username) {
      mountAccount(cached.username);
      updateCuisineLabels();
      maybeShowCuisineModal();
      return;
    }
    // 本地没有缓存用户名时向服务端要一次（例如 token 是别处写入的）
    nativeFetch('/api/v1/auth/me', {
      headers: { Authorization: 'Bearer ' + getToken() }
    })
      .then(function (res) {
        if (!res.ok) throw new Error('unauthorized');
        return res.json();
      })
      .then(function (user) {
        try { localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch (e) { /* ignore */ }
        mountAccount(user.username);
        updateCuisineLabels();
        maybeShowCuisineModal();
      })
      .catch(function () {
        clearSession();
        redirectToLogin();
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadUser);
  } else {
    loadUser();
  }
})();
