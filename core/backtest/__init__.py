"""回測 / 樣本外驗證。"""

from .validate import OutOfSampleReport, walk_forward_validate

__all__ = ["OutOfSampleReport", "walk_forward_validate"]
