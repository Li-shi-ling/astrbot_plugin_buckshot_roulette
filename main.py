import asyncio
import dataclasses
import json
import random
import re
from pathlib import Path
from typing import Any

from botpy.types import inline as qinline
from botpy.types.message import MarkdownPayload

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.config import AstrBotConfig

try:
    from .core.roulette_db import (
        RouletteDBManager,
        RouletteUserRepo,
        validate_display_name,
    )
    from .core.roulette_game import (
        ITEM_BEER,
        ITEM_CIGARETTE,
        ITEM_EXPIRED_MEDICINE,
        ITEM_HANDCUFFS,
        ITEM_INVERTER,
        ITEM_MAGNIFIER,
        ITEM_PHONE,
        ITEM_SAW,
        MAX_PLAYERS,
        RouletteGame,
        RouletteGameError,
        RoulettePlayer,
        RouletteSettings,
    )
except ImportError:
    import sys

    plugin_dir = Path(__file__).resolve().parent
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    from core.roulette_db import RouletteDBManager, RouletteUserRepo, validate_display_name
    from core.roulette_game import (
        ITEM_BEER,
        ITEM_CIGARETTE,
        ITEM_EXPIRED_MEDICINE,
        ITEM_HANDCUFFS,
        ITEM_INVERTER,
        ITEM_MAGNIFIER,
        ITEM_PHONE,
        ITEM_SAW,
        MAX_PLAYERS,
        RouletteGame,
        RouletteGameError,
        RoulettePlayer,
        RouletteSettings,
    )


QQOFFICIAL_PLATFORMS = {"qq_official", "qq_official_webhook"}
QQOFFICIAL_MESSAGE_EVENT_NAMES = {
    "QQOfficialMessageEvent",
    "QQOfficialWebhookMessageEvent",
}
QQOFFICIAL_MESSAGE_EVENT_MODULE_PREFIXES = (
    "astrbot.core.platform.sources.qqofficial.",
    "astrbot.core.platform.sources.qqofficial_webhook.",
)
BOT_AT_MARKER = "qq_official"
ROULETTE_COMMAND_PREFIX = "轮盘"
ROULETTE_NO_TARGET_BUTTON_ITEMS = (
    ITEM_BEER,
    ITEM_CIGARETTE,
    ITEM_SAW,
    ITEM_HANDCUFFS,
    ITEM_MAGNIFIER,
    ITEM_EXPIRED_MEDICINE,
    ITEM_PHONE,
    ITEM_INVERTER,
)


def _build_roulette_button(
    button_id: str,
    label: str,
    data: str,
) -> qinline.Button:
    return {
        "id": button_id,
        "render_data": {
            "label": label,
            "visited_label": label,
            "style": 1,
        },
        "action": {
            "type": 2,
            "permission": {
                "type": 2,
            },
            "data": data,
            "reply": True,
            "enter": False,
            "unsupport_tips": "当前客户端不支持该按钮。",
        },
    }


def _short_button_name(name: str, max_len: int = 5) -> str:
    if len(name) <= max_len:
        return name
    return name[:max_len]


def _build_roulette_keyboard(game: RouletteGame | None) -> qinline.Keyboard:
    if not game:
        return {"content": {"rows": []}}

    if game.phase == "waiting":
        return {
            "content": {
                "rows": [
                    {
                        "buttons": [
                            _build_roulette_button("roulette_join", "加入", "轮盘加入"),
                            _build_roulette_button("roulette_leave", "退出", "轮盘退出"),
                            _build_roulette_button("roulette_start", "开始", "轮盘开始"),
                        ]
                    }
                ]
            }
        }

    rows: list[dict[str, list[qinline.Button]]] = [
        {
            "buttons": [
                _build_roulette_button("roulette_shoot_self", "打自己", "轮盘开枪 自己"),
                _build_roulette_button("roulette_status", "状态", "轮盘状态"),
                _build_roulette_button("roulette_end", "结束", "轮盘结束"),
            ]
        }
    ]

    target_buttons: list[qinline.Button] = []
    current_user_id = game.current_player().user_id
    for number, player in enumerate(game.players, start=1):
        if not player.alive or player.user_id == current_user_id:
            continue
        label = f"{number}.{_short_button_name(player.display_name)}"
        target_buttons.append(
            _build_roulette_button(
                f"roulette_target_{number}",
                label,
                f"轮盘开枪 {number}",
            )
        )
    for offset in range(0, min(len(target_buttons), 10), 5):
        rows.append({"buttons": target_buttons[offset : offset + 5]})

    current_items = set(game.current_player().items)
    item_buttons: list[qinline.Button] = []
    for item_name in ROULETTE_NO_TARGET_BUTTON_ITEMS:
        if item_name in current_items:
            item_buttons.append(
                _build_roulette_button(
                    f"roulette_item_{item_name}",
                    item_name,
                    f"轮盘道具 {item_name}",
                )
            )
    for offset in range(0, len(item_buttons), 5):
        if len(rows) >= 5:
            break
        rows.append({"buttons": item_buttons[offset : offset + 5]})

    return {"content": {"rows": rows[:5]}}


def _build_roulette_message_payload(
    text: str,
    game: RouletteGame | None = None,
    *,
    with_keyboard: bool = True,
    settings_keyboard: bool = False,
    handcuffs_target_keyboard: bool = False,
    settings: RouletteSettings | None = None,
) -> dict[str, Any]:
    keyboard = None
    if with_keyboard:
        if handcuffs_target_keyboard:
            keyboard = _build_roulette_handcuffs_target_keyboard(game)
        elif settings_keyboard:
            keyboard = _build_roulette_settings_keyboard(settings or (game.settings if game else None))
        else:
            keyboard = _build_roulette_keyboard(game)
    return {
        "msg_type": 2,
        "markdown": MarkdownPayload(content=_format_roulette_markdown(text or "轮盘")),
        "keyboard": keyboard,
    }


def _build_roulette_handcuffs_target_keyboard(game: RouletteGame | None) -> qinline.Keyboard:
    if not game or game.phase != "playing":
        return {"content": {"rows": []}}
    current_user_id = game.current_player().user_id
    buttons: list[qinline.Button] = []
    for number, player in enumerate(game.players, start=1):
        if not player.alive or player.user_id == current_user_id:
            continue
        label = f"{number}.{_short_button_name(player.display_name)}"
        buttons.append(
            _build_roulette_button(
                f"roulette_handcuffs_target_{number}",
                label,
                f"轮盘道具 手铐 {number}",
            )
        )
    buttons.append(_build_roulette_button("roulette_handcuffs_cancel", "取消", "轮盘状态"))
    rows = [{"buttons": buttons[offset : offset + 5]} for offset in range(0, len(buttons), 5)]
    return {"content": {"rows": rows[:5]}}


def _build_roulette_settings_keyboard(settings: RouletteSettings | None) -> qinline.Keyboard:
    settings = settings or RouletteSettings()
    settings.normalize()
    random_shell_target = "否" if settings.random_shell_count else "是"
    random_item_target = "否" if settings.random_item_count else "是"
    random_hp_target = "否" if settings.random_hp else "是"
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _build_roulette_button("roulette_set_shell_max", "子弹上限", "轮盘设置 子弹上限 [数量]"),
                        _build_roulette_button("roulette_set_shell_min", "子弹下限", "轮盘设置 子弹下限 [数量]"),
                        _build_roulette_button("roulette_set_item_max", "道具刷新上限", "轮盘设置 道具刷新上限 [数量]"),
                        _build_roulette_button("roulette_set_item_min", "道具刷新下限", "轮盘设置 道具刷新下限 [数量]"),
                    ]
                },
                {
                    "buttons": [
                        _build_roulette_button("roulette_set_inventory_max", "持有上限", "轮盘设置 道具持有上限 [数量]"),
                        _build_roulette_button("roulette_set_hp_max", "血量上限", "轮盘设置 血量上限 [数量]"),
                        _build_roulette_button("roulette_set_hp_min", "血量下限", "轮盘设置 血量下限 [数量]"),
                    ]
                },
                {
                    "buttons": [
                        _build_roulette_button(
                            "roulette_set_random_shell",
                            f"随机子弹：{random_shell_target}",
                            f"轮盘设置 随机子弹 {random_shell_target}",
                        ),
                        _build_roulette_button(
                            "roulette_set_random_item",
                            f"随机道具：{random_item_target}",
                            f"轮盘设置 随机道具 {random_item_target}",
                        ),
                        _build_roulette_button(
                            "roulette_set_random_hp",
                            f"随机血量：{random_hp_target}",
                            f"轮盘设置 随机血量 {random_hp_target}",
                        ),
                    ]
                },
                {
                    "buttons": [
                        _build_roulette_button("roulette_create", "轮盘创建", "轮盘创建"),
                        _build_roulette_button("roulette_start", "开始", "轮盘开始"),
                    ]
                },
            ]
        }
    }


def _build_roulette_menu_keyboard() -> qinline.Keyboard:
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _build_roulette_button("roulette_menu_create", "房间创建", "轮盘创建"),
                        _build_roulette_button("roulette_menu_settings", "游戏设置", "轮盘设置"),
                    ]
                },
                {
                    "buttons": [
                        _build_roulette_button("roulette_menu_help", "轮盘帮助", "轮盘帮助"),
                        _build_roulette_button("roulette_menu_items", "轮盘道具查看", "轮盘道具查看"),
                    ]
                }
            ]
        }
    }


def _build_roulette_menu_payload() -> dict[str, Any]:
    return {
        "msg_type": 2,
        "markdown": MarkdownPayload(content=_format_roulette_markdown("轮盘菜单")),
        "keyboard": _build_roulette_menu_keyboard(),
    }


def _format_roulette_markdown(text: str) -> str:
    if not str(text or "").strip() or str(text).strip() == "轮盘":
        return "轮盘"
    lines = str(text).splitlines()
    formatted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted.append("")
            continue
        if stripped in {
            "恶魔轮盘状态",
            "恶魔轮盘玩法帮助：",
            "恶魔轮盘玩法帮助:",
            "道具效果：",
            "道具效果:",
            "轮盘设置",
            "轮盘菜单",
            "选择手铐目标",
        }:
            formatted.append(f"## {stripped.rstrip('：:')}")
        elif stripped.startswith("当前弹队列："):
            formatted.append(f"**当前弹队列**：{stripped.removeprefix('当前弹队列：')}")
        elif stripped.startswith("阶段："):
            formatted.append(f"**阶段**：{stripped.removeprefix('阶段：')}")
        elif stripped.startswith("当前行动："):
            formatted.append(f"> **当前行动**：{stripped.removeprefix('当前行动：')}")
        elif stripped.startswith("轮到 "):
            formatted.append(f"> **{stripped}**")
        elif re.match(r"^\d+\. ", stripped):
            number, rest = stripped.split(". ", 1)
            formatted.append(f"- `{number}` {rest}")
        elif stripped.startswith("轮盘"):
            formatted.append(f"- `{stripped}`")
        elif stripped.startswith("提示："):
            formatted.append(f"> {stripped}")
        else:
            formatted.append(stripped)
    return "\n".join(formatted)


def _first_non_empty_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return None


def _extract_message_reference_id(raw_message: Any, message_obj: Any) -> str | None:
    return _first_non_empty_str(
        getattr(raw_message, "id", None),
        getattr(message_obj, "message_id", None),
    )


def _add_passive_reply_context(
    payload: dict[str, Any],
    *,
    msg_id: str | None = None,
    event_id: str | None = None,
    msg_seq: int | None = None,
) -> dict[str, Any]:
    if msg_id:
        payload["msg_id"] = msg_id
    elif event_id:
        payload["event_id"] = event_id
    if payload.get("msg_id") or payload.get("event_id"):
        payload["msg_seq"] = msg_seq if msg_seq is not None else random.randint(1, 10000)
    return payload


def _debug_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _is_qqofficial_message_event(event: AstrMessageEvent) -> bool:
    event_type = type(event)
    module_name = event_type.__module__.lower()
    return (
        event_type.__name__ in QQOFFICIAL_MESSAGE_EVENT_NAMES
        and module_name.startswith(QQOFFICIAL_MESSAGE_EVENT_MODULE_PREFIXES)
    )


@register(
    "astrbot_plugin_buckshot_roulette",
    "lishining,Codex",
    "QQOfficial 群聊多人无庄家恶魔轮盘插件",
    "1.2.5",
)
class BuckshotRoulettePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.roulette_settings = self._load_roulette_settings_from_config()
        self.roulette_db_path = Path(StarTools.get_data_dir()) / "roulette.db"
        self.roulette_db = RouletteDBManager(self.roulette_db_path)
        self.roulette_user_repo = RouletteUserRepo(self.roulette_db)
        self.roulette_games: dict[str, RouletteGame] = {}
        self.roulette_locks: dict[str, asyncio.Lock] = {}

    async def initialize(self):
        await self.roulette_db.init_db()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘")
    async def roulette_help_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘帮助")
    async def roulette_help_named_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘道具查看")
    async def roulette_item_help_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘菜单")
    async def roulette_menu_command(self, event: AstrMessageEvent):
        async for result in self._handle_roulette_menu(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘绑定")
    async def roulette_bind_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘我的名字")
    async def roulette_my_name_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘改名")
    async def roulette_rename_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘创建")
    async def roulette_create_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘加入")
    async def roulette_join_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘退出")
    async def roulette_leave_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘设置")
    async def roulette_settings_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘开始")
    async def roulette_start_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘状态")
    async def roulette_status_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘结束")
    async def roulette_end_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘开枪")
    async def roulette_shoot_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘道具")
    async def roulette_item_command(self, event: AstrMessageEvent):
        async for result in self._handle_registered_roulette(event):
            yield result

    async def _handle_roulette_menu(self, event: AstrMessageEvent):
        command_text = self._extract_roulette_command_text(event)
        if command_text is None:
            return
        async for result in self._send_qqofficial_group_markdown(
            event,
            command_name="roulette_menu",
            payload=_build_roulette_menu_payload(),
        ):
            yield result
        event.stop_event()

    async def _handle_registered_roulette(self, event: AstrMessageEvent):
        command_text = self._extract_roulette_command_text(event)
        if command_text is None:
            return

        context = self._extract_roulette_group_context(event)
        if context is None:
            yield event.plain_result("未能识别 QQOfficial 群聊身份，无法使用轮盘。")
            event.stop_event()
            return

        group_openid, platform_user_id = context
        session_id = self._roulette_session_id(event, group_openid)
        lock = self.roulette_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            try:
                message, game, with_keyboard = await self._handle_roulette_command(
                    command_text,
                    group_openid=group_openid,
                    platform_user_id=platform_user_id,
                    session_id=session_id,
                    event=event,
                )
            except (RouletteGameError, ValueError) as exc:
                message = str(exc)
                game = self.roulette_games.get(session_id)
                with_keyboard = False
            except Exception as exc:
                logger.exception("[BuckshotRoulette] 轮盘指令处理失败: %s", exc)
                message = f"轮盘处理失败：{exc}"
                game = self.roulette_games.get(session_id)
                with_keyboard = False

        async for result in self._send_qqofficial_group_markdown(
            event,
            command_name="roulette",
            payload=_build_roulette_message_payload(
                message,
                game,
                with_keyboard=with_keyboard,
                settings_keyboard=self._is_roulette_settings_command(command_text),
                handcuffs_target_keyboard=self._is_roulette_handcuffs_select_command(command_text),
                settings=self.roulette_settings,
            ),
        ):
            yield result
        event.stop_event()

    async def _handle_roulette_command(
        self,
        command_text: str,
        *,
        group_openid: str,
        platform_user_id: str,
        session_id: str,
        event: AstrMessageEvent,
    ) -> tuple[str, RouletteGame | None, bool]:
        stripped_command = command_text.strip()
        if not stripped_command.startswith(ROULETTE_COMMAND_PREFIX):
            return self._roulette_help_text(), self.roulette_games.get(session_id), False
        remainder = stripped_command[len(ROULETTE_COMMAND_PREFIX) :].strip()
        parts = remainder.split()
        action = parts[0] if parts else "帮助"
        args = parts[1:]

        if action in {"帮助", "help"}:
            return self._roulette_help_text(), self.roulette_games.get(session_id), False
        if action == "道具查看":
            return self._roulette_item_help_text(), self.roulette_games.get(session_id), False
        if action in {"绑定", "改名"}:
            if not args:
                raise ValueError(f"请提供昵称，例如：轮盘{action} 玩家名")
            display_name = validate_display_name(" ".join(args))
            profile = await self.roulette_user_repo.upsert_profile(
                group_openid,
                platform_user_id,
                display_name,
            )
            game = self.roulette_games.get(session_id)
            if game:
                player = game.get_player(platform_user_id)
                if player:
                    player.display_name = profile.display_name
            return f"已绑定轮盘昵称：{profile.display_name}", game, False
        if action == "我的名字":
            profile = await self.roulette_user_repo.get_profile(
                group_openid,
                platform_user_id,
            )
            if profile:
                return f"你当前的轮盘昵称是：{profile.display_name}", self.roulette_games.get(session_id), False
            fallback = await self.roulette_user_repo.resolve_display_name(
                group_openid,
                platform_user_id,
            )
            return f"你还没有绑定昵称，当前会显示为：{fallback}", self.roulette_games.get(session_id), False

        if action == "设置":
            message = self._handle_roulette_settings_command(self.roulette_settings, args)
            self._save_roulette_settings_to_config()
            game = self.roulette_games.get(session_id)
            if game and game.phase == "waiting":
                game.settings = dataclasses.replace(self.roulette_settings)
            return message, game, True

        if action == "创建":
            if session_id in self.roulette_games and self.roulette_games[session_id].phase != "ended":
                raise RouletteGameError("本群已经有一局轮盘。")
            display_name = await self.roulette_user_repo.resolve_display_name(
                group_openid,
                platform_user_id,
            )
            game = RouletteGame(
                group_openid=group_openid,
                owner_id=platform_user_id,
                settings=dataclasses.replace(self.roulette_settings),
            )
            game.add_player(platform_user_id, display_name)
            self.roulette_games[session_id] = game
            hint = self._roulette_bind_hint(display_name, platform_user_id)
            return (
                f"{display_name} 创建了恶魔轮盘房间（1/{MAX_PLAYERS}）。\n"
                "发送“轮盘加入”加入，房主发送“轮盘开始”开始。\n"
                f"{hint}",
                game,
                True,
            )

        game = self.roulette_games.get(session_id)
        if not game or game.phase == "ended":
            raise RouletteGameError("本群当前没有进行中的轮盘。请先发送：轮盘创建")

        if action == "加入":
            display_name = await self.roulette_user_repo.resolve_display_name(
                group_openid,
                platform_user_id,
            )
            result = game.add_player(platform_user_id, display_name)
            hint = self._roulette_bind_hint(display_name, platform_user_id)
            return f"{result.message}\n{hint}", game, True
        if action == "退出":
            if game.phase != "waiting":
                raise RouletteGameError("本局已经开始，不能退出房间。")
            player = game.get_player(platform_user_id)
            if not player:
                raise RouletteGameError("你还没有加入本局。")
            player_name = player.display_name
            game.players = [
                existing
                for existing in game.players
                if existing.user_id != platform_user_id
            ]
            if not game.players:
                self.roulette_games.pop(session_id, None)
                return f"{player_name} 退出了房间，房间已关闭。", None, False
            if game.owner_id == platform_user_id:
                game.owner_id = game.players[0].user_id
            return f"{player_name} 退出了房间（{len(game.players)}/{MAX_PLAYERS}）。", game, True
        if action == "开始":
            if platform_user_id != game.owner_id:
                return "只有房主可以开始本局。", game, False
            result = game.start(platform_user_id)
            return f"{result.message}\n\n{game.format_status()}", game, True
        if action == "状态":
            return game.format_status(), game, True
        if action == "结束":
            if not self._can_end_roulette(event, game, platform_user_id):
                raise RouletteGameError("只有房主或管理员可以结束本局。")
            self.roulette_games.pop(session_id, None)
            return "本群轮盘已结束。", None, False
        if action == "开枪":
            if not args:
                raise RouletteGameError("请指定目标编号或“自己”，例如：轮盘开枪 自己")
            result = game.shoot(platform_user_id, args[0])
            if result.ended:
                self.roulette_games.pop(session_id, None)
            return (
                f"{result.message}\n\n{game.format_status()}",
                game if not result.ended else None,
                not result.ended,
            )
        if action == "道具":
            if not args:
                raise RouletteGameError("请指定道具名，例如：轮盘道具 啤酒")
            target_number = None
            item_name = game.normalize_item_name(args[0])
            if item_name == ITEM_HANDCUFFS and len(args) < 2:
                actor = game.require_playing_actor(platform_user_id)
                if ITEM_HANDCUFFS not in actor.items:
                    raise RouletteGameError(f"你没有道具：{ITEM_HANDCUFFS}")
                return self._roulette_handcuffs_target_text(game), game, True
            if len(args) >= 2:
                try:
                    target_number = int(args[1])
                except ValueError as exc:
                    raise RouletteGameError("目标请使用玩家编号。") from exc
            result = game.use_item(platform_user_id, item_name, target_number)
            if result.ended:
                self.roulette_games.pop(session_id, None)
            return (
                f"{result.message}\n\n{game.format_status()}",
                game if not result.ended else None,
                not result.ended,
            )

        raise RouletteGameError("未知轮盘指令。发送“轮盘帮助”查看用法。")

    async def _send_qqofficial_group_markdown(
        self,
        event: AstrMessageEvent,
        *,
        command_name: str,
        payload: dict[str, Any],
    ):
        if not _is_qqofficial_message_event(event):
            yield event.plain_result("该指令仅支持 QQOfficial 群聊。")
            return
        raw_message = getattr(event.message_obj, "raw_message", None)
        group_openid = self._extract_group_openid(event)
        if raw_message is None or not group_openid:
            yield event.plain_result(payload["markdown"]["content"])
            return
        _add_passive_reply_context(
            payload,
            msg_id=_extract_message_reference_id(raw_message, event.message_obj),
            msg_seq=getattr(raw_message, "msg_seq", None),
        )
        logger.info(
            "[BuckshotRoulette] %s 发送轮盘群聊 payload: %s",
            command_name,
            _debug_json(payload),
        )
        try:
            await event.bot.api.post_group_message(
                group_openid=group_openid,
                **payload,
            )
        except Exception as exc:
            logger.exception("[BuckshotRoulette] %s 发送轮盘消息失败: %s", command_name, exc)
            yield event.plain_result(f"发送轮盘消息失败：{exc}")

    def _extract_roulette_command_text(self, event: AstrMessageEvent) -> str | None:
        if event.get_platform_name() not in QQOFFICIAL_PLATFORMS:
            return None
        if not self._starts_with_bot_mention(event):
            return None
        text = event.get_message_str() or ""
        stripped = text.strip()
        if stripped.startswith(ROULETTE_COMMAND_PREFIX):
            return stripped
        return None

    def _is_roulette_keyboardless_error_command(self, command_text: str) -> bool:
        stripped = str(command_text or "").strip()
        if not stripped.startswith(ROULETTE_COMMAND_PREFIX):
            return False
        remainder = stripped[len(ROULETTE_COMMAND_PREFIX) :].strip()
        parts = remainder.split()
        action = parts[0] if parts else ""
        return action in {"绑定", "改名", "退出"}

    def _is_roulette_settings_command(self, command_text: str) -> bool:
        stripped = str(command_text or "").strip()
        if not stripped.startswith(ROULETTE_COMMAND_PREFIX):
            return False
        remainder = stripped[len(ROULETTE_COMMAND_PREFIX) :].strip()
        parts = remainder.split()
        return bool(parts) and parts[0] == "设置"

    def _is_roulette_handcuffs_select_command(self, command_text: str) -> bool:
        stripped = str(command_text or "").strip()
        if not stripped.startswith(ROULETTE_COMMAND_PREFIX):
            return False
        parts = stripped[len(ROULETTE_COMMAND_PREFIX) :].strip().split()
        if len(parts) != 2 or parts[0] != "道具":
            return False
        return parts[1] in {"手铐", "铐"}

    def _extract_roulette_group_context(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, str] | None:
        group_openid = self._extract_group_openid(event)
        platform_user_id = self._extract_platform_user_id(event)
        if not group_openid or not platform_user_id:
            logger.warning(
                "[BuckshotRoulette] 轮盘身份解析失败: group_openid=%r, platform_user_id=%r",
                group_openid,
                platform_user_id,
            )
            return None
        return group_openid, platform_user_id

    def _extract_group_openid(self, event: AstrMessageEvent) -> str | None:
        raw_message = getattr(event.message_obj, "raw_message", None)
        return _first_non_empty_str(
            getattr(raw_message, "group_openid", None),
            getattr(raw_message, "group_id", None),
            event.get_group_id() if hasattr(event, "get_group_id") else None,
        )

    def _extract_platform_user_id(self, event: AstrMessageEvent) -> str | None:
        raw_message = getattr(event.message_obj, "raw_message", None)
        author = getattr(raw_message, "author", None)
        return _first_non_empty_str(
            getattr(author, "member_openid", None),
            getattr(raw_message, "member_openid", None),
            getattr(author, "user_openid", None),
            event.get_sender_id() if hasattr(event, "get_sender_id") else None,
        )

    def _roulette_session_id(self, event: AstrMessageEvent, group_openid: str) -> str:
        platform = event.get_platform_name() if hasattr(event, "get_platform_name") else "qq_official"
        return f"{platform}:{group_openid}"

    def _roulette_bind_hint(self, display_name: str, platform_user_id: str) -> str:
        if display_name == f"玩家_{platform_user_id[-6:]}":
            return "提示：可发送“轮盘绑定 昵称”设置群内显示名。"
        return ""

    def _roulette_help_text(self) -> str:
        return (
            "恶魔轮盘玩法帮助：\n"
            "轮盘绑定 昵称 / 轮盘我的名字 / 轮盘改名 新昵称\n"
            "轮盘创建 / 轮盘加入 / 轮盘退出 / 轮盘设置 / 轮盘开始 / 轮盘状态 / 轮盘结束\n"
            "轮盘开枪 自己 / 轮盘开枪 编号\n"
            "轮盘道具 啤酒|香烟|锯子|放大镜|过期药|电话|转变器 / 轮盘道具 手铐 编号\n"
            "发送“轮盘道具查看”查看全部道具效果。"
        )

    def _roulette_item_help_text(self) -> str:
        return (
            "道具效果：\n"
            "啤酒：移除当前第一发子弹，并公开它的类型。\n"
            "香烟：恢复 1 点血量，但不能超过最大血量。\n"
            "锯子：下一发实弹伤害增加 1；若为空弹则效果消失。\n"
            "手铐：指定一名其他玩家，使其跳过下一次行动。\n"
            "放大镜：查看当前第一发是实弹还是空弹。\n"
            "过期药：随机恢复 2 点血量或失去 1 点血量。\n"
            "电话：随机查看弹队列中某一发子弹的类型。\n"
            "转变器：反转当前第一发子弹的类型。"
        )

    def _handle_roulette_settings_command(self, settings: RouletteSettings, args: list[str]) -> str:
        settings.normalize()
        if not args:
            return self._roulette_settings_text(settings)

        field = args[0]
        value = args[1] if len(args) >= 2 else None
        if field in {"子弹上限", "子弹下限", "道具数量", "道具刷新上限", "道具刷新下限", "道具持有上限", "血量上限", "血量下限"}:
            if value is None or value == "[数量]":
                raise RouletteGameError(f"请把 [数量] 改成数字，例如：轮盘设置 {field} 4")
            try:
                number = int(value)
            except ValueError as exc:
                raise RouletteGameError("设置数量必须是整数。") from exc
            if field == "子弹上限":
                settings.shell_count_max = max(2, number)
            elif field == "子弹下限":
                settings.shell_count_min = max(2, number)
            elif field in {"道具数量", "道具刷新上限"}:
                settings.item_count_max = max(0, number)
            elif field == "道具刷新下限":
                settings.item_count_min = max(0, number)
            elif field == "道具持有上限":
                settings.item_inventory_max = max(0, min(20, number))
            elif field == "血量上限":
                settings.hp_max = max(1, number)
            elif field == "血量下限":
                settings.hp_min = max(1, number)
            settings.normalize()
            return f"已更新：{field} = {number}\n\n{self._roulette_settings_text(settings)}"

        if field in {"随机子弹", "随机道具", "随机血量"}:
            if value not in {"是", "否"}:
                raise RouletteGameError(f"请使用：轮盘设置 {field} 是/否")
            enabled = value == "是"
            if field == "随机子弹":
                settings.random_shell_count = enabled
            elif field == "随机道具":
                settings.random_item_count = enabled
            else:
                settings.random_hp = enabled
            settings.normalize()
            return f"已更新：{field} = {value}\n\n{self._roulette_settings_text(settings)}"

        raise RouletteGameError("未知设置项。发送“轮盘设置”查看可用设置。")

    def _roulette_settings_text(self, settings: RouletteSettings) -> str:
        settings.normalize()
        return (
            "轮盘设置\n"
            f"子弹上限：{settings.shell_count_max}\n"
            f"子弹下限：{settings.shell_count_min}\n"
            f"随机子弹数：{'是' if settings.random_shell_count else '否'}\n"
            f"道具刷新上限：{settings.item_count_max}\n"
            f"道具刷新下限：{settings.item_count_min}\n"
            f"随机道具刷新数：{'是' if settings.random_item_count else '否'}\n"
            f"道具持有上限：{settings.item_inventory_max}\n"
            f"血量上限：{settings.hp_max}\n"
            f"血量下限：{settings.hp_min}\n"
            f"随机开局血量：{'是' if settings.random_hp else '否'}"
        )

    def _roulette_handcuffs_target_text(self, game: RouletteGame) -> str:
        current_user_id = game.current_player().user_id
        lines = ["选择手铐目标"]
        for index, player in enumerate(game.players, start=1):
            if not player.alive or player.user_id == current_user_id:
                continue
            lines.append(f"{index}. {player.display_name} | HP {player.hp}/{player.max_hp}")
        lines.append("也可以发送：轮盘道具 手铐 [目标编号]")
        return "\n".join(lines)

    def _load_roulette_settings_from_config(self) -> RouletteSettings:
        raw = self._get_config_section("roulette_settings")
        settings = RouletteSettings(
            shell_count_max=self._config_int(raw, "shell_count_max", 4),
            shell_count_min=self._config_int(raw, "shell_count_min", 2),
            random_shell_count=self._config_bool(raw, "random_shell_count", False),
            item_count_max=self._config_int(
                raw,
                "item_count_max",
                self._config_int(raw, "item_count_per_reload", 1),
            ),
            item_count_min=self._config_int(raw, "item_count_min", 1),
            random_item_count=self._config_bool(raw, "random_item_count", False),
            item_inventory_max=self._config_int(raw, "item_inventory_max", 8),
            hp_max=self._config_int(raw, "hp_max", 2),
            hp_min=self._config_int(raw, "hp_min", 1),
            random_hp=self._config_bool(raw, "random_hp", False),
        )
        settings.normalize()
        return settings

    def _save_roulette_settings_to_config(self) -> None:
        data = {
            "shell_count_max": self.roulette_settings.shell_count_max,
            "shell_count_min": self.roulette_settings.shell_count_min,
            "random_shell_count": self.roulette_settings.random_shell_count,
            "item_count_max": self.roulette_settings.item_count_max,
            "item_count_min": self.roulette_settings.item_count_min,
            "random_item_count": self.roulette_settings.random_item_count,
            "item_inventory_max": self.roulette_settings.item_inventory_max,
            "hp_max": self.roulette_settings.hp_max,
            "hp_min": self.roulette_settings.hp_min,
            "random_hp": self.roulette_settings.random_hp,
        }
        try:
            self.config["roulette_settings"] = data
            if hasattr(self.config, "save_config"):
                self.config.save_config()
        except Exception:
            logger.warning("[BuckshotRoulette] 无法写回轮盘设置到插件配置对象。")

    def _get_config_section(self, key: str) -> dict[str, Any]:
        section = self.config.get(key, {}) if hasattr(self.config, "get") else {}
        return section if isinstance(section, dict) else {}

    def _config_int(self, section: dict[str, Any], key: str, default: int) -> int:
        try:
            return int(section.get(key, default))
        except (TypeError, ValueError):
            return default

    def _config_bool(self, section: dict[str, Any], key: str, default: bool) -> bool:
        value = section.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "是"}
        return bool(value)

    def _can_end_roulette(
        self,
        event: AstrMessageEvent,
        game: RouletteGame,
        platform_user_id: str,
    ) -> bool:
        if platform_user_id == game.owner_id:
            return True
        for attr in ("is_admin", "is_group_admin", "is_group_owner"):
            checker = getattr(event, attr, None)
            if callable(checker):
                try:
                    if checker():
                        return True
                except TypeError:
                    continue
            elif checker:
                return True
        return False

    def _starts_with_bot_mention(self, event: AstrMessageEvent) -> bool:
        for component in event.get_messages():
            if isinstance(component, Comp.Plain) and not component.text.strip():
                continue
            return self._is_bot_at(component, event)
        return False

    def _is_bot_at(self, component: Any, event: AstrMessageEvent) -> bool:
        if not isinstance(component, Comp.At):
            return False
        mentioned_id = str(component.qq).strip()
        if mentioned_id.lower() == BOT_AT_MARKER:
            return True
        return mentioned_id in self._get_bot_self_ids(event)

    def _get_bot_self_ids(self, event: AstrMessageEvent) -> set[str]:
        candidates = {
            str(getattr(event.message_obj, "self_id", "") or "").strip(),
            str(event.get_self_id() if hasattr(event, "get_self_id") else "" or "").strip(),
        }
        raw_message = getattr(event.message_obj, "raw_message", None)
        bot = getattr(raw_message, "bot", None)
        if bot is not None:
            candidates.add(str(getattr(bot, "id", "") or "").strip())
        return {candidate for candidate in candidates if candidate}

    async def terminate(self):
        await self.roulette_db.close()
