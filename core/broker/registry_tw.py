"""台灣券商範例框架(元大 / 富邦 / 凱基 / 群益 / 統一 / 元富)。

這些都是「只寫到定位」的程式碼骨架 —— 交易者複製後,填入自己的 API
key / 憑證與下單實作。本工具刻意不內建真實金鑰呼叫,以免在交易者尚未
準備好時誤觸真錢下單。

重要:台灣券商 API 多需臨櫃簽署風險預告書、申請審核(常需數個工作天),
且部分需安裝憑證或 Windows 元件。各範本的 notes 已標注關鍵前置條件。
所有資訊以各券商官方文件為準。
"""

from __future__ import annotations

from .registry import BrokerTemplate

# 各台股券商範本共用的 import 開頭(產出專案內為自包含的 broker_lib)
_TW_IMPORT = '''from broker_lib import (
    AccountInfo, BrokerAdapter, Order, OrderResult, OrderSide, OrderType, Position,
)
'''


# ── 元大 SPARK API(元大證券)──────────────────────────────
_YUANTA = BrokerTemplate(
    key="yuanta",
    name="元大 SPARK API(元大證券,台股/期貨)",
    market="tw_stock",
    sdk_install="依元大 SPARK API 官方說明安裝下單元件",
    notes=(
        "需為元大證券客戶,臨櫃親簽風險預告書後申請(約 5~10 個工作天)。"
        "元大僅提供下單 API 元件,不提供程式教學。請依官方 SPARK API "
        "文件確認實際的模組名稱與方法。測試請先用模擬/測試環境。"
    ),
    code=f'''"""元大 SPARK API 券商連接器(範例框架 — 請依官方文件填入實作)。

注意:元大 SPARK API 的實際 SDK 介面以官方文件為準,以下為對應到本工具
統一介面的骨架;每個 TODO 都需要你查官方文件後填上真實呼叫。
"""

{_TW_IMPORT}

class YuantaBroker(BrokerAdapter):
    name = "yuanta"
    is_live = True   # 真實下單 —— 受安全閘門保護

    def __init__(self, account: str, password: str, simulation: bool = True):
        super().__init__()
        self.account = account
        self.password = password
        self.simulation = simulation   # 強烈建議先用模擬環境
        self.api = None

    def connect(self) -> None:
        # TODO: 依元大 SPARK API 官方文件初始化並登入,例如:
        #   from yuanta_spark import SparkAPI   # 模組名以官方為準
        #   self.api = SparkAPI(simulation=self.simulation)
        #   self.api.login(self.account, self.password)
        raise NotImplementedError("請依元大 SPARK API 官方文件實作 connect()")

    def get_account(self) -> AccountInfo:
        # TODO: 查詢帳戶餘額/權益,組成 AccountInfo(currency="TWD")
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        # TODO: 查詢持倉,逐筆轉成 Position(代號, 數量, 均價)
        return []

    def get_price(self, symbol: str) -> float:
        # TODO: 取得 symbol 最新成交價
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()   # 真實下單前的安全檢查,務必保留
        order.validate()
        # TODO: 依官方 API 下單。台股數量單位請注意「股 vs 張」,
        #       本工具的 order.quantity 以「股」為單位(1 張 = 1000 股)。
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        # TODO: 依官方 API 取消委託
        raise NotImplementedError
''',
)


# ── 富邦新一代 API(富邦證券,fubon-neo)─────────────────────
_FUBON = BrokerTemplate(
    key="fubon",
    name="富邦新一代 API(富邦證券,台股/複委託/期貨)",
    market="tw_stock",
    sdk_install="pip install fubon-neo  # 以富邦官方公布的套件名為準",
    notes=(
        "需為富邦證券客戶並申請新一代 API、安裝憑證。請依富邦官方文件確認"
        "套件名稱、登入流程與憑證載入方式。測試請先用模擬環境。"
    ),
    code=f'''"""富邦新一代 API 券商連接器(範例框架 — 請依官方文件填入實作)。"""

{_TW_IMPORT}

class FubonBroker(BrokerAdapter):
    name = "fubon"
    is_live = True

    def __init__(self, account_id: str, password: str, cert_path: str = "",
                 cert_password: str = ""):
        super().__init__()
        self.account_id = account_id
        self.password = password
        self.cert_path = cert_path
        self.cert_password = cert_password
        self.sdk = None
        self.account = None

    def connect(self) -> None:
        # TODO: 依富邦新一代 API 官方文件初始化、登入並載入憑證,例如:
        #   from fubon_neo.sdk import FubonSDK   # 模組名以官方為準
        #   self.sdk = FubonSDK()
        #   accounts = self.sdk.login(self.account_id, self.password,
        #                             self.cert_path, self.cert_password)
        #   self.account = accounts.data[0]
        raise NotImplementedError("請依富邦新一代 API 官方文件實作 connect()")

    def get_account(self) -> AccountInfo:
        # TODO: 用 SDK 查詢銀行餘額 / 庫存市值,組成 AccountInfo(currency="TWD")
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        # TODO: 查詢庫存,逐筆轉成 Position
        return []

    def get_price(self, symbol: str) -> float:
        # TODO: 用富邦行情取得最新成交價
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        # TODO: 依官方 API 組委託單並下單。注意「股 vs 張」單位。
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        # TODO: 依官方 API 取消委託
        raise NotImplementedError
''',
)


# ── 凱基 KGI SUPER PY(凱基證券)────────────────────────────
_KGI = BrokerTemplate(
    key="kgi",
    name="凱基 KGI SUPER PY(凱基證券,台股+美股,功能最全)",
    market="tw_stock",
    sdk_install="pip install kgisuperpy",
    notes=(
        "需為凱基證券客戶並申請 API。KGI SUPER PY 同時支援台股與美股的"
        "交易/帳務/即時行情/回測。請依官方文件確認登入與憑證流程,"
        "測試先用模擬模式。"
    ),
    code=f'''"""凱基 KGI SUPER PY 券商連接器(範例框架 — 請依官方文件填入實作)。"""

{_TW_IMPORT}

class KGIBroker(BrokerAdapter):
    name = "kgi"
    is_live = True

    def __init__(self, account: str, password: str, simulation: bool = True):
        super().__init__()
        self.account = account
        self.password = password
        self.simulation = simulation
        self.api = None

    def connect(self) -> None:
        # TODO: 依 KGI SUPER PY 官方文件初始化並登入,例如:
        #   import kgisuperpy as kgi          # 介面以官方為準
        #   self.api = kgi.SuperPy(simulation=self.simulation)
        #   self.api.login(self.account, self.password)
        raise NotImplementedError("請依凱基 KGI SUPER PY 官方文件實作 connect()")

    def get_account(self) -> AccountInfo:
        # TODO: 查詢帳務,組成 AccountInfo(台股 TWD / 美股 USD)
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        # TODO: 查詢庫存
        return []

    def get_price(self, symbol: str) -> float:
        # TODO: 用即時行情取得最新成交價
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        # TODO: 依官方 API 下單。台股注意「股 vs 張」單位。
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        # TODO: 依官方 API 取消委託
        raise NotImplementedError
''',
)


# ── 群益 / 統一 / 元富 期貨類(共用一個範本)─────────────────
_TW_FUTURES = BrokerTemplate(
    key="tw_futures",
    name="群益 / 統一 / 元富 期貨類 API(台期)",
    market="tw_stock",
    sdk_install="群益:官方 SKCOM.dll(社群套件 pip install skcom);統一/元富依官方文件",
    notes=(
        "⚠️ 這是『期貨類』通用骨架。群益官方為 SKCOM.dll(Windows COM 元件,"
        "需註冊;社群 skcom 套件主要支援報價,下單/期貨需另行處理)。"
        "統一、元富各有自己的 API,介面不同。請依你實際使用的券商官方文件"
        "替換對應呼叫。期貨數量單位為『口』,非股數。"
    ),
    code=f'''"""台灣期貨類券商連接器(群益/統一/元富 通用範例框架)。

⚠️ 各券商 API 介面差異大,以下為對應到本工具統一介面的骨架,
   每個 TODO 都需依你實際券商的官方文件填上真實呼叫。
   期貨的數量單位是『口』,本工具 order.quantity 在期貨情境請以口數理解。
"""

{_TW_IMPORT}

class TaiwanFuturesBroker(BrokerAdapter):
    name = "tw_futures"
    is_live = True

    def __init__(self, account: str, password: str, broker: str = "capital"):
        super().__init__()
        self.account = account
        self.password = password
        self.broker = broker   # "capital"(群益) / "pfcf"(統一) / "masterlink"(元富)
        self.api = None

    def connect(self) -> None:
        # TODO: 依你的券商官方文件初始化。例如群益需先註冊 SKCOM.dll:
        #   import comtypes.client
        #   self.api = comtypes.client.CreateObject("...", interface=...)
        # 統一 / 元富 則用各自的 Python 套件,流程不同。
        raise NotImplementedError("請依你使用的期貨券商官方文件實作 connect()")

    def get_account(self) -> AccountInfo:
        # TODO: 查詢期貨保證金權益數,組成 AccountInfo(currency="TWD")
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        # TODO: 查詢未平倉部位(口數),逐筆轉成 Position
        return []

    def get_price(self, symbol: str) -> float:
        # TODO: 取得商品最新成交價(如 TXFG5 台指期)
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderResult:
        self._guard_live()
        order.validate()
        # TODO: 依官方 API 下期貨委託。數量為『口數』。
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        # TODO: 依官方 API 取消/刪單
        raise NotImplementedError
''',
)


TW_BROKER_TEMPLATES = [_YUANTA, _FUBON, _KGI, _TW_FUTURES]
