# Digest the raw data dump into the compact dataset the client-wins mockup renders.
# Writes mockups/wins-data.js (git-ignored) as `window.WINS = {...}`.

import json
import re
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "mockups" / "wins-data.js"

MAX_SERIES_POINTS = 40
MIN_LIFT_SESSIONS = 8
MIN_LIFT_GAIN_PCT = 15
LIFTS_PER_CLIENT = 4
WIN_TEXT_LENGTH = (40, 240)
WARMUP_WORDS = ("warm up", "cool down", "routine")
# "Two wins" answers that open by saying there were none are not wins.
NOT_A_WIN = re.compile(r"^\s*(none|no wins?|no\b|not really|nothing|nil|i didn|didn|i don't|can't think)", re.IGNORECASE)


def load(name):
    return json.loads((DATA / name).read_text())


def downsample(points, limit=MAX_SERIES_POINTS):
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def clean_series(series):
    # Workout session bodyweights are carried forward from the last reading, so they are not readings.
    return [[p["date"], p["value"]] for p in series["points"] if not p["flags"] and p["source"] != "workout_log"]


def shape_metric(series):
    pts = clean_series(series)
    if len(pts) < 2:
        return None
    return {
        "points": downsample(pts),
        "start": pts[0][1],
        "end": pts[-1][1],
        "min": min(v for _, v in pts),
        "max": max(v for _, v in pts),
        "delta": round(pts[-1][1] - pts[0][1], 1),
        "start_date": pts[0][0],
        "end_date": pts[-1][0],
        "n": len(pts),
    }


def avg(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 1) if values else None


def main():
    roster = load("roster.json")
    included = {r["client_id"]: r for r in roster if r["include_suggested"]}
    clients = [{
        "id": r["client_id"],
        "name": r["first_name"],
        "checkins": r["checkins"],
        "workouts": r["workouts_completed"],
        "tenure_days": r["tenure_days"],
        "first_activity": r["first_activity"][:10] if r["first_activity"] else None,
        "last_activity": r["last_activity"][:10] if r["last_activity"] else None,
        "primary_goals": r["primary_goals"],
    } for r in roster if r["client_id"] in included]

    # Body change: weight plus waist per client, clean points only.
    by_client_metric = {(s["client_id"], s["metric"]): s for s in load("measurements.json")}
    body = []
    for cid in included:
        weight = by_client_metric.get((cid, "weight"))
        waist = by_client_metric.get((cid, "waist"))
        w = shape_metric(weight) if weight else None
        if not w or w["n"] < 6:
            continue
        body.append({
            "client": cid,
            "weight": w,
            "waist": shape_metric(waist) if waist else None,
        })
    body.sort(key=lambda b: b["weight"]["delta"])

    # Strength: the best-progressing weighted lifts per client, plus rep-based bodyweight moves.
    lifts, reps_moves = [], []
    per_client = {}
    for s in load("strength.json"):
        if s["client_id"] not in included or s["summary"].get("flags"):
            continue
        if any(w in s["exercise"].lower() for w in WARMUP_WORDS):
            continue
        u = s["summary"]
        if s["kind"] == "weighted" and u["sessions"] >= MIN_LIFT_SESSIONS and u.get("e1rm_gain_pct", 0) >= MIN_LIFT_GAIN_PCT:
            per_client.setdefault(s["client_id"], []).append({
                "client": s["client_id"],
                "exercise": s["exercise"],
                "muscle_group": s["muscle_group"],
                "sessions": u["sessions"],
                "first_date": u["first_date"],
                "last_date": u["last_date"],
                "baseline_e1rm": u["baseline_e1rm"],
                "best_e1rm": u["best"]["e1rm"],
                "gain_pct": u["e1rm_gain_pct"],
                "first_set": f'{u["first"]["weight_kg"]:g} kg x {u["first"]["reps"]}',
                "best_set": f'{u["best"]["weight_kg"]:g} kg x {u["best"]["reps"]}',
                "points": downsample([[x["date"], x["e1rm"]] for x in s["sessions"]]),
            })
        elif s["kind"] == "bodyweight" and u["sessions"] >= 5 and u["best"]["reps"] > u["first"]["reps"] and not u["best"].get("added_kg"):
            reps_moves.append({
                "client": s["client_id"],
                "exercise": s["exercise"],
                "sessions": u["sessions"],
                "first_reps": u["first"]["reps"],
                "best_reps": u["best"]["reps"],
                "first_date": u["first_date"],
                "best_date": u["best_date"],
            })
    for cid, items in per_client.items():
        items.sort(key=lambda x: (-x["sessions"], -x["gain_pct"]))
        lifts.extend(items[:LIFTS_PER_CLIENT])

    goals = [{
        "client": g["client_id"],
        "title": g["title"],
        "metric_key": g["metric_key"],
        "progress_mode": g["progress_mode"],
        "exercise": g["exercise"],
        "start": g["start_value"],
        "target": g["target_value"],
        "achieved": g["current_value"],
        "started": g["started"],
        "ended": g["ended"],
        "note": g["coach_closing_note"],
    } for g in load("goals.json") if g["status"] == "completed" and g["client_id"] in included]
    goals.sort(key=lambda g: g["ended"])

    lo, hi = WIN_TEXT_LENGTH
    text = load("checkin_text.json")
    wins = [{"client": e["client_id"], "date": e["date"], "text": e["answer"]}
            for e in text if e["client_id"] in included and e["category"] == "wins"
            and lo <= len(e["answer"]) <= hi and not NOT_A_WIN.match(e["answer"])]
    workout_notes = [{"client": e["client_id"], "date": e["date"], "text": e["answer"]}
                     for e in text if e["client_id"] in included and e["category"] == "workouts" and lo <= len(e["answer"]) <= hi]

    habits = []
    for cid, h in load("habits.json").items():
        if cid not in included or len(h["checkins"]) < 8:
            continue
        cis = h["checkins"]
        first, last = cis[:4], cis[-4:]
        weeks = h["workouts_per_week"]
        habits.append({
            "client": cid,
            "checkins": len(cis),
            "first_checkin": cis[0]["date"],
            "last_checkin": cis[-1]["date"],
            "steps_first": avg(c["avg_steps"] for c in first),
            "steps_last": avg(c["avg_steps"] for c in last),
            "sleep_first": avg(c["avg_sleep_h"] for c in first),
            "sleep_last": avg(c["avg_sleep_h"] for c in last),
            "logged_weeks": len(weeks),
            "avg_workouts_per_logged_week": round(statistics.mean(weeks.values()), 1) if weeks else None,
        })

    stats = load("coach_stats.json")
    real_clients = [r for r in roster if r["kind"] == "client" and r["checkins"] >= 1]
    stats["derived"] = {
        "clients_with_checkins": len(real_clients),
        "clients_in_mockup": len(included),
        "clients_6_months_plus": sum(1 for r in real_clients if (r["tenure_days"] or 0) >= 180),
        "reply_words_estimate": round(stats["totals"]["total_reply_chars"] / 5.5),
    }

    payload = {
        "generated_from": json.loads((DATA / "meta.json").read_text())["generated_at"],
        "clients": clients,
        "stats": stats,
        "body": body,
        "lifts": lifts,
        "reps_moves": reps_moves,
        "goals": goals,
        "wins": wins,
        "workout_notes": workout_notes,
        "habits": habits,
    }
    OUT.write_text("window.WINS = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    print(f"clients {len(clients)}, body {len(body)}, lifts {len(lifts)}, reps_moves {len(reps_moves)}, goals {len(goals)}, wins {len(wins)}, workout_notes {len(workout_notes)}, habits {len(habits)} -> {OUT.name} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Collage dataset (mockups/collage-data.js): the polished layer on top of the raw dump.
# ---------------------------------------------------------------------------

COLLAGE_OUT = HERE / "mockups" / "collage-data.js"
CURATION = DATA / "curation.json"


# Weighted moves compare the heaviest set in the first logged session with the heaviest set logged since.
# Rep moves compare the first session with the best sustained over the last three sessions, so a one-off entry does not count.
LB_PER_KG = 2.20462
MIN_WEIGHT_GAIN = 1.15
MIN_REPS_GAIN = 1.3
EXERCISE_LABELS = {
    "Booty Builder machine": "Hip thrust machine",
    "Overhead Military Press / DB Shoulder Press": "Overhead press",
    "Barbell Overhead Press": "Barbell overhead press",
    "Leg press incline version": "Leg press (incline)",
    "Seated Dumbbell Shoulder Press": "Dumbbell shoulder press",
    "Barbell Romanian Deadlift": "Romanian deadlift",
    "Seated Cable Rows": "Seated cable row",
    "Lat Pull Down": "Lat pulldown",
    "Chest Press": "Chest press",
    "Leg press": "Leg press",
    "Push-Up": "Push-ups",
}


def in_unit(kg, unit):
    if unit == "lbs":
        return round(kg * LB_PER_KG * 2) / 2
    return round(kg, 1)


def unit_label(unit):
    return "lb" if unit == "lbs" else "kg"


def display_label(raw):
    if raw in EXERCISE_LABELS:
        return EXERCISE_LABELS[raw]
    words = raw.split()
    return " ".join(w if w.isupper() else (w.capitalize() if i == 0 else w.lower()) for i, w in enumerate(words))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_collage():
    roster = load("roster.json")
    by_name = {}
    for r in roster:
        by_name.setdefault(r["first_name"], r)
    collage_names = {r["first_name"] for r in roster if r["include_suggested"]}
    ordered = sorted((by_name[n] for n in collage_names), key=lambda r: r["first_activity"] or "9999")
    codes = {r["client_id"]: f"#{i + 1:02d}" for i, r in enumerate(ordered)}
    curation = json.loads(CURATION.read_text())
    # Everything that names a client lives in the git-ignored curation file, not in this script.
    WEIGHT_TILES = [tuple(t) for t in curation["weight_tiles"]]
    EXERCISE_PICKS = {tuple(t) for t in curation["exercise_picks"]}
    SPOTLIGHT_ORDER = [tuple(t) for t in curation["spotlight_order"]]
    REPORTED_PROGRESSIONS = curation["reported_progressions"]
    GOAL_PROGRESSIONS = curation["goal_progressions"]
    clients = [{
        "id": r["client_id"], "name": r["first_name"], "code": codes[r["client_id"]],
        "initial": r["first_name"][0],
        "since": (r["first_activity"] or "")[:7],
        "persona": curation.get("personas", {}).get(r["first_name"], ""),
        "pseudonym": curation.get("pseudonyms", {}).get(r["first_name"], ""),
        "age": r["age"],
        "gender": r["gender"],
    } for r in ordered]
    name_to_id = {r["first_name"]: r["client_id"] for r in ordered}

    measurements = {(s["client_id"], s["metric"]): s for s in load("measurements.json")}
    weight = []
    for name, cutoff in WEIGHT_TILES:
        cid = name_to_id[name]
        series = measurements.get((cid, "weight"))
        pts = [[p["date"], p["value"]] for p in series["points"] if not p["flags"] and p["source"] != "workout_log"]
        if cutoff:
            pts = [p for p in pts if p[0] <= cutoff]
        r = by_name[name]
        weight.append({
            "id": f"w-{slug(name)}",
            "client": cid,
            "age": r["age"],
            "gender": r["gender"],
            "points": pts,
            "start": pts[0][1], "end": pts[-1][1], "delta": round(pts[-1][1] - pts[0][1], 1),
            "start_date": pts[0][0], "end_date": pts[-1][0], "n": len(pts),
            "cut_at_low_point": bool(cutoff),
        })

    quotes = []
    for i, qd in enumerate(curation["quotes"]):
        cid = name_to_id.get(qd["name"]) or by_name[qd["name"]]["client_id"]
        quotes.append({
            "id": f"q-{i:02d}-{slug(qd['name'])}-{qd['date']}",
            "client": cid,
            "code": codes.get(cid, "#--"),
            "date": qd["date"],
            "tag": qd["tag"],
            "text": qd["text"],
            "selected": qd["selected"],
        })

    units = {r["client_id"]: r["weight_unit"] for r in roster}
    exercises = []
    for s in load("strength.json"):
        cid = s["client_id"]
        if cid not in codes or s["summary"].get("flags"):
            continue
        if any(w in s["exercise"].lower() for w in WARMUP_WORDS):
            continue
        u, name, unit = s["summary"], by_name_id(roster, cid), units[cid]
        picked = (name, s["exercise"]) in EXERCISE_PICKS
        if s["kind"] == "weighted":
            first, heaviest = u.get("first_top"), u.get("heaviest")
            if not first or not heaviest:
                continue
            gain = heaviest["weight_kg"] / first["weight_kg"] if first["weight_kg"] else 0
            if gain < MIN_WEIGHT_GAIN or (not picked and (u["sessions"] < 8 or first["weight_kg"] < 5)):
                continue
            exercises.append({
                "id": f"x-{slug(name)}-{slug(s['exercise'])}",
                "client": cid, "kind": "weight", "key": [name, s["exercise"]],
                "exercise": display_label(s["exercise"]), "raw_exercise": s["exercise"],
                "from": in_unit(first["weight_kg"], unit), "to": in_unit(heaviest["weight_kg"], unit), "unit": unit_label(unit),
                "from_reps": first["reps"], "to_reps": heaviest["reps"],
                "gain_pct": round(100 * (gain - 1)), "sessions": u["sessions"],
                "first_date": u["first_date"], "last_date": heaviest["date"],
                "points": downsample([[x["date"], in_unit(x["top_weight_kg"], unit)] for x in s["sessions"]], 30),
                "selected": picked,
            })
        elif s["kind"] == "bodyweight":
            reps = [x["reps"] for x in s["sessions"]]
            sustained = int(statistics.median(reps[-3:]))
            if u["sessions"] < 6 or sustained < reps[0] * MIN_REPS_GAIN or u["best"].get("added_kg"):
                continue
            exercises.append({
                "id": f"x-{slug(name)}-{slug(s['exercise'])}",
                "client": cid, "kind": "reps", "key": [name, s["exercise"]],
                "exercise": display_label(s["exercise"]), "raw_exercise": s["exercise"],
                "from": reps[0], "to": sustained, "unit": "reps",
                "sessions": u["sessions"], "first_date": u["first_date"], "last_date": u["last_date"],
                "points": downsample([[x["date"], x["reps"]] for x in s["sessions"]], 30),
                "selected": picked,
            })
    goals_by = {(by_name_id(roster, g["client_id"]), g["title"]): g for g in load("goals.json") if g["status"] == "completed"}
    for gp in GOAL_PROGRESSIONS:
        g = goals_by.get((gp["name"], gp["title"]))
        if not g:
            continue
        exercises.append({
            "id": f"x-{slug(gp['name'])}-goal-{slug(gp['title'])}",
            "client": g["client_id"], "kind": "goal", "key": [gp["name"], gp["title"]],
            "exercise": "Pull-ups" if "pull" in gp["title"].lower() else gp["title"],
            "from": round(g["start_value"] or 0), "to": round(g["current_value"]), "unit": "reps",
            "target": round(g["target_value"]), "first_date": g["started"], "last_date": g["ended"],
            "note": g["coach_closing_note"], "points": [], "selected": gp["selected"],
        })
    for rp in REPORTED_PROGRESSIONS:
        exercises.append({
            "id": f"x-{slug(rp['name'])}-reported-{slug(rp['exercise'])}",
            "client": name_to_id[rp["name"]], "kind": "reported", "key": [rp["name"], rp["exercise"]],
            "exercise": rp["exercise"], "from": rp["from"], "to": rp["to"], "unit": rp["unit"],
            "first_date": rp["first_date"], "last_date": rp["last_date"], "points": [], "selected": rp["selected"],
        })
    order = {tuple(k): i for i, k in enumerate(SPOTLIGHT_ORDER)}
    exercises.sort(key=lambda x: (not x["selected"], order.get(tuple(x["key"]), 999), codes[x["client"]], x["exercise"]))

    payload = {"clients": clients, "weight": weight, "quotes": quotes, "exercises": exercises}
    COLLAGE_OUT.write_text("window.COLLAGE = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    print(f"collage: weight {len(weight)}, quotes {len(quotes)} ({sum(q['selected'] for q in quotes)} selected), "
          f"exercises {len(exercises)} ({sum(x['selected'] for x in exercises)} selected) -> {COLLAGE_OUT.name}")


def by_name_id(roster, cid):
    for r in roster:
        if r["client_id"] == cid:
            return r["first_name"]
    return "?"


if __name__ == "__main__":
    build_collage()


# ---------------------------------------------------------------------------
# Site export (src/data/coaching-year-one.json): the default picks, anonymised for the public post.
# ---------------------------------------------------------------------------

SITE_OUT = HERE.parent / "src" / "data" / "coaching-year-one.json"


def export_for_site():
    payload = json.loads(COLLAGE_OUT.read_text().split("=", 1)[1].rstrip().rstrip(";"))
    who = {c["id"]: c["persona"] for c in payload["clients"]}
    clients = {c["id"]: c for c in payload["clients"]}

    def weeks(a, b):
        from datetime import date
        return round((date.fromisoformat(b) - date.fromisoformat(a)).days / 7)

    def person(w):
        g = {"f": "Female", "female": "Female", "m": "Male", "male": "Male"}.get(str(w.get("gender") or "").strip().lower())
        parts = [p for p in (g, str(w["age"]) if w.get("age") else None) if p]
        return ", ".join(parts)

    site = {
        "weight": [{
            "who": person(w),
            "points": w["points"],
            "delta": w["delta"],
            "weeks": weeks(w["start_date"], w["end_date"]),
            "start": w["start"],
            "end": w["end"],
        } for w in payload["weight"]],
        "quotes": [{
            "text": q["text"],
            "who": person(clients.get(q["client"], {})),
            "month": q["date"][:7],
        } for q in payload["quotes"] if q["selected"]],
        "progressions": [{
            "exercise": x["exercise"],
            "from": x["from"],
            "to": x["to"],
            "unit": x["unit"],
            "who": person(clients.get(x["client"], {})),
            "weeks": weeks(x["first_date"], x["last_date"]),
            "kind": x["kind"],
            "points": x.get("points") or [[x["first_date"], x["from"]], [x["last_date"], x["to"]]],
        } for x in payload["exercises"] if x["selected"]],
    }
    SITE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SITE_OUT.write_text(json.dumps(site, indent=2, ensure_ascii=False) + "\n")
    print(f"site export: {len(site['weight'])} weight tiles, {len(site['quotes'])} quotes, {len(site['progressions'])} progressions -> {SITE_OUT.relative_to(HERE.parent)}")


if __name__ == "__main__":
    export_for_site()
