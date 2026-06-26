"""圖表範本與樣式預覽。

接入市面上主要的開源圖表庫,為交易者產生對應的圖表程式:
  - Lightweight Charts (TradingView 開源, Apache-2.0) — web 前端, 最接近專業看盤
  - Plotly (MIT) — 純 Python 互動圖表
  - mplfinance (BSD) — matplotlib 靜態 K 線
  - ECharts (Apache-2.0) — 功能全面, 中文生態好, web 前端

並提供 build_preview_page() 產生一個預覽 HTML,
把四種樣式並排,讓交易者先挑喜歡的再產生專案。
"""

from .registry import CHART_LIBS, ChartLib, get_chart_lib, list_chart_libs
from .preview import build_preview_page

__all__ = [
    "CHART_LIBS",
    "ChartLib",
    "get_chart_lib",
    "list_chart_libs",
    "build_preview_page",
]
