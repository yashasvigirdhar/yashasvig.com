# Read-only dump of client-progress data for the "one year of coaching" blog post.
# Connects to the prod DB through the local read-only SSH tunnel and writes JSON files into ./data/.

import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
# Git-ignored: {"coach_email": "...", "self_emails": ["..."]} so no addresses live in the public repo.
CONFIG_PATH = HERE / "config.local.json"

# Plausibility floors per body metric (cm, kg). Anything below is almost certainly a typo
# or an inch value from the pre-app Google Forms era.
METRIC_FLOORS = {
    "weight": 35,
    "waist": 50,
    "chest": 60,
    "hips": 60,
    "thighs": 35,
    "arms": 18,
    "neck": 25,
}
# Relative distance from the median of neighbouring readings beyond which a reading is flagged.
# Neighbours rather than the whole history, so a real 13% weight change is not mistaken for a typo.
OUTLIER_TOLERANCE = {"weight": 0.06}
DEFAULT_OUTLIER_TOLERANCE = 0.15
NEIGHBOUR_WINDOW = 3

LABEL_CATEGORIES = [
    ("wins", ("win", "highlight")),
    ("workouts", ("improvement", "strength gain", "how are the workouts")),
    ("feeling", ("feel overall",)),
    ("struggles", ("struggle", "challenge", "deviation", "craving", "digestion", "hunger")),
    ("sleep", ("sleep routine",)),
    ("open", ("anything else", "like to share", "want to share", "change next week")),
]


def categorise(label: str) -> str:
    low = label.lower()
    for category, needles in LABEL_CATEGORIES:
        if any(n in low for n in needles):
            return category
    return "other"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"{CONFIG_PATH.name} is missing; see README.md for its shape.")
    config = json.loads(CONFIG_PATH.read_text())
    config["self_emails"] = set(config.get("self_emails", []))
    return config


def connect() -> psycopg.Connection:
    password = os.environ.get("AC_PROD_DB_PASSWORD")
    if not password:
        sys.exit("AC_PROD_DB_PASSWORD is not set; source ~/.zshrc or export it before running.")
    conn = psycopg.connect(
        host=os.environ.get("AC_PROD_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("AC_PROD_DB_LOCAL_PORT", "15432")),
        dbname="assistantcoach",
        user="ac_readonly",
        password=password,
        sslmode="require",
        row_factory=dict_row,
    )
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def q(conn, sql: str, params=None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"unserialisable {type(value)}")


def write(name: str, payload) -> None:
    path = DATA_DIR / name
    path.write_text(json.dumps(payload, indent=2, default=jsonable, ensure_ascii=False))
    size_kb = path.stat().st_size / 1024
    print(f"  wrote {name} ({size_kb:.0f} KB)")


def iso_week(d) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def local_median(values: list[float], index: int, floor: float) -> float | None:
    before = [v for v in values[max(0, index - NEIGHBOUR_WINDOW):index] if v >= floor]
    after = [v for v in values[index + 1:index + 1 + NEIGHBOUR_WINDOW] if v >= floor]
    neighbours = before + after
    return statistics.median(neighbours) if neighbours else None


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def collect_roster(conn, coach_id, self_emails: set[str]) -> list[dict]:
    rows = q(conn, """
        select c.id, c.full_name, c.email, c.is_demo, c.is_archived, c.lifecycle_stage::text as lifecycle_stage,
               coalesce(c.gender, c.intake_form_data->>'Gender') as gender,
               coalesce(c.age, nullif(c.intake_form_data->>'Age', '')::int) as age,
               c.height, c.weight as intake_weight, c.primary_goals, c.intake_date, c.created_at,
               c.weight_unit::text as weight_unit,
               (select count(*) from check_ins ci where ci.client_id = c.id) as checkins,
               (select min(ci.timestamp) from check_ins ci where ci.client_id = c.id) as first_checkin,
               (select max(ci.timestamp) from check_ins ci where ci.client_id = c.id) as last_checkin,
               (select count(*) from workout_logs wl where wl.client_id = c.id and wl.status = 'completed') as workouts,
               (select min(wl.started_at) from workout_logs wl where wl.client_id = c.id and wl.status = 'completed') as first_workout,
               (select max(wl.started_at) from workout_logs wl where wl.client_id = c.id and wl.status = 'completed') as last_workout,
               (select count(*) from body_measurement_log b where b.client_id = c.id) as measurements,
               (select count(*) from meal_photo_logs m where m.client_id = c.id) as meal_logs,
               (select count(*) from workout_exercise_videos v where v.client_id = c.id and v.deleted_at is null) as videos,
               (select count(*) from client_goals g where g.client_id = c.id) as goals,
               (select count(*) from client_goals g where g.client_id = c.id and g.completed) as goals_completed
        from clients c
        where c.coach_id = %(coach_id)s
        order by c.created_at
    """, {"coach_id": coach_id})

    roster = []
    for r in rows:
        if r["email"] in self_emails:
            kind = "self"
        elif r["is_demo"]:
            kind = "demo"
        elif r["checkins"] == 0 and r["workouts"] == 0:
            kind = "never_started"
        else:
            kind = "client"
        starts = [d for d in (r["intake_date"], r["first_checkin"], r["first_workout"]) if d]
        ends = [d for d in (r["last_checkin"], r["last_workout"]) if d]
        first = min(starts) if starts else None
        last = max(ends) if ends else None
        tenure_days = (last - first).days if first and last else None
        roster.append({
            "client_id": str(r["id"]),
            "full_name": r["full_name"],
            "first_name": r["full_name"].split()[0].title(),
            "email": r["email"],
            "kind": kind,
            "include_suggested": kind == "client" and r["checkins"] >= 3,
            "is_archived": r["is_archived"],
            "lifecycle_stage": r["lifecycle_stage"],
            "gender": r["gender"],
            "age": r["age"],
            "weight_unit": r["weight_unit"],
            "height_cm": r["height"],
            "intake_weight_kg": r["intake_weight"],
            "primary_goals": r["primary_goals"],
            "first_activity": first,
            "last_activity": last,
            "tenure_days": tenure_days,
            "checkins": r["checkins"],
            "workouts_completed": r["workouts"],
            "measurements": r["measurements"],
            "meal_logs": r["meal_logs"],
            "form_videos": r["videos"],
            "goals": r["goals"],
            "goals_completed": r["goals_completed"],
        })
    return roster


# ---------------------------------------------------------------------------
# Body measurements
# ---------------------------------------------------------------------------

def collect_measurements(conn, client_ids) -> list[dict]:
    rows = q(conn, """
        select b.client_id, b.metric, b.value, b.recorded_at, b.source
        from body_measurement_log b
        where b.client_id = any(%(ids)s)
        union all
        select wl.client_id, 'weight', wl.session_bodyweight, wl.started_at, 'workout_log'
        from workout_logs wl
        where wl.client_id = any(%(ids)s) and wl.status = 'completed' and wl.session_bodyweight is not null
        order by 1, 2, 4
    """, {"ids": client_ids})

    grouped = defaultdict(list)
    for r in rows:
        grouped[(str(r["client_id"]), r["metric"])].append(r)

    series = []
    for (client_id, metric), points in grouped.items():
        values = [float(p["value"]) for p in points]
        tolerance = OUTLIER_TOLERANCE.get(metric, DEFAULT_OUTLIER_TOLERANCE)
        floor = METRIC_FLOORS.get(metric, 0)
        out_points = []
        for i, p in enumerate(points):
            v = float(p["value"])
            flags = []
            if v < floor:
                flags.append("below_plausible_floor")
            else:
                local = local_median(values, i, floor)
                if local and abs(v - local) / local > tolerance:
                    flags.append("outlier_vs_neighbours")
            out_points.append({
                "date": p["recorded_at"].date(),
                "value": v,
                "source": p["source"],
                "flags": flags,
            })
        clean = [p for p in out_points if not p["flags"]]
        summary = None
        if len(clean) >= 2:
            summary = {
                "first_date": clean[0]["date"],
                "first_value": clean[0]["value"],
                "last_date": clean[-1]["date"],
                "last_value": clean[-1]["value"],
                "delta": round(clean[-1]["value"] - clean[0]["value"], 2),
                "min_value": min(p["value"] for p in clean),
                "max_value": max(p["value"] for p in clean),
                "clean_points": len(clean),
                "flagged_points": len(out_points) - len(clean),
            }
        series.append({
            "client_id": client_id,
            "metric": metric,
            "unit": "kg" if metric == "weight" else "cm",
            "points": out_points,
            "summary": summary,
        })
    return series


# ---------------------------------------------------------------------------
# Strength from set logs
# ---------------------------------------------------------------------------

def epley(weight: float, reps: int) -> float:
    return weight * (1 + reps / 30.0)


def best_set(sets: list[dict], kind: str) -> dict | None:
    if kind == "assisted":
        usable = [s for s in sets if s["assist_weight"] is not None and s["reps"]]
        if not usable:
            return None
        s = min(usable, key=lambda s: (float(s["assist_weight"]), -s["reps"]))
        return {"assist_kg": float(s["assist_weight"]), "reps": s["reps"], "score": -float(s["assist_weight"])}
    if kind == "weighted":
        usable = [s for s in sets if s["weight"] is not None and s["reps"]]
        if not usable:
            return None
        s = max(usable, key=lambda s: epley(float(s["weight"]), s["reps"]))
        e = epley(float(s["weight"]), s["reps"])
        return {"weight_kg": float(s["weight"]), "reps": s["reps"], "e1rm": round(e, 1), "score": e}
    if kind == "bodyweight":
        usable = [s for s in sets if s["reps"]]
        if not usable:
            return None
        s = max(usable, key=lambda s: (float(s["added_weight"] or 0), s["reps"]))
        return {"reps": s["reps"], "added_kg": float(s["added_weight"] or 0), "score": s["reps"] + 100 * float(s["added_weight"] or 0)}
    if kind == "duration":
        usable = [s for s in sets if s["duration_seconds"]]
        if not usable:
            return None
        s = max(usable, key=lambda s: (float(s["weight"] or 0), s["duration_seconds"]))
        return {"duration_s": s["duration_seconds"], "weight_kg": float(s["weight"]) if s["weight"] else None, "score": s["duration_seconds"]}
    return None


def classify_set_kind(row: dict) -> str:
    if row["assist_weight"] is not None:
        return "assisted"
    if row["exercise_type"] in ("weighted",) and row["weight"] is not None:
        return "weighted"
    if row["exercise_type"] == "bodyweight":
        return "bodyweight"
    if row["exercise_type"] in ("duration", "weighted_duration"):
        return "duration"
    return "other"


def collect_strength(conn, client_ids) -> list[dict]:
    rows = q(conn, """
        select wl.client_id, wl.started_at::date as session_date, e.name as exercise, e.muscle_group,
               esl.exercise_type::text as exercise_type, esl.weight, esl.reps, esl.added_weight,
               esl.duration_seconds, esl.assist_weight
        from exercise_set_logs esl
        join workout_logs wl on wl.id = esl.workout_log_id
        join exercises e on e.id = esl.exercise_id
        where wl.client_id = any(%(ids)s) and wl.status = 'completed' and esl.deleted_at is null
        order by wl.client_id, e.name, wl.started_at
    """, {"ids": client_ids})

    grouped = defaultdict(lambda: defaultdict(list))
    meta = {}
    for r in rows:
        kind = classify_set_kind(r)
        if kind == "other":
            continue
        key = (str(r["client_id"]), r["exercise"], kind)
        grouped[key][r["session_date"]].append(r)
        meta[key] = r["muscle_group"]

    series = []
    for (client_id, exercise, kind), by_date in grouped.items():
        sessions = []
        for d in sorted(by_date):
            best = best_set(by_date[d], kind)
            if best:
                if kind == "weighted":
                    heaviest = max((x for x in by_date[d] if x["weight"] is not None and x["reps"]),
                                   key=lambda x: (float(x["weight"]), x["reps"]))
                    best["top_weight_kg"] = float(heaviest["weight"])
                    best["top_weight_reps"] = heaviest["reps"]
                sessions.append({"date": d, **best})
        if not sessions:
            continue
        scores = [s["score"] for s in sessions]
        baseline = statistics.median(scores[:3])
        best_score = max(scores)
        best_idx = scores.index(best_score)
        for s in sessions:
            s.pop("score")
        summary = {
            "sessions": len(sessions),
            "first_date": sessions[0]["date"],
            "last_date": sessions[-1]["date"],
            "first": {k: v for k, v in sessions[0].items() if k != "date"},
            "best": {k: v for k, v in sessions[best_idx].items() if k != "date"},
            "best_date": sessions[best_idx]["date"],
        }
        summary["baseline_score"] = round(baseline, 1)
        if kind == "weighted" and baseline > 0:
            summary["baseline_e1rm"] = round(baseline, 1)
            summary["e1rm_gain_pct"] = round(100 * (best_score - baseline) / baseline)
            heaviest = max(sessions, key=lambda x: (x["top_weight_kg"], x["top_weight_reps"]))
            summary["first_top"] = {"weight_kg": sessions[0]["top_weight_kg"], "reps": sessions[0]["top_weight_reps"]}
            summary["heaviest"] = {"weight_kg": heaviest["top_weight_kg"], "reps": heaviest["top_weight_reps"], "date": heaviest["date"]}
        if min(scores[:3]) < 0.5 * baseline:
            summary["flags"] = ["early_session_looks_like_a_slip"]
        series.append({
            "client_id": client_id,
            "exercise": exercise,
            "muscle_group": meta[(client_id, exercise, kind)],
            "kind": kind,
            "sessions": sessions,
            "summary": summary,
        })
    return series


def collect_prs(conn, client_ids) -> list[dict]:
    rows = q(conn, """
        select p.client_id, e.name as exercise, p.rep_count, p.weight, p.achieved_at
        from exercise_prs p join exercises e on e.id = p.exercise_id
        where p.client_id = any(%(ids)s)
        order by p.client_id, e.name, p.rep_count
    """, {"ids": client_ids})
    return [{
        "client_id": str(r["client_id"]),
        "exercise": r["exercise"],
        "reps": r["rep_count"],
        "weight_kg": r["weight"],
        "achieved_on": r["achieved_at"].date(),
    } for r in rows]


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def collect_goals(conn, client_ids) -> list[dict]:
    rows = q(conn, """
        select g.id, g.client_id, g.title, g.description, g.metric_key, g.progress_mode,
               g.start_value, g.target_value, g.current_value, g.completed,
               g.effective_at, g.ended_at, g.end_reason, e.name as exercise,
               coalesce((select json_agg(json_build_object('date', n.created_at::date, 'note', n.note) order by n.created_at)
                         from goal_progress_notes n where n.goal_id = g.id), '[]'::json) as progress_notes
        from client_goals g left join exercises e on e.id = g.exercise_id
        where g.client_id = any(%(ids)s)
        order by g.client_id, g.effective_at
    """, {"ids": client_ids})
    goals = []
    for r in rows:
        status = "completed" if r["completed"] else ("closed" if r["ended_at"] else "open")
        goals.append({
            "client_id": str(r["client_id"]),
            "title": r["title"],
            "description": r["description"],
            "metric_key": r["metric_key"],
            "progress_mode": r["progress_mode"],
            "exercise": r["exercise"],
            "start_value": r["start_value"],
            "target_value": r["target_value"],
            "current_value": r["current_value"],
            "status": status,
            "started": r["effective_at"].date(),
            "ended": r["ended_at"].date() if r["ended_at"] else None,
            "coach_closing_note": r["end_reason"],
            "progress_notes": r["progress_notes"],
        })
    return goals


# ---------------------------------------------------------------------------
# Subjective text from check-ins (pre-app JSON keys + app-era form submissions)
# ---------------------------------------------------------------------------

def collect_text(conn, client_ids) -> list[dict]:
    entries = []

    legacy = q(conn, """
        select ci.client_id, ci.id as check_in_id, ci.timestamp, ci.subjective_feedback
        from check_ins ci
        where ci.client_id = any(%(ids)s)
        order by ci.timestamp
    """, {"ids": client_ids})
    for r in legacy:
        for label, answer in (r["subjective_feedback"] or {}).items():
            if not isinstance(answer, str) or len(answer.strip()) < 3:
                continue
            entries.append({
                "client_id": str(r["client_id"]),
                "check_in_id": str(r["check_in_id"]),
                "date": r["timestamp"].date(),
                "era": "pre_app",
                "field": label,
                "question": label,
                "category": categorise(label),
                "answer": answer.strip(),
            })

    forms = q(conn, """
        select fs.client_id, fs.check_in_id, coalesce(fs.submitted_at, fs.created_at) as submitted_at,
               fs.schema_snapshot, fs.custom_answers, ft.form_type
        from form_submissions fs left join form_templates ft on ft.id = fs.template_id
        where fs.client_id = any(%(ids)s) and fs.status = 'submitted'
        order by fs.submitted_at
    """, {"ids": client_ids})
    for r in forms:
        labels, types = {}, {}
        for section in (r["schema_snapshot"] or {}).get("sections", []):
            for field in section.get("fields", []):
                labels[field["id"]] = field.get("label", field["id"])
                types[field["id"]] = field.get("type")
        for field_id, answer in (r["custom_answers"] or {}).items():
            if not isinstance(answer, str) or len(answer.strip()) < 3:
                continue
            if types.get(field_id) in ("number",):
                continue
            label = labels.get(field_id, field_id)
            entries.append({
                "client_id": str(r["client_id"]),
                "check_in_id": str(r["check_in_id"]) if r["check_in_id"] else None,
                "date": r["submitted_at"].date(),
                "era": "app",
                "form_type": r["form_type"],
                "field": field_id,
                "question": label,
                "category": "intake" if r["form_type"] == "intake" else categorise(label),
                "answer": answer.strip(),
            })

    entries.sort(key=lambda e: (e["client_id"], e["date"]))
    return entries


# ---------------------------------------------------------------------------
# Habits and consistency
# ---------------------------------------------------------------------------

def collect_habits(conn, client_ids) -> dict:
    checkins = q(conn, """
        select ci.client_id, ci.timestamp::date as date, ci.average_steps, ci.average_sleep,
               ci.days_worked_out, ci.average_water_intake, ci.reviewed_at, r.created_at as responded_at
        from check_ins ci left join check_in_responses r on r.check_in_id = ci.id
        where ci.client_id = any(%(ids)s)
        order by ci.client_id, ci.timestamp
    """, {"ids": client_ids})
    by_client = defaultdict(list)
    for r in checkins:
        by_client[str(r["client_id"])].append({
            "date": r["date"],
            "avg_steps": r["average_steps"],
            "avg_sleep_h": r["average_sleep"],
            "days_worked_out_self_reported": r["days_worked_out"],
            "avg_water_l": r["average_water_intake"],
            "coach_reviewed_on": r["reviewed_at"].date() if r["reviewed_at"] else None,
            "coach_replied_on": r["responded_at"].date() if r["responded_at"] else None,
        })

    workouts = q(conn, """
        select wl.client_id, wl.started_at::date as date, wl.duration_seconds,
               (select count(*) from exercise_set_logs s where s.workout_log_id = wl.id and s.deleted_at is null) as sets,
               jsonb_array_length(wl.new_prs) as new_prs
        from workout_logs wl
        where wl.client_id = any(%(ids)s) and wl.status = 'completed'
        order by wl.client_id, wl.started_at
    """, {"ids": client_ids})
    weekly = defaultdict(lambda: defaultdict(int))
    workout_list = defaultdict(list)
    for r in workouts:
        cid = str(r["client_id"])
        weekly[cid][iso_week(r["date"])] += 1
        workout_list[cid].append({
            "date": r["date"],
            "duration_min": round(r["duration_seconds"] / 60) if r["duration_seconds"] else None,
            "sets": r["sets"],
            "new_prs": r["new_prs"],
        })

    return {
        cid: {
            "checkins": by_client.get(cid, []),
            "workouts_logged": workout_list.get(cid, []),
            "workouts_per_week": dict(sorted(weekly.get(cid, {}).items())),
        }
        for cid in set(by_client) | set(workout_list)
    }


# ---------------------------------------------------------------------------
# Coach-side stats
# ---------------------------------------------------------------------------

def collect_coach_stats(conn, client_ids) -> dict:
    totals = q(conn, """
        select
          (select count(*) from check_ins where client_id = any(%(ids)s)) as checkins,
          (select count(*) from check_ins where client_id = any(%(ids)s) and reviewed) as checkins_reviewed,
          (select count(*) from check_in_responses r join check_ins ci on ci.id = r.check_in_id where ci.client_id = any(%(ids)s)) as written_replies,
          (select round(avg(length(r.response_text))) from check_in_responses r join check_ins ci on ci.id = r.check_in_id where ci.client_id = any(%(ids)s)) as avg_reply_chars,
          (select sum(length(r.response_text)) from check_in_responses r join check_ins ci on ci.id = r.check_in_id where ci.client_id = any(%(ids)s)) as total_reply_chars,
          (select count(*) from workout_logs where client_id = any(%(ids)s) and status = 'completed') as workouts_logged,
          (select count(*) from exercise_set_logs esl join workout_logs wl on wl.id = esl.workout_log_id where wl.client_id = any(%(ids)s) and esl.deleted_at is null) as sets_logged,
          (select coalesce(sum(jsonb_array_length(new_prs)),0) from workout_logs where client_id = any(%(ids)s) and status = 'completed') as pr_events,
          (select count(*) from exercise_prs where client_id = any(%(ids)s)) as current_pr_records,
          (select count(*) from workout_exercise_videos where client_id = any(%(ids)s) and deleted_at is null) as form_videos,
          (select count(*) from workout_exercise_video_comments vc join workout_exercise_videos v on v.id = vc.video_id where v.client_id = any(%(ids)s)) as video_comments,
          (select count(*) from workout_exercise_coach_comments wc join workout_logs wl on wl.id = wc.workout_log_id where wl.client_id = any(%(ids)s)) as exercise_comments,
          (select count(*) from workout_log_feedback f join workout_logs wl on wl.id = f.workout_log_id where wl.client_id = any(%(ids)s)) as workout_feedback_notes,
          (select count(*) from meal_photo_logs where client_id = any(%(ids)s)) as meals_logged,
          (select count(*) from meal_photo_coach_comments mc join meal_photo_logs m on m.id = mc.meal_photo_id where m.client_id = any(%(ids)s)) as meal_comments,
          (select count(*) from coach_notes where client_id = any(%(ids)s)) as coach_notes,
          (select count(*) from client_goals where client_id = any(%(ids)s)) as goals_set,
          (select count(*) from client_goals where client_id = any(%(ids)s) and completed) as goals_completed,
          (select count(*) from body_measurement_log where client_id = any(%(ids)s)) as measurements_recorded,
          (select count(*) from check_ins where client_id = any(%(ids)s) and coalesce(front_picture_gcs_url, front_picture_url) is not null) as checkins_with_photos
    """, {"ids": client_ids})[0]

    turnaround = q(conn, """
        with x as (
          select extract(epoch from (ci.reviewed_at - ci.timestamp))/3600 as review_h,
                 extract(epoch from (r.created_at - ci.timestamp))/3600 as reply_h
          from check_ins ci left join check_in_responses r on r.check_in_id = ci.id
          where ci.client_id = any(%(ids)s) and ci.timestamp >= '2026-01-01'
        )
        select count(*) as checkins_since_2026, count(review_h) as reviewed, count(reply_h) as replied,
               round(percentile_cont(0.5) within group (order by review_h)::numeric, 1) as review_median_h,
               round(percentile_cont(0.9) within group (order by review_h)::numeric, 1) as review_p90_h,
               round(percentile_cont(0.5) within group (order by reply_h)::numeric, 1) as reply_median_h,
               round(percentile_cont(0.9) within group (order by reply_h)::numeric, 1) as reply_p90_h,
               count(*) filter (where reply_h <= 24) as replied_within_24h,
               count(*) filter (where reply_h <= 48) as replied_within_48h
        from x
    """, {"ids": client_ids})[0]

    monthly = q(conn, """
        with months as (select generate_series(date '2025-07-01', date_trunc('month', now())::date, interval '1 month')::date as m)
        select to_char(m, 'YYYY-MM') as month,
          (select count(*) from check_ins ci where ci.client_id = any(%(ids)s) and date_trunc('month', ci.timestamp)::date = m) as checkins,
          (select count(distinct ci.client_id) from check_ins ci where ci.client_id = any(%(ids)s) and date_trunc('month', ci.timestamp)::date = m) as active_clients,
          (select count(*) from workout_logs wl where wl.client_id = any(%(ids)s) and wl.status = 'completed' and date_trunc('month', wl.started_at)::date = m) as workouts,
          (select count(*) from workout_exercise_videos v where v.client_id = any(%(ids)s) and v.deleted_at is null and date_trunc('month', v.created_at)::date = m) as form_videos,
          (select count(*) from meal_photo_logs ml where ml.client_id = any(%(ids)s) and date_trunc('month', ml.created_at)::date = m) as meals_logged,
          (select count(*) from check_in_responses r join check_ins ci on ci.id = r.check_in_id where ci.client_id = any(%(ids)s) and date_trunc('month', r.created_at)::date = m) as written_replies
        from months order by m
    """, {"ids": client_ids})

    return {
        "note": "Turnaround figures use check-ins from 2026-01-01 onwards, when reviews and replies started being timestamped in the app.",
        "totals": totals,
        "turnaround_2026": turnaround,
        "monthly": monthly,
    }


# ---------------------------------------------------------------------------

def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    config = load_config()
    conn = connect()
    coach = q(conn, "select id from coaches where email = %(email)s", {"email": config["coach_email"]})
    if not coach:
        sys.exit(f"coach {config['coach_email']} not found")
    coach_id = coach[0]["id"]

    print("collecting roster")
    roster = collect_roster(conn, coach_id, config["self_emails"])
    write("roster.json", roster)
    client_ids = [r["client_id"] for r in roster if r["kind"] == "client"]
    print(f"  {len(roster)} rows, {len(client_ids)} real clients (self/demo/never-started excluded from series files)")

    print("collecting body measurements")
    write("measurements.json", collect_measurements(conn, client_ids))
    print("collecting strength series")
    write("strength.json", collect_strength(conn, client_ids))
    write("prs.json", collect_prs(conn, client_ids))
    print("collecting goals")
    write("goals.json", collect_goals(conn, client_ids))
    print("collecting subjective text")
    text = collect_text(conn, client_ids)
    write("checkin_text.json", text)
    print("collecting habits")
    write("habits.json", collect_habits(conn, client_ids))
    print("collecting coach stats")
    write("coach_stats.json", collect_coach_stats(conn, client_ids))

    write("meta.json", {
        "generated_at": datetime.now().astimezone(),
        "coach_email": config["coach_email"],
        "clients_in_series": len(client_ids),
        "text_entries": len(text),
    })
    conn.close()
    print("done")


if __name__ == "__main__":
    main()
