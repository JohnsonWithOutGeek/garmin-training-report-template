from __future__ import annotations
import argparse, json, os, re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any
import requests

BASE = "https://intervals.icu/api/v1"
DRAFTS = Path("plans/drafts")

def session():
    key = os.environ["INTERVALS_API_KEY"]
    s = requests.Session()
    s.auth = ("API_KEY", key)
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return s

def get(s, path, params=None):
    r = s.get(BASE + path, params=params, timeout=45)
    r.raise_for_status()
    return r.json()

def latest_report():
    files = sorted(Path("reports").glob("weekly-*_to_*.json"))
    if not files:
        raise RuntimeError("No weekly Garmin report JSON found")
    p = files[-1]
    return json.loads(p.read_text(encoding="utf-8")), p

def compact(a):
    fields = ["id","name","type","start_date_local","icu_training_load","icu_intensity","moving_time","distance","total_elevation_gain","average_watts","weighted_average_watts","average_heartrate","max_heartrate","icu_ftp"]
    return {k:a[k] for k in fields if a.get(k) is not None}

def generate():
    report, source = latest_report()
    # The report period is historical context only. Never derive the target
    # week from it: a manually re-run workflow could otherwise create past
    # workouts when the latest report is several days old.
    timezone_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    try:
        local_today = datetime.now(ZoneInfo(timezone_name)).date()
    except Exception as exc:
        raise ValueError(f"Invalid TIMEZONE setting: {timezone_name}") from exc

    # Generate the current week only when run on Monday; from Tuesday onward,
    # target the next Monday so no already elapsed weekday receives a workout.
    days_until_monday = (-local_today.weekday()) % 7
    start = local_today + timedelta(days=days_until_monday)
    end = start + timedelta(days=6)
    if start < local_today or start.weekday() != 0 or end.weekday() != 6:
        raise ValueError("Target training week must be a complete, non-past Monday-Sunday week")
    if date.fromisoformat(report["period_end"]).weekday() != 6:
        raise ValueError("Source report must end on Sunday")
    s = session()
    recent = get(s, "/athlete/0/activities", {"oldest":(start-timedelta(days=42)).isoformat(),"newest":(start-timedelta(days=1)).isoformat(),"limit":100})
    events = get(s, "/athlete/0/events", {"oldest":start.isoformat(),"newest":end.isoformat()})
    if not isinstance(recent, list):
        raise ValueError("Intervals.icu activities response is not a list")
    if not isinstance(events, list):
        raise ValueError("Intervals.icu calendar response is not a list")
    try:
        wellness = get(s, "/athlete/0/wellness", {"oldest":(start-timedelta(days=14)).isoformat(),"newest":(start-timedelta(days=1)).isoformat()})
    except requests.HTTPError:
        wellness = []
    context = {
        "athlete":{"ftp_watts":int(os.getenv("FTP_WATTS",str(report.get("ftp_watts",250)))),"usual_weekly_distance_km":"200-300 km when schedule allows","timezone":os.getenv("TIMEZONE","Asia/Shanghai")},
        "garmin_previous_week":report,
        "intervals_recent_activities":[compact(a) for a in recent if isinstance(a,dict)],
        "intervals_target_week_calendar":[{k:e.get(k) for k in ("start_date_local","category","name","type","moving_time","icu_training_load")} for e in events if isinstance(e,dict)],
        "recent_wellness":wellness[-14:] if isinstance(wellness,list) else [],
        "target_week":{"start":start.isoformat(),"end":end.isoformat()}
    }
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY GitHub Actions secret")
    base = os.getenv("LLM_BASE_URL","https://api.deepseek.com").rstrip("/")
    model = os.getenv("LLM_MODEL","deepseek-flash")
    system = ("你是谨慎的公路自行车教练。只根据输入数据制定下周计划，不得编造。FTP按给定值。"
      "通常每周最多两次高强度课，强度日之间安排恢复；其余为耐力、恢复或休息。"
      "考虑每周通常200-300km，但不得为凑里程强行加量。数据不完整时保守安排并说明。"
      "只输出JSON对象，字段 weekly_objective、load_assessment、cautions、workouts。"
      "workouts每项字段 date(YYYY-MM-DD)、name、duration_min(整数)、focus、description、indoor(boolean)。"
      "description使用Intervals.icu原生训练文本，例如：- 15m 55-70% FTP，空行，4x，- 4m 105% FTP，- 4m 50% FTP。"
      "只列需要写入日历的骑行训练，type默认为Ride。")
    response = requests.post(base+"/chat/completions",headers={"Authorization":"Bearer "+api_key},json={
      "model":model,"temperature":0.25,"response_format":{"type":"json_object"},
      "thinking":{"type":"enabled"},"reasoning_effort":"high",
      "messages":[{"role":"system","content":system},{"role":"user","content":json.dumps(context,ensure_ascii=False)}]},timeout=180)
    response.raise_for_status()
    message=response.json()["choices"][0]["message"]
    if not message.get("content"):
        raise RuntimeError("DeepSeek returned an empty message content; inspect API response/usage")
    raw=message["content"].strip()
    raw=re.sub(r"^\s*\x60{3}(?:json)?\s*|\s*\x60{3}\s*$","",raw)
    plan=json.loads(raw)
    if not isinstance(plan.get("workouts"),list):
        raise ValueError("Model response has no workouts array")
    seen=set()
    for w in plan["workouts"]:
        d=date.fromisoformat(w["date"])
        if d < local_today:
            raise ValueError("Refusing to schedule a workout on a past date: "+str(d))
        if not start<=d<=end or d.isoformat() in seen: raise ValueError("Invalid or duplicate workout date: "+str(d))
        seen.add(d.isoformat())
        if not w.get("name") or not w.get("description"): raise ValueError("Workout name/description missing")
        if not isinstance(w.get("duration_min"),int) or not 1<=w["duration_min"]<=360: raise ValueError("Invalid duration_min")
    plan.update({"period_start":start.isoformat(),"period_end":end.isoformat(),"ftp_watts":context["athlete"]["ftp_watts"],"source_report":str(source),"generated_at":datetime.now().astimezone().isoformat(),"model":model})
    DRAFTS.mkdir(parents=True,exist_ok=True)
    stem="next-week-"+start.isoformat()
    (DRAFTS/(stem+".json")).write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=[f"# 下周训练计划草案：{start} 至 {end}","","- FTP："+str(plan["ftp_watts"])+" W","- 周目标："+plan.get("weekly_objective","—"),"- 负荷判断："+plan.get("load_assessment","—"),"","此文件仅供审核，尚未写入日历。",""]
    for w in sorted(plan["workouts"],key=lambda x:x["date"]):
        lines += ["## "+w["date"]+" · "+w["name"],"- 时长："+str(w["duration_min"])+" 分钟","- 重点："+w.get("focus","—"),"", "~~~text",w["description"],"~~~",""]
    lines += ["## 注意事项",""]+["- "+x for x in plan.get("cautions",[])]
    (DRAFTS/(stem+".md")).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("Draft saved: "+str(DRAFTS/(stem+".json")))

def import_plan(path):
    if os.getenv("CONFIRM_IMPORT","").lower()!="true":
        raise RuntimeError("Import blocked: explicit confirmation checkbox is required")
    plan=json.loads(Path(path).read_text(encoding="utf-8"))
    start=date.fromisoformat(plan["period_start"]); end=date.fromisoformat(plan["period_end"])
    events=[]
    for w in plan["workouts"]:
        slug=re.sub(r"[^a-z0-9]+","-",w["name"].lower()).strip("-")[:40] or "ride"
        events.append({"external_id":"garmin-chatgpt-plan-"+w["date"]+"-"+slug,"category":"WORKOUT",
          "start_date_local":w["date"]+"T00:00:00","type":"Ride","name":w["name"],
          "description":w["description"],"moving_time":w["duration_min"]*60,
          "indoor":bool(w.get("indoor",False)),"icu_ftp":int(plan.get("ftp_watts",268))})
    if not events: raise RuntimeError("No workouts in plan")
    s=session()
    r=s.post(BASE+"/athlete/0/events/bulk?upsert=true",json=events,timeout=90); r.raise_for_status()
    created=r.json()
    expected={e["external_id"] for e in events}
    if {e.get("external_id") for e in created}!=expected: raise RuntimeError("API response did not match requested events")
    listed=get(s,"/athlete/0/events",{"oldest":start.isoformat(),"newest":end.isoformat()})
    present={e.get("external_id") for e in listed if isinstance(e,dict)}
    missing=expected-present
    if missing: raise RuntimeError("Post-import verification failed: "+", ".join(sorted(missing)))
    print(f"Imported and verified {len(events)} workouts for {start} through {end}")

if __name__=="__main__":
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("draft")
    q=sub.add_parser("import"); q.add_argument("--plan",required=True)
    a=p.parse_args()
    if a.cmd=="draft": generate()
    else: import_plan(a.plan)
