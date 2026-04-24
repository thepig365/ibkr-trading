"""MTF 未入场诊断（Prompt 10D）：解释为何未达 FULL_ALIGNMENT，不下单、不改配置。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

BlockingLayer = Literal[
    "NONE",
    "DATA_MISSING",
    "NEWS_OR_REGIME",
    "DAILY_BIAS",
    "FOUR_H_STRUCTURE",
    "THIRTY_MIN_SETUP",
    "FIVE_MIN_TRIGGER",
    "PREMIUM_DISCOUNT",
    "RISK",
    "CONFLICTED",
]

def _tfd(m: dict) -> dict[str, Any]:
    return m.get("timeframes") or {}


def _data_sufficient(m: dict) -> tuple[bool, str]:
    t = _tfd(m)
    h4, s30, m5 = t.get("4h") or {}, t.get("30min") or {}, t.get("5min") or {}
    if h4.get("loaded") is False or ("bars" in h4 and (h4.get("bars") or 0) < 1):
        return False, "4H 无有效 K 线"
    if s30.get("loaded") is False or ("bars" in s30 and (s30.get("bars") or 0) < 1):
        return False, "30 分钟无有效 K 线"
    r5 = str(m5.get("reason", "") or "")
    if m5.get("loaded") is False and (
        "missing" in r5.lower() or "5min" in r5.lower() or "not included" in r5.lower()
    ):
        return False, "5 分钟未加载或数据缺失"
    return True, ""


def _is_conflicted_mtf(m: dict) -> bool:
    b = (m.get("mtf_bias_daily") or {}).get("bias", "")
    s4 = (_tfd(m).get("4h") or {}).get("structure", "")
    s30 = (_tfd(m).get("30min") or {}).get("setup_state", "")
    if b == "bullish" and s4 == "bearish_confirmed":
        return True
    if b == "bearish" and s30 in ("full_setup_valid", "waiting_for_pullback", "too_extended"):
        return True
    if b == "bearish" and s4 == "bearish_confirmed" and s30 == "full_setup_valid":
        return True
    return False


def compute_mtf_diagnostics(mtf: dict[str, Any]) -> dict[str, Any]:
    """从单标的 ``run_mtf_smc`` 风格 JSON 计算诊断块（不修改原 dict）。"""
    tfd = _tfd(mtf)
    d = tfd.get("daily") or {}
    h4 = tfd.get("4h") or {}
    t30 = tfd.get("30min") or {}
    t5 = tfd.get("5min") or {}
    bias = str((mtf.get("mtf_bias_daily") or {}).get("bias") or d.get("bias", "unknown"))
    s4 = str(h4.get("structure", "unknown"))
    s30 = str(t30.get("setup_state", "unknown"))
    t5s = str(t5.get("trigger_state", "unknown"))
    pz = str((mtf.get("premium_discount") or {}).get("current_zone", "unknown"))
    regime = str(mtf.get("market_regime", "neutral"))
    mtf_s = int(mtf.get("mtf_alignment_score") or 0)
    reason30 = str(t30.get("reason", "") or "")
    t30_reason_5m = str(t5.get("reason", "") or "")

    daily_bias_status = f"bias={bias}"
    four_h_structure_status = f"structure={s4}"
    thirty_min_setup_status = f"setup={s30}"
    five_min_trigger_status = f"trigger={t5s}"
    premium_discount_status = f"zone={pz}"

    blocking_layer: str = "NONE"
    primary_missing_condition = "已达 FULL 条件（本诊断用于未达标标的）"
    next_condition_to_watch = "无（若已 FULL，可等待下一根 K 或复核）"

    if mtf.get("alignment_category") == "FULL_ALIGNMENT" and mtf.get(
        "eligible_for_future_paper_trade"
    ):
        expl = _explanation_zh(
            "NONE", bias, s4, s30, t5s, pz, regime, mtf_s, reason30, t30_reason_5m
        )
        return {
            "daily_bias_status": daily_bias_status,
            "four_h_structure_status": four_h_structure_status,
            "thirty_min_setup_status": thirty_min_setup_status,
            "five_min_trigger_status": five_min_trigger_status,
            "premium_discount_status": premium_discount_status,
            "blocking_layer": "NONE",
            "primary_missing_condition": "无（FULL_ALIGNMENT）",
            "next_condition_to_watch": "管理持仓 / 仅复核，本工具不下单。",
            "explanation_zh": expl,
        }

    ok_data, dmsg = _data_sufficient(mtf)
    if not ok_data:
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "DATA_MISSING",
            dmsg,
            "等待各周期 K 线拉齐后重扫。",
        )
    elif regime in ("risk_off", "crisis", "unknown"):
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "NEWS_OR_REGIME",
            f"市场制度/波动情境为 {regime}，研究逻辑限制做多结构",
            "待制度回到 neutral/风险可接受后再看 4H/30m。",
        )
    elif s30 == "blocked" and (
        "news" in reason30.lower()
        or "regime" in reason30.lower()
        or "halt" in reason30.lower()
    ):
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "NEWS_OR_REGIME",
            reason30 or "30m 被新闻/制度阻断",
            "盘前/新闻环境改善后再扫。",
        )
    elif s30 == "blocked":
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "RISK",
            reason30 or "30m 被风险规则阻断",
            "等待波动与新闻过滤允许后再评。",
        )
    elif s30 == "invalid_risk":
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "RISK",
            "止损过宽、R/R 或目标价不满足 30m 阈值",
            "等结构收紧或波动下降使止损%与 R/R 进入允许区间。",
        )
    elif _is_conflicted_mtf(mtf):
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "CONFLICTED",
            "多周期方向互相矛盾（如日图与 4H 或 30m 冲突）",
            "等日线与 4H 方向趋同或 30m 与宏观一致后再评估。",
        )
    elif bias == "bearish":
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "DAILY_BIAS",
            "日线偏空，与做多 FULL 研究路径不一致",
            "观察日线是否转为中性/偏多及流动性位置。",
        )
    elif s4 == "bearish_confirmed":
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "FOUR_H_STRUCTURE",
            "4H 为空头确认结构，压制做多 FULL 对齐",
            "关注 4H 是否出现更高低点 / 空头削弱信号。",
        )
    elif s4 == "unknown" and (h4.get("bars") or 0) < 20:
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "DATA_MISSING",
            "4H 数据或摆动不足以判定结构",
            "数据拉长或等待结构清晰。",
        )
    elif s4 not in (
        "bullish_confirmed",
        "transitional",
        "range",
        "unknown",
    ):
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "FOUR_H_STRUCTURE",
            f"4H 结构 {s4} 未达多头确认/震荡可操作区间",
            "等 4H 出现扫流动性后的 ChoCH 或 BOS 确认。",
        )
    elif s4 == "unknown" and (h4.get("bars") or 0) >= 20:
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "FOUR_H_STRUCTURE",
            "4H 结构仍属 unknown / 不分明",
            "等 4H 走出明确区间或结构标签。",
        )
    elif s30 == "full_setup_valid" and pz == "premium":
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "PREMIUM_DISCOUNT",
            "价位处于溢价区，与做多折扣偏好冲突",
            "等回踩折扣区或中轴再对齐 5m 触发。",
        )
    elif s30 == "full_setup_valid" and pz != "premium" and t5s == "confirmed" and mtf_s < 75:
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "RISK",
            f"多周期综合分 {mtf_s} < 75，未达 FULL 分数门槛",
            "等更高周期与溢价评分同步抬升。",
        )
    elif s30 == "full_setup_valid" and pz != "premium" and t5s != "confirmed":
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "FIVE_MIN_TRIGGER",
            f"5m 触发 {t5s}，需 confirmed",
            "在 30m 区附近等待 5m sweep+ChoCH 与 FVG/位移。",
        )
    elif s30 in ("incomplete", "unknown", "waiting_for_pullback", "too_extended"):
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "THIRTY_MIN_SETUP",
            f"30m 未达 full_setup_valid（当前 {s30}）",
            "观察 30m 是否完成 sweep+ChoCH+FVG/OB 或等待回踩/收敛延伸。"
            if s30 in ("incomplete", "unknown")
            else "等价格回靠入场区/止损风险收敛。",
        )
    else:
        blocking_layer, primary_missing_condition, next_condition_to_watch = (
            "THIRTY_MIN_SETUP",
            f"30m 状态 {s30}，未满足做多 FULL 链",
            "完成 30m 研究链上各项后再看 5m。",
        )

    expl = _explanation_zh(
        blocking_layer, bias, s4, s30, t5s, pz, regime, mtf_s, reason30, t30_reason_5m
    )
    return {
        "daily_bias_status": daily_bias_status,
        "four_h_structure_status": four_h_structure_status,
        "thirty_min_setup_status": thirty_min_setup_status,
        "five_min_trigger_status": five_min_trigger_status,
        "premium_discount_status": premium_discount_status,
        "blocking_layer": blocking_layer,
        "primary_missing_condition": primary_missing_condition,
        "next_condition_to_watch": next_condition_to_watch,
        "explanation_zh": expl,
    }


def _explanation_zh(
    layer: str,
    bias: str,
    s4: str,
    s30: str,
    t5: str,
    pz: str,
    regime: str,
    mtf_s: int,
    r30: str,
    r5: str,
) -> str:
    bzh = {
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性",
        "unknown": "未明",
    }.get(bias, bias)
    if layer == "DATA_MISSING":
        return "数据未齐或 K 线不足，无法可靠判定多周期；请先保证各周期拉取成功后再诊断。"
    if layer == "NEWS_OR_REGIME":
        return f"市场或新闻制度为 {regime}，与做多条件冲突；{r30 or ''} 下一步待环境稳定后再看结构。"
    if layer == "DAILY_BIAS":
        return f"日线{bzh}，与做多 FULL 路径不一致。下一步观察日线是否转向中性/偏多，以及流动性位置。"
    if layer == "FOUR_H_STRUCTURE":
        return f"4H 结构为 {s4}，尚未形成对做多有利的确认/可过渡状态。下一步关注 4H 是否出现扫流动性后的反转结构。"
    if layer == "THIRTY_MIN_SETUP":
        rline = f" 细节：{r30}。" if r30 else ""
        return (
            f"30 分钟未形成完整可执行设置（{s30}），通常缺少 sweep→ChoCH→FVG/OB 等链条。{rline} "
            "下一步观察 30m 是否完成结构反转与风险参数。"
        )
    if layer == "FIVE_MIN_TRIGGER":
        return (
            f"30m 有进展，但 5m 触发为 {t5}。{r5} "
            "下一步等价格回靠 30m 入场/ FVG-OB 区后，观察 5m sweep+ChoCH。"
        )
    if layer == "PREMIUM_DISCOUNT":
        return f"价位处于 {pz} 区，做多更偏好折扣/均衡；下一步等回踩或中轴再对 5m 触发。"
    if layer == "RISK":
        return (
            f"风险维度未达标（如止损%、R/R、或综合分 {mtf_s}/100）。"
            "下一步收敛止损距离或等更高周期与分数同步改善。"
        )
    if layer == "CONFLICTED":
        return "多周期方向冲突：请优先统一日线与 4H，再谈 30m/5m 触发。"
    if layer == "NONE":
        return "多周期与分数已满足 FULL 研究条件（本行用于已 FULL 的复核说明）。"
    return f"日{bzh}，4H {s4}，30m {s30}，5m {t5}，溢价 {pz}；{r30} {r5}"


def build_diagnostic_report(
    date: str,
    *,
    source_summary: str,
    items: list[dict[str, Any]],
    top: int = 10,
) -> dict[str, Any]:
    """聚合诊断 JSON 主体。"""
    enriched: list[dict[str, Any]] = []
    by_layer: dict[str, int] = {}
    full_n = 0
    for row in items:
        m = dict(row) if isinstance(row, dict) else {}
        if m.get("alignment_category") == "FULL_ALIGNMENT" and m.get(
            "eligible_for_future_paper_trade"
        ):
            full_n += 1
        diag = compute_mtf_diagnostics(m)
        symu = str(m.get("symbol", "?"))
        ex = str(diag.get("explanation_zh", ""))
        if symu and not ex.startswith(symu + "："):
            diag = {**diag, "explanation_zh": f"{symu}：\n{ex}"}
        layer = str(diag.get("blocking_layer") or "THIRTY_MIN_SETUP")
        by_layer[layer] = by_layer.get(layer, 0) + 1
        m["diagnostics"] = diag
        enriched.append(m)
    # nearest: highest score
    near = sorted(
        enriched,
        key=lambda x: -float(x.get("mtf_alignment_score") or 0),
    )[: max(0, int(top))]

    top_list = [
        {
            "symbol": x.get("symbol"),
            "mtf_alignment_score": x.get("mtf_alignment_score"),
            "alignment_category": x.get("alignment_category"),
            "blocking_layer": (x.get("diagnostics") or {}).get("blocking_layer"),
            "next_condition_to_watch": (x.get("diagnostics") or {}).get(
                "next_condition_to_watch"
            ),
        }
        for x in near
    ]
    return {
        "date": date,
        "source_summary": source_summary,
        "full_alignment_count": full_n,
        "counts_by_blocking_layer": by_layer,
        "top_nearest_to_full_alignment": top_list,
        "items": enriched,
    }


def _parse_date_from_filename(name: str) -> str | None:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-", name)
    return m.group(1) if m else None


def list_mtf_smc_per_symbol_jsons(
    mtf_dir: Path, date: str
) -> list[Path]:
    """如 ``2026-04-24-AAPL-mtf-smc.json``，不含 watchlist 汇总名。"""
    return sorted(
        p
        for p in mtf_dir.glob(f"{date}-*-mtf-smc.json")
        if "watchlist" not in p.name
    )


def find_latest_mtf_date(mtf_dir: Path) -> str | None:
    """按文件修改时间选最新有数据的日期（优先含 watchlist 汇总）。"""
    candidates: list[Path] = list(mtf_dir.glob("*-watchlist-mtf-smc-summary.json"))
    if not candidates:
        candidates = list(mtf_dir.glob("*-*-mtf-smc.json"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return _parse_date_from_filename(latest.name)


def load_mtf_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def format_mtf_diagnostic_digest_zh(
    rep: dict[str, Any],
    *,
    paper_gate_disabled: bool = True,
) -> str:
    """中文 Telegram 摘要。"""
    day = str(rep.get("date", ""))
    full_c = int(rep.get("full_alignment_count") or 0)
    layers = rep.get("counts_by_blocking_layer") or {}
    topn = rep.get("top_nearest_to_full_alignment") or []
    lines = [
        f"【MTF SMC/ICT 未入场诊断】{day}",
        f"FULL_ALIGNMENT 数量：{full_c}",
        "各阻碍层计数：",
    ]
    for k, v in sorted(layers.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {k}: {v}")
    lines.append("最接近满分的前若干标的：")
    for t in topn[:10]:
        lines.append(
            f"  {t.get('symbol')}  score={t.get('mtf_alignment_score')}  "
            f"层={t.get('blocking_layer')}  下一步：{t.get('next_condition_to_watch', '')[:80]}"
        )
    if paper_gate_disabled:
        lines.append(
            "提醒：未下单；execution 未启用，纸面 gate 未开放（或 dry_run）。"
        )
    else:
        lines.append("提醒：本报告仅诊断，不自动下单。")
    return "\n".join(lines)


__all__ = [
    "build_diagnostic_report",
    "compute_mtf_diagnostics",
    "find_latest_mtf_date",
    "format_mtf_diagnostic_digest_zh",
    "list_mtf_smc_per_symbol_jsons",
    "load_mtf_json",
]
