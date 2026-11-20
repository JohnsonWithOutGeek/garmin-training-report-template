from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import fitdecode

BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
ACTIVITY_SEARCH_PATH = "/activitylist-service/activities/search/activities"
ACTIVITY_DETAIL_PATH = "/activity-service/activity/{activity_id}"
ACTIVITY_DOWNLOAD_PATH = "/download-service/files/activity/{activity_id}"
TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")
FTP_WATTS = int(os.getenv("FTP_WATTS", "250"))
REPORT_DIR = Path("reports")


class GarminApiError(RuntimeError):
    pass


def get_access_token(api_key: str) -> str:
    response = requests.post(
        BASE_URL + TOKEN_PATH,
        headers={"Authorization": f"ApiKey {api_key}", "Accept": "*/*"},
        timeout=30,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise GarminApiError(
            f"Garmin token request failed: HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    token = response.text.strip().strip('"')
    if not token:
        raise GarminApiError("Garmin token endpoint returned an empty token")
    # The Personal API token endpoint currently returns the token as plain text.
    # Handle JSON responses defensively in case Garmin changes the response format.
    if token.startswith("{"):
        try:
            payload = json.loads(token)
            token = payload.get("access_token", "")
        except json.JSONDecodeError:
            pass
    if not token:
        raise GarminApiError("Could not extract access token from Garmin response")
    return token


class GarminClient:
    def __init__(self, api_key: str):
        self.token = get_access_token(api_key)
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "garmin-training-report/1.0",
        })

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(
            BASE_URL + path,
            params=params,
            timeout=45,
            allow_redirects=False,
        )
        if response.status_code == 401:
            raise GarminApiError("Garmin access token rejected (HTTP 401)")
        if response.status_code == 429:
            raise GarminApiError("Garmin rate limit reached (HTTP 429); try later")
        if response.status_code != 200:
            raise GarminApiError(
                f"Garmin request failed: HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content

    def activities(self, day: date) -> list[dict[str, Any]]:
        # The Personal API Swagger defines start/limit and date filtering for this endpoint.
        data = self.get(
            ACTIVITY_SEARCH_PATH,
            params={
                "start": 0,
                "limit": 100,
                "startDate": day.isoformat(),
                "endDate": day.isoformat(),
            },
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("activities", "activityList", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        raise GarminApiError(
            "Unexpected activity-search response. Check Garmin Swagger parameters/response."
        )

    def activity_detail(self, activity_id: Any) -> dict[str, Any]:
        data = self.get(ACTIVITY_DETAIL_PATH.format(activity_id=activity_id))
        return data if isinstance(data, dict) else {}

    def download_activity(self, activity_id: Any) -> bytes:
        # FIT downloads are ZIP/binary responses, not JSON. Override the
        # session's default Accept: application/json header for this endpoint.
        response = self.session.get(
            BASE_URL + ACTIVITY_DOWNLOAD_PATH.format(activity_id=activity_id),
            headers={"Accept": "application/zip, application/octet-stream, */*"},
            timeout=60,
            allow_redirects=True,
        )
        if response.status_code != 200:
            raise GarminApiError(
                f"Garmin activity download failed: HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        return response.content


POWER_ZONES = [
    ("恢复", 0.00, 0.55),
    ("有氧耐力", 0.55, 0.76),
    ("节奏", 0.76, 0.88),
    ("甜区", 0.88, 0.95),
    ("阈值", 0.95, 1.06),
    ("VO2max", 1.06, 1.21),
    ("无氧", 1.21, float("inf")),
]
INTERVAL_MIN_SECONDS = 120


def extract_fit_bytes(download: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(download)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".fit")]
            if not names:
                raise GarminApiError("Downloaded ZIP contains no FIT file")
            return archive.read(names[0])
    except zipfile.BadZipFile:
        if download[:4] == b".FIT":
            return download
        raise GarminApiError("Downloaded activity file is neither ZIP nor FIT")


def extract_fit_elevation(fit_bytes: bytes) -> list[dict[str, Any]]:
    """Return the complete timestamp/distance/elevation record stream from a FIT file."""
    profile = []
    with fitdecode.FitReader(io.BytesIO(fit_bytes)) as fit:
        for frame in fit:
            if getattr(frame, "name", None) != "record" or not hasattr(frame, "fields"):
                continue
            fields = {field.name: field.value for field in frame.fields}
            timestamp = fields.get("timestamp")
            distance = number(fields.get("distance"))
            altitude = number(fields.get("enhanced_altitude"))
            if altitude is None:
                altitude = number(fields.get("altitude"))
            if timestamp is None or distance is None or altitude is None:
                continue
            profile.append({
                "timestamp": timestamp.isoformat(),
                "elapsed_seconds": round((timestamp - profile_start).total_seconds(), 1) if profile else 0.0,
                "distance_m": round(distance, 1),
                "altitude_m": round(altitude, 1),
            })
            if len(profile) == 1:
                profile_start = timestamp
    return profile


def analyze_climbs(profile: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect meaningful valley-to-summit climbs from smoothed FIT elevation."""
    if len(profile) < 60:
        return []
    # Centered 31-record moving average suppresses short GPS/barometric noise.
    altitudes = [float(p["altitude_m"]) for p in profile]
    smooth = []
    radius = 15
    for i in range(len(altitudes)):
        lo, hi = max(0, i - radius), min(len(altitudes), i + radius + 1)
        smooth.append(sum(altitudes[lo:hi]) / (hi - lo))

    # Zig-zag turning points: ignore reversals smaller than 12 m.
    threshold = 12.0
    low_i = high_i = 0
    low_v = high_v = smooth[0]
    trend = 0
    turns = []
    for i, value in enumerate(smooth):
        if value < low_v:
            low_v, low_i = value, i
        if value > high_v:
            high_v, high_i = value, i
        if trend == 0:
            if value - low_v >= threshold:
                turns.append(("low", low_i))
                trend = 1
                high_v, high_i = value, i
            elif high_v - value >= threshold:
                turns.append(("high", high_i))
                trend = -1
                low_v, low_i = value, i
        elif trend == 1:
            if value > high_v:
                high_v, high_i = value, i
            elif high_v - value >= threshold:
                turns.append(("high", high_i))
                trend = -1
                low_v, low_i = value, i
        else:
            if value < low_v:
                low_v, low_i = value, i
            elif value - low_v >= threshold:
                turns.append(("low", low_i))
                trend = 1
                high_v, high_i = value, i

    if trend == 1:
        turns.append(("high", high_i))
    elif trend == -1:
        turns.append(("low", low_i))

    climbs = []
    for j in range(len(turns) - 1):
        kind_a, ia = turns[j]
        kind_b, ib = turns[j + 1]
        if kind_a != "low" or kind_b != "high" or ib <= ia:
            continue
        start, end = profile[ia], profile[ib]
        distance = float(end["distance_m"]) - float(start["distance_m"])
        gain = smooth[ib] - smooth[ia]
        if distance < 300 or gain < 30:
            continue
        climbs.append({
            "start_time": start["timestamp"],
            "end_time": end["timestamp"],
            "start_distance_km": round(float(start["distance_m"]) / 1000, 2),
            "end_distance_km": round(float(end["distance_m"]) / 1000, 2),
            "distance_km": round(distance / 1000, 2),
            "elevation_gain_m": round(gain, 1),
            "average_grade_pct": round(gain / distance * 100, 2),
        })
    return sorted(climbs, key=lambda x: x["average_grade_pct"], reverse=True)


def analyze_fit_power(fit_bytes: bytes) -> dict[str, Any]:
    samples = []
    with fitdecode.FitReader(io.BytesIO(fit_bytes)) as fit:
        for frame in fit:
            if getattr(frame, "name", None) != "record" or not hasattr(frame, "fields"):
                continue
            fields = {field.name: field.value for field in frame.fields}
            try:
                power = float(fields.get("power"))
            except (TypeError, ValueError):
                continue
            samples.append({
                "timestamp": fields.get("timestamp"),
                "power": power,
                "hr": fields.get("heart_rate"),
            })

    if not samples:
        return {"available": False, "reason": "FIT 文件没有可用的功率记录"}

    # Smooth power with a trailing 5-second mean to avoid splitting intervals
    # because of brief one-second fluctuations.
    smoothed = []
    for i in range(len(samples)):
        chunk = [x["power"] for x in samples[max(0, i - 4):i + 1]]
        smoothed.append(sum(chunk) / len(chunk))

    labels = []
    for power in smoothed:
        ratio = power / FTP_WATTS if FTP_WATTS else 0
        labels.append(next((name for name, low, high in POWER_ZONES if low <= ratio < high), "未分类"))

    zone_seconds = {name: 0 for name, _, _ in POWER_ZONES}
    for label in labels:
        if label in zone_seconds:
            zone_seconds[label] += 1

    # Build contiguous runs. Join repetitions of the same work zone when
    # interruptions are short (<=120s), but keep the statistics based only
    # on samples that actually belong to the target zone.
    def make_runs(zone_labels):
        runs = []
        run_start = 0
        for pos in range(1, len(zone_labels) + 1):
            if pos == len(zone_labels) or zone_labels[pos] != zone_labels[run_start]:
                runs.append({"zone": zone_labels[run_start], "start": run_start, "end": pos})
                run_start = pos
        return runs

    raw_runs = make_runs(labels)
    merged_runs = []
    index = 0
    while index < len(raw_runs):
        run = dict(raw_runs[index])
        last_index = index
        search = index + 1
        while search < len(raw_runs):
            candidate = raw_runs[search]
            if candidate["start"] - run["end"] > 120:
                break
            if candidate["zone"] == run["zone"]:
                run["end"] = candidate["end"]
                last_index = search
            search += 1
        merged_runs.append(run)
        index = last_index + 1

    intervals = []
    for run in merged_runs:
        zone = run["zone"]
        if zone not in zone_seconds or zone == "恢复":
            continue
        block = [
            samples[pos] for pos in range(run["start"], run["end"])
            if labels[pos] == run["zone"]
        ]
        duration = len(block)
        if duration < INTERVAL_MIN_SECONDS:
            continue
        powers = [x["power"] for x in block]
        # Classify the sustained block by its own mean power so that a short
        # transition at the beginning/end cannot give it the wrong zone.
        block_ratio = (sum(powers) / len(powers)) / FTP_WATTS if FTP_WATTS else 0
        zone = next((name for name, low, high in POWER_ZONES if low <= block_ratio < high), zone)
        hrs = [float(x["hr"]) for x in block if x["hr"] is not None]
        midpoint = len(block) // 2
        first_half_power = [x["power"] for x in block[:midpoint]]
        second_half_power = [x["power"] for x in block[midpoint:]]
        first_half_hr = [float(x["hr"]) for x in block[:midpoint] if x["hr"] is not None]
        second_half_hr = [float(x["hr"]) for x in block[midpoint:] if x["hr"] is not None]
        first_ts, last_ts = block[0]["timestamp"], block[-1]["timestamp"]
        if first_ts is not None:
            first_ts = first_ts.astimezone(ZoneInfo(TIMEZONE))
        if last_ts is not None:
            last_ts = last_ts.astimezone(ZoneInfo(TIMEZONE))
        avg_power = round(sum(powers) / len(powers), 1)
        avg_hr = round(sum(hrs) / len(hrs), 1) if hrs else None
        half1_power = round(sum(first_half_power) / len(first_half_power), 1) if first_half_power else None
        half2_power = round(sum(second_half_power) / len(second_half_power), 1) if second_half_power else None
        half1_hr = round(sum(first_half_hr) / len(first_half_hr), 1) if first_half_hr else None
        half2_hr = round(sum(second_half_hr) / len(second_half_hr), 1) if second_half_hr else None
        intervals.append({
            "zone": zone,
            "start_time": first_ts.isoformat(sep=" ") if first_ts else None,
            "end_time": last_ts.isoformat(sep=" ") if last_ts else None,
            "duration_seconds": duration,
            "duration": seconds_to_hms(duration),
            "average_power_w": avg_power,
            "max_power_w": max(powers),
            "average_hr": avg_hr,
            "max_hr": max(hrs) if hrs else None,
            "first_half_average_power_w": half1_power,
            "second_half_average_power_w": half2_power,
            "power_change_w": round(half2_power - half1_power, 1) if half1_power is not None and half2_power is not None else None,
            "first_half_average_hr": half1_hr,
            "second_half_average_hr": half2_hr,
            "hr_change_bpm": round(half2_hr - half1_hr, 1) if half1_hr is not None and half2_hr is not None else None,
        })

    # Compare repetitions only with the previous interval in the same power zone.
    previous_by_zone = {}
    for block in intervals:
        previous = previous_by_zone.get(block["zone"])
        if previous:
            block["power_change_vs_previous_w"] = round(block["average_power_w"] - previous["average_power_w"], 1)
            block["power_change_vs_previous_pct"] = round(
                (block["average_power_w"] / previous["average_power_w"] - 1) * 100, 1
            ) if previous["average_power_w"] else None
            block["hr_change_vs_previous_bpm"] = (
                round(block["average_hr"] - previous["average_hr"], 1)
                if block["average_hr"] is not None and previous["average_hr"] is not None else None
            )
        else:
            block["power_change_vs_previous_w"] = None
            block["power_change_vs_previous_pct"] = None
            block["hr_change_vs_previous_bpm"] = None
        previous_by_zone[block["zone"]] = block

    return {
        "available": True,
        "ftp_watts": FTP_WATTS,
        "method": "FIT 功率记录；5 秒移动平均分区；同一功率区间在不超过 120 秒的短暂中断后可合并为一组，统计时只计算该区间内的有效样本；有效做功段按该段平均功率复核分区；累计有效做功至少 120 秒才列出；统计每段前后半功率/心率变化及同区间重复段差异",
        "zone_time_seconds": zone_seconds,
        "intervals": intervals,
    }


def first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def seconds_to_hms(value: Any) -> str | None:
    seconds = number(value)
    if seconds is None:
        return None
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_activity(summary: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    merged = {**summary, **detail}
    duration = number(first(merged, "duration", "movingDuration"))
    distance = number(first(merged, "distance"))
    avg_power = number(first(merged, "averagePower", "avgPower"))
    normalized = {
        "activity_id": first(merged, "activityId", "activity_id", "id"),
        "name": first(merged, "activityName", "name") or "未命名活动",
        "type": first(merged, "activityType", "activityTypeDTO", "sportType"),
        "start_time": first(merged, "startTimeLocal", "startTimeGMT", "startTime"),
        "duration_seconds": duration,
        "duration": seconds_to_hms(duration),
        "distance_m": distance,
        "distance_km": round(distance / 1000, 2) if distance is not None else None,
        "elevation_gain_m": number(first(merged, "elevationGain", "totalElevationGain")),
        "average_speed_mps": number(first(merged, "averageSpeed")),
        "average_hr": number(first(merged, "averageHR", "avgHR")),
        "max_hr": number(first(merged, "maxHR", "maximumHR")),
        "average_power_w": avg_power,
        "max_power_w": number(first(merged, "maxPower")),
        "normalized_power_w": number(first(merged, "normPower", "normalizedPower")),
        "calories": number(first(merged, "calories", "totalCalories")),
        "training_effect_aerobic": number(first(merged, "aerobicTrainingEffect", "aerobicTrainingEffect")),
        "training_effect_anaerobic": number(first(merged, "anaerobicTrainingEffect")),
    }
    np = normalized["normalized_power_w"]
    if np is not None and FTP_WATTS > 0:
        normalized["intensity_factor"] = round(np / FTP_WATTS, 3)
        if duration is not None:
            normalized["tss_estimate"] = round(
                duration * np * np / (FTP_WATTS * FTP_WATTS * 3600) * 100,
                1,
            )
    return normalized


def build_report(day: date, activities: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = []
    for item in activities:
        activity_id = first(item, "activityId", "activity_id", "id")
        detail: dict[str, Any] = {}
        if activity_id is not None:
            # Activity summary already contains useful values; detail enriches fields.
            # A detail failure should not discard the summary record.
            try:
                detail = CLIENT.activity_detail(activity_id)
            except Exception as exc:
                print(f"Warning: detail fetch failed for {activity_id}: {exc}", file=sys.stderr)
        activity = normalize_activity(item, detail)
        try:
            fit_data = CLIENT.download_activity(activity_id)
            fit_bytes = extract_fit_bytes(fit_data)
            activity["power_interval_analysis"] = analyze_fit_power(fit_bytes)
            activity["elevation_profile"] = extract_fit_elevation(fit_bytes)
            activity["climb_analysis"] = analyze_climbs(activity["elevation_profile"])
            print(f"Extracted {len(activity['elevation_profile'])} elevation samples for {activity_id}")
            for climb in activity["climb_analysis"][:10]:
                print("CLIMB " + json.dumps(climb, ensure_ascii=False))
        except Exception as exc:
            print(f"Warning: FIT analysis unavailable for {activity_id}: {exc}", file=sys.stderr)
            activity["power_interval_analysis"] = {"available": False, "reason": str(exc)[:300]}
        normalized.append(activity)

    totals = {
        "activity_count": len(normalized),
        "duration_seconds": round(sum(x["duration_seconds"] or 0 for x in normalized), 1),
        "distance_km": round(sum(x["distance_km"] or 0 for x in normalized), 2),
        "elevation_gain_m": round(sum(x["elevation_gain_m"] or 0 for x in normalized), 1),
        "calories": round(sum(x["calories"] or 0 for x in normalized), 1),
    }
    totals["duration"] = seconds_to_hms(totals["duration_seconds"])
    power_values = [x["average_power_w"] for x in normalized if x["average_power_w"] is not None]
    hr_values = [x["average_hr"] for x in normalized if x["average_hr"] is not None]
    totals["mean_activity_average_power_w"] = (
        round(sum(power_values) / len(power_values), 1) if power_values else None
    )
    totals["mean_activity_average_hr"] = (
        round(sum(hr_values) / len(hr_values), 1) if hr_values else None
    )
    return {
        "date": day.isoformat(),
        "timezone": TIMEZONE,
        "ftp_watts_for_if_tss_estimates": FTP_WATTS,
        "totals": totals,
        "activities": normalized,
        "notes": [
            "TSS is an estimate derived from normalized power and configured FTP where available.",
            "Garmin summary metrics may omit NP, power, or detailed time-in-zone data for some activity types.",
            "Averages across activities are arithmetic means of activity-level averages, not duration-weighted session averages.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        f"# Garmin 运动日报 — {report['date']}",
        "",
        f"- 活动次数：{totals['activity_count']}",
        f"- 总时长：{totals['duration'] or '—'}",
        f"- 总距离：{totals['distance_km']:.2f} km",
        f"- 总爬升：{totals['elevation_gain_m']:.0f} m",
        f"- 总热量：{totals['calories']:.0f} kcal",
        f"- FTP 分析基准：{FTP_WATTS} W",
        "",
        "## 活动明细",
        "",
    ]
    if not report["activities"]:
        lines.append("前一天没有找到 Garmin 活动记录。")
    else:
        for activity in report["activities"]:
            lines.extend([
                f"### {activity['name']}",
                f"- 类型：{activity['type'] or '—'}",
                f"- 开始时间：{activity['start_time'] or '—'}",
                f"- 时长：{activity['duration'] or '—'}",
                f"- 距离：{activity['distance_km'] if activity['distance_km'] is not None else '—'} km",
                f"- 爬升：{activity['elevation_gain_m'] if activity['elevation_gain_m'] is not None else '—'} m",
                f"- 平均/最大心率：{activity['average_hr'] or '—'} / {activity['max_hr'] or '—'} bpm",
                f"- 平均/最大功率：{activity['average_power_w'] or '—'} / {activity['max_power_w'] or '—'} W",
                f"- NP：{activity['normalized_power_w'] or '—'} W",
                f"- IF / 估算 TSS：{activity.get('intensity_factor', '—')} / {activity.get('tss_estimate', '—')}",
                "",
            ])
            interval_data = activity.get("power_interval_analysis", {})
            lines.append("")
            lines.append("#### 功率区间分析")
            if not interval_data.get("available"):
                lines.append(f"- 暂不可用：{interval_data.get('reason', '无 FIT 功率数据')}")
            else:
                lines.append(f"- FTP 分区基准：{interval_data.get('ftp_watts', FTP_WATTS)} W")
                for zone, seconds in interval_data.get("zone_time_seconds", {}).items():
                    if seconds >= 60:
                        lines.append(f"- {zone}：{seconds_to_hms(seconds)}")
                if interval_data.get("intervals"):
                    lines.append("")
                    lines.append("**有效做功段表现（按时间顺序）**")
                    lines.append("")
                    lines.append("| # | 区间 | 时间 | 时长 | 平均功率 | 功率前半→后半 | 平均心率 | 心率前半→后半 |")
                    lines.append("|---:|---|---|---:|---:|---:|---:|---:|")
                    for index, block in enumerate(interval_data["intervals"], start=1):
                        power_halves = (
                            f"{block.get('first_half_average_power_w') or '—'} → "
                            f"{block.get('second_half_average_power_w') or '—'} W"
                        )
                        hr_halves = (
                            f"{block.get('first_half_average_hr') or '—'} → "
                            f"{block.get('second_half_average_hr') or '—'} bpm"
                        )
                        time_label = f"{block.get('start_time') or '—'}"
                        lines.append(
                            f"| {index} | {block['zone']} | {time_label} | {block['duration']} | "
                            f"{block.get('average_power_w') or '—'} W | {power_halves} | "
                            f"{block.get('average_hr') or '—'} bpm | {hr_halves} |"
                        )
                        if block.get("power_change_vs_previous_w") is not None:
                            lines.append(
                                f"  - 第 {index} 段相对上一段同区间：功率 "
                                f"{block['power_change_vs_previous_w']:+.1f} W "
                                f"({block['power_change_vs_previous_pct']:+.1f}%)；"
                                f"平均心率 {block['hr_change_vs_previous_bpm']:+.1f} bpm"
                                if block.get("hr_change_vs_previous_bpm") is not None else
                                f"  - 第 {index} 段相对上一段同区间：功率 "
                                f"{block['power_change_vs_previous_w']:+.1f} W "
                                f"({block['power_change_vs_previous_pct']:+.1f}%)；心率数据不完整"
                            )
                        if block.get("power_change_w") is not None or block.get("hr_change_bpm") is not None:
                            power_change = (
                                f"{block['power_change_w']:+.1f} W" if block.get("power_change_w") is not None else "—"
                            )
                            hr_change = (
                                f"{block['hr_change_bpm']:+.1f} bpm" if block.get("hr_change_bpm") is not None else "—"
                            )
                            lines.append(f"  - 本段前半到后半变化：功率 {power_change}；心率 {hr_change}")
                else:
                    lines.append("- 没有达到 2 分钟识别门槛的连续做功段。")
                lines.append(f"- 识别口径：{interval_data.get('method', '—')}")
            lines.append("")
    lines.extend(["## 口径说明", ""] + [f"- {note}" for note in report["notes"]])
    return "\n".join(lines) + "\n"


CLIENT: GarminClient


def main() -> None:
    global CLIENT
    api_key = os.getenv("GARMIN_API_KEY")
    if not api_key:
        raise SystemExit("Missing GARMIN_API_KEY environment secret")

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    report_date = os.getenv("REPORT_DATE", "").strip()
    target_day = date.fromisoformat(report_date) if report_date else now.date() - timedelta(days=1)
    CLIENT = GarminClient(api_key)
    summaries = CLIENT.activities(target_day)
    report = build_report(target_day, summaries)

    REPORT_DIR.mkdir(exist_ok=True)
    json_path = REPORT_DIR / f"{target_day.isoformat()}.json"
    md_path = REPORT_DIR / f"{target_day.isoformat()}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Saved {json_path} and {md_path}; activities={len(summaries)}")


if __name__ == "__main__":
    main()

# Manual workflow trigger: generate the requested 2026-11-18 Garmin report.
