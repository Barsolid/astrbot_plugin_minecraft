// 我的世界机器人（mineflayer）+ 本地控制 API
// 供 AstrBot 插件通过 HTTP 调用，让「柚叶」能进游戏陪玩。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import express from "express";
import mineflayer from "mineflayer";
import pathfinderPkg from "mineflayer-pathfinder";

const { pathfinder, Movements, goals } = pathfinderPkg;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cfgPath = path.join(__dirname, "config.json");
const config = JSON.parse(fs.readFileSync(cfgPath, "utf-8"));

const API_PORT = config.api_port || 3001;

let bot = null;
let state = { connected: false, lastError: "", spawnTime: 0 };
// 事件队列：把游戏内聊天等推给 AstrBot 轮询
const eventQueue = [];
let eventSeq = 0;

function pushEvent(type, data) {
  eventQueue.push({ seq: ++eventSeq, time: Date.now(), type, data });
  if (eventQueue.length > 200) eventQueue.shift();
}

function log(...args) {
  console.log(new Date().toLocaleTimeString(), ...args);
}

function createBotInstance() {
  if (bot) {
    try { bot.quit(); } catch (_) {}
    bot = null;
  }
  log(`连接服务器 ${config.host}:${config.port} 版本 ${config.version} ...`);
  bot = mineflayer.createBot({
    host: config.host,
    port: config.port,
    username: config.username || "柚叶Bot",
    version: config.version || false,
    auth: config.auth || "offline",
  });
  bot.loadPlugin(pathfinder);

  bot.once("spawn", () => {
    state.connected = true;
    state.spawnTime = Date.now();
    state.lastError = "";
    log("已进入游戏");
    pushEvent("spawn", { username: bot.username });
  });
  bot.on("error", (e) => { state.lastError = String(e); log("错误:", e.message); });
  bot.on("kicked", (r) => { state.lastError = "kicked: " + r; log("被踢出:", r); });
  bot.on("end", (r) => {
    state.connected = false;
    log("断开连接:", r);
    pushEvent("end", { reason: String(r) });
    // 自动重连
    setTimeout(createBotInstance, 5000);
  });
  bot.on("chat", (username, message) => {
    pushEvent("chat", { username, message });
  });
  bot.on("message", (jsonMsg) => {
    // 系统消息（成就、加入/离开等）
    const text = jsonMsg.toString();
    if (text && !text.startsWith("<")) pushEvent("system", { text });
  });
  return bot;
}

// ---------- 动作实现 ----------
function ensureBot() {
  if (!bot || !state.connected) throw new Error("机器人未连接到游戏");
  return bot;
}

function findPlayerBlock(playerName) {
  const p = bot.players[playerName];
  return p && p.entity ? p.entity.position : null;
}

// 未指定玩家时，选最近的其他玩家
function pickPlayer() {
  const me = bot.entity.position;
  let best = null;
  let bestD = Infinity;
  for (const [name, p] of Object.entries(bot.players)) {
    if (name === bot.username || !p.entity) continue;
    const d = p.entity.position.distanceTo(me);
    if (d < bestD) { bestD = d; best = name; }
  }
  return best;
}

async function doFollow(playerName, distance = 3) {
  ensureBot();
  const name = playerName || config.owner || pickPlayer();
  const target = bot.players[name]?.entity;
  if (!target) throw new Error(`找不到玩家 ${name}`);
  const { GoalFollow } = goals;
  bot.pathfinder.setMovements(new Movements(bot));
  bot.pathfinder.setGoal(new GoalFollow(target, distance), true);
  return `开始跟随 ${name}`;
}

async function doCome(playerName) {
  ensureBot();
  const name = playerName || config.owner || pickPlayer();
  const pos = findPlayerBlock(name);
  if (!pos) throw new Error(`找不到玩家 ${name}`);
  const { GoalNear } = goals;
  bot.pathfinder.setMovements(new Movements(bot));
  await bot.pathfinder.goto(new GoalNear(pos.x, pos.y, pos.z, 2));
  return `已来到 ${name} 身边`;
}

async function doGoto(x, y, z) {
  ensureBot();
  const { GoalNear } = goals;
  bot.pathfinder.setMovements(new Movements(bot));
  await bot.pathfinder.goto(new GoalNear(Math.round(x), Math.round(y), Math.round(z), 2));
  return `已到达 (${x}, ${y}, ${z})`;
}

function doStop() {
  ensureBot();
  bot.pathfinder.setGoal(null);
  bot.clearControlStates();
  return "已停下";
}

async function doMine(blockName, count = 1) {
  ensureBot();
  const id = bot.registry.blocksByName[blockName]?.id;
  if (id === undefined) throw new Error(`未知方块: ${blockName}`);
  const { GoalNear } = goals;
  bot.pathfinder.setMovements(new Movements(bot));
  let mined = 0;
  for (let i = 0; i < count; i++) {
    const block = bot.findBlock({ matching: id, maxDistance: 64 });
    if (!block) break;
    try {
      await bot.pathfinder.goto(new GoalNear(block.position.x, block.position.y, block.position.z, 1));
      await bot.dig(block);
      mined++;
    } catch (e) {
      log("挖掘失败:", e.message);
      break;
    }
  }
  return `已挖取 ${mined} 个 ${blockName}`;
}

async function doPlace(blockName, x, y, z) {
  ensureBot();
  const item = bot.inventory.items().find((it) => it.name === blockName);
  if (!item) throw new Error(`背包里没有 ${blockName}`);
  await bot.equip(item, "hand");
  const { Vec3 } = await import("vec3");
  const target = new Vec3(Math.floor(x), Math.floor(y), Math.floor(z));
  const refBlock = bot.blockAt(target.offset(0, -1, 0));
  if (!refBlock) throw new Error("目标位置下方没有可依附的方块");
  await bot.placeBlock(refBlock, new Vec3(0, 1, 0));
  return `已在 (${x}, ${y}, ${z}) 放置 ${blockName}`;
}

async function doCollect(blockName, count = 1) {
  // 挖掉方块后拾取掉落物
  await doMine(blockName, count);
  await new Promise((r) => setTimeout(r, 1500));
  return `完成采集 ${blockName} x${count}`;
}

function summarizeInventory() {
  ensureBot();
  const items = bot.inventory.items();
  if (!items.length) return "背包是空的";
  return items.map((it) => `${it.name} x${it.count}`).join(", ");
}

function status() {
  if (!bot || !state.connected) {
    return { connected: false, error: state.lastError };
  }
  const p = bot.entity.position;
  return {
    connected: true,
    username: bot.username,
    position: { x: Math.round(p.x), y: Math.round(p.y), z: Math.round(p.z) },
    health: bot.health,
    food: bot.food,
    held: bot.heldItem ? bot.heldItem.name : null,
    players: Object.keys(bot.players),
    time: bot.time.timeOfDay,
    inventory: summarizeInventory(),
  };
}

// ---------- HTTP API ----------
const app = express();
app.use(express.json());

app.get("/status", (req, res) => res.json(status()));
app.get("/players", (req, res) => {
  if (!bot || !state.connected) return res.json({ players: [] });
  res.json({ players: Object.keys(bot.players) });
});

app.post("/say", (req, res) => {
  try { ensureBot(); bot.chat(String(req.body.text || "")); res.json({ ok: true }); }
  catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.post("/follow", async (req, res) => {
  try { res.json({ ok: true, message: await doFollow(req.body.player || config.owner, req.body.distance || 3) }); }
  catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.post("/come", async (req, res) => {
  try { res.json({ ok: true, message: await doCome(req.body.player || config.owner) }); }
  catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.post("/goto", async (req, res) => {
  try {
    const { x, y, z, player } = req.body;
    if (player) res.json({ ok: true, message: await doCome(player) });
    else res.json({ ok: true, message: await doGoto(Number(x), Number(y), Number(z)) });
  } catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.post("/stop", (req, res) => {
  try { res.json({ ok: true, message: doStop() }); }
  catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.post("/mine", async (req, res) => {
  try { res.json({ ok: true, message: await doMine(req.body.block, req.body.count || 1) }); }
  catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.post("/place", async (req, res) => {
  try {
    const { block, x, y, z } = req.body;
    res.json({ ok: true, message: await doPlace(block, Number(x), Number(y), Number(z)) });
  } catch (e) { res.status(400).json({ ok: false, error: e.message }); }
});

app.get("/events", (req, res) => {
  const since = Number(req.query.since || 0);
  res.json({ events: eventQueue.filter((e) => e.seq > since), last: eventSeq });
});

app.get("/config", (req, res) => {
  res.json({
    host: config.host,
    port: config.port,
    username: config.username,
    version: config.version,
    auth: config.auth,
    owner: config.owner,
    skin_username: config.skin_username || "",
    connected: state.connected,
    lastError: state.lastError,
  });
});

app.post("/connect", (req, res) => {
  const { host, port, version, username, owner, skin_username } = req.body || {};
  if (host) config.host = host;
  if (port) config.port = Number(port);
  if (version) config.version = version;
  if (username) config.username = username;
  if (owner !== undefined) config.owner = owner;
  if (skin_username !== undefined) config.skin_username = skin_username;
  try {
    fs.writeFileSync(cfgPath, JSON.stringify(config, null, 2));
  } catch (e) {
    log("保存配置失败:", e.message);
  }
  createBotInstance();
  res.json({ ok: true, ...config });
});

app.listen(API_PORT, "127.0.0.1", () => log(`控制 API 已启动: http://127.0.0.1:${API_PORT}`));

// 启动即连接
createBotInstance();
