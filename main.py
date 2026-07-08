import asyncio
import dataclasses
import hashlib
import json
import os
import random
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import botpy.message as botpy_message
from botpy.interaction import Interaction
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
BOX_COMMAND_PATTERN = re.compile(r"^\s*盒\s+(\d+)\s*$")
BUTTON_A_ID = "qqofficial_btn_a"
BUTTON_B_ID = "qqofficial_btn_b"
BUTTON_A_DATA = "qqofficial_button_a"
BUTTON_B_DATA = "qqofficial_button_b"
CALLBACK_BUTTON_A_ID = "qqofficial_callback_btn_a"
CALLBACK_BUTTON_B_ID = "qqofficial_callback_btn_b"
CALLBACK_BUTTON_A_DATA = "callback_button_a"
CALLBACK_BUTTON_B_DATA = "callback_button_b"
BUTTON_PROMPT = "请选择一个按钮"
CALLBACK_BUTTON_PROMPT = "请选择一个回调按钮"
BUTTON_A_LABEL = "按钮 A"
BUTTON_B_LABEL = "按钮 B"
CALLBACK_BUTTON_A_LABEL = "回调 A"
CALLBACK_BUTTON_B_LABEL = "回调 B"
BUTTON_A_VISITED_LABEL = "已按下 A"
BUTTON_B_VISITED_LABEL = "已按下 B"
CALLBACK_BUTTON_A_VISITED_LABEL = "已回调 A"
CALLBACK_BUTTON_B_VISITED_LABEL = "已回调 B"
QQOFFICIAL_INTERACTION_INTENT = 1 << 26
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

TENCENT_MAP_API_BASE = "https://apis.map.qq.com"
TENCENT_MAP_PLACE_SEARCH_PATH = "/ws/place/v1/search"
TENCENT_MAP_STATIC_PATH = "/ws/staticmap/v2/"
DEFAULT_PLACE_KEYWORDS = [
    "公园",
    "商场",
    "酒店",
    "学校",
    "医院",
    "景点",
    "美食",
    "超市",
]

MAX_DUMP_DEPTH = 8
MAX_TEXT_CHUNK_SIZE = 1800
SENSITIVE_KEYWORDS = (
    "secret",
    "token",
    "authorization",
    "password",
    "passwd",
    "cookie",
    "session_key",
)
SKIPPED_EVENT_FIELDS = {
    "bot",
    "context",
    "ctx",
    "message_obj",
    "platform",
    "platform_meta",
    "session",
    "span",
    "trace",
}


@dataclass(frozen=True)
class GeneratedLocation:
    address: str
    map_url: str | None = None


@dataclass(frozen=True)
class QQOfficialButtonSpec:
    button_id: str
    label: str
    visited_label: str
    data: str


@dataclass(frozen=True)
class QQOfficialInteractionContext:
    interaction_id: str
    scene: str | None
    chat_type: int | None
    user_openid: str | None = None
    group_openid: str | None = None
    group_member_openid: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    button_id: str | None = None
    button_data: str | None = None
    message_id: str | None = None


class QQOfficialEventPlatformProxy:
    def __init__(self, client: Any, platform_name: str | None):
        self.client = client
        self._platform_name = platform_name

    def meta(self) -> Any:
        return SimpleNamespace(name=self._platform_name)


def _build_button_keyboard() -> qinline.Keyboard:
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _build_button_payload(
                            QQOfficialButtonSpec(
                                button_id=BUTTON_A_ID,
                                label=BUTTON_A_LABEL,
                                visited_label=BUTTON_A_VISITED_LABEL,
                                data=BUTTON_A_DATA,
                            )
                        ),
                        _build_button_payload(
                            QQOfficialButtonSpec(
                                button_id=BUTTON_B_ID,
                                label=BUTTON_B_LABEL,
                                visited_label=BUTTON_B_VISITED_LABEL,
                                data=BUTTON_B_DATA,
                            )
                        ),
                    ]
                }
            ]
        }
    }


def _build_button_payload(spec: QQOfficialButtonSpec) -> qinline.Button:
    return {
        "id": spec.button_id,
        "render_data": {
            "label": spec.label,
            "visited_label": spec.visited_label,
            "style": 1,
        },
        "action": {
            "type": 2,
            "permission": {
                "type": 2,
            },
            "data": spec.data,
            "reply": True,
            "enter": False,
            "unsupport_tips": "当前客户端不支持该按钮",
        },
    }


def _build_callback_button_keyboard() -> qinline.Keyboard:
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _build_callback_button_payload(
                            QQOfficialButtonSpec(
                                button_id=CALLBACK_BUTTON_A_ID,
                                label=CALLBACK_BUTTON_A_LABEL,
                                visited_label=CALLBACK_BUTTON_A_VISITED_LABEL,
                                data=CALLBACK_BUTTON_A_DATA,
                            )
                        ),
                        _build_callback_button_payload(
                            QQOfficialButtonSpec(
                                button_id=CALLBACK_BUTTON_B_ID,
                                label=CALLBACK_BUTTON_B_LABEL,
                                visited_label=CALLBACK_BUTTON_B_VISITED_LABEL,
                                data=CALLBACK_BUTTON_B_DATA,
                            )
                        ),
                    ]
                }
            ]
        }
    }


def _build_callback_button_payload(spec: QQOfficialButtonSpec) -> qinline.Button:
    return {
        "id": spec.button_id,
        "render_data": {
            "label": spec.label,
            "visited_label": spec.visited_label,
            "style": 1,
        },
        "action": {
            "type": 1,
            "permission": {
                "type": 2,
                "specify_user_ids": [],
                "specify_role_ids": [],
            },
            "data": spec.data,
            "click_limit": 0,
            "at_bot_show_channel_list": False,
            "unsupport_tips": "当前客户端不支持该按钮",
        },
    }


def _button_display_text(button_id: str | None, button_data: str | None) -> str:
    if button_id in {BUTTON_A_ID, CALLBACK_BUTTON_A_ID} or button_data in {
        BUTTON_A_DATA,
        CALLBACK_BUTTON_A_DATA,
    }:
        return "A"
    if button_id in {BUTTON_B_ID, CALLBACK_BUTTON_B_ID} or button_data in {
        BUTTON_B_DATA,
        CALLBACK_BUTTON_B_DATA,
    }:
        return "B"
    return "未知"


def _build_button_message_payload() -> dict[str, Any]:
    return {
        "msg_type": 2,
        "markdown": MarkdownPayload(content=BUTTON_PROMPT),
        "keyboard": _build_button_keyboard(),
    }


def _build_callback_button_message_payload() -> dict[str, Any]:
    return {
        "msg_type": 2,
        "markdown": MarkdownPayload(content=CALLBACK_BUTTON_PROMPT),
        "keyboard": _build_callback_button_keyboard(),
    }


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
                            _build_roulette_button(
                                "roulette_join",
                                "加入",
                                "轮盘加入",
                            ),
                            _build_roulette_button(
                                "roulette_leave",
                                "退出",
                                "轮盘退出",
                            ),
                            _build_roulette_button(
                                "roulette_start",
                                "开始",
                                "轮盘开始",
                            ),
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
    buttons.append(
        _build_roulette_button(
            "roulette_handcuffs_cancel",
            "取消",
            "轮盘状态",
        )
    )
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
                        _build_roulette_button("roulette_room_status", "房间", "轮盘状态"),
                        _build_roulette_button("roulette_start", "开始", "轮盘开始"),
                    ]
                },
            ]
        }
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
        if stripped in {"恶魔轮盘状态", "恶魔轮盘指令：", "恶魔轮盘指令:", "轮盘设置", "选择手铐目标"}:
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


def _build_button_reply_payload(text: str) -> dict[str, Any]:
    return {
        "content": text,
        "msg_type": 0,
        "markdown": None,
        "keyboard": None,
    }


def _build_button_reply_text(button_id: str | None, button_data: str | None) -> str:
    return f"你按了 {_button_display_text(button_id, button_data)}"


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
    return json.dumps(_json_safe(value), ensure_ascii=False, default=str)


def _build_button_debug_context(raw_message: Any, message_obj: Any) -> dict[str, Any]:
    author = getattr(raw_message, "author", None)
    return {
        "raw_message_class": f"{type(raw_message).__module__}.{type(raw_message).__name__}",
        "raw_id": getattr(raw_message, "id", None),
        "raw_msg_seq": getattr(raw_message, "msg_seq", None),
        "raw_event_id": getattr(raw_message, "event_id", None),
        "message_obj_message_id": getattr(message_obj, "message_id", None),
        "group_openid": getattr(raw_message, "group_openid", None),
        "author_user_openid": getattr(author, "user_openid", None),
        "author_member_openid": getattr(author, "member_openid", None),
    }


def _extract_interaction_context(raw_event: Any) -> QQOfficialInteractionContext | None:
    if raw_event is None:
        return None

    interaction: Any
    if isinstance(raw_event, Interaction):
        interaction = raw_event
    else:
        interaction = raw_event

    data = getattr(interaction, "data", None)
    resolved = getattr(data, "resolved", None)
    button_id = getattr(resolved, "button_id", None)
    button_data = getattr(resolved, "button_data", None)
    message_id = getattr(resolved, "message_id", None)
    return QQOfficialInteractionContext(
        interaction_id=str(getattr(interaction, "id", "") or ""),
        scene=getattr(interaction, "scene", None),
        chat_type=getattr(interaction, "chat_type", None),
        user_openid=getattr(interaction, "user_openid", None),
        group_openid=getattr(interaction, "group_openid", None),
        group_member_openid=getattr(interaction, "group_member_openid", None),
        guild_id=getattr(interaction, "guild_id", None),
        channel_id=getattr(interaction, "channel_id", None),
        button_id=str(button_id) if button_id is not None else None,
        button_data=str(button_data) if button_data is not None else None,
        message_id=str(message_id) if message_id is not None else None,
    )


def _interaction_to_debug_dict(context: QQOfficialInteractionContext) -> dict[str, Any]:
    return {
        "interaction_id": context.interaction_id,
        "scene": context.scene,
        "chat_type": context.chat_type,
        "user_openid": context.user_openid,
        "group_openid": context.group_openid,
        "group_member_openid": context.group_member_openid,
        "guild_id": context.guild_id,
        "channel_id": context.channel_id,
        "button_id": context.button_id,
        "button_data": context.button_data,
        "message_id": context.message_id,
    }


def _is_qqofficial_message_event(event: AstrMessageEvent) -> bool:
    event_type = type(event)
    module_name = event_type.__module__.lower()
    return (
        event_type.__name__ in QQOFFICIAL_MESSAGE_EVENT_NAMES
        and module_name.startswith(QQOFFICIAL_MESSAGE_EVENT_MODULE_PREFIXES)
    )


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in SENSITIVE_KEYWORDS)


def _safe_repr(value: Any) -> str:
    try:
        text = repr(value)
    except Exception as exc:
        return f"<repr failed: {exc}>"
    if len(text) > 500:
        return text[:500] + "...<truncated>"
    return text


def _iter_object_fields(obj: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if hasattr(obj, "__dict__"):
        try:
            fields.update(vars(obj))
        except Exception:
            pass

    for cls in type(obj).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if not isinstance(slot, str) or slot.startswith("__") or slot in fields:
                continue
            try:
                fields[slot] = getattr(obj, slot)
            except Exception as exc:
                fields[slot] = f"<getattr failed: {exc}>"
    return fields


def _json_safe(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()
    if depth > MAX_DUMP_DEPTH:
        return f"<max depth {MAX_DUMP_DEPTH} reached: {type(value).__module__}.{type(value).__name__}>"

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return {"__type__": "bytes", "length": len(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)

    value_id = id(value)
    if value_id in seen:
        return f"<circular reference: {type(value).__module__}.{type(value).__name__}>"

    if isinstance(value, dict):
        seen.add(value_id)
        result = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = (
                "<redacted>"
                if _is_sensitive_key(key_text)
                else _json_safe(item, depth=depth + 1, seen=seen)
            )
        seen.discard(value_id)
        return result

    if isinstance(value, list | tuple | set | frozenset):
        seen.add(value_id)
        result = [_json_safe(item, depth=depth + 1, seen=seen) for item in value]
        seen.discard(value_id)
        return result

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        seen.add(value_id)
        result = {}
        for field in dataclasses.fields(value):
            result[field.name] = (
                "<redacted>"
                if _is_sensitive_key(field.name)
                else _json_safe(getattr(value, field.name), depth=depth + 1, seen=seen)
            )
        seen.discard(value_id)
        return result

    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(), depth=depth + 1, seen=seen)
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _json_safe(value.dict(), depth=depth + 1, seen=seen)
        except Exception:
            pass

    fields = _iter_object_fields(value)
    if fields:
        seen.add(value_id)
        result: dict[str, Any] = {
            "__class__": f"{type(value).__module__}.{type(value).__name__}",
            "__repr__": _safe_repr(value),
        }
        for key, item in fields.items():
            if key.startswith("_api") or key in {"_connection", "_http", "api", "http"}:
                result[key] = f"<skipped: {type(item).__module__}.{type(item).__name__}>"
            elif _is_sensitive_key(key):
                result[key] = "<redacted>"
            else:
                result[key] = _json_safe(item, depth=depth + 1, seen=seen)
        seen.discard(value_id)
        return result

    return {
        "__class__": f"{type(value).__module__}.{type(value).__name__}",
        "__repr__": _safe_repr(value),
    }


def _event_getters(event: AstrMessageEvent) -> dict[str, Any]:
    getter_names = [
        "get_platform_name",
        "get_platform_id",
        "get_message_str",
        "get_message_outline",
        "get_message_type",
        "get_session_id",
        "get_group_id",
        "get_self_id",
        "get_sender_id",
        "get_sender_name",
        "is_private_chat",
        "is_wake_up",
        "is_admin",
        "is_stopped",
    ]
    values: dict[str, Any] = {}
    for name in getter_names:
        try:
            values[name] = _json_safe(getattr(event, name)())
        except Exception as exc:
            values[name] = f"<call failed: {exc}>"
    return values


def _event_public_attrs(event: AstrMessageEvent) -> dict[str, Any]:
    attrs = {}
    for key, value in _iter_object_fields(event).items():
        if key in SKIPPED_EVENT_FIELDS:
            continue
        if key.startswith("_") and key not in {"_extras", "_result", "_has_send_oper"}:
            continue
        attrs[key] = _json_safe(value)
    return attrs


def _component_dump(event: AstrMessageEvent) -> list[Any]:
    dumped = []
    for component in event.get_messages():
        item = _json_safe(component)
        if not isinstance(item, dict):
            item = {"value": item}
        try:
            item["toDict"] = component.toDict()
        except Exception as exc:
            item["toDict"] = f"<toDict failed: {exc}>"
        dumped.append(item)
    return dumped


def _build_event_dump(event: AstrMessageEvent) -> dict[str, Any]:
    message_obj = event.message_obj
    raw_message = getattr(message_obj, "raw_message", None)
    bot = getattr(event, "bot", None)

    return {
        "notice": "bot/client internals and secret-like fields are skipped or redacted to avoid leaking credentials in chat.",
        "event_class": f"{type(event).__module__}.{type(event).__name__}",
        "event_repr": _safe_repr(event),
        "platform_name": event.get_platform_name(),
        "platform_id": event.get_platform_id(),
        "unified_msg_origin": event.unified_msg_origin,
        "getters": _event_getters(event),
        "event_attrs": _event_public_attrs(event),
        "event_extra": _json_safe(event.get_extra()),
        "platform_meta": _json_safe(event.platform_meta),
        "session": _json_safe(event.session),
        "message_obj": _json_safe(message_obj),
        "message_components": _component_dump(event),
        "raw_message_class": f"{type(raw_message).__module__}.{type(raw_message).__name__}",
        "raw_message": _json_safe(raw_message),
        "bot_client": {
            "class": f"{type(bot).__module__}.{type(bot).__name__}" if bot else None,
            "repr": _safe_repr(bot) if bot else None,
            "skipped": True,
        },
    }


def _chunk_text(text: str, size: int = MAX_TEXT_CHUNK_SIZE) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


@register(
    "astrbot_plugin_buckshot_roulette",
    "lishining,Codex",
    "QQOfficial 群聊多人无庄家恶魔轮盘插件",
    "1.2.1",
)
class QQOfficialUtilPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.location_data: dict = {}
        self.location_pool: list[str] = []
        self.search_region_pool: list[dict[str, str]] = []
        self._interaction_hook_installed = False
        self.roulette_settings = self._load_roulette_settings_from_config()
        self.roulette_db_path = Path(StarTools.get_data_dir()) / "roulette.db"
        self.roulette_db = RouletteDBManager(self.roulette_db_path)
        self.roulette_user_repo = RouletteUserRepo(self.roulette_db)
        self.roulette_games: dict[str, RouletteGame] = {}
        self.roulette_locks: dict[str, asyncio.Lock] = {}
        self._load_location_data()

    async def initialize(self):
        await self.roulette_db.init_db()
        self._install_interaction_hook()

    @filter.on_platform_loaded()
    async def _on_platform_loaded(self):
        self._install_interaction_hook()

    def _install_interaction_hook(self) -> None:
        patched = False
        seen_platforms = 0
        for platform in self.context.platform_manager.platform_insts:
            if platform.meta().name not in QQOFFICIAL_PLATFORMS:
                continue
            seen_platforms += 1
            client = getattr(platform, "client", None)
            if client is None:
                continue
            self._ensure_interaction_intent(client)
            if self._patch_qqofficial_client(platform, client):
                patched = True
        if patched:
            self._interaction_hook_installed = True
        elif seen_platforms == 0:
            logger.warning("[QQOfficialUtil] 未发现已加载的 QQOfficial 平台，暂无法安装按钮回调 hook。")

    def _install_interaction_hook_from_event(self, event: AstrMessageEvent) -> None:
        client = getattr(event, "bot", None)
        if client is None:
            logger.warning("[QQOfficialUtil] 当前事件缺少 bot client，无法兜底安装按钮回调 hook。")
            return

        self._ensure_interaction_intent(client)
        platform = QQOfficialEventPlatformProxy(
            client,
            event.get_platform_name() if hasattr(event, "get_platform_name") else None,
        )
        if self._patch_qqofficial_client(platform, client):
            self._interaction_hook_installed = True

    def _ensure_interaction_intent(self, client: Any) -> None:
        current_intents = getattr(client, "intents", None)
        if not isinstance(current_intents, int):
            logger.warning(
                "[QQOfficialUtil] 无法确认 interaction intent: client=%s, intents=%r",
                type(client).__name__,
                current_intents,
            )
            return
        if current_intents & QQOFFICIAL_INTERACTION_INTENT:
            logger.info(
                "[QQOfficialUtil] QQOfficial client 已包含 interaction intent: intents=%s",
                current_intents,
            )
            return
        client.intents = current_intents | QQOFFICIAL_INTERACTION_INTENT
        logger.warning(
            "[QQOfficialUtil] 已为 QQOfficial client 补充 interaction intent: %s -> %s。"
            "如果 websocket 已连接，需要重启/重连 QQOfficial 平台后才会收到按钮点击事件。",
            current_intents,
            client.intents,
        )

    def _patch_qqofficial_client(self, platform: Any, client: Any) -> bool:
        if (
            getattr(client, "_codex_button_hook_installed", False)
            and getattr(client, "_codex_button_hook_owner", None) == id(self)
        ):
            logger.info(
                "[QQOfficialUtil] QQOfficial interaction hook 已安装在当前插件实例: client=%s",
                type(client).__name__,
            )
            return False

        plugin = self
        original = getattr(
            client,
            "_codex_button_hook_original",
            getattr(client, "on_interaction_create", None),
        )

        async def on_interaction_create(interaction: Interaction):
            logger.info(
                "[QQOfficialUtil] 收到按钮回调原始事件: %s",
                _debug_json(interaction),
            )
            await plugin._handle_qqofficial_interaction(platform, interaction)
            if original and original is not on_interaction_create:
                maybe = original(interaction)
                if asyncio.iscoroutine(maybe):
                    await maybe

        client.on_interaction_create = on_interaction_create
        client._codex_button_hook_installed = True
        client._codex_button_hook_owner = id(self)
        client._codex_button_hook_original = original
        logger.info(
            "[QQOfficialUtil] 已安装 QQOfficial interaction hook: platform=%r, client=%s, original_handler=%s",
            platform.meta().name if hasattr(platform, "meta") else None,
            type(client).__name__,
            bool(original),
        )
        return True

    async def _handle_qqofficial_interaction(
        self,
        platform: Any,
        interaction: Interaction,
    ) -> None:
        context = _extract_interaction_context(interaction)
        if not context or not context.interaction_id:
            logger.warning("[QQOfficialUtil] 忽略按钮回调：缺少 interaction_id。")
            return
        logger.info(
            "[QQOfficialUtil] 解析按钮回调上下文: %s",
            _debug_json(_interaction_to_debug_dict(context)),
        )
        if context.button_id not in {BUTTON_A_ID, BUTTON_B_ID} and context.button_data not in {
            BUTTON_A_DATA,
            BUTTON_B_DATA,
        }:
            logger.info(
                "[QQOfficialUtil] 忽略非本插件按钮回调: button_id=%r, button_data=%r",
                context.button_id,
                context.button_data,
            )
            return

        try:
            ack_ret = await platform.client.api.on_interaction_result(context.interaction_id, 0)
            logger.info(
                "[QQOfficialUtil] 按钮回调 ACK 成功: interaction_id=%s, ret=%s",
                context.interaction_id,
                _debug_json(ack_ret),
            )
        except Exception as exc:
            logger.warning(f"[QQOfficialUtil] interaction ACK 失败: {exc}")

        await self._reply_button_press(platform, context)

    async def _reply_button_press(self, platform: Any, context: QQOfficialInteractionContext) -> None:
        text = _build_button_reply_text(context.button_id, context.button_data)
        payload = _build_button_reply_payload(text)
        raw_scene = (context.scene or "").lower()
        try:
            _add_passive_reply_context(payload, event_id=context.interaction_id)
            if raw_scene == "group" and context.group_openid:
                logger.info(
                    "[QQOfficialUtil] 准备回复按钮回调(group): group_openid=%s, payload=%s",
                    context.group_openid,
                    _debug_json(payload),
                )
                ret = await platform.client.api.post_group_message(
                    group_openid=context.group_openid,
                    **payload,
                )
                logger.info("[QQOfficialUtil] 按钮回调回复成功(group): ret=%s", _debug_json(ret))
                return
            if raw_scene == "c2c" and context.user_openid:
                logger.info(
                    "[QQOfficialUtil] 准备回复按钮回调(c2c): openid=%s, payload=%s",
                    context.user_openid,
                    _debug_json(payload),
                )
                ret = await platform.client.api.post_c2c_message(
                    openid=context.user_openid,
                    **payload,
                )
                logger.info("[QQOfficialUtil] 按钮回调回复成功(c2c): ret=%s", _debug_json(ret))
                return
            logger.warning(
                f"[QQOfficialUtil] 按钮回调场景不受支持: scene={context.scene!r}, chat_type={context.chat_type!r}"
            )
        except Exception as exc:
            logger.warning(f"[QQOfficialUtil] 回复按钮回调失败: {exc}")

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def qqofficial_roulette(self, event: AstrMessageEvent):
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
                logger.exception("[QQOfficialUtil] 轮盘指令处理失败: %s", exc)
                message = f"轮盘处理失败：{exc}"
                game = self.roulette_games.get(session_id)
                with_keyboard = False

        async for result in self._send_qqofficial_group_markdown(
            event,
            command_name="qqofficial_roulette",
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

    async def _send_qqofficial_button_message(
        self,
        event: AstrMessageEvent,
        *,
        command_name: str,
        payload: dict[str, Any],
    ):
        logger.info(
            "[QQOfficialUtil] %s 触发: event_class=%s.%s, platform=%r, message=%r",
            command_name,
            type(event).__module__,
            type(event).__name__,
            event.get_platform_name() if hasattr(event, "get_platform_name") else None,
            event.get_message_str() if hasattr(event, "get_message_str") else None,
        )
        if not _is_qqofficial_message_event(event):
            logger.warning(
                "[QQOfficialUtil] %s 拒绝：非 QQOfficial 事件 event_class=%s.%s",
                command_name,
                type(event).__module__,
                type(event).__name__,
            )
            yield event.plain_result("该指令仅支持 QQOfficialMessageEvent / QQOfficialWebhookMessageEvent。")
            return

        self._install_interaction_hook_from_event(event)

        raw_message = getattr(event.message_obj, "raw_message", None)
        if raw_message is None:
            logger.warning(
                "[QQOfficialUtil] %s 拒绝：message_obj 缺少 raw_message。message_obj=%s",
                command_name,
                _debug_json(getattr(event, "message_obj", None)),
            )
            yield event.plain_result("未找到 QQOfficial 原始消息对象，无法发送按钮。")
            return

        debug_context = _build_button_debug_context(raw_message, event.message_obj)
        _add_passive_reply_context(
            payload,
            msg_id=_extract_message_reference_id(raw_message, event.message_obj),
            msg_seq=getattr(raw_message, "msg_seq", None),
        )
        logger.info(
            "[QQOfficialUtil] %s 原始上下文: %s",
            command_name,
            _debug_json(debug_context),
        )
        logger.info(
            "[QQOfficialUtil] %s 最终发送 payload: %s",
            command_name,
            _debug_json(payload),
        )
        if not payload.get("msg_id") and not payload.get("event_id"):
            logger.warning(
                "[QQOfficialUtil] %s 未找到 msg_id/event_id，平台可能按主动消息处理。",
                command_name,
            )
        try:
            if isinstance(raw_message, botpy_message.GroupMessage):
                logger.info(
                    "[QQOfficialUtil] %s 使用 group 接口发送: group_openid=%s",
                    command_name,
                    raw_message.group_openid,
                )
                ret = await event.bot.api.post_group_message(
                    group_openid=raw_message.group_openid,
                    **payload,
                )
                logger.info(
                    "[QQOfficialUtil] %s group API 返回: %s",
                    command_name,
                    _debug_json(ret),
                )
            elif isinstance(raw_message, botpy_message.C2CMessage):
                logger.info(
                    "[QQOfficialUtil] %s 使用 c2c 接口发送: openid=%s",
                    command_name,
                    raw_message.author.user_openid,
                )
                ret = await event.bot.api.post_c2c_message(
                    openid=raw_message.author.user_openid,
                    **payload,
                )
                logger.info(
                    "[QQOfficialUtil] %s c2c API 返回: %s",
                    command_name,
                    _debug_json(ret),
                )
            else:
                logger.warning(
                    "[QQOfficialUtil] %s 不支持的 raw_message 类型: %s",
                    command_name,
                    debug_context["raw_message_class"],
                )
                yield event.plain_result(
                    "该指令仅支持群聊和 C2C 消息类型。"
                )
                return
        except Exception as exc:
            logger.exception("[QQOfficialUtil] %s 发送按钮消息失败: %s", command_name, exc)
            yield event.plain_result(f"发送按钮消息失败：{exc}")
            return

        logger.info("[QQOfficialUtil] %s 发送流程结束，停止事件传播。", command_name)
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
            return (
                f"{player_name} 退出了房间（{len(game.players)}/{MAX_PLAYERS}）。",
                game,
                True,
            )
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
                return (
                    self._roulette_handcuffs_target_text(game),
                    game,
                    True,
                )
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
            "[QQOfficialUtil] %s 发送轮盘群聊 payload: %s",
            command_name,
            _debug_json(payload),
        )
        try:
            await event.bot.api.post_group_message(
                group_openid=group_openid,
                **payload,
            )
        except Exception as exc:
            logger.exception("[QQOfficialUtil] %s 发送轮盘消息失败: %s", command_name, exc)
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
                "[QQOfficialUtil] 轮盘身份解析失败: group_openid=%r, platform_user_id=%r",
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
            "恶魔轮盘指令：\n"
            "轮盘绑定 昵称 / 轮盘我的名字 / 轮盘改名 新昵称\n"
            "轮盘创建 / 轮盘加入 / 轮盘退出 / 轮盘设置 / 轮盘开始 / 轮盘状态 / 轮盘结束\n"
            "轮盘开枪 自己 / 轮盘开枪 编号\n"
            "轮盘道具 啤酒|香烟|手锯|放大镜|过期药|电话|换向器 / 轮盘道具 手铐 编号"
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
            logger.warning("[QQOfficialUtil] 无法写回轮盘设置到插件配置对象。")

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

    def _extract_box_command_text(self, event: AstrMessageEvent) -> str | None:
        if event.get_platform_name() not in QQOFFICIAL_PLATFORMS:
            return None
        if not self._starts_with_bot_mention(event):
            return None
        return event.get_message_str() or ""

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

    def _parse_box_target(self, command_text: str) -> str | None:
        match = BOX_COMMAND_PATTERN.fullmatch(command_text)
        if not match:
            return None
        qq = match.group(1).strip()
        if not self._validate_qq(qq):
            return None
        return qq

    async def generate_fake_dox(self, target_qq: str) -> tuple[str, str | None]:
        location = await self._generate_location()
        output = (
            "身份检索完成\n"
            f"账号：{target_qq}\n"
            f"手机：{self._generate_phone()}\n"
            f"IP地址：{self._generate_ip()}\n"
            f"物理地址：{location.address}\n"
            "说明：以上信息均为随机生成，仅供娱乐。"
        )
        return output, location.map_url

    def _load_location_data(self) -> None:
        data_path = Path(__file__).resolve().parent / "china_clean_v2.json"
        try:
            if not data_path.exists():
                logger.warning(f"[QQOfficialUtil] 未找到地理位置文件：{data_path}")
                return

            with data_path.open("r", encoding="utf-8") as file:
                self.location_data = json.load(file)

            if not isinstance(self.location_data, dict):
                logger.warning("[QQOfficialUtil] 地理位置数据格式无效，应为字典")
                self.location_data = {}
                return

            self.location_pool = self._flatten_locations(self.location_data)
            self.search_region_pool = self._flatten_search_regions(self.location_data)
            logger.info(
                f"[QQOfficialUtil] 已加载 {len(self.location_pool)} 条地理位置数据"
            )
        except json.JSONDecodeError as exc:
            logger.error(f"[QQOfficialUtil] 解析地理位置 JSON 失败：{exc}")
            self.location_data = {}
            self.location_pool = []
            self.search_region_pool = []
        except Exception as exc:
            logger.error(f"[QQOfficialUtil] 加载地理位置数据失败：{exc}")
            self.location_data = {}
            self.location_pool = []
            self.search_region_pool = []

    def _flatten_locations(self, data: dict) -> list[str]:
        locations: list[str] = []
        for provinces in data.values():
            if not isinstance(provinces, dict):
                continue

            for province_name, cities in provinces.items():
                if not isinstance(cities, dict):
                    locations.append(str(province_name))
                    continue

                for city_name, districts in cities.items():
                    if not isinstance(districts, dict) or not districts:
                        locations.append(f"{province_name}{city_name}")
                        continue

                    for district_name, streets in districts.items():
                        if not isinstance(streets, dict) or not streets:
                            locations.append(
                                f"{province_name}{city_name}{district_name}"
                            )
                            continue

                        for street_name in streets.keys():
                            locations.append(
                                f"{province_name}{city_name}{district_name}{street_name}"
                            )

        return locations

    def _flatten_search_regions(self, data: dict) -> list[dict[str, str]]:
        regions: list[dict[str, str]] = []
        for provinces in data.values():
            if not isinstance(provinces, dict):
                continue

            for province_name, cities in provinces.items():
                if not isinstance(cities, dict):
                    continue

                for city_name, districts in cities.items():
                    if not isinstance(districts, dict) or not districts:
                        regions.append(
                            {
                                "region": str(city_name),
                                "prefix": f"{province_name}{city_name}",
                            }
                        )
                        continue

                    for district_name, streets in districts.items():
                        street_name = self._pick_street_name(streets)
                        prefix = f"{province_name}{city_name}{district_name}"
                        if street_name:
                            prefix += street_name
                        regions.append(
                            {
                                "region": str(city_name),
                                "prefix": prefix,
                            }
                        )

        return regions

    def _pick_street_name(self, streets: object) -> str:
        if not isinstance(streets, dict) or not streets:
            return ""
        return str(random.choice(list(streets.keys())))

    def _get_bot_self_ids(self, event: AstrMessageEvent) -> set[str]:
        self_ids = {BOT_AT_MARKER}
        raw_message = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw_message, dict):
            self_id = raw_message.get("self_id")
            if self_id:
                self_ids.add(str(self_id))

        message_self_id = getattr(event.message_obj, "self_id", None)
        if message_self_id:
            self_ids.add(str(message_self_id))

        try:
            self_id = event.get_self_id()
        except Exception as exc:
            logger.debug(f"[QQOfficialUtil] 读取 event.get_self_id 失败：{exc}")
        else:
            if self_id:
                self_ids.add(str(self_id))
        return self_ids

    def _validate_qq(self, qq: str) -> bool:
        if not qq or not isinstance(qq, str):
            return False
        if not qq.isdigit():
            logger.warning(f"[QQOfficialUtil] 检测到无效 QQ 格式：{qq}")
            return False
        return True

    def _is_user_allowed(self, user_id: str | None) -> bool:
        if not user_id:
            return True

        mode = str(self.config.get("user_list_mode", "none")).lower()
        if mode not in {"whitelist", "blacklist", "none"}:
            mode = "none"
        if mode == "none":
            return True

        user_list = {str(item) for item in self.config.get("user_list", [])}
        is_in_list = str(user_id) in user_list
        if mode == "whitelist":
            return is_in_list
        if mode == "blacklist":
            return not is_in_list
        return True

    def _generate_phone(self) -> str:
        prefixes = [
            "130",
            "131",
            "132",
            "133",
            "135",
            "136",
            "137",
            "138",
            "139",
            "150",
            "151",
            "152",
            "155",
            "156",
            "157",
            "158",
            "159",
            "166",
            "177",
            "180",
            "181",
            "182",
            "183",
            "184",
            "185",
            "186",
            "187",
            "188",
            "189",
            "198",
            "199",
        ]
        prefix = random.choice(prefixes)
        suffix = "".join(str(random.randint(0, 9)) for _ in range(8))
        return f"{prefix}{suffix}"

    def _generate_ip(self) -> str:
        first = random.choice(
            [
                58,
                61,
                110,
                112,
                113,
                114,
                115,
                116,
                117,
                118,
                119,
                120,
                121,
                122,
                123,
                124,
                125,
                126,
                127,
                172,
                192,
            ]
        )
        second = random.randint(1, 255)
        third = random.randint(0, 255)
        fourth = random.randint(1, 254)
        return f"{first}.{second}.{third}.{fourth}"

    async def _generate_location(self) -> GeneratedLocation:
        if not self._is_map_enrichment_enabled():
            return GeneratedLocation(self._generate_fallback_location())

        api_key = self._get_tencent_map_key()
        if not api_key:
            logger.warning("[QQOfficialUtil] 已启用地图增强，但未配置 tencent_map_key")
            return GeneratedLocation(self._generate_fallback_location())

        region = self._pick_search_region()
        if not region:
            logger.warning("[QQOfficialUtil] 未加载到可搜索地区，回退本地随机地址")
            return GeneratedLocation(self._generate_fallback_location())

        poi = await self._search_random_poi(api_key, region["region"])
        if not poi:
            return GeneratedLocation(region["prefix"])

        address = self._format_poi_address(region["prefix"], poi)
        map_url = self._build_static_map_url(api_key, poi)
        return GeneratedLocation(address, map_url)

    def _generate_fallback_location(self) -> str:
        if self.location_pool:
            return random.choice(self.location_pool)
        return "四川省成都市金牛区"

    def _is_map_enrichment_enabled(self) -> bool:
        return bool(self._get_map_config("enable_static_map", False))

    def _get_tencent_map_key(self) -> str:
        return str(
            self._get_map_config("tencent_map_key", "")
            or os.getenv("TENCENT_MAP_KEY")
            or ""
        ).strip()

    def _get_tencent_map_sk(self) -> str:
        return str(
            self._get_map_config("tencent_map_sk", "")
            or os.getenv("TENCENT_MAP_SK")
            or ""
        ).strip()

    def _pick_search_region(self) -> dict[str, str] | None:
        if not self.search_region_pool:
            return None
        return random.choice(self.search_region_pool)

    async def _search_random_poi(
        self, api_key: str, region_name: str
    ) -> dict | None:
        page_size = self._get_int_config("place_search_page_size", 10, 1, 20)
        keywords = self._pick_place_keywords_for_search()
        for keyword in keywords:
            params = {
                "key": api_key,
                "keyword": keyword,
                "boundary": f"region({region_name},1)",
                "page_size": str(page_size),
                "page_index": "1",
                "output": "json",
            }
            url = self._build_tencent_get_url(TENCENT_MAP_PLACE_SEARCH_PATH, params)
            try:
                payload = await self._http_get_json(url)
            except Exception as exc:
                logger.warning(f"[QQOfficialUtil] 腾讯地图地点搜索失败：{exc}")
                return None

            if not isinstance(payload, dict) or payload.get("status") != 0:
                logger.warning(
                    "[QQOfficialUtil] 腾讯地图地点搜索返回异常："
                    f"{self._summarize_api_payload(payload)}"
                )
                return None

            pois = payload.get("data")
            if not isinstance(pois, list) or not pois:
                continue
            valid_pois = [poi for poi in pois if isinstance(poi, dict)]
            if valid_pois:
                return random.choice(valid_pois)
        return None

    def _pick_place_keywords_for_search(self) -> list[str]:
        keywords = self._get_map_config("place_search_keywords", DEFAULT_PLACE_KEYWORDS)
        if not isinstance(keywords, list):
            keywords = DEFAULT_PLACE_KEYWORDS
        normalized = [str(item).strip() for item in keywords if str(item).strip()]
        if not normalized:
            normalized = DEFAULT_PLACE_KEYWORDS
        random.shuffle(normalized)
        retry_count = self._get_int_config(
            "place_search_retry_keywords", 4, 1, len(normalized)
        )
        return normalized[:retry_count]

    async def _http_get_json(self, url: str) -> dict:
        timeout = self._get_int_config("tencent_api_timeout", 5, 1, 30)

        def _request() -> dict:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "astrbot-plugin-util-official/1.1"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset)
            return json.loads(body)

        return await asyncio.to_thread(_request)

    def _format_poi_address(self, region_prefix: str, poi: dict) -> str:
        title = str(poi.get("title") or "").strip()
        address = str(poi.get("address") or "").strip()
        if address and title:
            return f"{address}（{title}）"
        if title:
            return f"{region_prefix}{title}"
        return address or region_prefix

    def _build_static_map_url(self, api_key: str, poi: dict) -> str | None:
        location = poi.get("location")
        if not isinstance(location, dict):
            return None
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is None or lng is None:
            return None

        zoom = self._get_int_config("static_map_zoom", 17, 4, 18)
        size = str(self._get_map_config("static_map_size", "500x400")).strip() or "500x400"
        marker_style = (
            str(self._get_map_config("static_map_marker_style", "size:large|color:red"))
            .strip()
            .strip("|")
        )
        params = {
            "key": api_key,
            "center": f"{lat},{lng}",
            "zoom": str(zoom),
            "size": size,
        }
        if marker_style:
            params["markers"] = f"{marker_style}|{lat},{lng}"
        return self._build_tencent_get_url(TENCENT_MAP_STATIC_PATH, params)

    def _build_tencent_get_url(self, path: str, params: dict[str, str]) -> str:
        request_params = dict(params)
        secret_key = self._get_tencent_map_sk()
        if secret_key:
            request_params["sig"] = self._sign_tencent_get(path, params, secret_key)
        query = urllib.parse.urlencode(request_params)
        return f"{TENCENT_MAP_API_BASE}{path}?{query}"

    def _sign_tencent_get(
        self, path: str, params: dict[str, str], secret_key: str
    ) -> str:
        raw_query = "&".join(f"{key}={params[key]}" for key in sorted(params.keys()))
        source = f"{path}?{raw_query}{secret_key}"
        return hashlib.md5(source.encode("utf-8")).hexdigest()

    def _get_map_config(self, key: str, default=None):
        tencent_map = self.config.get("tencent_map", {})
        if hasattr(tencent_map, "get"):
            value = tencent_map.get(key, None)
            if value is not None:
                return value
        return self.config.get(key, default)

    def _summarize_api_payload(self, payload) -> str:
        if not isinstance(payload, dict):
            return repr(payload)[:300]
        summary = {
            "status": payload.get("status"),
            "message": payload.get("message"),
            "count": payload.get("count"),
            "request_id": payload.get("request_id"),
        }
        return json.dumps(summary, ensure_ascii=False)

    def _get_int_config(
        self, key: str, default: int, minimum: int, maximum: int
    ) -> int:
        try:
            value = int(self._get_map_config(key, default))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    async def terminate(self):
        await self.roulette_db.close()
