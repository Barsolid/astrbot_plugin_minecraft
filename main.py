# -*- coding: utf-8 -*-
"""astrbot_plugin_minecraft

让「柚叶」陪你玩我的世界（Java 版）：
通过本地的 mineflayer 机器人控制 API，把游戏内操作暴露成 LLM 工具，
并提供游戏内聊天的反向转发（游戏里说话 → QQ）。

依赖：D:\\bot\\minecraft_bot 的 bot.mjs 正在运行，控制 API 默认 http://127.0.0.1:3001
"""
import asyncio
import re
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

# 这些是「动作类」指令，交给机器人自身的关键词层处理，不再走 LLM 聊天
GAME_ACTION_RE = re.compile(
    r"过来|来我|到我这|跟着我|跟随|停下|别动|你在哪|坐标|你好|hi|hello|stop|come|follow",
    re.I,
)


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

    async def give(self, block: str, count: int = 1) -> dict:
        return await self._req("post", "/give", json={"block": block, "count": count})

    async def get_mode(self) -> dict:
        return await self._req("get", "/mode")

    async def set_mode(self, mode: str) -> dict:
        return await self._req("post", "/mode", json={"mode": mode})

    async def set_move_mode(self, mode: str) -> dict:
        return await self._req("post", "/movemode", json={"mode": mode})

    async def cancel_build(self) -> dict:
        return await self._req("post", "/build/cancel")

    async def set_build_method(self, method: str) -> dict:
        return await self._req("post", "/buildmethod", json={"method": method})

    async def set_auto_stand(self, on: bool) -> dict:
        return await self._req("post", "/autostand", json={"on": on})

    async def photo(self, player: str = "", stop: bool = False) -> dict:
        if stop:
            return await self._req("post", "/photo", json={"stop": True})
        return await self._req("post", "/photo", json={"player": player})

    async def set_task(self, task: str) -> dict:
        return await self._req("post", "/task", json={"task": task})

    async def build_status(self) -> dict:
        return await self._req("get", "/build")

    async def build(self, shape: str, block: str = "red_wool") -> dict:
        return await self._req("post", "/build", json={"shape": shape, "block": block})

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
class McGiveTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_give"
    description: str = (
        "给机器人一些方块/物品（创造模式下可直接获得，无需真实拥有）。"
        "用于让她能放置指定方块。block 用英文物品 id，如 stone、oak_planks、glass。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "block": {"type": "string", "description": "物品英文 id"},
            "count": {"type": "integer", "description": "数量，默认 1"},
        },
        "required": ["block"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_give(
            context.context.event, str(kwargs.get("block", "")), int(kwargs.get("count", 1) or 1)
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


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McModeTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_mode"
    description: str = (
        "切换机器人的行为模式：controlled=待命（只在你命令时行动）；"
        "free=自由活动（自己到处走动、像真人一样活动）。"
        "用户说‘自由活动/自己玩/去逛逛’就选 free，说‘待命/别乱动/回来/跟随模式’就选 controlled。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "description": "controlled 或 free", "enum": ["controlled", "free"]}
        },
        "required": ["mode"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_mode(context.context.event, str(kwargs.get("mode", "")))


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McMoveModeTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_move_mode"
    description: str = (
        "切换机器人“过来/前往”时的移动方式：auto=自动（优先走路，卡住或在飞时自动改飞）、"
        "walk=只走路、fly=只飞（创造模式）。当用户说‘用走的过来’‘别飞’‘用飞的’‘自动移动’时调用。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "description": "auto / walk / fly", "enum": ["auto", "walk", "fly"]}
        },
        "required": ["mode"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_move_mode(context.context.event, str(kwargs.get("mode", "")))


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McBuildTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_build"
    description: str = (
        "让机器人在游戏里建造立体建筑/雕像/景观。形状：heart=爱心、house=小房子、"
        "villa=现代别墅（导入投影原版，24×39 较大）、fountain=小喷泉、miku=初音未来雕像（导入原版）。"
        "用户说‘别墅/现代别墅’用 villa；‘爱心’heart；‘小房子’house；‘喷泉’fountain；‘初音/miku’miku。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "shape": {
                "type": "string",
                "description": "形状：heart / house / villa(现代别墅原版) / fountain(喷泉) / miku",
                "enum": ["heart", "house", "villa", "fountain", "miku"],
            },
            "block": {"type": "string", "description": "方块英文 id（可选，主要用于爱心/房子）"},
        },
        "required": ["shape"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_build(
            context.context.event,
            str(kwargs.get("shape", "heart")),
            str(kwargs.get("block", "")),
        )


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McBuildCancelTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_build_cancel"
    description: str = (
        "让正在建造中的机器人停下来（取消当前建造/雕像任务）。"
        "当用户说‘别建了’‘不建了’‘取消建造’‘停下’‘别造了’时调用。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {},
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_build_cancel(context.context.event)


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McBuildMethodTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_build_method"
    description: str = (
        "设置机器人建造方式：auto=自动（小建筑逐块建、大建筑用命令秒建并建完站旁边）、"
        "command=/fill 命令秒建（精确、无空洞，需要权限）、place=逐块放置。"
        "用户说‘用命令建/精准建/逐块建/自动建’时调用。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "method": {"type": "string", "description": "auto / command / place", "enum": ["auto", "command", "place"]}
        },
        "required": ["method"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_build_method(context.context.event, str(kwargs.get("method", "")))


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McPhotoTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_photo"
    description: str = (
        "让机器人进入合影模式：会自动跟随某玩家，靠近后同步视角一起看镜头。"
        "action=start 开始（用户说‘拍照/看镜头/合个影/茄子’时用），action=stop 结束（‘拍好了/不拍了’）。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "start 或 stop", "enum": ["start", "stop"]},
            "player": {"type": "string", "description": "跟随的玩家名（可选，默认主人）"},
        },
        "required": ["action"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_photo(
            context.context.event,
            str(kwargs.get("action", "")),
            str(kwargs.get("player", "")),
        )


@pydantic_dataclass(config=dict(arbitrary_types_allowed=True))
class McDoTaskTool(FunctionTool[AstrAgentContext]):
    name: str = "mc_do_task"
    description: str = (
        "让机器人自由活动时干指定的活：mine=挖矿、chop=砍树、farm=种地、stop=不指定（随便活动）。"
        "机器人会自动选用正确且够等级的工具。用户说‘去挖矿/去砍树/去种地/别干了’时调用。"
    )
    parameters: dict = Field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "mine / chop / farm / stop", "enum": ["mine", "chop", "farm", "stop"]}
        },
        "required": ["task"],
    })
    plugin: Any = Field(default=None)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        return await self.plugin.tool_do_task(context.context.event, str(kwargs.get("task", "")))


@register("astrbot_plugin_minecraft", "A3uracY", "让柚叶陪玩我的世界", "0.1.0")
class MinecraftPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.mc = McClient(config.get("api_base", DEFAULT_API_BASE))
        self._last_umo: str = ""
        self._poll_task: asyncio.Task | None = None
        self._bot_username: str = ""
        self._owner: str = ""
        self._bot_names: list[str] = ["Youo", "柚叶", "小柚"]
        self._persona_prompt: str = ""
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
                McGiveTool(plugin=self),
                McPlayersTool(plugin=self),
                McConnectTool(plugin=self),
                McModeTool(plugin=self),
                McMoveModeTool(plugin=self),
                McBuildTool(plugin=self),
                McBuildCancelTool(plugin=self),
                McBuildMethodTool(plugin=self),
                McPhotoTool(plugin=self),
                McDoTaskTool(plugin=self),
            )
            logger.info("[minecraft] 已注册 18 个 LLM 工具")
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
            logger.info("[minecraft] 已启动游戏聊天转发/对话轮询")
        self._last_umo = self.config.get("owner_umo", "") or ""
        await self._ensure_bot_info()

    async def _ensure_bot_info(self):
        try:
            cfg = await self.mc.get_config()
            self._bot_username = cfg.get("username", "") or self._bot_username
            self._owner = cfg.get("owner", "") or self._owner
        except Exception:  # noqa: BLE001
            pass

    async def _get_persona_prompt(self) -> str:
        if self._persona_prompt:
            return self._persona_prompt
        pid = self.config.get("persona_id", "柚叶")
        try:
            p = await self.context.persona_manager.get_persona(pid)
            self._persona_prompt = getattr(p, "system_prompt", "") or ""
        except Exception:  # noqa: BLE001
            self._persona_prompt = ""
        return self._persona_prompt

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
        extra = ""
        if s.get("building"):
            pr = s.get("progress") or {}
            if pr.get("total"):
                extra = f" 正在建造 {s['building']}（{pr.get('done', 0)}/{pr.get('total')}）～"
            else:
                extra = f" 正在建造 {s['building']}～"
        return (
            f"我在游戏里～（{'生存' if s.get('survival') else '创造'}模式） 坐标({p['x']},{p['y']},{p['z']})，"
            f"血量 {s.get('health')}，饥饿 {s.get('food')}，"
            f"手持 {s.get('held') or '空'}，在线玩家：{', '.join(s.get('players', [])) or '无'}。"
            f"背包：{s.get('inventory')}{extra}"
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
            if self.config.get("creative_mode", True):
                try:
                    await self.mc.give(block, 1)  # 创造模式先直接取物
                except Exception:  # noqa: BLE001
                    pass
            r = await self.mc.place(block, x, y, z)
            return "✅ " + str(r.get("message", "已放置"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 放置失败：{e}"

    async def tool_give(self, event: AstrMessageEvent, block: str, count: int) -> str:
        self._mark(event)
        if not block:
            return "要给她什么方块呀？"
        try:
            r = await self.mc.give(block, count)
            return "✅ " + str(r.get("message", "已获得"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 获取物品失败：{e}（创造模式下才可直接获得）"

    async def tool_mode(self, event: AstrMessageEvent, mode: str) -> str:
        self._mark(event)
        m = (mode or "").strip().lower()
        if m in ("free", "自由", "自由活动", "自由模式"):
            m = "free"
        elif m in ("controlled", "待命", "受控", "跟随模式", "回来"):
            m = "controlled"
        else:
            return "模式只能是 free（自由活动）或 controlled（待命）。"
        try:
            await self.mc.set_mode(m)
            return "✅ 已切换到" + (
                "自由活动模式（我会自己到处逛逛～）"
                if m == "free"
                else "待命模式（只在你命令时行动）"
            )
        except Exception as e:  # noqa: BLE001
            return f"❌ 切换模式失败：{e}"

    async def tool_move_mode(self, event: AstrMessageEvent, mode: str) -> str:
        self._mark(event)
        m = (mode or "").strip().lower()
        if m in ("auto", "自动", "自动移动"):
            m = "auto"
        elif m in ("walk", "走", "走路", "用走的"):
            m = "walk"
        elif m in ("fly", "飞", "飞行", "用飞的"):
            m = "fly"
        else:
            return "移动方式只能是 auto（自动）/ walk（走路）/ fly（飞行）。"
        try:
            await self.mc.set_move_mode(m)
            desc = {"auto": "自动（走不动才飞）", "walk": "只走路", "fly": "只飞行"}[m]
            return f"✅ 移动方式已切换为：{desc}"
        except Exception as e:  # noqa: BLE001
            return f"❌ 切换失败：{e}"

    async def tool_build(self, event: AstrMessageEvent, shape: str, block: str) -> str:
        self._mark(event)
        shape = (shape or "heart").strip().lower()
        block = (block or "").strip().lower()
        if not block:
            block = "oak_planks" if shape == "house" else "red_wool"
        try:
            r = await self.mc.build(shape, block)
            return "✅ " + str(r.get("message", "已建造"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 建造失败：{e}"

    async def tool_build_cancel(self, event: AstrMessageEvent) -> str:
        self._mark(event)
        try:
            r = await self.mc.cancel_build()
            return "✅ " + str(r.get("message", "已取消建造"))
        except Exception as e:  # noqa: BLE001
            return f"❌ 取消失败：{e}"

    async def tool_build_method(self, event: AstrMessageEvent, method: str) -> str:
        self._mark(event)
        m = (method or "").strip().lower()
        if m in ("auto", "自动", "自动选择", "智能"):
            m = "auto"
        elif m in ("command", "命令", "fill", "/fill", "精准", "秒建"):
            m = "command"
        elif m in ("place", "逐块", "放置", "一块块"):
            m = "place"
        else:
            return "建造方式只能是 auto（自动）/ command（命令/fill）/ place（逐块）。"
        try:
            await self.mc.set_build_method(m)
            desc = {
                "auto": "自动（小建筑逐块 / 大建筑命令+站旁边）",
                "command": "命令 /fill（精确秒建）",
                "place": "逐块放置",
            }[m]
            return "✅ 建造方式已切换为：" + desc
        except Exception as e:  # noqa: BLE001
            return f"❌ 切换失败：{e}"

    async def tool_photo(self, event: AstrMessageEvent, action: str = "", player: str = "") -> str:
        self._mark(event)
        a = (action or "").strip().lower()
        stop = a in ("stop", "off", "结束", "停", "停止", "别拍")
        try:
            r = await self.mc.photo(player, stop)
            return "✅ " + str(r.get("message", "ok"))
        except Exception as e:  # noqa: BLE001
            return f"❌ {e}"

    async def tool_do_task(self, event: AstrMessageEvent, task: str = "") -> str:
        self._mark(event)
        t = (task or "").strip().lower()
        if t in ("mine", "挖矿", "采矿", "挖石头"):
            t = "mine"
        elif t in ("chop", "砍树", "木头", "砍木头", "伐木"):
            t = "chop"
        elif t in ("farm", "种地", "种田", "种庄稼"):
            t = "farm"
        elif t in ("stop", "none", "停", "别干了", "不干", "随便"):
            t = ""
        else:
            return "任务只能是 mine（挖矿）/ chop（砍树）/ farm（种地）/ stop（不指定）。"
        try:
            r = await self.mc.set_task(t)
            return "✅ " + str(r.get("message", "ok"))
        except Exception as e:  # noqa: BLE001
            return f"❌ {e}"

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
                # 机器人进程重启后事件序号会归零；检测到就重置，避免漏消息
                if int(data.get("last", 0)) < last:
                    last = 0
                    data = await self.mc.events(0)
                for ev in data.get("events", []):
                    last = max(last, int(ev.get("seq", last)))
                    if ev.get("type") == "chat":
                        await self._handle_ingame_chat(ev)
                    await self._forward_event(ev)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(2)

    async def _handle_ingame_chat(self, ev: dict):
        """游戏内聊天 -> 本地 LLM -> 在游戏里回复。"""
        if not self.config.get("ingame_chat", True):
            return
        d = ev.get("data", {})
        username = str(d.get("username", ""))
        message = str(d.get("message", "")).strip()
        if not username or not message:
            return
        await self._ensure_bot_info()
        if username == self._bot_username:
            return
        names = self._bot_names + [self._bot_username]
        mentioned = any(n and n in message for n in names)
        if not (mentioned or (self._owner and username == self._owner)):
            return
        if GAME_ACTION_RE.search(message):
            return  # 动作类交给机器人自身关键词层
        prov = self.context.get_using_provider(umo=self._last_umo or None)
        if not prov:
            return
        try:
            persona = await self._get_persona_prompt()
            scene = (
                "你正在《我的世界》游戏里，通过聊天栏和玩家实时聊天。"
                "请用简短、自然、口语化的一两句话回复（不超过60字），"
                "不要输出换行、不要 markdown、不要括号动作。"
            )
            sys_prompt = (persona + "\n\n" + scene) if persona else scene
            resp = await prov.text_chat(
                prompt=f"{username} 在游戏里对你说：{message}",
                system_prompt=sys_prompt,
            )
            text = (resp.completion_text or "").strip().replace("\n", " ")
            if text:
                await self.mc.say(text[:180])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[minecraft] 游戏内聊天回复失败: {e}")

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
            elif action == "give":
                parts = arg.split()
                block = parts[0] if parts else ""
                cnt = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                yield event.plain_result(await self.tool_give(event, block, cnt))
            elif action == "mode":
                yield event.plain_result(await self.tool_mode(event, arg))
            elif action in ("movemode", "move"):
                yield event.plain_result(await self.tool_move_mode(event, arg))
            elif action in ("cancelbuild", "cancel", "stopbuild"):
                yield event.plain_result(await self.tool_build_cancel(event))
            elif action in ("buildmethod", "method"):
                yield event.plain_result(await self.tool_build_method(event, arg))
            elif action == "progress":
                try:
                    r = await self.mc.build_status()
                except Exception as e:  # noqa: BLE001
                    r = {"error": str(e)}
                if r.get("building"):
                    pr = r.get("progress") or {}
                    if pr.get("total"):
                        yield event.plain_result(
                            f"正在建造 {r['building']}：{pr.get('done', 0)}/{pr.get('total')}"
                        )
                    else:
                        yield event.plain_result(f"正在建造 {r['building']}…")
                else:
                    yield event.plain_result("现在没有在建造哦")
            elif action == "autostand":
                on = str(arg).strip().lower() not in ("off", "false", "0", "关", "否")
                try:
                    rr = await self.mc.set_auto_stand(on)
                    yield event.plain_result("✅ 建完自动站位：" + ("开" if rr.get("auto_stand") else "关"))
                except Exception as e:  # noqa: BLE001
                    yield event.plain_result(f"❌ {e}")
            elif action in ("photo", "合影", "拍照"):
                a = arg.strip()
                if a in ("stop", "off", "结束", "停", "停止", "不拍"):
                    yield event.plain_result(await self.tool_photo(event, "stop"))
                else:
                    yield event.plain_result(await self.tool_photo(event, "start", a))
            elif action in ("task", "干活", "任务"):
                yield event.plain_result(await self.tool_do_task(event, arg))
            elif action in ("bag", "inventory", "背包", "物品"):
                try:
                    s = await self.mc.status()
                    yield event.plain_result("🎒 背包：" + str(s.get("inventory", "空")))
                except Exception as e:  # noqa: BLE001
                    yield event.plain_result(f"❌ {e}")
            elif action == "build":
                parts = arg.split()
                first = parts[0].lower() if parts else ""
                if first in ("heart", "house", "villa", "fountain", "miku"):
                    yield event.plain_result(
                        await self.tool_build(event, first, parts[1] if len(parts) > 1 else "")
                    )
                else:
                    yield event.plain_result(await self.tool_build(event, "heart", first))
            else:
                yield event.plain_result(
                    "用法：/mc status|players|follow [玩家]|come [玩家]|stop|say 文本|give 方块 [数量]|bag(背包)|mode free|controlled|movemode auto|walk|fly|cancelbuild|buildmethod auto|command|place|progress|autostand on|off|photo [stop]|task mine|chop|farm|stop|build [方块]|connect 端口"
                )
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"❌ {e}")
        event.stop_event()
