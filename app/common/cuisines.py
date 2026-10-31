"""八大菜系。前端选择弹窗与后端校验共用同一份列表。"""

CUISINES = ("川菜", "鲁菜", "粤菜", "苏菜", "浙菜", "闽菜", "湘菜", "徽菜")


def normalize_cuisine(value: str | None) -> str:
    """去掉空白并校验；非法菜系返回空字符串。"""
    cuisine = (value or "").strip()
    return cuisine if cuisine in CUISINES else ""
