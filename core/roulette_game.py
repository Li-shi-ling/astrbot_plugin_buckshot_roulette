from __future__ import annotations

import random
from dataclasses import dataclass, field


MIN_PLAYERS = 2
MAX_PLAYERS = 10
DEFAULT_HP = 2
DEFAULT_SHELL_COUNT = 4
SHELL_LIVE = "live"
SHELL_BLANK = "blank"
ITEM_BEER = "啤酒"
ITEM_CIGARETTE = "香烟"
ITEM_SAW = "手锯"
ITEM_HANDCUFFS = "手铐"
ITEM_MAGNIFIER = "放大镜"
ITEM_EXPIRED_MEDICINE = "过期药"
ITEM_PHONE = "电话"
ITEM_INVERTER = "换向器"
ITEM_POOL = [
    ITEM_BEER,
    ITEM_CIGARETTE,
    ITEM_SAW,
    ITEM_HANDCUFFS,
    ITEM_MAGNIFIER,
    ITEM_EXPIRED_MEDICINE,
    ITEM_PHONE,
    ITEM_INVERTER,
]
NO_TARGET_ITEMS = {
    ITEM_BEER,
    ITEM_CIGARETTE,
    ITEM_SAW,
    ITEM_MAGNIFIER,
    ITEM_EXPIRED_MEDICINE,
    ITEM_PHONE,
    ITEM_INVERTER,
}


class RouletteGameError(ValueError):
    pass


@dataclass
class RoulettePlayer:
    user_id: str
    display_name: str
    hp: int = DEFAULT_HP
    max_hp: int = DEFAULT_HP
    items: list[str] = field(default_factory=list)
    skip_turns: int = 0

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class RouletteActionResult:
    message: str
    ended: bool = False


@dataclass
class RouletteSettings:
    shell_count_max: int = DEFAULT_SHELL_COUNT
    shell_count_min: int = 2
    random_shell_count: bool = False
    item_count_max: int = 1
    item_count_min: int = 1
    random_item_count: bool = False
    item_inventory_max: int = 8
    hp_max: int = DEFAULT_HP
    hp_min: int = 1
    random_hp: bool = False

    def normalize(self) -> None:
        self.shell_count_min = max(2, int(self.shell_count_min))
        self.shell_count_max = max(2, int(self.shell_count_max))
        if self.shell_count_max < self.shell_count_min:
            self.shell_count_max = self.shell_count_min
        self.item_count_min = max(0, int(self.item_count_min))
        self.item_count_max = max(0, int(self.item_count_max))
        if self.item_count_max < self.item_count_min:
            self.item_count_max = self.item_count_min
        self.item_inventory_max = max(0, min(20, int(self.item_inventory_max)))
        self.hp_min = max(1, int(self.hp_min))
        self.hp_max = max(1, int(self.hp_max))
        if self.hp_max < self.hp_min:
            self.hp_max = self.hp_min


@dataclass
class RouletteGame:
    group_openid: str
    owner_id: str
    rng: random.Random = field(default_factory=random.Random)
    settings: RouletteSettings = field(default_factory=RouletteSettings)
    players: list[RoulettePlayer] = field(default_factory=list)
    phase: str = "waiting"
    current_index: int = 0
    shell_queue: list[str] = field(default_factory=list)
    next_live_damage: int = 1

    def add_player(self, user_id: str, display_name: str) -> RouletteActionResult:
        if self.phase != "waiting":
            raise RouletteGameError("本局已经开始，不能再加入。")
        if self.get_player(user_id):
            return RouletteActionResult(f"{display_name} 已经在房间里。")
        if len(self.players) >= MAX_PLAYERS:
            raise RouletteGameError("房间已满，最多 10 人。")
        self.players.append(RoulettePlayer(user_id=user_id, display_name=display_name))
        return RouletteActionResult(
            f"{display_name} 加入了恶魔轮盘房间（{len(self.players)}/{MAX_PLAYERS}）。"
        )

    def start(self, actor_id: str) -> RouletteActionResult:
        if actor_id != self.owner_id:
            raise RouletteGameError("只有房主可以开始本局。")
        if self.phase != "waiting":
            raise RouletteGameError("本局已经开始。")
        if len(self.players) < MIN_PLAYERS:
            raise RouletteGameError("至少需要 2 名玩家才能开始。")
        self.phase = "playing"
        self.current_index = 0
        self.apply_starting_hp()
        self.reload_shells(deal_items=True)
        current = self.current_player()
        return RouletteActionResult(
            "恶魔轮盘开始。\n"
            f"{self.shell_count_text()}\n"
            f"当前行动：{current.display_name}"
        )

    def get_player(self, user_id: str) -> RoulettePlayer | None:
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None

    def player_number(self, user_id: str) -> int | None:
        for index, player in enumerate(self.players, start=1):
            if player.user_id == user_id:
                return index
        return None

    def player_by_number(self, number: int) -> RoulettePlayer:
        if number < 1 or number > len(self.players):
            raise RouletteGameError("目标编号不存在。")
        player = self.players[number - 1]
        if not player.alive:
            raise RouletteGameError("目标玩家已经淘汰。")
        return player

    def current_player(self) -> RoulettePlayer:
        if not self.players:
            raise RouletteGameError("房间里还没有玩家。")
        if self.current_index >= len(self.players):
            self.current_index = 0
        return self.players[self.current_index]

    def require_playing_actor(self, actor_id: str) -> RoulettePlayer:
        if self.phase != "playing":
            raise RouletteGameError("本局还没有开始。")
        player = self.get_player(actor_id)
        if not player:
            raise RouletteGameError("你还没有加入本局。")
        if not player.alive:
            raise RouletteGameError("你已经淘汰，只能查看状态。")
        current = self.current_player()
        if current.user_id != actor_id:
            raise RouletteGameError(f"还没轮到你。当前行动：{current.display_name}")
        return player

    def shoot(self, actor_id: str, target: str) -> RouletteActionResult:
        actor = self.require_playing_actor(actor_id)
        if target == "自己":
            target_player = actor
            target_self = True
        else:
            try:
                target_player = self.player_by_number(int(target))
            except ValueError as exc:
                raise RouletteGameError("目标请使用玩家编号，或使用“自己”。") from exc
            target_self = target_player.user_id == actor.user_id

        if not self.shell_queue:
            self.reload_shells(deal_items=True)

        shell = self.shell_queue.pop(0)
        lines = [f"{actor.display_name} 向 {target_player.display_name} 开枪。"]
        if shell == SHELL_LIVE:
            damage = self.next_live_damage
            self.next_live_damage = 1
            target_player.hp = max(0, target_player.hp - damage)
            lines.append(f"砰，实弹！造成 {damage} 点伤害。")
            if not target_player.alive:
                lines.append(f"{target_player.display_name} 淘汰。")
            self._advance_turn(lines)
        else:
            saw_active = self.next_live_damage > 1
            self.next_live_damage = 1
            lines.append("咔，空弹。")
            if saw_active:
                lines.append("手锯效果落空并消失。")
            if not target_self:
                self._advance_turn(lines)
            else:
                lines.append(f"{actor.display_name} 打自己遇到空弹，保留回合。")

        ended = self._append_finish_or_reload(lines)
        return RouletteActionResult("\n".join(lines), ended=ended)

    def use_item(
        self, actor_id: str, item_name: str, target_number: int | None = None
    ) -> RouletteActionResult:
        actor = self.require_playing_actor(actor_id)
        item_name = self.normalize_item_name(item_name)
        if item_name not in ITEM_POOL:
            raise RouletteGameError("未知道具。可用：啤酒、香烟、手锯、手铐、放大镜、过期药、电话、换向器。")
        if item_name not in actor.items:
            raise RouletteGameError(f"你没有道具：{item_name}")

        lines: list[str] = []
        if item_name == ITEM_BEER:
            self._consume_item(actor, item_name)
            if not self.shell_queue:
                self.reload_shells(deal_items=True)
                lines.append("弹队列为空，已重新装填。")
            shell = self.shell_queue.pop(0)
            lines.append(
                f"{actor.display_name} 使用啤酒，退掉了一发{'实弹' if shell == SHELL_LIVE else '空弹'}。"
            )
        elif item_name == ITEM_CIGARETTE:
            if actor.hp >= actor.max_hp:
                raise RouletteGameError("你的血量已满，暂时不能使用香烟。")
            self._consume_item(actor, item_name)
            actor.hp = min(actor.max_hp, actor.hp + 1)
            lines.append(f"{actor.display_name} 使用香烟，恢复 1 点血。")
        elif item_name == ITEM_SAW:
            if self.next_live_damage > 1:
                raise RouletteGameError("手锯效果已经生效。")
            self._consume_item(actor, item_name)
            self.next_live_damage = 2
            lines.append(f"{actor.display_name} 使用手锯，下一发子弹若为实弹，伤害 +1；若为空弹，效果消失。")
        elif item_name == ITEM_HANDCUFFS:
            if target_number is None:
                raise RouletteGameError("手铐需要目标编号，例如：轮盘道具 手铐 2")
            target = self.player_by_number(target_number)
            if target.user_id == actor.user_id:
                raise RouletteGameError("不能对自己使用手铐。")
            self._consume_item(actor, item_name)
            target.skip_turns += 1
            lines.append(f"{actor.display_name} 使用手铐，{target.display_name} 将跳过下一次行动。")
        elif item_name == ITEM_MAGNIFIER:
            self._consume_item(actor, item_name)
            if not self.shell_queue:
                self.reload_shells(deal_items=True)
                lines.append("弹队列为空，已重新装填。")
            shell_text = "实弹" if self.shell_queue[0] == SHELL_LIVE else "空弹"
            lines.append(f"{actor.display_name} 使用放大镜，公开查看：下一发是{shell_text}。")
        elif item_name == ITEM_EXPIRED_MEDICINE:
            self._consume_item(actor, item_name)
            if self.rng.choice([True, False]):
                actor.hp = min(actor.max_hp, actor.hp + 2)
                lines.append(f"{actor.display_name} 使用过期药，运气不错，恢复 2 点血。")
            else:
                actor.hp = max(0, actor.hp - 1)
                lines.append(f"{actor.display_name} 使用过期药，药效反噬，受到 1 点伤害。")
                if not actor.alive:
                    lines.append(f"{actor.display_name} 淘汰。")
                    self._advance_turn(lines)
        elif item_name == ITEM_PHONE:
            self._consume_item(actor, item_name)
            if not self.shell_queue:
                self.reload_shells(deal_items=True)
                lines.append("弹队列为空，已重新装填。")
            position = self.rng.randrange(len(self.shell_queue))
            shell_text = "实弹" if self.shell_queue[position] == SHELL_LIVE else "空弹"
            lines.append(f"{actor.display_name} 使用电话，公开情报：第 {position + 1} 发是{shell_text}。")
        elif item_name == ITEM_INVERTER:
            self._consume_item(actor, item_name)
            if not self.shell_queue:
                self.reload_shells(deal_items=True)
                lines.append("弹队列为空，已重新装填。")
            self.shell_queue[0] = (
                SHELL_BLANK if self.shell_queue[0] == SHELL_LIVE else SHELL_LIVE
            )
            lines.append(f"{actor.display_name} 使用换向器，当前弹已反转。")

        ended = self._append_finish_or_reload(lines)
        if self.phase == "playing":
            lines.append(f"当前行动：{self.current_player().display_name}")
        return RouletteActionResult("\n".join(lines), ended=ended)

    def normalize_item_name(self, name: str) -> str:
        aliases = {
            "烟": ITEM_CIGARETTE,
            "香烟": ITEM_CIGARETTE,
            "锯": ITEM_SAW,
            "手锯": ITEM_SAW,
            "啤酒": ITEM_BEER,
            "手铐": ITEM_HANDCUFFS,
            "铐": ITEM_HANDCUFFS,
            "镜": ITEM_MAGNIFIER,
            "放大镜": ITEM_MAGNIFIER,
            "药": ITEM_EXPIRED_MEDICINE,
            "过期药": ITEM_EXPIRED_MEDICINE,
            "电话": ITEM_PHONE,
            "换向器": ITEM_INVERTER,
            "逆转器": ITEM_INVERTER,
        }
        return aliases.get(name.strip(), name.strip())

    def apply_starting_hp(self) -> None:
        self.settings.normalize()
        for player in self.players:
            hp = self.settings.hp_max
            if self.settings.random_hp:
                hp = self.rng.randint(self.settings.hp_min, self.settings.hp_max)
            player.max_hp = hp
            player.hp = hp

    def reload_shells(self, *, deal_items: bool) -> None:
        self.settings.normalize()
        total = self.settings.shell_count_max
        if self.settings.random_shell_count:
            total = self.rng.randint(
                self.settings.shell_count_min,
                self.settings.shell_count_max,
            )
        total = max(2, total)
        live_count = self.rng.randint(1, total - 1)
        blank_count = total - live_count
        self.shell_queue = [SHELL_LIVE] * live_count + [SHELL_BLANK] * blank_count
        self.rng.shuffle(self.shell_queue)
        if deal_items:
            item_count = self.settings.item_count_max
            if self.settings.random_item_count:
                item_count = self.rng.randint(
                    self.settings.item_count_min,
                    self.settings.item_count_max,
                )
            for player in self.alive_players():
                for _ in range(item_count):
                    if len(player.items) >= self.settings.item_inventory_max:
                        break
                    player.items.append(self.rng.choice(ITEM_POOL))

    def alive_players(self) -> list[RoulettePlayer]:
        return [player for player in self.players if player.alive]

    def shell_count_text(self) -> str:
        live = self.shell_queue.count(SHELL_LIVE)
        blank = self.shell_queue.count(SHELL_BLANK)
        return f"当前弹队列：实弹 {live} / 空弹 {blank}"

    def format_status(self) -> str:
        lines = ["恶魔轮盘状态", self.shell_count_text()]
        if self.phase == "waiting":
            lines.append(f"阶段：等待开始（{len(self.players)}/{MAX_PLAYERS}）")
        elif self.phase == "playing":
            lines.append(f"阶段：进行中；当前行动：{self.current_player().display_name}")
        else:
            lines.append("阶段：已结束")
        for index, player in enumerate(self.players, start=1):
            marker = " <- 当前" if self.phase == "playing" and index - 1 == self.current_index else ""
            state = "存活" if player.alive else "淘汰"
            items = "、".join(player.items) if player.items else "无"
            lines.append(
                f"{index}. {player.display_name} | {state} | HP {player.hp}/{player.max_hp} | 道具：{items}{marker}"
            )
        return "\n".join(lines)

    def _consume_item(self, player: RoulettePlayer, item_name: str) -> None:
        player.items.remove(item_name)

    def _advance_turn(self, lines: list[str]) -> None:
        alive = self.alive_players()
        if len(alive) <= 1:
            return
        start = self.current_index
        while True:
            self.current_index = (self.current_index + 1) % len(self.players)
            candidate = self.players[self.current_index]
            if not candidate.alive:
                if self.current_index == start:
                    return
                continue
            if candidate.skip_turns > 0:
                candidate.skip_turns -= 1
                lines.append(f"{candidate.display_name} 被手铐限制，跳过本次行动。")
                if self.current_index == start:
                    return
                continue
            break
        lines.append(f"轮到 {self.players[self.current_index].display_name}。")

    def _append_finish_or_reload(self, lines: list[str]) -> bool:
        alive = self.alive_players()
        if len(alive) == 1:
            self.phase = "ended"
            lines.append(f"{alive[0].display_name} 获胜。")
            return True
        if self.phase == "playing" and not self.shell_queue:
            self.reload_shells(deal_items=True)
            lines.append("弹队列耗尽，重新装填并发放道具。")
            lines.append(self.shell_count_text())
        return False


def short_user_id(user_id: str) -> str:
    cleaned = str(user_id or "").strip()
    if not cleaned:
        return "unknown"
    return cleaned[-6:]
