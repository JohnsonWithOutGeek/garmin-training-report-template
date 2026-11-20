from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import sys\nsys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))\n\nimport garmin_report as daily

TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")
REPORT_DIR = Path("reports")
SLEEP_PATH = "/sleep-service/sleep/dailySleep"


def has_sleep_data(payload: Any) -> bool:
    if payload is None:
        return False
    if isinstance(payload, dict):
        dto = payload.get("dailySleepDTO")
        return bool(dto) or bool(payload)
    if isinstance(payload, list):
        return bool(payload)
    return bool(payload)


def fetch_latest_sleep() -> tuple[date, Any]:
    raw_date = os.getenv("SLEEP_DATE", "").strip()
    candidates = [date.fromisoformat(raw_date)] if raw_date else [
        datetime.now(ZoneInfo(TIMEZONE)).date()
    ]
    # Garmin's sleep date is the calendar date on which the sleep session ends.
    # If today's record has not synced yet, fall back to the previous date.
    if not raw_date:
        candidates.append(candidates[0] - timedelta(days=1))

    client = daily.GarminClient(os.environ["GARMIN_API_KEY"])
    errors: list[str] = []
    for target in candidates:
        try:
            payload = client.get(SLEEP_PATH, params={"date": target.isoformat()})
            if has_sleep_data(payload):
                return target, payload
        except Exception as exc:
            errors.append(f"{target}: {exc}")
    if errors:
        raise RuntimeError("Garmin sleep request failed: " + "; ".join(errors))
    raise RuntimeError(
        "No sleep record found for " + ", ".join(x.isoformat() for x in candidates)
    )


def find_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    dto = payload.get("dailySleepDTO")
    return dto if isinstance(dto, dict) else payload


def fmt_seconds(value: Any) -> str:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return "—"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours}小时{minutes}分钟"


def render_markdown(target: date, payload: Any) -> str:
    summary = find_summary(payload)
    lines = [
        f"# Garmin 睡眠数据 — {target.isoformat()}",
        "",
        "## 睡眠摘要",
        "",
    ]
    fields = [
        ("睡眠时长", ("sleepTimeSeconds", "sleepDurationSeconds"), True),
        ("深睡", ("deepSleepSeconds", "deepSleepDurationSeconds"), True),
        ("浅睡", ("lightSleepSeconds", "lightSleepDurationSeconds"), True),
        ("快速眼动睡眠", ("remSleepSeconds", "remSleepDurationSeconds"), True),
        ("清醒时长", ("awakeSleepSeconds", "awakeDurationSeconds"), True),
        ("睡眠评分", ("sleepScore",), False),
        ("静息心率", ("restingHeartRate",), False),
    ]
    found = False
    for label, keys, is_duration in fields:
        value = next((summary.get(key) for key in keys if summary.get(key) is not None), None)
        if value is not None:
            found = True
            lines.append(f"- {label}：{fmt_seconds(value) if is_duration else value}")
    for label, keys in [
        ("入睡时间", ("sleepStartTimestampLocal", "sleepStartTimestampGMT")),
        ("醒来时间", ("sleepEndTimestampLocal", "sleepEndTimestampGMT")),
    ]:
        value = next((summary.get(key) for key in keys if summary.get(key)), None)
        if value:
            found = True
            lines.append(f"- {label}：{value}")
    if not found:
        lines.append("接口返回了睡眠数据，但未匹配到常见摘要字段；请查看下方原始 JSON。")
    lines.extend([
        "",
        "## 原始数据",
        "",
        "完整 Garmin API 响应保存在同目录 JSON 文件中。",
        "",
        "## 数据说明",
        "",
        "- 日期为 Garmin 睡眠记录归属日期（通常是睡眠结束的当地日期）。",
        "- 本报告保留 Garmin API 原始字段，不对缺失字段进行推算。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    if not os.getenv("GARMIN_API_KEY"):
        raise SystemExit("Missing GARMIN_API_KEY environment secret")
    target, payload = fetch_latest_sleep()
    REPORT_DIR.mkdir(exist_ok=True)
    stem = f"sleep-{target.isoformat()}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(target, payload), encoding="utf-8")
    print(f"Saved {json_path} and {md_path}; sleep_date={target.isoformat()}")


if __name__ == "__main__":
    main()
