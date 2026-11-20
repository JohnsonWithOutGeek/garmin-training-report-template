from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import garmin_report as daily

TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")
FTP_WATTS = int(os.getenv("FTP_WATTS", "250"))
REPORT_DIR = Path("reports")


def resolve_week_start() -> date:
    raw = os.getenv("REPORT_WEEK_START", "").strip()
    if raw:
        start = date.fromisoformat(raw)
        if start.weekday() != 0:
            raise SystemExit("REPORT_WEEK_START must be a Monday (YYYY-MM-DD)")
        return start
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def sum_field(items: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(item.get(field) or 0) for item in items), 1)


def build_weekly_report(week_start: date) -> dict[str, Any]:
    week_end = week_start + timedelta(days=6)
    daily.CLIENT = daily.GarminClient(os.environ["GARMIN_API_KEY"])
    daily.FTP_WATTS = FTP_WATTS

    days = []
    all_activities = []
    for offset in range(7):
        target = week_start + timedelta(days=offset)
        summaries = daily.CLIENT.activities(target)
        report = daily.build_report(target, summaries)
        days.append(report)
        for activity in report["activities"]:
            all_activities.append({**activity, "report_date": target.isoformat()})

    zone_seconds: dict[str, int] = {}
    for activity in all_activities:
        analysis = activity.get("power_interval_analysis") or {}
        for zone, seconds in (analysis.get("zone_time_seconds") or {}).items():
            zone_seconds[zone] = zone_seconds.get(zone, 0) + int(seconds or 0)

    total_duration = sum_field(all_activities, "duration_seconds")
    total_distance = sum_field(all_activities, "distance_km")
    total_elevation = sum_field(all_activities, "elevation_gain_m")
    total_calories = sum_field(all_activities, "calories")
    total_tss = round(sum(float(x.get("tss_estimate") or 0) for x in all_activities), 1)

    totals = {
        "activity_count": len(all_activities),
        "duration_seconds": total_duration,
        "duration": daily.seconds_to_hms(total_duration),
        "distance_km": total_distance,
        "elevation_gain_m": total_elevation,
        "calories": total_calories,
        "estimated_tss": total_tss,
        "activities_with_np": sum(1 for x in all_activities if x.get("normalized_power_w") is not None),
    }
    return {
        "period_start": week_start.isoformat(),
        "period_end": week_end.isoformat(),
        "timezone": TIMEZONE,
        "ftp_watts": FTP_WATTS,
        "totals": totals,
        "daily_totals": [
            {
                "date": report["date"],
                **report["totals"],
                "estimated_tss": round(sum(
                    float(x.get("tss_estimate") or 0) for x in report["activities"]
                ), 1),
            }
            for report in days
        ],
        "power_zone_time_seconds": zone_seconds,
        "activities": all_activities,
        "daily_reports": days,
        "notes": [
            "周期为周一至周日，按配置时区计算；默认自动选择最近一个完整自然周。",
            "估算 TSS 仅对 Garmin 提供 NP 且有时长的活动计算，不能视为训练平台的官方 TSS。",
            "功率区间时间来自可读取 FIT 功率记录的活动；缺失 FIT 或功率记录的活动不计入。",
            "每一天单独查询 Garmin 活动列表，以避免跨日边界和接口日期过滤问题。",
        ],
    }


def render_weekly_markdown(report: dict[str, Any]) -> str:
    t = report["totals"]
    lines = [
        f"# Garmin 周训练报告 — {report['period_start']} 至 {report['period_end']}",
        "",
        f"- FTP：{report['ftp_watts']} W",
        f"- 活动次数：{t['activity_count']}",
        f"- 总时长：{t['duration'] or '—'}",
        f"- 总距离：{t['distance_km']:.2f} km",
        f"- 总爬升：{t['elevation_gain_m']:.0f} m",
        f"- 总热量：{t['calories']:.0f} kcal",
        f"- 估算 TSS：{t['estimated_tss']:.1f}",
        f"- 含 NP 的活动：{t['activities_with_np']}/{t['activity_count']}",
        "",
        "## 每日汇总",
        "",
        "| 日期 | 活动 | 时长 | 距离 (km) | 爬升 (m) | 估算 TSS |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for day in report["daily_totals"]:
        lines.append(
            f"| {day['date']} | {day['activity_count']} | {day['duration'] or '—'} | "
            f"{day['distance_km']:.2f} | {day['elevation_gain_m']:.0f} | "
            f"{day['estimated_tss']:.1f} |"
        )

    lines.extend([
        "",
        "## 活动明细",
        "",
        "| 日期 | 活动 | 类型 | 时长 | 距离 (km) | 爬升 (m) | 平均/最大心率 | 平均/最大功率 | NP | IF | TSS |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for a in report["activities"]:
        hr = f"{a.get('average_hr') or '—'}/{a.get('max_hr') or '—'}"
        power = f"{a.get('average_power_w') or '—'}/{a.get('max_power_w') or '—'}"
        lines.append(
            f"| {a['report_date']} | {a.get('name', '未命名活动')} | {a.get('type') or '—'} | "
            f"{a.get('duration') or '—'} | {a.get('distance_km') if a.get('distance_km') is not None else '—'} | "
            f"{a.get('elevation_gain_m') if a.get('elevation_gain_m') is not None else '—'} | "
            f"{hr} bpm | {power} W | {a.get('normalized_power_w') or '—'} W | "
            f"{a.get('intensity_factor', '—')} | {a.get('tss_estimate', '—')} |"
        )

    lines.extend(["", "## 功率区间累计时间", ""])
    zones = report.get("power_zone_time_seconds") or {}
    if zones:
        for zone, seconds in zones.items():
            lines.append(f"- {zone}：{daily.seconds_to_hms(seconds)}")
    else:
        lines.append("没有可用的 FIT 功率记录，无法汇总功率区间。")

    lines.extend(["", "## 有效做功段", ""])
    found = False
    for a in report["activities"]:
        analysis = a.get("power_interval_analysis") or {}
        for block in analysis.get("intervals", []):
            found = True
            lines.append(
                f"- **{a['report_date']} · {a.get('name', '未命名活动')} · {block['zone']}**："
                f"{block['duration']}，平均 {block['average_power_w']} W，"
                f"平均心率 {block.get('average_hr') or '—'} bpm；"
                f"功率前后半变化 {block.get('power_change_w', '—')} W，"
                f"心率前后半变化 {block.get('hr_change_bpm', '—')} bpm"
            )
    if not found:
        lines.append("没有识别到达到 2 分钟门槛的有效做功段。")

    lines.extend(["", "## 统计口径", ""])
    lines.extend(f"- {note}" for note in report["notes"])
    return "\n".join(lines) + "\n"


def main() -> None:
    if not os.getenv("GARMIN_API_KEY"):
        raise SystemExit("Missing GARMIN_API_KEY environment secret")
    week_start = resolve_week_start()
    report = build_weekly_report(week_start)
    REPORT_DIR.mkdir(exist_ok=True)
    stem = f"weekly-{report['period_start']}_to_{report['period_end']}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_weekly_markdown(report), encoding="utf-8")
    print(
        f"Saved {json_path} and {md_path}; "
        f"activities={report['totals']['activity_count']}; "
        f"period={report['period_start']}..{report['period_end']}"
    )


if __name__ == "__main__":
    main()
