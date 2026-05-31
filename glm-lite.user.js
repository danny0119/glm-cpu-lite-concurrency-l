// ==UserScript==
// @name         GLM Coding Helper Lite
// @namespace    https://glm-coding-helper.lite
// @version      1.0.0
// @description  自动识别中文验证码 — 点击坐标自动填充
// @author       GLM Coding Helper
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @grant        GM_setValue
// @grant        GM_getValue
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  // ── 配置 ──────────────────────────────────────────────────────────────
  const CONFIG = {
    backendUrl: GM_getValue("backendUrl", "http://127.0.0.1:8888"),
    autoClick: GM_getValue("autoClick", true),
    debug: GM_getValue("debug", false),
  };

  // ── 样式注入 ──────────────────────────────────────────────────────────
  GM_addStyle(`
    .glm-badge {
      position: fixed; bottom: 12px; right: 12px; z-index: 999999;
      background: rgba(0,0,0,0.75); color: #fff; padding: 6px 14px;
      border-radius: 20px; font: 13px/1.5 sans-serif; cursor: pointer;
      transition: 0.2s; user-select: none;
    }
    .glm-badge:hover { background: rgba(0,0,0,0.9); }
    .glm-badge.ok  { border-left: 3px solid #22c55e; }
    .glm-badge.err { border-left: 3px solid #ef4444; }
    .glm-badge.busy { border-left: 3px solid #f59e0b; }
    .glm-toast {
      position: fixed; bottom: 52px; right: 12px; z-index: 999999;
      background: rgba(0,0,0,0.85); color: #fff; padding: 8px 16px;
      border-radius: 8px; font: 12px/1.4 sans-serif;
      max-width: 320px; animation: glmFadeIn 0.2s;
    }
    @keyframes glmFadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
    .glm-overlay {
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.3); z-index: 999998; cursor: crosshair;
    }
  `);

  // ── UI 组件 ───────────────────────────────────────────────────────────
  let badge = null;
  let toastTimer = null;

  function showBadge(status, text) {
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "glm-badge";
      badge.textContent = "🧠 GLM";
      badge.addEventListener("click", () => {
        const url = prompt("后端地址:", CONFIG.backendUrl);
        if (url) { CONFIG.backendUrl = url; GM_setValue("backendUrl", url); toast("已更新后端地址"); }
      });
      document.body.appendChild(badge);
    }
    badge.className = `glm-badge ${status}`;
    if (text) badge.textContent = text;
  }

  function toast(msg, ms = 3000) {
    if (toastTimer) clearTimeout(toastTimer);
    let el = document.querySelector(".glm-toast");
    if (!el) { el = document.createElement("div"); el.className = "glm-toast"; document.body.appendChild(el); }
    el.textContent = msg;
    toastTimer = setTimeout(() => el.remove(), ms);
  }

  function log(...args) { if (CONFIG.debug) console.log("[GLM]", ...args); }

  // ── 与后端通信 ───────────────────────────────────────────────────────
  function solveCaptcha(text, imageDataUrl) {
    const body = JSON.stringify({ text, image: imageDataUrl });
    const url = CONFIG.backendUrl + "/captcha_direct";
    // 优先 fetch() —— 浏览器原生并发，多窗口互不阻塞
    return new Promise((resolve, reject) => {
      const doFetch = () => {
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        }).then(r => r.json()).then(res => {
          if (res.success) resolve(res.result);
          else reject(new Error(res.error || "识别失败"));
        }).catch(() => {
          if (typeof GM_xmlhttpRequest !== 'undefined') doGM();
          else reject(new Error("both fetch and GM_xmlhttpRequest failed"));
        });
      };
      const doGM = () => {
        GM_xmlhttpRequest({
          method: "POST",
          url: url,
          headers: { "Content-Type": "application/json" },
          data: body,
          timeout: 30000,
          onload: (r) => {
            try {
              const res = JSON.parse(r.responseText);
              if (res.success) resolve(res.result);
              else reject(new Error(res.error || "识别失败"));
            } catch (e) { reject(e); }
          },
          onerror: reject,
          ontimeout: () => reject(new Error("后端超时")),
        });
      };
      doFetch();
    });
  }

  // ── 自动点击 ─────────────────────────────────────────────────────────
  function performClicks(clickCoords, container) {
    const rect = container.getBoundingClientRect();
    clickCoords.forEach(({ char, nx, ny }) => {
      const x = rect.left + nx * rect.width;
      const y = rect.top + ny * rect.height;
      log(`Click: "${char}" at (${x.toFixed(0)}, ${y.toFixed(0)})`);

      // 模拟鼠标点击
      ["mousedown", "mouseup", "click"].forEach((type) => {
        container.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true, clientX: x, clientY: y,
        }));
      });
    });
  }

  // ── 检测验证码容器 ───────────────────────────────────────────────────
  function findCaptchaContainer() {
    // 通用特征: 包含中文验证码提示 + 图片
    // 支持常见框架: vue-captcha, sliding-captcha, 自定义 captcha

    // 1) 查找包含"验证码"文字的容器
    const allEls = document.querySelectorAll(
      "div, section, form, .captcha, .captcha-box, " +
      "[class*=captcha], [class*=verify], [class*=sliding], " +
      "#captcha, #verify, #sliding"
    );

    for (const el of allEls) {
      const text = el.textContent.trim();
      if (/验证码|请点击|汉字|字符|顺序|中文/.test(text)) {
        // 查找内部的图片
        const imgs = el.querySelectorAll("img");
        for (const img of imgs) {
          if (img.width >= 100 && img.height >= 30 && img.src) {
            return { container: el, img, text };
          }
        }
      }
    }

    // 2) 回退: 找页面中任何中等尺寸的图片 + 周围中文文本
    const allImgs = document.querySelectorAll("img");
    for (const img of allImgs) {
      if (img.width >= 100 && img.height >= 30 && img.complete && img.src) {
        // 检查父容器
        let parent = img.parentElement;
        for (let i = 0; i < 3 && parent; i++) {
          const txt = parent.textContent.trim();
          if (/验证码|请点击|汉字|字符/.test(txt)) {
            return { container: parent, img, text: txt };
          }
          parent = parent.parentElement;
        }
      }
    }

    return null;
  }

  // ── 主流程 ───────────────────────────────────────────────────────────
  async function autoSolve() {
    const captcha = findCaptchaContainer();
    if (!captcha) {
      log("未找到验证码");
      showBadge("ok", "🧠 待命");
      return;
    }

    const { container, img, text } = captcha;
    log(`发现验证码: "${text}" img=${img.src.substring(0, 60)}...`);
    showBadge("busy", "🧠 识别中...");

    try {
      // 获取图片 base64
      const resp = await fetch(img.src);
      const blob = await resp.blob();
      const dataUrl = await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.readAsDataURL(blob);
      });

      const result = await solveCaptcha(text, dataUrl);
      log("识别结果:", result);
      showBadge("ok", `🧠 ${result.pred_text || result.prompt} (${result.elapsed_ms}ms)`);

      if (CONFIG.autoClick && result.click_coords && result.click_coords.length > 0) {
        toast(`🧠 已识别: ${result.prompt} → 正在点击...`);
        performClicks(result.click_coords, img);
        toast(`✅ 点击完成: ${result.prompt}`);
      } else {
        toast(`🧠 识别: ${result.prompt || result.pred_text}`);
      }
    } catch (e) {
      log("识别失败:", e);
      showBadge("err", "🧠 失败");
      toast(`❌ ${e.message}`);
    }
  }

  // ── 定时轮询 ─────────────────────────────────────────────────────────
  let lastCheck = 0;
  let checkInterval = 2000;    // 2 秒检查一次
  let checkTimer = null;

  function startPolling() {
    if (checkTimer) return;
    checkTimer = setInterval(() => {
      // 避免密集请求
      const now = Date.now();
      if (now - lastCheck < checkInterval) return;

      // 检查后端健康状态
      fetch(CONFIG.backendUrl + "/health", { method: "GET" })
        .then(r => r.json())
        .then(res => {
          if (res.status === "ok") {
            showBadge("ok", `🧠 在线 (${res.workers}W)`);
            autoSolve();
          }
        })
        .catch(() => showBadge("err", "🧠 离线"));
      lastCheck = now;
    }, checkInterval);
  }

  // ── 初始化 ───────────────────────────────────────────────────────────
  function init() {
    log("初始化, 后端:", CONFIG.backendUrl);
    showBadge("busy", "🧠 连接中...");
    startPolling();

    // 用户手动触发热键: Ctrl+Shift+S
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.shiftKey && e.key === "S") {
        e.preventDefault();
        toast("🔄 手动识别...");
        autoSolve();
      }
    });
  }

  // 等待 DOM 加载
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
