import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Opportunity Scoring & Project Matching",
    page_icon="🎯",
    layout="wide",
)

st.title("🎯 Etapa 35 — AI Opportunity Scoring & Project Matching")
st.caption(
    "Evaluează numai oportunitățile autorizate de ultimul Guard Etapa 33. "
    "Nu inventează TRL, buget, eligibilitate, parteneri sau alte dovezi lipsă."
)


# ---------------------------------------------------------------------
# Config / clients
# ---------------------------------------------------------------------
def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def get_supabase():
    return create_client(
        secret("SUPABASE_URL"),
        secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"),
    )


@st.cache_resource
def get_openai():
    return OpenAI(api_key=secret("OPENAI_API_KEY"))


def model_name() -> str:
    return secret("OPENAI_MODEL", "gpt-4.1-mini")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows(table: str, filters=None, order="created_at", limit=1000):
    try:
        q = supabase.table(table).select("*")
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                q = q.eq(key, value)
        if order:
            q = q.order(order, desc=True)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def restore_auth_session(sb) -> None:
    session = st.session_state.get("auth_session")
    if not session:
        return

    access_token = (
        session.get("access_token")
        if isinstance(session, dict)
        else getattr(session, "access_token", None)
    )
    refresh_token = (
        session.get("refresh_token")
        if isinstance(session, dict)
        else getattr(session, "refresh_token", None)
    )

    if access_token and refresh_token:
        try:
            sb.auth.set_session(access_token, refresh_token)
        except Exception:
            pass


def current_user_id(sb) -> str | None:
    for key in ("auth_user", "user"):
        user = st.session_state.get(key)
        if isinstance(user, dict) and user.get("id"):
            return str(user["id"])
        if getattr(user, "id", None):
            return str(user.id)

    for key in ("user_id", "auth_user_id"):
        value = st.session_state.get(key)
        if value:
            return str(value)

    try:
        user = sb.auth.get_user().user
        if user and getattr(user, "id", None):
            return str(user.id)
    except Exception:
        pass

    return None


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def clean_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = (
            text.replace("```json", "", 1)
            .replace("```JSON", "", 1)
            .replace("```", "")
            .strip()
        )
    return text


def clamp(value, low, high):
    try:
        x = float(value)
    except Exception:
        x = 0.0
    return max(low, min(high, x))


def normalized_verdict(score: float, blocked: bool, insufficient: bool) -> str:
    if blocked:
        return "BLOCKED"
    if insufficient:
        return "INSUFFICIENT_EVIDENCE"
    if score >= 80:
        return "STRONG_MATCH"
    if score >= 65:
        return "GOOD_MATCH"
    if score >= 50:
        return "CONDITIONAL_MATCH"
    return "WEAK_MATCH"


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase nu este configurat corect: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Intră în cont din pagina principală și revino.")
    st.stop()


# ---------------------------------------------------------------------
# Project selector
# ---------------------------------------------------------------------
projects = rows("projects", {"user_id": user_id}, "updated_at", 100)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_options = {
    f"{p.get('name') or 'Project'} — {str(p.get('id'))[:8]}": p
    for p in projects
}

selected_project_label = st.selectbox("Project", list(project_options.keys()))
project = project_options[selected_project_label]
project_id = str(project["id"])
project_data = as_dict(project.get("data"))

st.write(f"**Project:** {project.get('name') or '—'}")


# ---------------------------------------------------------------------
# Latest Etapa 33 Guard run for selected project
# ---------------------------------------------------------------------
# Etapa 33 validează oportunitățile independent de proiect.
# Preferăm un Guard specific proiectului, dacă există; altfel folosim
# ultimul Guard global finalizat al utilizatorului.
project_guard_runs = rows(
    "opportunity_engine_guard_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    100,
)

latest_guard_run = next(
    (r for r in project_guard_runs if str(r.get("run_status") or "") == "Completed"),
    None,
)
guard_source = "Project-specific Etapa 33"

if not latest_guard_run:
    all_guard_runs = rows(
        "opportunity_engine_guard_runs",
        {"user_id": user_id},
        "created_at",
        500,
    )
    latest_guard_run = next(
        (
            r for r in all_guard_runs
            if str(r.get("run_status") or "") == "Completed"
            and not r.get("project_id")
        ),
        None,
    )
    guard_source = "Global Etapa 33"

if not latest_guard_run:
    st.warning(
        "Nu există niciun Guard Etapa 33 finalizat pentru proiect și niciun Guard global finalizat."
    )
    st.stop()

guard_run_id = str(latest_guard_run["id"])

st.success(
    f"Guard source: {guard_source} — run {guard_run_id[:8]} "
    f"— VALID raportate: {int(latest_guard_run.get('valid_opportunities') or 0)}"
)

# Citim toate check-urile utilizatorului și le restrângem apoi la run-ul
# Etapei 33 selectat. Astfel funcționează atât Guard-ul global (project_id NULL),
# cât și unul specific proiectului.
validity_checks = rows(
    "opportunity_engine_validity_checks",
    {"user_id": user_id},
    "created_at",
    5000,
)

authorized_checks = []
for check in validity_checks:
    metadata = as_dict(check.get("metadata"))
    check_guard_run_id = str(
        metadata.get("guard_run_id")
        or metadata.get("opportunity_engine_guard_run_id")
        or metadata.get("run_id")
        or ""
    )
    if check_guard_run_id != guard_run_id:
        continue
    if str(check.get("validity_status") or "") != "VALID":
        continue
    if check.get("eligible_for_scoring") is not True:
        continue
    authorized_checks.append(check)

# Deduplicate identity inside the latest run.
by_identity = {}
for check in authorized_checks:
    identity = str(check.get("opportunity_identity") or "").strip()
    if identity and identity not in by_identity:
        by_identity[identity] = check
authorized_checks = list(by_identity.values())

if not authorized_checks:
    st.warning(
        "Ultimul Guard Etapa 33 nu conține oportunități VALID autorizate pentru scoring."
    )
    st.stop()


# ---------------------------------------------------------------------
# Opportunity data lookup
# ---------------------------------------------------------------------
opportunity_rows = rows("opportunities", {"user_id": user_id}, "updated_at", 2000)
opportunity_by_identity = {}
for row in opportunity_rows:
    identity = str(row.get("identity") or "").strip()
    if identity and identity not in opportunity_by_identity:
        opportunity_by_identity[identity] = row


# ---------------------------------------------------------------------
# Existing scoring run
# ---------------------------------------------------------------------
scoring_runs = rows(
    "opportunity_scoring_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    100,
)
latest_scoring_run = scoring_runs[0] if scoring_runs else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("VALID opportunities", len(authorized_checks))
m2.metric(
    "Last scored",
    int(latest_scoring_run.get("scored_opportunities") or 0)
    if latest_scoring_run else 0,
)
m3.metric(
    "Strong matches",
    int(latest_scoring_run.get("strong_matches") or 0)
    if latest_scoring_run else 0,
)
m4.metric(
    "Good matches",
    int(latest_scoring_run.get("good_matches") or 0)
    if latest_scoring_run else 0,
)

st.info(
    "Ponderi: Thematic 25 • Eligibility 20 • TRL 15 • Funding 15 • "
    "Geographic 10 • Consortium 10 • Deadline feasibility 5. "
    "Lipsa unei dovezi reduce încrederea; nu este completată prin presupuneri."
)


# ---------------------------------------------------------------------
# AI scoring
# ---------------------------------------------------------------------
SYSTEM = """You are a strict EU grant opportunity scoring engine.

You compare ONE project against ONE already-valid opportunity.

IMPORTANT:
- The opportunity has already passed a validity/deadline guard.
- Use only the supplied project data, opportunity data, and validity check.
- Never invent applicant eligibility, TRL, budget, consortium, geographic eligibility,
  funding rate, call requirements, KPI values, or official rules.
- Distinguish thematic similarity from verified eligibility.
- Missing evidence must be explicitly listed under missing_evidence.
- A high thematic match must NOT automatically imply applicant eligibility.
- BLOCKED is allowed only when supplied evidence positively establishes a critical incompatibility.
- INSUFFICIENT_EVIDENCE is appropriate when essential scoring evidence is too sparse to make a defensible match assessment.
- Score components using exactly these maxima:
  thematic_score 0-25
  eligibility_score 0-20
  trl_score 0-15
  funding_score 0-15
  geographic_score 0-10
  consortium_score 0-10
  deadline_score 0-5
- overall_score must equal the sum of those seven components.
- Return JSON only.

JSON schema:
{
  "thematic_score": 0,
  "eligibility_score": 0,
  "trl_score": 0,
  "funding_score": 0,
  "geographic_score": 0,
  "consortium_score": 0,
  "deadline_score": 0,
  "overall_score": 0,

  "blocked": false,
  "insufficient_evidence": false,
  "confidence": "Low|Medium|High",

  "thematic_fit": "",
  "eligibility_fit": "",
  "trl_fit": "",
  "funding_fit": "",
  "geographic_fit": "",
  "consortium_fit": "",
  "deadline_fit": "",

  "strengths": [],
  "critical_gaps": [],
  "missing_evidence": [],

  "why_it_matches": "",
  "recommended_action": ""
}
"""


def score_one(check: dict, opportunity_row: dict | None) -> dict:
    client = get_openai()

    opportunity_data = as_dict(opportunity_row.get("data")) if opportunity_row else {}

    payload = {
        "current_date": date.today().isoformat(),
        "project": {
            "id": project_id,
            "name": project.get("name"),
            "data": project_data,
        },
        "opportunity": {
            "id": opportunity_row.get("id") if opportunity_row else check.get("opportunity_id"),
            "identity": check.get("opportunity_identity"),
            "title": check.get("opportunity_title"),
            "programme": check.get("programme"),
            "country_or_region": check.get("country_or_region"),
            "deadline_date": check.get("deadline_date"),
            "status": check.get("opportunity_status"),
            "source_url": check.get("source_url"),
            "data": opportunity_data,
        },
        "guard_authorization": {
            "validity_check_id": check.get("id"),
            "guard_run_id": guard_run_id,
            "validity_status": check.get("validity_status"),
            "eligible_for_scoring": check.get("eligible_for_scoring"),
            "confidence": check.get("confidence"),
            "verification_reason": check.get("verification_reason"),
        },
    }

    response = client.chat.completions.create(
        model=model_name(),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
    )

    result = json.loads(clean_json(response.choices[0].message.content))

    if not isinstance(result, dict):
        raise ValueError("Răspuns AI invalid.")

    # Enforce component bounds and recompute total ourselves.
    result["thematic_score"] = clamp(result.get("thematic_score"), 0, 25)
    result["eligibility_score"] = clamp(result.get("eligibility_score"), 0, 20)
    result["trl_score"] = clamp(result.get("trl_score"), 0, 15)
    result["funding_score"] = clamp(result.get("funding_score"), 0, 15)
    result["geographic_score"] = clamp(result.get("geographic_score"), 0, 10)
    result["consortium_score"] = clamp(result.get("consortium_score"), 0, 10)
    result["deadline_score"] = clamp(result.get("deadline_score"), 0, 5)

    total = (
        result["thematic_score"]
        + result["eligibility_score"]
        + result["trl_score"]
        + result["funding_score"]
        + result["geographic_score"]
        + result["consortium_score"]
        + result["deadline_score"]
    )
    result["overall_score"] = round(total, 2)

    confidence = str(result.get("confidence") or "Low")
    if confidence not in ("Low", "Medium", "High"):
        confidence = "Low"
    result["confidence"] = confidence

    result["verdict"] = normalized_verdict(
        result["overall_score"],
        bool(result.get("blocked")),
        bool(result.get("insufficient_evidence")),
    )

    for key in ("strengths", "critical_gaps", "missing_evidence"):
        if not isinstance(result.get(key), list):
            result[key] = []

    return result


# ---------------------------------------------------------------------
# Run scoring
# ---------------------------------------------------------------------
if st.button(
    "🎯 Rulează Opportunity Scoring & Matching",
    type="primary",
    use_container_width=True,
):
    run_id = None

    with st.spinner(
        f"Evaluez {len(authorized_checks)} oportunități VALID pentru proiect..."
    ):
        try:
            run_insert = (
                supabase.table("opportunity_scoring_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "source_guard_run_id": guard_run_id,
                    "total_valid_opportunities": len(authorized_checks),
                    "run_status": "Running",
                    "started_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .execute()
            ).data or []

            if not run_insert:
                raise RuntimeError("Nu am putut crea scoring run.")

            run_id = str(run_insert[0]["id"])

            counters = {
                "scored_opportunities": 0,
                "strong_matches": 0,
                "good_matches": 0,
                "conditional_matches": 0,
                "weak_matches": 0,
                "blocked_matches": 0,
                "insufficient_evidence": 0,
            }

            progress = st.progress(0)
            status_box = st.empty()

            for index, check in enumerate(authorized_checks, start=1):
                identity = str(check.get("opportunity_identity") or "")
                opportunity_row = opportunity_by_identity.get(identity)

                status_box.write(
                    f"Scoring {index}/{len(authorized_checks)} — "
                    f"{check.get('opportunity_title') or identity}"
                )

                result = score_one(check, opportunity_row)
                verdict = result["verdict"]

                if verdict == "STRONG_MATCH":
                    counters["strong_matches"] += 1
                elif verdict == "GOOD_MATCH":
                    counters["good_matches"] += 1
                elif verdict == "CONDITIONAL_MATCH":
                    counters["conditional_matches"] += 1
                elif verdict == "WEAK_MATCH":
                    counters["weak_matches"] += 1
                elif verdict == "BLOCKED":
                    counters["blocked_matches"] += 1
                elif verdict == "INSUFFICIENT_EVIDENCE":
                    counters["insufficient_evidence"] += 1

                counters["scored_opportunities"] += 1

                supabase.table("opportunity_scoring_results").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "scoring_run_id": run_id,
                    "validity_check_id": check["id"],
                    "opportunity_id": (
                        opportunity_row.get("id")
                        if opportunity_row
                        else check.get("opportunity_id")
                    ),
                    "opportunity_identity": identity,
                    "opportunity_title": check.get("opportunity_title"),
                    "programme": check.get("programme"),
                    "country_or_region": check.get("country_or_region"),
                    "deadline_date": check.get("deadline_date"),

                    "thematic_score": result["thematic_score"],
                    "eligibility_score": result["eligibility_score"],
                    "trl_score": result["trl_score"],
                    "funding_score": result["funding_score"],
                    "geographic_score": result["geographic_score"],
                    "consortium_score": result["consortium_score"],
                    "deadline_score": result["deadline_score"],
                    "overall_score": result["overall_score"],

                    "verdict": verdict,
                    "confidence": result["confidence"],

                    "thematic_fit": str(result.get("thematic_fit") or ""),
                    "eligibility_fit": str(result.get("eligibility_fit") or ""),
                    "trl_fit": str(result.get("trl_fit") or ""),
                    "funding_fit": str(result.get("funding_fit") or ""),
                    "geographic_fit": str(result.get("geographic_fit") or ""),
                    "consortium_fit": str(result.get("consortium_fit") or ""),
                    "deadline_fit": str(result.get("deadline_fit") or ""),

                    "strengths": result["strengths"],
                    "critical_gaps": result["critical_gaps"],
                    "missing_evidence": result["missing_evidence"],

                    "why_it_matches": str(result.get("why_it_matches") or ""),
                    "recommended_action": str(result.get("recommended_action") or ""),
                    "ai_result": result,
                    "updated_at": now_iso(),
                }).execute()

                progress.progress(index / len(authorized_checks))

            summary = {
                "stage": 35,
                "guard_run_id": guard_run_id,
                "weights": {
                    "thematic": 25,
                    "eligibility": 20,
                    "trl": 15,
                    "funding": 15,
                    "geographic": 10,
                    "consortium": 10,
                    "deadline": 5,
                },
            }

            supabase.table("opportunity_scoring_runs").update({
                **counters,
                "run_status": "Completed",
                "summary": summary,
                "completed_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", run_id).eq("user_id", user_id).execute()

            st.success(
                f"Scoring finalizat: {counters['scored_opportunities']} oportunități evaluate."
            )
            st.rerun()

        except Exception as exc:
            error_text = str(exc)

            if run_id:
                try:
                    supabase.table("opportunity_scoring_runs").update({
                        "run_status": "Failed",
                        "summary": {
                            "stage": 35,
                            "error": error_text[:4000],
                            "guard_run_id": guard_run_id,
                        },
                        "completed_at": now_iso(),
                        "updated_at": now_iso(),
                    }).eq("id", run_id).eq("user_id", user_id).execute()
                except Exception:
                    pass

            st.error(f"Etapa 35 nu a putut finaliza scoring-ul: {error_text}")


# ---------------------------------------------------------------------
# Latest scoring results
# ---------------------------------------------------------------------
scoring_runs = rows(
    "opportunity_scoring_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    100,
)

latest_completed_run = next(
    (r for r in scoring_runs if str(r.get("run_status") or "") == "Completed"),
    None,
)

st.divider()
st.subheader("Opportunity Ranking")

if not latest_completed_run:
    st.caption("Nu există încă un scoring Etapa 35 finalizat.")
else:
    latest_run_id = str(latest_completed_run["id"])

    results = rows(
        "opportunity_scoring_results",
        {
            "user_id": user_id,
            "project_id": project_id,
            "scoring_run_id": latest_run_id,
        },
        "overall_score",
        1000,
    )

    a, b, c, d, e = st.columns(5)
    a.metric("Scored", int(latest_completed_run.get("scored_opportunities") or 0))
    b.metric("Strong", int(latest_completed_run.get("strong_matches") or 0))
    c.metric("Good", int(latest_completed_run.get("good_matches") or 0))
    d.metric(
        "Conditional",
        int(latest_completed_run.get("conditional_matches") or 0),
    )
    e.metric(
        "Weak / Blocked",
        int(latest_completed_run.get("weak_matches") or 0)
        + int(latest_completed_run.get("blocked_matches") or 0),
    )

    if results:
        st.dataframe(
            [
                {
                    "Rank": i,
                    "Opportunity": r.get("opportunity_title"),
                    "Identity": r.get("opportunity_identity"),
                    "Score": float(r.get("overall_score") or 0),
                    "Verdict": r.get("verdict"),
                    "Confidence": r.get("confidence"),
                    "Deadline": r.get("deadline_date"),
                    "Recommended action": r.get("recommended_action"),
                }
                for i, r in enumerate(results, start=1)
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Top opportunities")

        for rank, r in enumerate(results[:10], start=1):
            label = (
                f"#{rank} — {r.get('opportunity_title') or r.get('opportunity_identity')} "
                f"— {float(r.get('overall_score') or 0):.1f}/100 "
                f"[{r.get('verdict')}]"
            )

            with st.expander(label, expanded=(rank <= 3)):
                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Thematic", f"{float(r.get('thematic_score') or 0):.1f}/25")
                s2.metric("Eligibility", f"{float(r.get('eligibility_score') or 0):.1f}/20")
                s3.metric("TRL", f"{float(r.get('trl_score') or 0):.1f}/15")
                s4.metric("Funding", f"{float(r.get('funding_score') or 0):.1f}/15")

                s5, s6, s7 = st.columns(3)
                s5.metric("Geographic", f"{float(r.get('geographic_score') or 0):.1f}/10")
                s6.metric("Consortium", f"{float(r.get('consortium_score') or 0):.1f}/10")
                s7.metric("Deadline", f"{float(r.get('deadline_score') or 0):.1f}/5")

                st.write("**Why it matches**")
                st.write(r.get("why_it_matches") or "—")

                strengths = r.get("strengths") or []
                if strengths:
                    st.write("**Strengths**")
                    for item in strengths:
                        st.write(f"• {item}")

                gaps = r.get("critical_gaps") or []
                if gaps:
                    st.write("**Critical gaps**")
                    for item in gaps:
                        st.write(f"• {item}")

                missing = r.get("missing_evidence") or []
                if missing:
                    st.write("**Missing evidence**")
                    for item in missing:
                        st.write(f"• {item}")

                st.write("**Recommended action**")
                st.write(r.get("recommended_action") or "—")

    else:
        st.caption("Run-ul există, dar nu are rezultate salvate.")


with st.expander("Istoric Etapa 35"):
    if scoring_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "guard_run": str(r.get("source_guard_run_id") or "")[:8],
                    "valid": r.get("total_valid_opportunities"),
                    "scored": r.get("scored_opportunities"),
                    "strong": r.get("strong_matches"),
                    "good": r.get("good_matches"),
                    "conditional": r.get("conditional_matches"),
                    "weak": r.get("weak_matches"),
                    "blocked": r.get("blocked_matches"),
                    "insufficient": r.get("insufficient_evidence"),
                    "status": r.get("run_status"),
                }
                for r in scoring_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 35.")

st.caption(
    "Etapa 35 nu validează oficial eligibilitatea. Ea produce un ranking de potrivire "
    "numai pentru oportunitățile deja autorizate de Etapa 33. "
    "Oportunitățile selectate trebuie să treacă ulterior prin Opportunity Fit / Evidence / Official Verification."
)
