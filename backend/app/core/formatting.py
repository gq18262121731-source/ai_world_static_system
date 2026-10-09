"""生命体征的统一展示格式, 避免同一个数值在不同界面里一会儿整数一会儿小数。"""

# 体温保留一位小数, 其余生命体征按整数展示
FLOAT_VITALS = {"temperature"}


def format_vital(key: str, value: float | int | None) -> str:
    if value is None:
        return "--"
    if key in FLOAT_VITALS:
        return f"{float(value):.1f}"
    return str(int(round(float(value))))
