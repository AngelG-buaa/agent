"""当前时间工具。"""

from datetime import datetime

from tooling.base import Tool, ToolParameter


class GetTimeTool(Tool):
    def __init__(self):
        super().__init__(
            name="get_current_time",
            description="获取当前日期和时间。",
        )

    def get_parameters(self):
        return []

    def run(self, params):
        now = datetime.now()
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
        }
