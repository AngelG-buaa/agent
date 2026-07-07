"""时间工具 —— 获取当前日期和时间。"""

from datetime import datetime

from tool import Tool


def _get_time() -> dict:
    now = datetime.now()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
    }


tool_get_time = Tool(
    name="get_current_time",
    description="获取当前日期和时间。",
    parameters={},
    fn=_get_time,
)
