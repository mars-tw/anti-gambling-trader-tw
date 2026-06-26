"""互動式詐騙風險自我檢測清單。

讓使用者回答一連串「是 / 否」問題,依命中的高風險特徵,給出詐騙風險評分
與對應的反詐提醒。可由 CLI 互動執行,也可程式化傳入答案。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .patterns import SCAM_PATTERNS, ScamPattern, find_pattern


@dataclass
class CheckItem:
    """一道檢測題。"""

    key: str
    question: str
    pattern_code: str        # 對應的詐騙型態
    weight: int              # 命中時的風險分數(高風險特徵權重高)


# 檢測題庫:每題對應一種詐騙特徵。weight 越高代表越接近「鐵證」。
CHECK_ITEMS: list[CheckItem] = [
    CheckItem("pulled_in", "是否有人『主動』把你拉進投資 LINE / Telegram 群?",
              "fake_stock_group", 2),
    CheckItem("teacher_calls", "群裡是否有『老師 / 分析師』每天報明牌、帶你進出場?",
              "fake_stock_group", 2),
    CheckItem("upgrade_vip", "是否被要求『升級 VIP / 進二群 / 付費才能拿到更準的單』?",
              "fake_stock_group", 3),
    CheckItem("member_screenshots", "群裡是否充斥『學員獲利截圖、感謝老師』的訊息?",
              "fake_guru", 2),
    CheckItem("guaranteed", "對方是否宣稱『保證獲利 / 穩賺不賠 / 零風險高報酬』?",
              "guaranteed_return", 3),
    CheckItem("high_winrate_only", "對方是否只強調『高勝率』,卻從不談『賠的時候賠多少』?",
              "guaranteed_return", 2),
    CheckItem("urgency", "是否被催促『現在不進場就錯過』,讓你來不及冷靜思考?",
              "guaranteed_return", 1),
    CheckItem("guru_flex", "對方是否炫耀豪車 / 名錶 / 財富自由,暗示跟著就能致富?",
              "fake_guru", 1),
    CheckItem("only_screenshots", "你看到的『績效』是否只有精選截圖,沒有完整連續的紀錄?",
              "fake_guru", 2),
    CheckItem("transfer_platform", "是否被要求把錢匯到某個『平台 / 錢包 / 客服指定帳戶』?",
              "fake_platform", 3),
    CheckItem("cant_withdraw", "是否出金時被要求『先繳稅 / 刷流水 / 付解凍金』才能領回?",
              "fake_platform", 3),
    CheckItem("small_withdraw_bait", "是否一開始能小額出金,等你加碼後就領不出來?",
              "fake_platform", 3),
]


@dataclass
class ScamCheckResult:
    """檢測結果。"""

    score: int                              # 命中總風險分數
    max_score: int                          # 滿分
    risk_level: str                         # "極高" | "高" | "中" | "低"
    hit_patterns: list[ScamPattern] = field(default_factory=list)
    headline: str = ""
    advice: list[str] = field(default_factory=list)

    @property
    def risk_pct(self) -> float:
        return self.score / self.max_score if self.max_score else 0.0


def evaluate(answers: dict[str, bool]) -> ScamCheckResult:
    """依答案(key -> 是否命中)計算詐騙風險。"""
    max_score = sum(it.weight for it in CHECK_ITEMS)
    score = 0
    hit_codes: set[str] = set()
    # 任何一題權重 3(鐵證級)被命中,直接視為極高風險
    hard_hit = False
    for it in CHECK_ITEMS:
        if answers.get(it.key):
            score += it.weight
            hit_codes.add(it.pattern_code)
            if it.weight >= 3:
                hard_hit = True

    hit_patterns = [p for p in SCAM_PATTERNS if p.code in hit_codes]

    pct = score / max_score if max_score else 0.0
    if hard_hit or pct >= 0.5:
        risk_level = "極高"
        headline = "🚨 詐騙風險極高:你描述的情境命中了典型投資詐騙的關鍵特徵。"
    elif pct >= 0.3:
        risk_level = "高"
        headline = "⚠️ 詐騙風險偏高:出現多個常見詐騙特徵,請高度警覺。"
    elif pct > 0:
        risk_level = "中"
        headline = "🟡 有一些可疑徵兆,建議對照下方反詐提醒再三確認。"
    else:
        risk_level = "低"
        headline = "✅ 目前沒有命中明顯的詐騙特徵 — 但仍請保持基本警覺。"

    advice: list[str] = []
    if score > 0:
        advice.append(
            "把對方給過的所有『推薦 / 帶單』完整記錄下來(含失敗的),"
            "用本工具 analyze 算期望值與顯著性 —— 別只看他挑給你的成功案例。"
        )
    for p in hit_patterns:
        advice.append(f"【{p.name}】{p.rebuttal}")
    if risk_level in ("極高", "高"):
        advice += [
            "不要再匯任何錢、不要『升級』、不要下載對方指定的 App。",
            "保留對話與匯款紀錄。台灣可撥打『165 反詐騙諮詢專線』查證與求助。",
        ]

    return ScamCheckResult(
        score=score,
        max_score=max_score,
        risk_level=risk_level,
        hit_patterns=hit_patterns,
        headline=headline,
        advice=advice,
    )


def run_scam_check(input_fn=input, output_fn=print) -> ScamCheckResult:
    """互動式跑一遍檢測清單。回傳結果。

    input_fn / output_fn 可注入,方便測試;預設用標準輸入輸出。
    """
    output_fn("=" * 60)
    output_fn("           反詐投資王 — 投資詐騙風險自我檢測")
    output_fn("=" * 60)
    output_fn("請依你目前遇到的情況,回答以下問題(y = 是 / n = 否):\n")

    answers: dict[str, bool] = {}
    for i, it in enumerate(CHECK_ITEMS, 1):
        raw = str(input_fn(f"  {i}. {it.question} [y/n] ")).strip().lower()
        answers[it.key] = raw in ("y", "yes", "是", "1", "有")

    result = evaluate(answers)

    output_fn("")
    output_fn("=" * 60)
    output_fn(f"  風險評分:{result.score}/{result.max_score}"
              f"({result.risk_pct:.0%})  風險等級:{result.risk_level}")
    output_fn(f"  {result.headline}")
    output_fn("=" * 60)
    if result.advice:
        output_fn("\n【給你的反詐提醒】")
        for a in result.advice:
            output_fn(f"  • {a}")
    output_fn(
        "\n記住:真正的投資優勢經得起統計檢定與時間考驗,"
        "不需要靠『拉群、保證、急迫感』來說服你。"
    )
    return result
