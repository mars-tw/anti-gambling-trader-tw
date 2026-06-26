"""統計裁決:判斷「優勢」還是「賭博」,並決定是否勸退。"""

from .judge import Verdict, VerdictLevel, judge

__all__ = ["Verdict", "VerdictLevel", "judge"]
