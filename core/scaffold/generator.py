"""專案腳架產生器。"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..broker.registry import BROKER_TEMPLATES
from ..charts.registry import get_chart_lib
from ..onboarding import StageAssessment
from ..verdict.judge import Verdict
from . import templates as T

# 專案名只允許安全字元(它會被當成資料夾名,且嵌入產出的程式碼)。
# 拒絕引號、大括號、反斜線、路徑分隔、`..` 等,杜絕路徑穿越與產碼注入。
_SAFE_PROJECT_NAME = re.compile(r"^[\w一-鿿][\w一-鿿\-. ]{0,63}$")
# 標的代號:英數、底線、點、斜線(如 BTC/USDT)。拒絕引號等危險字元。
_SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9_.\-/]{1,32}$")


@dataclass
class ScaffoldOptions:
    """產生個人交易程式專案的選項。"""

    project_name: str = "my_trading_bot"
    broker: str = "paper"               # 完整清單見 anti-gambling-trader brokers
    chart: str = "lightweight"          # lightweight | plotly | mplfinance | echarts
    market: str = "us_stock"            # tw_stock | us_stock | crypto
    symbols: list[str] = field(default_factory=lambda: ["AAPL"])
    verdict: Optional[Verdict] = None   # 若有分析過交易紀錄,帶入裁決以嵌入安全閘門
    stage: Optional[StageAssessment] = None  # 完整 stage/OOS 安全分流；缺少時一律不開 live

    def validate(self) -> None:
        """驗證輸入,拒絕會造成路徑穿越或產碼注入的危險值。"""
        name = (self.project_name or "").strip()
        if not _SAFE_PROJECT_NAME.match(name) or ".." in name:
            raise ValueError(
                f"專案名稱不合法: {self.project_name!r}。"
                "只允許中英文、數字、底線、減號、點與空白(不可含引號 / 斜線 / .. 等)。"
            )
        self.project_name = name
        cleaned = []
        for s in self.symbols:
            s = str(s).strip()
            if not _SAFE_SYMBOL.match(s):
                raise ValueError(
                    f"標的代號不合法: {s!r}。只允許英數、底線、點、減號、斜線。"
                )
            cleaned.append(s)
        self.symbols = cleaned or ["AAPL"]


@dataclass
class GeneratedFile:
    relpath: str
    content: str


def generate_project(opts: ScaffoldOptions) -> list[GeneratedFile]:
    """產生整個專案的檔案清單(尚未寫入磁碟)。"""
    opts.validate()   # 先驗證輸入,擋下路徑穿越與產碼注入
    files: list[GeneratedFile] = []

    chart_lib = get_chart_lib(opts.chart)
    broker_tmpl = BROKER_TEMPLATES.get(opts.broker)  # paper 不在註冊表中,為 None

    # 真錢旗標必須依完整 stage（含 OOS、幣別與回撤基準），不能只看
    # 樣本內 verdict。沒有 stage 也代表尚未完成驗證，保守維持 false。
    live_stage_approved = bool(
        opts.stage and opts.stage.code == "tiny_live_validation"
    )
    discouraged = not live_stage_approved
    verdict_level = opts.verdict.level.value if opts.verdict else "unknown"
    if opts.stage:
        verdict_headline = f"{opts.stage.title}：{opts.stage.reason}"
    else:
        verdict_headline = (
            opts.verdict.headline if opts.verdict
            else "(尚未完成含樣本外與風險基準的交易階段分析)"
        )

    # ── README ──
    files.append(GeneratedFile("README.md", T.readme(opts, chart_lib, broker_tmpl,
                                                     discouraged, verdict_headline)))

    # ── 設定檔 ──
    files.append(GeneratedFile("config.example.yaml",
                               T.config_yaml(opts, discouraged, verdict_level)))

    # ── 依賴 ──
    files.append(GeneratedFile("requirements.txt",
                               T.requirements(chart_lib, broker_tmpl)))

    # ── 策略檔(進出場規則待填,含安全閘門)──
    files.append(GeneratedFile("strategy.py",
                               T.strategy_py(opts, discouraged, verdict_level,
                                             verdict_headline)))

    # ── 券商接入 ──
    files.append(GeneratedFile("broker_setup.py",
                               T.broker_setup_py(opts, broker_tmpl)))
    if broker_tmpl is not None:
        # 把選定券商的範例框架也寫進專案,方便交易者填寫
        files.append(GeneratedFile(f"brokers/{broker_tmpl.key}_broker.py",
                                   broker_tmpl.code))
        files.append(GeneratedFile("brokers/__init__.py", ""))

    # ── 圖表模組 ──
    files.append(GeneratedFile("charting.py", chart_lib.module_code))

    # ── 自包含的券商函式庫(讓專案不依賴反詐投資王本體即可執行)──
    # 這是修正「ModuleNotFoundError: No module named 'core'」的關鍵:
    # 產出專案會放到獨立目錄,那裡沒有 core 套件,因此把券商介面 +
    # PaperBroker 直接內嵌成專案內的 broker_lib.py。
    files.append(GeneratedFile("broker_lib.py", _build_broker_lib()))

    # ── 主程式(預設紙上模擬)──
    files.append(GeneratedFile("main.py",
                               T.main_py(opts, chart_lib, broker_tmpl, discouraged)))

    # ── 資料餵送(回測 / 即時的統一介面)──
    files.append(GeneratedFile("data_feed.py", T.data_feed_py(opts)))

    # ── .gitignore(避免把金鑰、個人資料上傳)──
    files.append(GeneratedFile(".gitignore", T.gitignore()))

    return files


def _build_broker_lib() -> str:
    """組出自包含的 broker_lib.py:券商介面 + PaperBroker,無外部相依。

    直接取用本專案 base.py 與 paper.py 的原始碼,移除 paper.py 對 base 的
    相對匯入(因為兩者已合併在同一檔)。這樣未來修改本體,產出專案會自動同步,
    不會發生「範本與本體脫節」的問題。
    """
    broker_dir = Path(__file__).resolve().parent.parent / "broker"
    base_src = (broker_dir / "base.py").read_text(encoding="utf-8")
    paper_src = (broker_dir / "paper.py").read_text(encoding="utf-8")

    # 移除 paper.py 開頭對 .base 的相對匯入(類別將合併在同一檔)。
    # 同時支援括號多行與單行兩種寫法 —— 只支援一種的話,日後 paper.py
    # 改寫 import 形式會讓產出專案帶著壞掉的相對匯入,靜默炸在使用者端。
    paper_src = re.sub(r"from \.base import \([^)]*\)\n", "", paper_src)
    paper_src = re.sub(r"from \.base import [^\n(]+\n", "", paper_src)
    if "from .base" in paper_src or "from . import" in paper_src:
        raise RuntimeError(
            "broker_lib 合併失敗:paper.py 仍殘留相對匯入,"
            "請更新 _build_broker_lib() 的剝除規則"
        )

    # 兩個檔各自的模組 docstring 與 `from __future__` 都要剝掉,
    # 因為合併後整檔只能有一個檔首 docstring、且 `from __future__` 必須
    # 緊接其後(否則 SyntaxError)。用 ast 安全地剝除各自的開頭。
    def _strip_header(src: str) -> str:
        # 移除開頭的模組 docstring(三引號區塊)
        src = re.sub(r'^\s*"""(?:[^"]|"(?!""))*"""\s*\n', "", src, count=1, flags=re.DOTALL)
        # 移除 from __future__ 匯入行(稍後統一加一行在最前)
        src = re.sub(r"from __future__ import [^\n]*\n", "", src)
        return src.lstrip("\n")

    base_body = _strip_header(base_src)
    paper_body = _strip_header(paper_src)

    header = (
        '"""自包含的券商函式庫(由反詐投資王腳架產生)。\n\n'
        "本檔內含交易介面與 PaperBroker(紙上模擬),讓本專案不依賴\n"
        "反詐投資王本體即可獨立執行。真實券商連接器請見 brokers/ 目錄。\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
    )
    divider = "\n\n# " + "=" * 70 + "\n# PaperBroker(紙上模擬)\n# " + "=" * 70 + "\n\n"
    return header + base_body + divider + paper_body


def write_project(opts: ScaffoldOptions, dest_dir: str | Path) -> Path:
    """產生專案並寫入指定目錄。回傳專案根目錄路徑。"""
    # 先清理/驗證 project_name，再用它組路徑；不可讓未 strip 的名稱先決定
    # 寫入位置，之後才在 generate_project() 裡悄悄改名。
    opts.validate()
    destination = Path(dest_dir)
    root = destination / opts.project_name
    files = generate_project(opts)

    def is_link_or_reparse(path: Path) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        attrs = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return path.is_symlink() or bool(attrs & reparse_flag)

    if is_link_or_reparse(destination):
        raise ValueError(f"輸出目錄不可是 symlink/reparse point: {destination}")
    if is_link_or_reparse(root):
        raise ValueError(f"專案根目錄不可是 symlink/reparse point: {root}")
    if root.exists():
        if not root.is_dir():
            raise ValueError(f"專案目的地已存在且不是目錄: {root}")
        if any(root.iterdir()):
            raise ValueError(
                f"專案目的地已存在且非空，為避免覆蓋既有程式已拒絕: {root}"
            )

    # 所有碰撞/穿越/連結檢查必須在 mkdir 或 write 之前一次完成，這樣偵測到
    # 問題時不會留下半套專案或覆蓋使用者的 strategy.py。
    root_resolved = root.resolve(strict=False)
    targets: list[tuple[GeneratedFile, Path]] = []
    seen_targets: set[str] = set()
    for generated in files:
        rel = Path(generated.relpath)
        if (
            not generated.relpath
            or rel.is_absolute()
            or ".." in rel.parts
        ):
            raise ValueError(f"產出檔案路徑不合法: {generated.relpath!r}")
        target = root / rel
        target_resolved = target.resolve(strict=False)
        try:
            common = os.path.commonpath((str(root_resolved), str(target_resolved)))
        except ValueError as exc:
            raise ValueError(f"產出檔案越過專案根目錄: {generated.relpath!r}") from exc
        if os.path.normcase(common) != os.path.normcase(str(root_resolved)):
            raise ValueError(f"產出檔案越過專案根目錄: {generated.relpath!r}")
        target_key = os.path.normcase(str(target_resolved))
        if target_key in seen_targets:
            raise ValueError(f"產出檔案路徑重複: {generated.relpath!r}")
        seen_targets.add(target_key)

        current = target
        while current != root:
            if is_link_or_reparse(current):
                raise ValueError(
                    f"產出目標不可經過 symlink/reparse point: {current}"
                )
            current = current.parent
        targets.append((generated, target))

    root.mkdir(parents=True, exist_ok=True)
    for generated, target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        # The preflight above cannot reserve a pathname against a concurrent
        # creator. Exclusive creation makes that race fail closed instead of
        # replacing a file that appeared after preflight.
        with target.open("x", encoding="utf-8") as handle:
            handle.write(generated.content)
    return root
