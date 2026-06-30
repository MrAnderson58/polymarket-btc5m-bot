"""Report memory, experiments, AI notes, and archive export."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR, DATABASE_PATH

MEMORY_INDEX = "memory_index.json"
EXPERIMENTS_FILE = "experiments.json"
AI_NOTES_FILE = "ai_notes.md"

_reports_root_override: Path | None = None


def set_reports_root(path: Path | None) -> None:
    global _reports_root_override
    _reports_root_override = path


def _reports_root() -> Path:
    if _reports_root_override is not None:
        return _reports_root_override
    return BASE_DIR / "reports"


def _day_dir(stamp: datetime) -> Path:
    return _reports_root() / stamp.strftime("%Y-%m-%d")


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _config_fingerprint(configuration: dict[str, Any]) -> str:
    payload = json.dumps(configuration, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def load_experiments() -> list[dict[str, Any]]:
    path = _reports_root() / EXPERIMENTS_FILE
    return _load_json(path) or []


def update_experiments(configuration: dict[str, Any], report_summary: dict[str, Any]) -> list[dict[str, Any]]:
    path = _reports_root() / EXPERIMENTS_FILE
    experiments: list[dict[str, Any]] = _load_json(path) or []
    fp = _config_fingerprint(configuration)
    now = datetime.now(timezone.utc).isoformat()
    total_trades = int(report_summary.get("trades", 0))

    def _param_changes(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        tracked = (
            ("entry_offset", "entry_offset"),
            ("stop_loss_pct", "stop_loss_pct"),
            ("trailing_activation", "trailing_activation"),
            ("trailing_distance", "trailing_distance"),
            ("time_stop_sec", "time_stop_sec"),
        )
        changes = []
        for label, key in tracked:
            if old_cfg.get(key) != new_cfg.get(key):
                changes.append(
                    {
                        "parameter": label,
                        "from": old_cfg.get(key),
                        "to": new_cfg.get(key),
                        "reason": "Config change detected",
                    }
                )
        btc_old = old_cfg.get("btc_filter_live", {})
        btc_new = new_cfg.get("btc_filter_live", {})
        if btc_old != btc_new:
            changes.append(
                {
                    "parameter": "btc_filter",
                    "from": btc_old,
                    "to": btc_new,
                    "reason": "BTC filter thresholds changed",
                }
            )
        return changes

    if experiments and experiments[-1].get("config_fingerprint") == fp:
        exp = experiments[-1]
        exp["last_report_at"] = now
        exp["trades"] = total_trades
        exp["profit_factor"] = report_summary.get("profit_factor", 0)
        exp["win_rate"] = report_summary.get("win_rate", 0)
        exp["avg_pnl"] = report_summary.get("avg_pnl", 0)
        _evaluate_experiment_milestones(exp)
    else:
        prev_cfg = experiments[-1].get("configuration", {}) if experiments else {}
        changes = _param_changes(prev_cfg, configuration) if prev_cfg else []
        if experiments:
            experiments[-1]["ended_at"] = now
            experiments[-1]["result"] = _classify_experiment_result(experiments[-1])
        experiments.append(
            {
                "id": len(experiments) + 1,
                "name": (
                    configuration.get("enabled_strategies_v2", ["?"])[0]
                    if isinstance(configuration.get("enabled_strategies_v2"), list)
                    else "config_change"
                ),
                "config_fingerprint": fp,
                "configuration": configuration,
                "changes": changes,
                "started_at": now,
                "last_report_at": now,
                "ended_at": None,
                "trades_at_start": total_trades,
                "trades": total_trades,
                "profit_factor": report_summary.get("profit_factor", 0),
                "win_rate": report_summary.get("win_rate", 0),
                "avg_pnl": report_summary.get("avg_pnl", 0),
                "result": "Pending",
                "milestones": {"100": None, "200": None, "300": None},
                "accepted": None,
            }
        )
    _save_json(path, experiments)
    return experiments


def _classify_experiment_result(exp: dict[str, Any]) -> str:
    start_pf = exp.get("start_profit_factor")
    end_pf = exp.get("profit_factor")
    if start_pf is None or end_pf is None:
        return "Pending"
    if end_pf > start_pf * 1.05:
        return "Improved"
    if end_pf < start_pf * 0.95:
        return "Worse"
    return "No change"


def _evaluate_experiment_milestones(exp: dict[str, Any]) -> None:
    since = max(0, int(exp.get("trades", 0)) - int(exp.get("trades_at_start", 0)))
    milestones: dict[str, str | None] = exp.setdefault(
        "milestones", {"100": None, "200": None, "300": None}
    )
    start_pf = exp.get("start_profit_factor", exp.get("profit_factor"))
    if "start_profit_factor" not in exp:
        exp["start_profit_factor"] = start_pf
    current_pf = exp.get("profit_factor", 0)
    for threshold in (100, 200, 300):
        key = str(threshold)
        if since >= threshold and milestones.get(key) is None:
            if current_pf > start_pf * 1.05:
                milestones[key] = "Improved"
            elif current_pf < start_pf * 0.95:
                milestones[key] = "Worse"
            else:
                milestones[key] = "No change"
    if since >= 300 and exp.get("result") == "Pending":
        exp["result"] = milestones.get("300", "Pending")


def append_memory_index(
    *,
    md_path: Path,
    json_path: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    root = _reports_root()
    index_path = root / MEMORY_INDEX
    index: list[dict[str, Any]] = _load_json(index_path) or []

    overall = report.get("overall_performance", {})
    scores = report.get("final_score", {})
    entry = {
        "generated_at": report["meta"]["generated_at"],
        "md_path": str(md_path.relative_to(root)),
        "json_path": str(json_path.relative_to(root)),
        "profit_factor": overall.get("profit_factor"),
        "win_rate": overall.get("win_rate"),
        "avg_pnl": overall.get("avg_pnl"),
        "trades": overall.get("trades"),
        "max_drawdown_pct": report.get("equity_curve", {}).get("max_drawdown_pct"),
        "final_score": scores.get("overall"),
    }
    index.append(entry)
    _save_json(index_path, index[-365:])

    return {
        "entries": len(index),
        "last_7_trend": _build_trend(index[-7:]),
        "last_14_trend": _build_trend(index[-14:]),
        "last_30_trend": _build_trend(index[-30:]),
        "trends": {
            "7": _trend_summary(index[-7:]),
            "14": _trend_summary(index[-14:]),
            "30": _trend_summary(index[-30:]),
        },
    }


def _build_trend(slice_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trend = []
    for item in slice_:
        trend.append(
            {
                "date": item["generated_at"][:10],
                "profit_factor": item.get("profit_factor"),
                "win_rate": item.get("win_rate"),
                "avg_pnl": item.get("avg_pnl"),
                "final_score": item.get("final_score"),
            }
        )
    return trend


def _trend_summary(slice_: list[dict[str, Any]]) -> dict[str, Any]:
    if len(slice_) < 2:
        return {"direction": "flat", "pf_delta": 0.0, "score_delta": 0.0}
    first, last = slice_[0], slice_[-1]
    pf0 = first.get("profit_factor") or 0
    pf1 = last.get("profit_factor") or 0
    s0 = first.get("final_score") or 0
    s1 = last.get("final_score") or 0
    pf_delta = pf1 - pf0 if pf0 not in (0, float("inf")) else 0
    direction = "up" if pf_delta > 0.1 else "down" if pf_delta < -0.1 else "flat"
    return {
        "direction": direction,
        "pf_delta": round(pf_delta, 2),
        "wr_delta": round((last.get("win_rate") or 0) - (first.get("win_rate") or 0), 3),
        "avg_pnl_delta": round((last.get("avg_pnl") or 0) - (first.get("avg_pnl") or 0), 2),
        "score_delta": s1 - s0,
        "drawdown_delta": round(
            (last.get("max_drawdown_pct") or 0) - (first.get("max_drawdown_pct") or 0),
            2,
        ),
    }


def write_ai_notes(report: dict[str, Any], *, day_dir: Path) -> Path:
    notes_path = day_dir / AI_NOTES_FILE
    lines = [
        f"# AI Notes — {report['meta']['generated_at'][:10]}",
        "",
    ]
    decision = report.get("ai_decision", {})
    plan = report.get("final_action_plan", {})
    lines.append(
        f"**Recommended action:** {decision.get('recommendation', 'N/A')} "
        f"(confidence {decision.get('confidence_pct', 0)}%)"
    )
    lines.append(f"- {decision.get('reason', '')}")
    lines.append(f"- **Today:** {plan.get('today', 'MONITOR')}")
    lines.append("")

    drift = report.get("drift_detector", {})
    if drift.get("message"):
        lines.append(f"**Drift:** {drift['message']}")
    lines.append("")

    opt = report.get("parameter_optimizer", {})
    if opt:
        cur = opt.get("current", {})
        best = opt.get("optimal", {})
        lines.append(
            f"**Optimizer:** entry {cur.get('entry')} → {best.get('entry')}, "
            f"expected +{opt.get('expected_improvement_pct', 0):.1f}%"
        )
        lines.append("")

    for rec in report.get("recommendations", [])[:8]:
        lines.append(f"- {rec}")

    notebook = report.get("research_notebook", {})
    if notebook.get("findings"):
        lines.append("")
        lines.append("**Research notebook:**")
        for f in notebook["findings"][:5]:
            lines.append(f"- {f['finding']}")

    if notes_path.exists():
        existing = notes_path.read_text(encoding="utf-8")
        lines = [existing.rstrip(), "", "---", ""] + lines[2:]

    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return notes_path


def archive_day_copies(
    *,
    md_path: Path,
    json_path: Path,
    stamp: datetime,
) -> Path:
    day = _day_dir(stamp)
    day.mkdir(parents=True, exist_ok=True)
    shutil.copy2(md_path, day / md_path.name)
    shutil.copy2(json_path, day / json_path.name)
    return day


def _sqlite_dump(db_path: Path, out_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            subprocess.run(
                ["sqlite3", str(db_path), ".dump"],
                stdout=fh,
                check=True,
                timeout=120,
            )
        return True
    except (OSError, subprocess.SubprocessError):
        shutil.copy2(db_path, out_path.with_suffix(".db"))
        return True


def create_report_bundle(
    *,
    md_path: Path,
    json_path: Path,
    ai_notes_path: Path | None,
    stamp: datetime,
) -> Path:
    bundle_dir = _reports_root() / f"bundle_{stamp.strftime('%Y-%m-%d_%H-%M')}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(md_path, bundle_dir / "report.md")
    shutil.copy2(json_path, bundle_dir / "report.json")
    if ai_notes_path and ai_notes_path.exists():
        shutil.copy2(ai_notes_path, bundle_dir / AI_NOTES_FILE)

    dump_path = bundle_dir / "report.db_snapshot.sql"
    _sqlite_dump(DATABASE_PATH, dump_path)

    log_sources = [BASE_DIR / "bot.log", BASE_DIR / "logs" / "bot.log"]
    log_out = bundle_dir / "report.log"
    for src in log_sources:
        if src.exists():
            shutil.copy2(src, log_out)
            break
    else:
        log_out.write_text("(no log file found)\n", encoding="utf-8")

    zip_path = _reports_root() / f"report_{stamp.strftime('%Y-%m-%d_%H-%M')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in bundle_dir.iterdir():
            zf.write(file, arcname=file.name)
    shutil.rmtree(bundle_dir, ignore_errors=True)
    return zip_path


def build_version_comparison(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from bot.performance import build_version_summaries, fetch_closed_trades

    trades = fetch_closed_trades(conn)
    summaries = build_version_summaries(trades)
    return [
        {
            "version": s.version,
            "trades": s.trades,
            "win_rate": s.win_rate,
            "avg_pnl": s.average_pnl_percent,
            "profit_factor": s.profit_factor,
            "max_drawdown_pct": s.max_drawdown_percent,
        }
        for s in summaries
    ]
