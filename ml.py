"""
Explainable ML pipeline (scikit-learn).

Models
------
* Academic risk      -> LogisticRegression (explainable) + RandomForest (non-linear), probabilities averaged.
                        Target: course exam percentage < 50.
* Low attendance     -> LogisticRegression + RandomForest trained on the FIRST 60 % of sessions to predict whether
                        attendance in the remaining 40 % falls below 75 %.
* Attendance trend   -> LinearRegression on weekly attendance percentage (forecast next weeks).

Explainability: per-student factor contributions come from the logistic-regression coefficients applied to
standardised features (coef x z-score); global importances come from the Random Forest. Predictions are
probability estimates, never guaranteed outcomes. If there is not enough data a transparent rule-based
heuristic is used instead.
"""
import logging
from datetime import timedelta

import joblib
import numpy as np
import pandas as pd
from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

MODEL_VERSION = "1.0"
FEATURES = ["attendance_pct", "late_pct", "trend", "assignment_avg", "submission_rate", "previous_cgpa"]
LABELS = {
    "attendance_pct": "Attendance", "late_pct": "Late arrivals", "trend": "Attendance trend",
    "assignment_avg": "Assignment scores", "submission_rate": "Assignment submission rate",
    "previous_cgpa": "Previous CGPA",
}
UNITS = {"attendance_pct": "%", "late_pct": "%", "trend": " pts/10 sessions", "assignment_avg": "%",
         "submission_rate": "%", "previous_cgpa": ""}
RISK_FAIL_BELOW = 50.0
LOW_ATT_BELOW = 75.0
DISCLAIMER = ("These are statistical estimates based on past patterns, not guaranteed outcomes. "
              "Use them to start supportive conversations, not to make final decisions about a student.")


# ------------------------------------------------------------------ features
def _att_features(statuses):
    counted = [s for s in statuses if s != "excused"]
    n = len(counted)
    if n < 3:
        return {"attendance_pct": np.nan, "late_pct": np.nan, "trend": 0.0}
    attended = np.array([1.0 if s in ("present", "late") else 0.0 for s in counted])
    late = np.mean([1.0 if s == "late" else 0.0 for s in counted]) * 100
    slope = np.polyfit(np.arange(n), attended, 1)[0] * 10 * 100 if n >= 5 else 0.0
    return {"attendance_pct": attended.mean() * 100, "late_pct": late, "trend": float(slope)}


def _pct(statuses):
    counted = [s for s in statuses if s != "excused"]
    return np.mean([1.0 if s in ("present", "late") else 0.0 for s in counted]) * 100 if counted else np.nan


def build_dataset(semester_ids=None):
    """Return (full, early) DataFrames: one row per (student, course) enrolment."""
    from apps.academics.models import AcademicRecord, Assignment, Result, Submission
    from apps.attendance.models import Attendance
    from apps.courses.models import Course, Semester
    from apps.students.models import Enrollment

    enr = Enrollment.objects.exclude(status="dropped")
    if semester_ids:
        enr = enr.filter(course__semester_id__in=semester_ids)
    enr = pd.DataFrame(list(enr.values("student_id", "course_id", "course__semester_id")))
    if enr.empty:
        return pd.DataFrame(), pd.DataFrame()

    att = pd.DataFrame(list(Attendance.objects.values("student_id", "session__course_id", "session__date", "status")))
    att_groups = {}
    if not att.empty:
        att = att.sort_values("session__date")
        for (s, c), g in att.groupby(["student_id", "session__course_id"]):
            att_groups[(s, c)] = list(g["status"])

    n_assign = dict(Assignment.objects.values_list("course_id").annotate(n=Count("id")))
    subs = {}
    for s, c, m, mx in Submission.objects.values_list("student_id", "assignment__course_id", "marks",
                                                       "assignment__max_marks"):
        d = subs.setdefault((s, c), {"n": 0, "pcts": []})
        d["n"] += 1
        if m is not None and mx:
            d["pcts"].append(float(m) / float(mx) * 100)

    res = {}
    for s, c, got, tot in Result.objects.values_list("student_id", "exam__course_id", "marks_obtained",
                                                      "exam__total_marks"):
        d = res.setdefault((s, c), [0.0, 0.0])
        d[0] += float(got)
        d[1] += float(tot)

    sem_start = {s.id: s.start_date for s in Semester.objects.all()}
    recs = {}
    for sid, semid, cg in AcademicRecord.objects.values_list("student_id", "semester_id", "cgpa"):
        recs.setdefault(sid, []).append((sem_start[semid], float(cg)))

    full_rows, early_rows = [], []
    for e in enr.itertuples(index=False):
        sid, cid, semid = e.student_id, e.course_id, e.course__semester_id
        statuses = att_groups.get((sid, cid), [])
        sd = subs.get((sid, cid), {"n": 0, "pcts": []})
        total_a = n_assign.get(cid, 0)
        prev = [cg for d, cg in sorted(recs.get(sid, [])) if d < sem_start.get(semid, d)]
        common = {
            "student_id": sid, "course_id": cid, "semester_id": semid,
            "assignment_avg": float(np.mean(sd["pcts"])) if sd["pcts"] else np.nan,
            "submission_rate": min(sd["n"] / total_a * 100, 100) if total_a else np.nan,
            "previous_cgpa": prev[-1] if prev else np.nan,
        }
        r = res.get((sid, cid))
        exam_pct = r[0] / r[1] * 100 if r and r[1] else np.nan
        split = int(round(len(statuses) * 0.6))
        future = _pct(statuses[split:]) if split >= 4 and len(statuses) - split >= 3 else np.nan
        full_rows.append({**common, **_att_features(statuses), "exam_pct": exam_pct, "future_att": future})
        early_rows.append({**common, **_att_features(statuses[:split]), "exam_pct": exam_pct, "future_att": future})
    return pd.DataFrame(full_rows), pd.DataFrame(early_rows)


# ------------------------------------------------------------------ models
def _fit_pair(X, y):
    def mk(kind):
        steps = [("imp", SimpleImputer(strategy="median"))]
        if kind == "lr":
            steps += [("sc", StandardScaler()),
                      ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))]
        else:
            steps += [("clf", RandomForestClassifier(n_estimators=150, max_depth=6, min_samples_leaf=3,
                                                     class_weight="balanced", random_state=42))]
        return Pipeline(steps)

    lr, rf = mk("lr"), mk("rf")
    cv = StratifiedKFold(n_splits=max(2, min(5, int(y.value_counts().min()))), shuffle=True, random_state=42)
    metrics = {"samples": int(len(y)), "positive_rate": round(float(y.mean()), 3)}
    for name, mdl in (("logistic_regression", lr), ("random_forest", rf)):
        try:
            sc = cross_val_score(mdl, X, y, cv=cv, scoring="roc_auc")
            metrics[name] = {"roc_auc_mean": round(float(sc.mean()), 3), "roc_auc_std": round(float(sc.std()), 3)}
        except Exception as exc:  # pragma: no cover
            log.warning("CV failed for %s: %s", name, exc)
            metrics[name] = None
        mdl.fit(X, y)
    metrics["rf_feature_importance"] = {f: round(float(v), 3) for f, v in
                                         zip(FEATURES, rf.named_steps["clf"].feature_importances_)}
    metrics["lr_coefficients"] = {f: round(float(v), 3) for f, v in
                                   zip(FEATURES, lr.named_steps["clf"].coef_[0])}
    return {"lr": lr, "rf": rf}, metrics


def _usable(y, n):
    return n >= 40 and y.nunique() == 2 and int(y.value_counts().min()) >= 5


def model_path():
    settings.ML_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    return settings.ML_MODEL_DIR / "risk_models.joblib"


def train_models():
    full, early = build_dataset()
    bundle = {"version": MODEL_VERSION, "trained_at": timezone.now().isoformat(), "academic": None,
              "attendance": None, "metrics": {"academic": None, "attendance": None}, "notes": []}
    if full.empty:
        bundle["notes"].append("No enrolment data available; rule-based heuristics are used.")
    else:
        d = full[full.exam_pct.notna()]
        y = (d.exam_pct < RISK_FAIL_BELOW).astype(int)
        if _usable(y, len(d)):
            bundle["academic"], bundle["metrics"]["academic"] = _fit_pair(d[FEATURES], y)
        else:
            bundle["notes"].append("Academic-risk model: not enough labelled results (need 40+ rows with both classes); "
                                   "using rule-based heuristic.")
        e = early[early.future_att.notna()]
        y2 = (e.future_att < LOW_ATT_BELOW).astype(int)
        if _usable(y2, len(e)):
            bundle["attendance"], bundle["metrics"]["attendance"] = _fit_pair(e[FEATURES], y2)
        else:
            bundle["notes"].append("Low-attendance model: not enough history; using rule-based heuristic.")
    joblib.dump(bundle, model_path())
    return bundle


def load_bundle(retrain=False):
    p = model_path()
    if retrain or not p.exists():
        return train_models()
    try:
        return joblib.load(p)
    except Exception:  # pragma: no cover
        return train_models()


_W = {"attendance_pct": -0.05, "late_pct": 0.02, "trend": -0.01, "assignment_avg": -0.03,
      "submission_rate": -0.02, "previous_cgpa": -0.8}
_REF = {"attendance_pct": 75, "late_pct": 8, "trend": 0, "assignment_avg": 60, "submission_rate": 75,
        "previous_cgpa": 2.8}


def _heuristic(X, bias):
    contrib = np.column_stack([_W[f] * (X[f].fillna(_REF[f]) - _REF[f]).values for f in FEATURES])
    return 1 / (1 + np.exp(-(contrib.sum(axis=1) + bias))), contrib


def _predict(models, X, bias):
    if models is None:
        return _heuristic(X, bias)
    lr, rf = models["lr"], models["rf"]
    prob = (lr.predict_proba(X)[:, 1] + rf.predict_proba(X)[:, 1]) / 2
    z = lr.named_steps["sc"].transform(lr.named_steps["imp"].transform(X))
    return prob, z * lr.named_steps["clf"].coef_[0]


def _fmt(f, v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{v:.2f}" if f == "previous_cgpa" else f"{v:.0f}{UNITS[f]}" if f != "trend" else f"{v:+.1f}{UNITS[f]}"


def _explain(level, factors):
    risk = [f for f in factors if f["impact"] > 0][:3]
    prot = [f for f in factors if f["impact"] < 0][:2]
    text = f"Estimated {level} risk (a probability-based estimate, not a certainty). "
    if risk:
        text += "Main contributing factors: " + "; ".join(f"{f['label']} ({f['display']})" for f in risk) + ". "
    if prot:
        text += "Helping factors: " + "; ".join(f"{f['label']} ({f['display']})" for f in prot) + "."
    return text.strip()


def run_predictions(semester=None, retrain=True):
    """Train (optionally) and store a RiskPrediction for every student in the target semester."""
    from apps.analytics.models import RiskPrediction
    from apps.courses.models import Semester
    from apps.notifications.services import notify

    bundle = load_bundle(retrain=retrain)
    sem = semester or Semester.objects.filter(is_current=True).first() or Semester.objects.order_by("-start_date").first()
    if sem is None:
        return {"count": 0, "detail": "No semester found."}
    full, _ = build_dataset([sem.id])
    if full.empty:
        return {"count": 0, "detail": "No enrolments in this semester."}
    X = full[FEATURES]
    p_ac, c_ac = _predict(bundle["academic"], X, -0.3)
    p_at, c_at = _predict(bundle["attendance"], X, -0.3)
    full = full.assign(p_ac=p_ac, p_at=p_at)
    contrib = 0.6 * c_ac + 0.4 * c_at
    counts = {"low": 0, "medium": 0, "high": 0}
    for sid, g in full.groupby("student_id"):
        idx = g.index.values
        pa, pl = float(g.p_ac.mean()), float(g.p_at.mean())
        score = round(100 * (0.6 * pa + 0.4 * pl), 1)
        level = "high" if score >= 60 else "medium" if score >= 35 else "low"
        c_mean = contrib[idx].mean(axis=0)
        vals = g[FEATURES].mean()
        factors = sorted(({"feature": f, "label": LABELS[f], "value": None if np.isnan(vals[f]) else round(float(vals[f]), 2),
                           "display": _fmt(f, vals[f]), "impact": round(float(c_mean[i]), 3),
                           "direction": "increases risk" if c_mean[i] > 0 else "reduces risk"}
                          for i, f in enumerate(FEATURES)), key=lambda x: -abs(x["impact"]))
        pred, created = RiskPrediction.objects.update_or_create(
            student_id=sid, semester=sem,
            defaults=dict(risk_score=score, risk_level=level, academic_risk_probability=round(pa, 3),
                          low_attendance_probability=round(pl, 3), needs_intervention=level == "high" or pl >= 0.6,
                          factors=factors[:6], explanation=_explain(level, factors), model_version=MODEL_VERSION))
        counts[level] += 1
        if level == "high" and created:
            from apps.students.models import Student
            st = Student.objects.select_related("user", "department").get(pk=sid)
            notify(st.user, "Academic support available",
                   "Your recent attendance and assignment patterns suggest extra support could help. "
                   "Consider talking to your course teacher.", "warning", "/analytics")
    return {"count": int(full.student_id.nunique()), "levels": counts, "semester": sem.name,
            "metrics": bundle["metrics"], "notes": bundle.get("notes", []), "trained_at": bundle.get("trained_at")}


# ------------------------------------------------------------------ trend forecasting
def attendance_forecast(qs, weeks_ahead=4):
    rows = list(qs.values_list("session__date", "status"))
    if not rows:
        return {"history": [], "forecast": [], "message": "No attendance data."}
    df = pd.DataFrame(rows, columns=["date", "status"])
    df = df[df.status != "excused"]
    df["date"] = pd.to_datetime(df["date"])
    df["week"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
    df["att"] = df.status.isin(["present", "late"]).astype(float) * 100
    wk = df.groupby("week")["att"].mean().reset_index().sort_values("week")
    history = [{"week": w.strftime("%Y-%m-%d"), "percentage": round(float(a), 1)} for w, a in zip(wk.week, wk.att)]
    if len(wk) < 3:
        return {"history": history, "forecast": [], "message": "At least 3 weeks of data are needed for a forecast."}
    x = np.arange(len(wk)).reshape(-1, 1)
    lr = LinearRegression().fit(x, wk.att.values)
    fx = np.arange(len(wk), len(wk) + weeks_ahead).reshape(-1, 1)
    preds = np.clip(lr.predict(fx), 0, 100)
    last = wk.week.iloc[-1]
    forecast = [{"week": (last + timedelta(weeks=i + 1)).strftime("%Y-%m-%d"), "percentage": round(float(p), 1)}
                for i, p in enumerate(preds)]
    slope = float(lr.coef_[0])
    trend = "improving" if slope > 0.5 else "declining" if slope < -0.5 else "stable"
    return {"history": history, "forecast": forecast, "slope_per_week": round(slope, 2), "trend": trend,
            "r2": round(float(lr.score(x, wk.att.values)), 3),
            "message": f"Attendance looks {trend} (about {slope:+.1f} percentage points per week). "
                       "This is a linear projection and may not reflect future changes."}
