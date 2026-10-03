const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
const out = $("out");

function fill(d) {
  $("username").value = d.username || "";
  $("owner").value = d.owner || "";
  $("skin_username").value = d.skin_username || "";
  $("version").value = d.version || "";
  $("host").value = d.host || "";
  $("port").value = d.port || "";
  out.textContent =
    "连接状态：" +
    (d.connected ? "✅ 已进入游戏" : "⏸️ 未连接") +
    (d.lastError ? "\n" + d.lastError : "");
}

async function load() {
  try {
    const r = await bridge.apiGet("identity");
    if (r.ok) fill(r.identity);
    else out.textContent = "读取失败：" + r.error;
  } catch (e) {
    out.textContent = "读取失败：" + e;
  }
}

async function save() {
  const body = {
    username: $("username").value.trim(),
    owner: $("owner").value.trim(),
    skin_username: $("skin_username").value.trim(),
    version: $("version").value.trim(),
    host: $("host").value.trim(),
    port: $("port").value.trim(),
  };
  out.textContent = "保存中…";
  try {
    const r = await bridge.apiPost("identity/save", body);
    if (r.ok) {
      fill(r.identity);
      out.textContent += "\n✅ 已保存，正在重连…";
    } else {
      out.textContent = "保存失败：" + r.error;
    }
  } catch (e) {
    out.textContent = "保存失败：" + e;
  }
}

try {
  await bridge.ready();
  $("save").addEventListener("click", save);
  $("refresh").addEventListener("click", load);
  load();
} catch (e) {
  out.textContent = "Bridge 初始化失败：" + e;
}
