"""Static element / mission / resource metadata for New-Star games.

The IDs below are the standard New-Star / 2Moons IDs.  A private game
instance may add or rename elements, so every parser still reads the live
HTML; these maps are only used for convenient name -> id lookups and for
pretty names when the page does not expose one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Iterable, Mapping, Optional, Union


@dataclass(frozen=True)
class Element:
    """A game element (building, technology, ship or defence)."""

    id: int
    name_zh: str
    name_en: str

    @property
    def label(self) -> str:
        return self.name_zh or self.name_en


def _elements(*rows: Iterable[object]) -> Dict[int, Element]:
    result: Dict[int, Element] = {}
    for row in rows:
        eid, zh, en = row  # type: ignore[misc]
        result[int(eid)] = Element(int(eid), str(zh), str(en))
    return result


BUILDINGS: Dict[int, Element] = _elements(
    (1, "合金冶炼厂", "Metal Mine"),
    (2, "水晶催化厂", "Crystal Mine"),
    (3, "重氢精炼厂", "Deuterium Synthesizer"),
    (4, "幽能发电站", "Energy Plant"),
    (6, "帝国大学", "Imperial University"),
    (12, "反幽能反应堆", "Anti-Energy Reactor"),
    (14, "自律工厂", "Robot Factory"),
    (15, "帝国主机", "Nanite Factory"),
    (21, "星际船坞", "Shipyard"),
    (22, "合金仓库", "Metal Storage"),
    (23, "水晶仓库", "Crystal Storage"),
    (24, "重氢仓库", "Deuterium Tank"),
    (31, "科研实验室", "Research Lab"),
    (33, "影子空间", "Terraformer"),
    (34, "联盟空间站", "Alliance Depot"),
    (41, "月球基地", "Lunar Base"),
    (42, "感应器方阵", "Sensor Phalanx"),
    (43, "星门", "Jump Gate"),
    (44, "星际导弹井", "Missile Silo"),
    (81, "资源处理单元", "Resource Processor"),
    (82, "防御建设单元", "Defense Builder"),
    (83, "军事生产单元", "Military Production Unit"),
    (84, "科研加速单元", "Research Accelerator"),
)

TECHNOLOGIES: Dict[int, Element] = _elements(
    (106, "间谍技术", "Espionage Technology"),
    (108, "计算机技术", "Computer Technology"),
    (109, "武器技术", "Weapons Technology"),
    (110, "护盾技术", "Shielding Technology"),
    (111, "装甲技术", "Armour Technology"),
    (113, "能源技术", "Energy Technology"),
    (114, "超时空技术", "Hyperspace Technology"),
    (115, "工质推进器", "Combustion Drive"),
    (117, "脉冲推进器", "Impulse Drive"),
    (118, "超空间推进器", "Hyperspace Drive"),
    (120, "激光技术", "Laser Technology"),
    (121, "离子技术", "Ion Technology"),
    (122, "等离子技术", "Plasma Technology"),
    (123, "星际科研网络", "Intergalactic Research Network"),
    (124, "探险技术", "Expedition Technology"),
    (131, "金属冶炼技术", "Metal Smelting Technology"),
    (132, "晶体催化技术", "Crystal Catalysis Technology"),
    (133, "重氢提纯技术", "Deuterium Purification Technology"),
    (199, "引力子技术", "Graviton Technology"),
)

SHIPS: Dict[int, Element] = _elements(
    (210, "间谍探针", "Espionage Probe"),
    (212, "太阳能卫星", "Solar Satellite"),
    (204, "轻型战机", "Light Fighter"),
    (202, "轻型运输舰", "Small Cargo"),
    (203, "重型运输舰", "Large Cargo"),
    (205, "重型战机", "Heavy Fighter"),
    (208, "殖民舰", "Colony Ship"),
    (209, "回收舰", "Recycler"),
    (206, "巡洋舰", "Cruiser"),
    (217, "巨型运输舰", "Huge Cargo"),
    (207, "战列舰", "Battleship"),
    (215, "战列巡洋舰", "Battlecruiser"),
    (213, "星际战舰", "Interstellar Battleship"),
    (211, "行星轰炸机", "Bomber"),
    (225, "毁灭者", "Annihilator"),
    (219, "巨型回收舰", "Giant Recycler"),
    (223, "舰队保护移动掩体", "Fleet Shield"),
    (226, "帝国驱逐舰", "Imperial Destroyer"),
    (214, "死星", "Deathstar"),
    (220, "暗物质收集舰", "Dark Matter Collector"),
    (216, "黑月", "Black Moon"),
    (227, "帝国护航舰", "Imperial Escort"),
    (228, "黑暗流浪者", "Dark Wanderer"),
    (222, "死亡收割者", "Death Reaper"),
    (218, "化身", "Avatar"),
)

DEFENSES: Dict[int, Element] = _elements(
    (402, "轻型激光塔", "Light Laser Tower"),
    (401, "导弹发射器", "Rocket Launcher"),
    (405, "离子炮", "Ion Cannon"),
    (403, "重型激光塔", "Heavy Laser Tower"),
    (404, "高斯炮", "Gauss Cannon"),
    (406, "等离子炮", "Plasma Cannon"),
    (416, "反物质炮", "Anti-Matter Cannon"),
    (417, "快子炮", "Tachyon Cannon"),
    (407, "小型护盾发生器", "Small Shield Dome"),
    (418, "光子炮", "Photon Cannon"),
    (408, "大型护盾发生器", "Large Shield Dome"),
    (409, "行星护盾发生器", "Planetary Shield Generator"),
    (412, "轻子炮", "Lepton Cannon"),
    (410, "引力子炮", "Graviton Cannon"),
    (413, "质子炮", "Proton Cannon"),
    (419, "质量加速器", "Mass Accelerator"),
    (414, "裂解炮", "Disintegration Cannon"),
    (415, "量子炮", "Quantum Cannon"),
    (411, "轨道防御平台", "Orbital Defense Platform"),
    (502, "拦截导弹", "Interceptor Missile"),
    (503, "星际导弹", "Interplanetary Missile"),
)

CATEGORY_BUILDING = "building"
CATEGORY_TECHNOLOGY = "technology"
CATEGORY_SHIP = "ship"
CATEGORY_DEFENSE = "defense"

CATEGORY_MAPS: Dict[str, Dict[int, Element]] = {
    CATEGORY_BUILDING: BUILDINGS,
    CATEGORY_TECHNOLOGY: TECHNOLOGIES,
    CATEGORY_SHIP: SHIPS,
    CATEGORY_DEFENSE: DEFENSES,
}

ALIASES: Mapping[str, int] = {
    # buildings
    "metal": 1,
    "metal_mine": 1,
    "合金": 1,
    "金属": 1,
    "金属矿": 1,
    "crystal": 2,
    "crystal_mine": 2,
    "水晶": 2,
    "deuterium": 3,
    "deut": 3,
    "deuterium_synthesizer": 3,
    "重氢": 3,
    "energy": 4,
    "energy_plant": 4,
    "solar_plant": 4,
    "幽能": 4,
    "robot": 14,
    "robot_factory": 14,
    "自律工厂": 14,
    "nanite": 15,
    "nanite_factory": 15,
    "帝国主机": 15,
    "shipyard": 21,
    "船坞": 21,
    "lab": 31,
    "research_lab": 31,
    "实验室": 31,
    # technologies
    "espionage": 106,
    "spy": 106,
    "间谍技术": 106,
    "computer": 108,
    "计算机技术": 108,
    "weapons": 109,
    "武器技术": 109,
    "shielding": 110,
    "护盾技术": 110,
    "armour": 111,
    "armor": 111,
    "装甲技术": 111,
    "energy_tech": 113,
    "能源技术": 113,
    "combustion": 115,
    "combustion_drive": 115,
    "工质推进器": 115,
    "impulse": 117,
    "impulse_drive": 117,
    "脉冲推进器": 117,
    "hyperspace": 118,
    "hyperspace_drive": 118,
    "超空间推进器": 118,
    # ships
    "small_cargo": 202,
    "sc": 202,
    "轻型运输舰": 202,
    "large_cargo": 203,
    "lc": 203,
    "重型运输舰": 203,
    "light_fighter": 204,
    "lf": 204,
    "轻型战机": 204,
    "heavy_fighter": 205,
    "hf": 205,
    "重型战机": 205,
    "cruiser": 206,
    "巡洋舰": 206,
    "battleship": 207,
    "bb": 207,
    "战列舰": 207,
    "colony_ship": 208,
    "殖民舰": 208,
    "recycler": 209,
    "回收舰": 209,
    "espionage_probe": 210,
    "probe": 210,
    "间谍探针": 210,
    "bomber": 211,
    "行星轰炸机": 211,
    "solar_satellite": 212,
    "ss": 212,
    "太阳能卫星": 212,
    "destroyer": 213,
    "星际战舰": 213,
    "deathstar": 214,
    "ds": 214,
    "死星": 214,
    "battlecruiser": 215,
    "bc": 215,
    "战列巡洋舰": 215,
    # defences
    "rocket_launcher": 401,
    "rl": 401,
    "导弹发射器": 401,
    "light_laser": 402,
    "ll": 402,
    "轻型激光塔": 402,
    "heavy_laser": 403,
    "hl": 403,
    "重型激光塔": 403,
    "gauss": 404,
    "高斯炮": 404,
    "ion": 405,
    "离子炮": 405,
    "plasma": 406,
    "等离子炮": 406,
    "small_shield": 407,
    "小型护盾发生器": 407,
    "large_shield": 408,
    "大型护盾发生器": 408,
}


class Mission(IntEnum):
    ATTACK = 1
    ACS_ATTACK = 2
    TRANSPORT = 3
    DEPLOY = 4
    HOLD = 5
    ESPIONAGE = 6
    COLONIZE = 7
    RECYCLE = 8
    DESTROY = 9
    MISSILE_ATTACK = 10
    DARK_MATTER = 11
    EXPEDITION = 15
    NEBULA_EXPEDITION = 18


MISSION_NAMES_ZH: Dict[int, str] = {
    1: "攻击",
    2: "联合攻击",
    3: "运输",
    4: "派遣",
    5: "驻留",
    6: "侦察",
    7: "殖民",
    8: "回收",
    9: "摧毁",
    10: "导弹攻击",
    11: "暗物质探查",
    15: "远征",
    18: "星云远征",
}

MISSION_NAMES_EN: Dict[int, str] = {
    1: "Attack",
    2: "ACS Attack",
    3: "Transport",
    4: "Deploy",
    5: "Hold",
    6: "Espionage",
    7: "Colonize",
    8: "Recycle",
    9: "Destroy",
    10: "Missile Attack",
    11: "Dark Matter Investigation",
    15: "Expedition",
    18: "Nebula Expedition",
}

MISSION_NAME_TO_ID: Dict[str, int] = {}
for _names in (MISSION_NAMES_EN, MISSION_NAMES_ZH):
    for _mid, _name in _names.items():
        MISSION_NAME_TO_ID.setdefault(_name.casefold(), _mid)

# Common short mission names.
MISSION_NAME_TO_ID.update(
    {
        "transport": 3,
        "deploy": 4,
        "station": 4,
        "spy": 6,
        "espionage": 6,
        "colonize": 7,
        "colonise": 7,
        "recycle": 8,
        "expedition": 15,
        "exp": 15,
    }
)


class PlanetType(IntEnum):
    PLANET = 1
    DEBRIS = 2
    MOON = 3


class ResourceId(IntEnum):
    METAL = 901
    CRYSTAL = 902
    DEUTERIUM = 903
    ENERGY = 911
    BUILD_POINTS = 912
    TECH_POINTS = 913
    FLEET_POINTS = 914
    DEFENSE_POINTS = 915
    MISSILE_POINTS = 916
    DARK_MATTER = 921
    ANTI_ENERGY = 922
    STARDUST = 923
    CHESTS = 924


RESOURCE_NAMES_ZH: Dict[int, str] = {
    901: "星际合金",
    902: "高能水晶",
    903: "重氢",
    911: "幽能",
    912: "建造点",
    913: "研发点",
    914: "船坞点",
    915: "防御点",
    916: "导弹点",
    921: "暗物质",
    922: "反幽能",
    923: "星尘",
    924: "宝箱",
}

RESOURCE_NAMES_EN: Dict[int, str] = {
    901: "Metal",
    902: "Crystal",
    903: "Deuterium",
    911: "Energy",
    912: "Build Points",
    913: "Research Points",
    914: "Shipyard Points",
    915: "Defense Points",
    916: "Missile Points",
    921: "Dark Matter",
    922: "Anti-Energy",
    923: "Stardust",
    924: "Chests",
}

#: Maps the id of a "current_*" span in the top navigation to a resource id.
RESOURCE_ELEMENT_IDS: Mapping[str, int] = {
    "current_metal": 901,
    "current_crystal": 902,
    "current_deuterium": 903,
    "current_energy": 911,
    "current_build": 912,
    "current_tech": 913,
    "current_fleet": 914,
    "current_defense": 915,
    "current_missile": 916,
    "current_darkmatter": 921,
    "current_antimatter": 922,
    "current_stardust": 923,
    "current_container": 924,
}

#: Resource ids that can be transported by a fleet.
TRANSPORTABLE_RESOURCE_IDS = (901, 902, 903)


def element_name(element_id: int, category: str) -> str:
    element = CATEGORY_MAPS.get(category, {}).get(int(element_id))
    return element.label if element else f"Element {element_id}"


def mission_name(mission_id: int, chinese: bool = True) -> str:
    names = MISSION_NAMES_ZH if chinese else MISSION_NAMES_EN
    return names.get(int(mission_id), f"Mission {mission_id}")


def resolve_element(
    value: Union[int, str],
    category: str,
    *,
    aliases: Optional[Mapping[str, int]] = None,
) -> int:
    """Resolve an element id, a Chinese/English name or a common alias to an id."""

    mapping = CATEGORY_MAPS[category]
    if isinstance(value, bool):
        raise TypeError("element id must be an int or a name, not bool")
    if isinstance(value, int):
        eid = int(value)
        if eid in mapping:
            return eid
        raise ValueError(f"unknown {category} id: {eid}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"empty {category} name")
    if text.isdigit():
        return resolve_element(int(text), category, aliases=aliases)

    folded = text.casefold()
    for eid, element in mapping.items():
        if folded in (element.name_zh.casefold(), element.name_en.casefold()):
            return eid

    alias_table = ALIASES if aliases is None else aliases
    if folded in alias_table:
        eid = int(alias_table[folded])
        if eid in mapping:
            return eid

    # Also accept e.g. "metal_mine" / "Metal Mine" without exact case.
    normalized = folded.replace("-", "_").replace(" ", "_")
    for eid, element in mapping.items():
        en = element.name_en.casefold().replace(" ", "_")
        if normalized == en:
            return eid
    raise ValueError(f"unknown {category} name: {value!r}")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

#: Message category ids used by ``game.php?page=messages&messcat=<id>``.
MESSAGE_NAMES_ZH: Dict[int, str] = {
    0: "间谍报告",
    1: "玩家消息",
    2: "盟友消息",
    3: "战斗报告",
    4: "系统消息",
    5: "运输报告",
    15: "探险报告",
    50: "游戏消息",
    99: "建造报告",
    100: "所有消息",
    200: "归档",
    999: "发件箱",
}

MESSAGE_NAMES_EN: Dict[int, str] = {
    0: "Espionage",
    1: "Player",
    2: "Alliance",
    3: "Battle",
    4: "System",
    5: "Transport",
    15: "Expedition",
    50: "Game",
    99: "Construction",
    100: "All",
    200: "Archive",
    999: "Outbox",
}

MESSAGE_CATEGORY_ALIASES: Dict[str, int] = {
    "all": 100,
    "inbox": 100,
    "all_messages": 100,
    "espionage": 0,
    "spy": 0,
    "player": 1,
    "players": 1,
    "pm": 1,
    "alliance": 2,
    "ally": 2,
    "battle": 3,
    "combat": 3,
    "cr": 3,
    "system": 4,
    "sys": 4,
    "transport": 5,
    "expedition": 15,
    "exp": 15,
    "explore": 15,
    "game": 50,
    "construction": 99,
    "build": 99,
    "archive": 200,
    "archived": 200,
    "outbox": 999,
    "sent": 999,
}

MESSAGE_NAME_TO_ID: Dict[str, int] = {}
for _names in (MESSAGE_NAMES_ZH, MESSAGE_NAMES_EN):
    for _cid, _name in _names.items():
        MESSAGE_NAME_TO_ID.setdefault(_name.casefold(), _cid)
MESSAGE_NAME_TO_ID.update(MESSAGE_CATEGORY_ALIASES)


def message_category_name(category: int, chinese: bool = True) -> str:
    names = MESSAGE_NAMES_ZH if chinese else MESSAGE_NAMES_EN
    return names.get(int(category), f"Category {category}")


def resolve_message_category(value: Union[int, str]) -> int:
    """Resolve a category id, a Chinese/English label or an alias."""

    if isinstance(value, bool):
        raise TypeError("message category must be an int or a name, not bool")
    if isinstance(value, int):
        category = int(value)
        if category in MESSAGE_NAMES_ZH:
            return category
        raise ValueError(f"unknown message category id: {category}")
    text = str(value).strip()
    if not text:
        raise ValueError("empty message category")
    if text.isdigit():
        return resolve_message_category(int(text))

    category = MESSAGE_NAME_TO_ID.get(text.casefold())
    if category is None:
        raise ValueError(f"unknown message category: {value!r}")
    return category
