import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Re-Review Orchestrator",
    page_icon="🔄",
    layout="wide",
)

st.title("🔄 Etapa 28 — AI Re-Review Orchestrator")
st.caption(
    "Preia modificările validate în Etapa 27 și construiește un nou ciclu de evaluare "
    "Reviewer → Compliance → Submission Readiness, fără a modifica automat conținutul proiectului."
)


# ---------------------------------------------------------------------
# Helpers
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


def norm(value: Any) -> str:
    return str(value or "").strip().lower()


def compact_json(obj: Any, limit: int = 32000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def first_nonempty(row: dict, keys: list[str], default=""):
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


# ---------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------
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
# Projects
# ---------------------------------------------------------------------
try:
    projects = (
        supabase.table("projects")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca proiectele: {exc}")
    st.stop()

if not projects:
    st.warning("Nu există proiecte disponibile.")
    st.stop()

project_labels = {
    f"{p.get('name') or p.get('title') or 'Project'} — {str(p.get('id'))[:8]}": p
    for p in projects
}

selected_project_label = st.selectbox("Project", list(project_labels.keys()))
project = project_labels[selected_project_label]
project_id = str(project["id"])


# ---------------------------------------------------------------------
# Stage 27 validated items
# ---------------------------------------------------------------------
try:
    validated_items = (
        supabase.table("post_execution_validation_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("validation_status", "Validated")
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca rezultatele validate din Etapa 27: {exc}")
    st.stop()

if not validated_items:
    st.warning(
        "Nu există rezultate Validated în Etapa 27 pentru acest proiect. "
        "Finalizează mai întâi AI Post-Execution Validator."
    )
    st.stop()

opportunity_identity = str(validated_items[0].get("opportunity_identity") or "")
validated_items = [
    row for row in validated_items
    if str(row.get("opportunity_identity") or "") == opportunity_identity
]

# Deduplicate latest per controlled execution.
dedup = {}
for row in validated_items:
    key = str(row.get("controlled_execution_id") or row.get("id") or "")
    if key and key not in dedup:
        dedup[key] = row
validated_items = list(dedup.values())

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)


# ---------------------------------------------------------------------
# Determine latest Stage 27 run and execution plan
# ---------------------------------------------------------------------
post_validation_run_id = None
execution_plan_id = None

if validated_items:
    post_validation_run_id = validated_items[0].get("validation_run_id")
    execution_plan_id = validated_items[0].get("execution_plan_id")


# ---------------------------------------------------------------------
# Existing Stage 28 runs
# ---------------------------------------------------------------------
try:
    existing_runs = (
        supabase.table("rereview_orchestration_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    existing_runs = []

latest_run = existing_runs[0] if existing_runs else None


# ---------------------------------------------------------------------
# Load prior scores where possible
# ---------------------------------------------------------------------
def load_latest_score(table_name: str, candidate_fields: list[str]) -> float | None:
    try:
        rows = (
            supabase.table(table_name)
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        ).data or []

        for row in rows:
            if opportunity_identity:
                row_opp = str(row.get("opportunity_identity") or row.get("call_identity") or "")
                if row_opp and row_opp != opportunity_identity:
                    continue

            for field in candidate_fields:
                score = safe_score(row.get(field))
                if score is not None:
                    return score
    except Exception:
        pass

    return None


reviewer_before = None
compliance_before = None
readiness_before = None

for table_name, fields, target in [
    ("proposal_reviews", ["score", "overall_score", "review_score", "total_score"], "reviewer"),
    ("proposal_review_runs", ["score", "overall_score", "review_score", "total_score"], "reviewer"),
    ("compliance_checks", ["score", "overall_score", "compliance_score"], "compliance"),
    ("compliance_runs", ["score", "overall_score", "compliance_score"], "compliance"),
    ("submission_readiness_runs", ["score", "overall_score", "readiness_score", "total_score"], "readiness"),
]:
    value = load_latest_score(table_name, fields)
    if value is not None:
        if target == "reviewer" and reviewer_before is None:
            reviewer_before = value
        elif target == "compliance" and compliance_before is None:
            compliance_before = value
        elif target == "readiness" and readiness_before is None:
            readiness_before = value


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Validated Etapa 27", len(validated_items))
c2.metric("Reviewer", str(latest_run.get("reviewer_status") if latest_run else "Pending"))
c3.metric("Compliance", str(latest_run.get("compliance_status") if latest_run else "Pending"))
c4.metric("Readiness", str(latest_run.get("readiness_status") if latest_run else "Pending"))

st.info(
    "Etapa 28 nu modifică propunerea. Ea pornește un nou ciclu de evaluare pe baza "
    "conținutului validat după Etapa 27 și salvează rezultatele separat."
)


# ---------------------------------------------------------------------
# Build context for re-review
# ---------------------------------------------------------------------
def build_validated_context():
    return [
        {
            "post_validation_item_id": str(row.get("id") or ""),
            "controlled_execution_id": str(row.get("controlled_execution_id") or ""),
            "resolution_task_id": str(row.get("resolution_task_id") or ""),
            "category": str(row.get("category") or "Other"),
            "target_section": str(row.get("target_section") or ""),
            "validated_content": str(row.get("applied_content") or ""),
            "validation_reason": str(row.get("validation_reason") or ""),
            "confidence": str(row.get("confidence") or ""),
        }
        for row in validated_items
    ]


def ai_rereview(validated_context):
    project_context = {
        "name": project.get("name") or project.get("title") or "",
        "description": first_nonempty(
            project,
            ["description", "summary", "project_description", "idea"],
            "",
        ),
    }

    prompt = f"""
You are orchestrating a controlled re-review of an EU grant proposal after validated corrections.

You receive:
1. Project context.
2. Validated post-execution changes from Stage 27.
3. Previous scores when available.

Your job is to simulate a fresh evaluation through three modules:
- Reviewer
- Compliance
- Submission Readiness

STRICT RULES:
- Use only the supplied project context and validated content.
- Never invent partners, budgets, eligibility facts, TRL, KPIs, legal status, official confirmations or external evidence.
- Do not declare eligibility unless explicitly supported.
- Do not claim official compliance verification.
- Recalculate only what can reasonably change because of the validated correction.
- If evidence is still missing, identify it explicitly.
- Scores must be 0-100.
- Return JSON only.

Return exactly:
{{
  "reviewer": {{
    "status": "Completed|Failed",
    "score_before": null,
    "score_after": 0,
    "summary": "",
    "remaining_issues": []
  }},
  "compliance": {{
    "status": "Completed|Failed",
    "score_before": null,
    "score_after": 0,
    "summary": "",
    "remaining_issues": []
  }},
  "readiness": {{
    "status": "Completed|Failed",
    "score_before": null,
    "score_after": 0,
    "summary": "",
    "remaining_issues": [],
    "submission_ready": false
  }},
  "overall_summary": ""
}}

PREVIOUS SCORES:
{compact_json({
    "reviewer": reviewer_before,
    "compliance": compliance_before,
    "readiness": readiness_before,
})}

PROJECT:
{compact_json(project_context)}

VALIDATED CHANGES:
{compact_json(validated_context)}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Re-review conservatively. "
            "Never invent grant facts or official verification."
        ),
        input=prompt,
    )

    result = json.loads(clean_json(response.output_text))
    if not isinstance(result, dict):
        raise ValueError("Răspunsul AI nu este un obiect JSON.")

    return result


# ---------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------
if st.button(
    "🔄 Rulează Re-Review: Reviewer → Compliance → Readiness",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Rulăm noul ciclu de evaluare..."):
        try:
            validated_context = build_validated_context()

            run_insert = (
                supabase.table("rereview_orchestration_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "post_validation_run_id": post_validation_run_id,
                    "execution_plan_id": execution_plan_id,
                    "validated_changes": len(validated_items),
                    "reviewer_status": "Running",
                    "compliance_status": "Pending",
                    "readiness_status": "Pending",
                    "overall_status": "Running",
                    "summary": {
                        "stage": 28,
                        "started_by_user": True,
                    },
                    "started_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .execute()
            )

            run_data = run_insert.data or []
            if not run_data:
                raise RuntimeError("Nu am putut crea orchestration run.")

            orchestration_run_id = str(run_data[0]["id"])

            result = ai_rereview(validated_context)

            module_map = [
                ("Reviewer", "reviewer", reviewer_before),
                ("Compliance", "compliance", compliance_before),
                ("Readiness", "readiness", readiness_before),
            ]

            module_results = {}

            for module_name, result_key, score_before in module_map:
                module_result = result.get(result_key, {}) or {}
                module_status = str(module_result.get("status") or "Completed")
                if module_status not in ("Completed", "Failed"):
                    module_status = "Completed"

                score_after = safe_score(module_result.get("score_after"))

                for validated_row in validated_items:
                    supabase.table("rereview_orchestration_items").insert({
                        "user_id": user_id,
                        "project_id": project_id,
                        "opportunity_identity": opportunity_identity,
                        "orchestration_run_id": orchestration_run_id,
                        "post_validation_item_id": validated_row.get("id"),
                        "controlled_execution_id": validated_row.get("controlled_execution_id"),
                        "resolution_task_id": validated_row.get("resolution_task_id"),
                        "category": validated_row.get("category"),
                        "target_section": validated_row.get("target_section"),
                        "validated_content": str(validated_row.get("applied_content") or ""),
                        "module_name": module_name,
                        "module_status": module_status,
                        "score_before": score_before,
                        "score_after": score_after,
                        "result": module_result,
                        "error_message": None if module_status == "Completed" else str(module_result.get("summary") or ""),
                        "updated_at": now_iso(),
                    }).execute()

                module_results[result_key] = module_result

            reviewer_status = str(module_results.get("reviewer", {}).get("status") or "Completed")
            compliance_status = str(module_results.get("compliance", {}).get("status") or "Completed")
            readiness_status = str(module_results.get("readiness", {}).get("status") or "Completed")

            if any(
                status == "Failed"
                for status in (reviewer_status, compliance_status, readiness_status)
            ):
                overall_status = "Failed"
            else:
                readiness_issues = module_results.get("readiness", {}).get("remaining_issues") or []
                compliance_issues = module_results.get("compliance", {}).get("remaining_issues") or []
                reviewer_issues = module_results.get("reviewer", {}).get("remaining_issues") or []

                overall_status = (
                    "Completed"
                    if not (readiness_issues or compliance_issues or reviewer_issues)
                    else "Needs attention"
                )

            (
                supabase.table("rereview_orchestration_runs")
                .update({
                    "reviewer_status": reviewer_status,
                    "compliance_status": compliance_status,
                    "readiness_status": readiness_status,
                    "overall_status": overall_status,
                    "reviewer_result": module_results.get("reviewer", {}),
                    "compliance_result": module_results.get("compliance", {}),
                    "readiness_result": module_results.get("readiness", {}),
                    "summary": {
                        "stage": 28,
                        "text": result.get("overall_summary", ""),
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .eq("id", orchestration_run_id)
                .eq("user_id", user_id)
                .execute()
            )

            st.success(
                "Re-Review finalizat. Rezultatele Reviewer, Compliance și Readiness au fost salvate."
            )
            st.rerun()

        except Exception as exc:
            st.error(f"Re-Review nu a putut fi executat: {exc}")


# ---------------------------------------------------------------------
# Reload latest run
# ---------------------------------------------------------------------
try:
    existing_runs = (
        supabase.table("rereview_orchestration_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    existing_runs = []

latest_run = existing_runs[0] if existing_runs else None


# ---------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------
st.subheader("Rezultate Re-Review")

if not latest_run:
    st.caption("Nu există încă un ciclu Re-Review pentru această oportunitate.")
else:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Overall", str(latest_run.get("overall_status") or "Pending"))
    r2.metric("Reviewer", str(latest_run.get("reviewer_status") or "Pending"))
    r3.metric("Compliance", str(latest_run.get("compliance_status") or "Pending"))
    r4.metric("Readiness", str(latest_run.get("readiness_status") or "Pending"))

    results = [
        ("🧠 Reviewer", latest_run.get("reviewer_result") or {}),
        ("🛡️ Compliance", latest_run.get("compliance_result") or {}),
        ("🚦 Submission Readiness", latest_run.get("readiness_result") or {}),
    ]

    for title, result in results:
        with st.expander(title, expanded=True):
            score_before = result.get("score_before")
            score_after = result.get("score_after")

            s1, s2 = st.columns(2)
            s1.metric(
                "Score before",
                "—" if score_before is None else f"{score_before}/100",
            )
            s2.metric(
                "Score after",
                "—" if score_after is None else f"{score_after}/100",
            )

            if result.get("summary"):
                st.write(result.get("summary"))

            issues = result.get("remaining_issues") or []
            if issues:
                st.warning("Au rămas probleme de rezolvat:")
                for issue in issues:
                    st.write(f"- {issue}")
            else:
                st.success("Nu au fost raportate probleme suplimentare de acest modul.")

            if "submission_ready" in result:
                st.write(
                    f"**Submission ready:** "
                    f"{'YES' if bool(result.get('submission_ready')) else 'NO'}"
                )

    if latest_run.get("summary"):
        summary_obj = latest_run.get("summary") or {}
        if isinstance(summary_obj, dict) and summary_obj.get("text"):
            st.info(str(summary_obj.get("text")))


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()

with st.expander("Istoric Re-Review Orchestration"):
    if existing_runs:
        display_cols = [
            "id",
            "validated_changes",
            "reviewer_status",
            "compliance_status",
            "readiness_status",
            "overall_status",
            "created_at",
            "completed_at",
        ]
        st.dataframe(
            [
                {key: row.get(key) for key in display_cols}
                for row in existing_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există încă rulări Etapa 28.")

st.caption(
    "Etapa 28 re-evaluează conținutul validat după execuție. "
    "Nu modifică propunerea și nu declară automat eligibilitatea. "
    "Rezultatele servesc ca bază pentru următorul ciclu de remediere sau pentru pregătirea finală."
)
