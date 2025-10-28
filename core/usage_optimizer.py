import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional


def _load_latest_parsed(path: str = "data") -> Optional[str]:
    try:
        files = [f for f in os.listdir(path) if f.startswith("history7d_parsed_") and f.endswith(".json")]
        if not files:
            return None
        files.sort(reverse=True)
        return os.path.join(path, files[0])
    except Exception:
        return None


def _parse_dt_flexible(ts: str) -> Optional[datetime]:
    """
    Accepts multiple timestamp formats found in parsed files:
    - MM/DD/YYYY HH:MM[:SS]
    - DD/MM/YYYY HH:MM[:SS]
    - YYYY-MM-DD HH:MM[:SS]
    Returns None when unparseable.
    """
    ts = (ts or "").strip()
    fmts = [
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(ts, fmt)
        except Exception:
            pass
    return None


def _hourly_bins(readings: List[Dict[str, Any]]) -> Dict[int, Dict[str, float]]:
    bins: Dict[int, Dict[str, float]] = {h: {"soc_sum": 0.0, "soc_count": 0.0, "solar_sum": 0.0, "solar_count": 0.0} for h in range(24)}
    for r in readings:
        t = r.get("time") or r.get("timestamp")
        dt = _parse_dt_flexible(t)
        if not dt:
            continue
        h = dt.hour
        soc = r.get("battery_soc_percent")
        sol = r.get("solar_generation_w")
        if isinstance(soc, (int, float)):
            bins[h]["soc_sum"] += float(soc)
            bins[h]["soc_count"] += 1.0
        if isinstance(sol, (int, float)):
            # Treat negative meter readings as zero solar generation for optimization
            bins[h]["solar_sum"] += max(0.0, float(sol))
            bins[h]["solar_count"] += 1.0
    return bins


def _compute_stats(parsed_focus: Dict[str, Any]) -> Dict[str, Any]:
    readings = parsed_focus.get("readings") or []
    if not readings and parsed_focus.get("data_points"):
        # fallback to generic parser structure
        # flatten into focus-like structure if available
        readings = []
        for p in parsed_focus.get("data_points", []):
            vals = p.get("values", {})
            readings.append({
                "time": p.get("timestamp"),
                "battery_soc_percent": (vals.get("Cbattery1") or {}).get("value"),
                "solar_generation_w": (vals.get("Pmeter") or {}).get("value"),
                "is_generating": ((vals.get("Pmeter") or {}).get("value") or 0) > 0,
                "battery_status": "unknown",
            })

    bins = _hourly_bins(readings)

    # average per hour
    avg_soc = {h: (bins[h]["soc_sum"] / bins[h]["soc_count"]) if bins[h]["soc_count"] else None for h in bins}
    avg_solar = {h: (bins[h]["solar_sum"] / bins[h]["solar_count"]) if bins[h]["solar_count"] else None for h in bins}

    # identify typical charge/discharge windows
    # heuristic: low SOC hours and low solar → likely discharge windows
    #            high solar hours → good charging/usage windows
    def _top_hours_nonzero(d: Dict[int, Optional[float]], reverse: bool, n: int = 3) -> List[int]:
        # consider only hours with data
        items = [(h, v) for h, v in d.items() if v is not None]
        if not items:
            return []
        # for solar, ignore zeros to avoid picking nighttime
        if reverse:
            items = [(h, v) for h, v in items if v > 0.0]
        items.sort(key=lambda x: x[1], reverse=reverse)
        return [h for h, _ in items[:n]]

    best_solar_hours = _top_hours_nonzero(avg_solar, reverse=True, n=4)
    lowest_soc_hours = _top_hours_nonzero(avg_soc, reverse=False, n=4)

    # Daily peaks summary
    soc_vals = [v for v in avg_soc.values() if v is not None]
    solar_vals = [v for v in avg_solar.values() if v is not None]
    overall_avg_soc = (sum(soc_vals) / len(soc_vals)) if soc_vals else None
    overall_avg_solar = (sum(solar_vals) / len(solar_vals)) if solar_vals else None

    return {
        "avg_soc_by_hour": avg_soc,
        "avg_solar_by_hour": avg_solar,
        "best_solar_hours": best_solar_hours,
        "lowest_soc_hours": lowest_soc_hours,
        "overall_avg_soc": round(overall_avg_soc, 1) if overall_avg_soc is not None else None,
        "overall_avg_solar": round(overall_avg_solar, 1) if overall_avg_solar is not None else None,
    }


def _format_short_report(stats: Dict[str, Any]) -> str:
    def hh(lst: List[int]) -> str:
        lst_sorted = sorted(lst)
        return ", ".join(f"{h:02d}:00" for h in lst_sorted)

    return (
        "Relatório de uso (últimos 7 dias, minuto a minuto)\n"
        f"- SOC médio por hora: sintetizado (use para tendências, não valores exatos).\n"
        f"- Horas com maior geração solar: {hh(stats.get('best_solar_hours') or [])}.\n"
        f"- Horas com menor SOC: {hh(stats.get('lowest_soc_hours') or [])}.\n"
        f"- SOC médio global: {stats.get('overall_avg_soc')}%.\n"
        f"- Geração solar média global: {stats.get('overall_avg_solar')} W.\n"
        "Sugestões devem priorizar: carregar veículos e eletrodomésticos pesados nas janelas de maior geração;"
        " evitar descargas profundas antes dos picos; e programar cargas em horas com melhor solar."
    )


def optimize_usage(parsed_focus: Optional[Dict[str, Any]] = None, parsed_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Produce a concise statistical summary for Gemini to craft recommendations.
    If parsed_focus is not provided, it loads the latest history7d_parsed_*.json.
    Returns a compact dict with stats and a small natural-language report.
    """
    if parsed_focus is None:
        if not parsed_path:
            parsed_path = _load_latest_parsed() or ""
        if not parsed_path or not os.path.exists(parsed_path):
            return {"error": "No parsed history file found. Run cli.py history7d first."}
        with open(parsed_path, "r", encoding="utf-8") as f:
            parsed_focus = json.load(f)

    stats = _compute_stats(parsed_focus)
    report = _format_short_report(stats)
    # Trim per-hour arrays to keep context small
    trimmed = {
        "best_solar_hours": stats.get("best_solar_hours"),
        "lowest_soc_hours": stats.get("lowest_soc_hours"),
        "overall_avg_soc": stats.get("overall_avg_soc"),
        "overall_avg_solar": stats.get("overall_avg_solar"),
    }
    return {"summary": trimmed, "report": report}


