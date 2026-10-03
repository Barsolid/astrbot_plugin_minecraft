# -*- coding: utf-8 -*-
"""astrbot_plugin_minecraft

让「柚叶」陪你玩我的世界（Java 版）：
通过本地的 mineflayer 机器人控制 API，把游戏内操作暴露成 LLM 工具，
并提供游戏内聊天的反向转发（游戏里说话 → QQ）。

依赖：D:\\bot\\minecraft_bot 的 bot.mjs 正在运行，控制 API 默认 http://127.0.0.1:3001
"""
import asyncio
from typing import Any

import httpx
from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from astrbot.api import AstrBotConfig, FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

DEFAULT_API_BASE = "http://127.0.0.1:3001"


class McClient:
    """封装对 mineflayer 控制 API 的调用。"""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    async def _req(self, method: str, path: str, **kw) -> dict:
        async with httpx.AsyncClient(
            base_url=self.base, trust_env=False, timeout=60.0
        ) as c:
            r = await getattr(c, method)(path, **kw)
            r.raise_for_status()
            return r.json()

    async def status(self) -> dict:
        return await self._req("get", "/status")

    async def players(self) -> dict:
        return await self._req("get", "/players")

    async def say(self, text: str) -> dict:
        return await self._req("post", "/say", json={"text": text})

    async def follow(self, player: str = "") -> dict:
        return await self._req("post", "/follow", json={"player": player})

    async def come(self, player: str = "") -> dict:
        return await self._req("post", "/come", json={"player": player})

    async def goto(self, x, y, z) -> dict:
        return await self._req("post", "/goto", json={"x": x, "y": y, "z": z})

    async def stop(self) -> dict:
        return await self._req("post", "/stop", json={})

    async def mine(self, block: str, count: int = 1) -> dict:
        return await self._req("post", "/mine", json={"block": block, "count": count})

    async def place(self, block: str, x, y, z) -> dict:
        return await self._req(
            "post", "/place", json={"block": block, "x": x, "y": y, "z": z}
        )

    async def events(self, since: int) -> dict:
        return await self._req("get", "/events", params={"since": since})

    async def get_config(self) -> dict:
        return await self._req("get", "/config")

    async def connect_full(self, body: dict) -> dict:
        async with httpx.AsyncClient(
            base_url=self.base, trust_env=False, timeout=30.0
        ) as c:
            r = await c.post("/connect", json=body)
            r.raise_for_status()
            return r.json()


# --------------------------- LLM 工具 ---------------------------
@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_status"
    description: str = (
        "查看我的世界机器人的状态：是否在线、坐标、血量、饥饿、手持物品、周围玩家。"
        "当用户问‘你在游戏里吗/在哪/什么状态’时使用。"
    )
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_status(context.context.event)


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McSayTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_say"
    description: str = "在游戏聊天框里说一句话（发给所有在线玩家）。"
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要在游戏里说的话"}},
        "required": ["text"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_say(context.context.event, str(kwargs.get("text", "")))


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McFollowTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_follow"
    description: str = "让机器人跟着某个玩家走。不填玩家名则跟随最近的玩家。"
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {"player": {"type": "string", "description": "玩家名，可留空"}},
        "required": [],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_follow(context.context.event, str(kwargs.get("player", "")))


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McComeTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_come"
    description: str = "让机器人走到某个玩家身边。不填玩家名则走向最近的玩家。"
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {"player": {"type": "string", "description": "玩家名，可留空"}},
        "required": [],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_come(context.context.event, str(kwargs.get("player", "")))


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McGotoTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_goto"
    description: str = "让机器人走到指定坐标 (x, y, z)。"
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}
        },
        "required": ["x", "y", "z"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_goto(
            context.context.event, kwargs.get("x"), kwargs.get("y"), kwargs.get("z")
        )


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McStopTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_stop"
    description: str = "让机器人停止当前的移动/跟随。"
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_stop(context.context.event)


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McMineTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_mine"
    description: str = (
        "让机器人自动寻找并挖掘指定方块（如 oak_log、stone、iron_ore、diamond_ore），"
        "自动寻路过去挖取。block 用英文方块 id。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "block": {"type": "string", "description": "方块英文 id，如 stone、oak_log、diamond_ore"},
            "count": {"type": "integer", "description": "数量，默认 1"},
        },
        "required": ["block"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_mine(
            context.context.event, str(kwargs.get("block", "")), int(kwargs.get("count", 1) or 1)
        )


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McPlaceTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_place"
    description: str = "让机器人在指定坐标放置手上/背包里的方块。block 用英文方块 id。"
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "block": {"type": "string", "description": "方块英文 id，如 cobblestone、oak_planks"},
            "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
        },
        "required": ["block", "x", "y", "z"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_place(
            context.context.event, str(kwargs.get("block", "")),
            kwargs.get("x"), kwargs.get("y"), kwargs.get("z"),
        )


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McPlayersTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_players"
    description: str = "查看当前游戏里有哪些玩家在线。"
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_players(context.context.event)


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McConnectTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_connect"
    description: str = (
        "让机器人（重新）连接到我的世界服务器/局域网房间。"
        "当用户开启了局域网并告诉你端口号时，用本工具连接。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "地址，默认 127.0.0.1"},
            "port": {"type": "integer", "description": "局域网端口，例如 54321"},
            "version": {"type": "string", "description": "游戏版本，如 1.21.6，可留空"},
        },
        "required": ["port"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_connect(
            context.context.event,
            str(kwargs.get("host", "") or ""),
            int(kwargs.get("port")),
            str(kwargs.get("version", "") or ""),
        )


@register("astrbot_plugin_minecraft", "A3uracY", "让柚叶陪玩我的世界", "0.1.0")
class MinecraftPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.mc = McClient(config.get("api_base", DEFAULT_API_BASE))
        self._last_umo: str = ""
        self._poll_task: asyncio.Task | None = None
        self._register_tools()
        self.register_web_routes()

    def _register_tools(self):
        if not self.config.get("enable_llm_tools", True):
            return
        try:
            self.context.add_llm_tools(
                McStatusTool(plugin=self),
                McSayTool(plugin=self),
                McFollowTool(plugin=self),
                McComeTool(plugin=self),
                McGotoTool(plugin=self),
                McStopTool(plugin=self),
                McMineTool(plugin=self),
                McPlaceTool(plugin=self),
                McPlayersTool(plugin=self),
                McConnectTool(plugin=self),
            )
            logger.info("[minecraft] 已注册 10 个 LLM 工具")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[minecraft] 注册工具失败: {e}", exc_info=True)

    def register_web_routes(self):
        """注册 WebUI 页面用的接口（身份读取/保存）。"""
        from quart import jsonify, request

        PLUGIN = "astrbot_plugin_minecraft"

        async def get_identity():
            try:
                d = await self.mc.get_config()
                return jsonify({"ok": True, "identity": d})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)})

        async def save_identity():
            try:
                data = await request.get_json() or {}
                body = {}
                for k in ("host", "port", "version", "username", "owner", "skin_username"):
                    if k in data and data[k] != "":
                        body[k] = data[k]
                d = await self.mc.connect_full(body)
                return jsonify({"ok": True, "identity": d})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)})

        self.context.register_web_api(
            f"/{PLUGIN}/identity", get_identity, ["GET"], "获取柚叶身份配置"
        )
        self.context.register_web_api(
            f"/{PLUGIN}/identity/save", save_identity, ["POST"], "保存柚叶身份并重连"
        )

    async def initialize(self):
        if self.config.get("forward_game_chat", True):
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info("[minecraft] 已启动游戏聊天转发轮询")
        # 记录一个默认会话：优先发给配置里的 owner_umo
        self._last_umo = self.config.get("owner_umo", "") or ""

    async def terminate(self):
        if self._poll_task:
            self._poll_task.cancel()

    # ---------- 工具实现 ----------
    def _mark(self, event: AstrMessageEvent):
        self._last_umo = event.unified_msg_origin

    async def tool_status(self, event: AstrMessageEvent) -> str:
        self._mark(event)
        try:
            s = await self.mc.status()
        except Exception as e:  # noqa: BLE001
            return f"❌ 连不上游戏机器人控制服务（{self.mc.base}）：{e}"
        if not s.get("connected"):
            return f"我不在游戏里哦（未连接）。当前：{s.get('error', '未知')}"
        p = s["position"]
        return (
            f"我在游戏里～ 坐标({p['x']},{p['y']},{p['z']})，"
            f"血量 {s.get('health')}，饥饿 {s.get('food')}，"
            f"手持 {s.get('held') or '空'}，在线玩家：{', '.join(s.get('players', [])) or '无'}。"
            f"背包：{s.get('inventory')}"
        )

    async def tool_players(self, event: AstrMessageEvent) -> str:
        self._mark(event)
        try:
            r = await self.mc.players()
            return "在线玩家：" + (", ".join(r.get("players", [])) or "无")
        except Exception as e:  # noqa: BLE001
            return f"❌ 查询失败：{e}"

    async def tool_say(self, event: AstrMessageEvent, text: str) -> str:
        self._mark(event)
        if not text.strip():
            return "要说点什么呀？"
        try:
            await self.mc.say(text)
            return f"已在游戏里说：{text}"
        except Exception as e:  # noqa: BLE001
            return f"❌ 发言失败：{e}"

    async def tool_follow(self, event: AstrMessageEvent, player: str) -> str:
        self._mark(event)
        try:
            r = await self.mc.follow(player)
            return "✅ " + str(r.get("message", "开始跟随"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 跟随失败：{e}"

    async def tool_come(self, event: AstrMessageEvent, player: str) -> str:
        self._mark(event)
        try:
            r = await self.mc.come(player)
            return "✅ " + str(r.get("message", "正在过去"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 过去失败：{e}"

    async def tool_goto(self, event: AstrMessageEvent, x, y, z) -> str:
        self._mark(event)
        try:
            r = await self.mc.goto(x, y, z)
            return "✅ " + str(r.get("message", "已到达"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 移动失败：{e}"

    async def tool_stop(self, event: AstrMessageEvent) -> str:
        self._mark(event)
        try:
            r = await self.mc.stop()
            return "✅ " + str(r.get("message", "已停下"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 停止失败：{e}"

    async def tool_mine(self, event: AstrMessageEvent, block: str, count: int) -> str:
        self._mark(event)
        try:
            r = await self.mc.mine(block, count)
            return "✅ " + str(r.get("message", "挖取完成"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 挖掘失败：{e}"

    async def tool_place(self, event: AstrMessageEvent, block: str, x, y, z) -> str:
        self._mark(event)
        try:
            r = await self.mc.place(block, x, y, z)
            return "✅ " + str(r.get("message", "已放置"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 放置失败：{e}"

    async def tool_connect(self, event: AstrMessageEvent, host: str, port: int, version: str) -> str:
        self._mark(event)
        try:
            body = {"port": port}
            if host:
                body["host"] = host
            if version:
                body["version"] = version
            async with httpx.AsyncClient(
                base_url=self.mc.base, trust_env=False, timeout=20.0
            ) as c:
                r = await c.post("/connect", json=body)
                r.raise_for_status()
                d = r.json()
            return (
                f"✅ 已请求连接 {d.get('host')}:{d.get('port')}"
                f"（版本 {d.get('version')}），稍等几秒后可以问我游戏状态。"
            )
        except Exception as e:  # noqa: BLE001
            return f"❌ 连接失败：{e}"

    # ---------- 游戏内聊天 -> QQ ----------
    async def _poll_loop(self):
        last = 0
        while True:
            try:
                data = await self.mc.events(last)
                for ev in data.get("events", []):
                    last = ev.get("seq", last)
                    await self._forward_event(ev)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(2)

    async def _forward_event(self, ev: dict):
        if not self._last_umo:
            return
        t = ev.get("type")
        d = ev.get("data", {})
        if t == "chat":
            msg = f"💬 游戏内 <{d.get('username')}>：{d.get('message')}"
        elif t == "spawn":
            msg = "🎮 柚叶已进入你的世界～"
        elif t == "end":
            msg = "🚪 柚叶离开了游戏（连接断开）"
        elif t == "system":
            text = d.get("text", "")
            if not text:
                return
            msg = f"ℹ️ {text}"
        else:
            return
        try:
            await self.context.send_message(self._last_umo, MessageChain().message(msg))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[minecraft] 转发失败: {e}")

    # ---------- 指令（可选，方便手动控制） ----------
    @filter.command("mc")
    async def mc_cmd(self, event: AstrMessageEvent, action: str = "", arg: str = ""):
        """我的世界控制：/mc status|follow|come|stop|say <文本>"""
        self._mark(event)
        try:
            if action in ("status", ""):
                yield event.plain_result(await self.tool_status(event))
            elif action == "players":
                yield event.plain_result(await self.tool_players(event))
            elif action in ("follow", "come"):
                r = await (self.mc.follow(arg) if action == "follow" else self.mc.come(arg))
                yield event.plain_result("✅ " + str(r.get("message", "ok")))
            elif action == "stop":
                await self.mc.stop()
                yield event.plain_result("✅ 已停下")
            elif action == "say":
                await self.mc.say(arg)
                yield event.plain_result(f"✅ 已在游戏里说：{arg}")
            elif action == "connect":
                port = int("".join(ch for ch in arg if ch.isdigit()) or 0)
                yield event.plain_result(await self.tool_connect(event, "", port, ""))
            else:
                yield event.plain_result(
                    "用法：/mc status|players|follow [玩家]|come [玩家]|stop|say 文本|connect 端口"
                )
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"❌ {e}")
        event.stop_event()
