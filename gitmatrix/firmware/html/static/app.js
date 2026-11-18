// Shared by every page: the header/nav, a small JSON API helper and a toast.
// Pages are plain HTML; all data comes from /api/v1 (see gitmatrix/_web.py).
"use strict";

const PAGES = [
  ["/", "Home"],
  ["/settings.html", "Settings"],
  ["/wifi.html", "WiFi"],
  ["/bluetooth.html", "Bluetooth"],
  ["/discover.html", "Discover"],
  ["/notifications.html", "Notifications"],
  ["/sounds.html", "Sounds"],
  ["/system.html", "System"],
];

const $ = (sel, root) => (root || document).querySelector(sel);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c != null) node.append(c instanceof Node ? c : String(c));
  }
  return node;
}

async function api(path, body, method) {
  const opts = { method: method || (body === undefined ? "GET" : "POST"), credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch("/api/v1" + path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) {
    const err = new Error(data.error || res.status + " " + res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// Flash writes can't happen while Bluetooth audio plays (the board can hang), so
// the device answers 409 {audio_streaming} to installs and settings saves. Ask,
// then retry with pause_audio (the speaker disconnects) or, if allowed, queue.
async function withAudio(path, body, canQueue) {
  try {
    return await api(path, body);
  } catch (e) {
    if (!e.data || !e.data.audio_streaming) throw e;
    const msg = "Bluetooth audio is playing. " + e.data.error + ".\n\n" +
      (canQueue ? "OK: stop the audio and do it now.\nCancel: wait until playback stops." : "OK: stop the audio and do it now.");
    if (confirm(msg)) return api(path, { ...body, pause_audio: true });
    if (canQueue) return api(path, { ...body, queue: true });
    throw new Error("Not saved: Bluetooth audio is playing");
  }
}

let toastTimer;
function toast(msg, bad) {
  let t = $("#toast");
  if (!t) { t = el("div", { id: "toast", role: "status" }); document.body.append(t); }
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
}

// run fn, toasting any error; returns its result (or undefined on failure)
async function attempt(fn, ok) {
  try {
    const out = await fn();
    if (ok) toast(ok);
    return out;
  } catch (e) {
    toast(e.message, true);
  }
}

function fmtBytes(n) {
  if (n == null) return "?";
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  return Math.round(n / 1024) + " KB";
}

function fmtAge(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
  return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
}

function rebooting(msg) {
  document.body.replaceChildren(
    el("section", {}, el("h1", {}, "Rebooting…"), el("p", {}, msg || "GitMatrix is restarting. This page will be back in a few seconds.")),
  );
}

(function header() {
  const here = location.pathname === "/index.html" ? "/" : location.pathname.replace(/\.html$/, "") ;
  const nav = el("nav", {});
  for (const [href, label] of PAGES) {
    const a = el("a", { href }, label);
    if (href.replace(/\.html$/, "") === here) a.setAttribute("aria-current", "page");
    nav.append(a);
  }
  const brand = el("a", { class: "brand", href: "/" }, "Git", el("span", {}, "Matrix"));
  document.body.prepend(el("header", {}, brand, nav));
})();
