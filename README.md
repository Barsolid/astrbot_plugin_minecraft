# astrbot_plugin_minecraft

让本地ai陪你玩 **Minecraft（Java 版）**。通过本地 [mineflayer](https://github.com/PrismarineJS/mineflayer) 机器人把游戏内操作暴露为 AstrBot 的 LLM 工具，并支持把游戏内聊天转发到 QQ。

## 功能

- 🤖 自然语言控制：跟随、过来、前往坐标、停止、发言、挖矿、放置方块
- 📊 状态查询：坐标 / 血量 / 饥饿 / 手持 / 在线玩家 / 背包
- 💬 游戏内聊天 → QQ 反向转发
- 🎨 WebUI「身份」页面：修改游戏名(ID)、主人玩家名、皮肤用户名、版本、地址端口，并一键重连
- 🧠 内置 `/mc` 指令作为兜底

## 目录结构

```
astrbot_plugin_minecraft/
├─ main.py              # 插件主体（LLM 工具 + WebUI 接口 + 聊天转发）
├─ metadata.yaml
├─ _conf_schema.json
├─ requirements.txt
├─ pages/identity/      # WebUI 身份管理页面
└─ bot/                 # mineflayer 机器人（需单独运行）
   ├─ bot.mjs
   ├─ package.json
   └─ config.example.json
```

## 安装

### 1. 安装并运行机器人

```bash
# 在 bot 目录
npm install mineflayer mineflayer-pathfinder express
cp config.example.json config.json   # 按需修改
node bot.mjs                          # 默认控制 API: http://127.0.0.1:3001
```

`config.json`：

```json
{
  "host": "127.0.0.1",
  "port": 25565,
  "username": "柚叶Bot",
  "version": "1.21.6",
  "auth": "offline",
  "owner": "",
  "api_port": 3001
}
```

### 2. 安装插件

把插件目录放入 AstrBot 的 `data/plugins/`，或在 WebUI 从本仓库安装，然后「重载插件」。

## 使用

### 开局域网让机器人进来

1. 进入单机存档 → `Esc` → **对局域网开放** → 记下端口（聊天栏的 `Local game hosted on port 54321`）。
2. 在 QQ 里发 `/mc connect 54321`，或对柚叶说「连我的世界，端口 54321」。

### 对话示例

- 「柚叶，你在游戏里吗」→ 报状态
- 「柚叶跟着我」「过来」「去 (100,64,200)」
- 「柚叶去挖 10 个石头」「在 (x,y,z) 放圆石」
- 你在游戏里打字 → QQ 会收到转发

### 指令

| 指令 | 说明 |
| --- | --- |
| `/mc status` | 查看机器人状态 |
| `/mc players` | 在线玩家 |
| `/mc follow [玩家]` | 跟随 |
| `/mc come [玩家]` | 过来 |
| `/mc stop` | 停止移动 |
| `/mc say 文本` | 游戏内发言 |
| `/mc connect 端口` | 连接局域网 |

## 关于皮肤

离线（局域网）模式下，原版服务器无法给机器人自定义皮肤；机器人显示的是默认皮肤。
若服务器装有皮肤插件（如 SkinsRestorer），可把「游戏名」或「皮肤用户名」设为已有皮肤的名字来生效。

## 配置

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `api_base` | `http://127.0.0.1:3001` | 机器人控制 API 地址 |
| `enable_llm_tools` | true | 启用自然语言工具 |
| `forward_game_chat` | true | 游戏内聊天转发到 QQ |
| `owner_umo` | 空 | 转发目标会话（留空=最近对话的会话） |

## WebUI 页面

插件管理 → 本插件 → 「柚叶 · 我的世界身份」页面，可修改身份并一键重连。

## AstrBot 版本

要求 `>= 4.5.1`。

## License

MIT
