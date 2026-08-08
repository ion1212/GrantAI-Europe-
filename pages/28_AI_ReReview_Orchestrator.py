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
    "Reevaluează proiectul după corecțiile validate în Etapa 27 folosind contextul real "
    "din Proposal Versions, Grant Reviews, Compliance și Submission Readiness."
)


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


def safe_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def compact_json(obj: Any, limit: int = 42000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def json_score(obj: Any) -> float | None:
    if not isinstance(obj, dict):
        return None

    for key in (
        "score",
        "overall_score",
        "compliance_score",
        "total_score",
        "readiness_score",
    ):
        value = safe_score(obj.get(key))
        if value is not None:
            return value

    for key in ("summary", "result", "evaluation"):
        nested = obj.get(key)
        if isinstance(nested, dict):
            found = json_score(nested)
            if found is not None:
                return found

    return None


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
# Project
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

selected_label = st.selectbox("Project", list(project_labels.keys()))
project = project_labels[selected_label]
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
    st.error(f"Nu am putut încărca Etapa 27: {exc}")
    st.stop()

if not validated_items:
    st.warning("Nu există rezultate Validated în Etapa 27.")
    st.stop()

opportunity_identity = str(validated_items[0].get("opportunity_identity") or "")
validated_items = [
    row for row in validated_items
    if str(row.get("opportunity_identity") or "") == opportunity_identity
]

dedup = {}
for row in validated_items:
    key = str(row.get("controlled_execution_id") or row.get("id") or "")
    if key and key not in dedup:
        dedup[key] = row
validated_items = list(dedup.values())

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

post_validation_run_id = validated_items[0].get("validation_run_id")
execution_plan_id = validated_items[0].get("execution_plan_id")


# ---------------------------------------------------------------------
# Real proposal content from proposal_versions
# Keep latest version per section.
# ---------------------------------------------------------------------
try:
    proposal_rows = (
        supabase.table("proposal_versions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    proposal_rows = []

latest_section_versions = {}
for row in proposal_rows:
    section = str(row.get("section") or row.get("title") or "General")
    if section not in latest_section_versions:
        latest_section_versions[section] = row

proposal_context = [
    {
        "section": section,
        "title": row.get("title"),
        "content": row.get("content"),
        "ai_score": row.get("ai_score"),
        "status": row.get("status"),
        "version_id": row.get("id"),
        "created_at": row.get("created_at"),
    }
    for section, row in latest_section_versions.items()
]


# ---------------------------------------------------------------------
# Real previous Reviewer result
# ---------------------------------------------------------------------
try:
    reviewer_rows = (
        supabase.table("grant_reviews")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    ).data or []
except Exception:
    reviewer_rows = []

previous_reviewer = reviewer_rows[0] if reviewer_rows else {}
reviewer_before = safe_score(previous_reviewer.get("overall_score"))


# ---------------------------------------------------------------------
# Real previous Compliance result
# ---------------------------------------------------------------------
try:
    compliance_rows = (
        supabase.table("grant_compliance_checks")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    ).data or []
except Exception:
    compliance_rows = []

previous_compliance = compliance_rows[0] if compliance_rows else {}
compliance_before = json_score(previous_compliance.get("result") or {})


# ---------------------------------------------------------------------
# Real previous Submission Readiness result
# ---------------------------------------------------------------------
try:
    readiness_rows = (
        supabase.table("submission_readiness_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    ).data or []
except Exception:
    readiness_rows = []

previous_readiness = readiness_rows[0] if readiness_rows else {}
readiness_before = safe_score(previous_readiness.get("readiness_score"))

try:
    readiness_items = (
        supabase.table("submission_readiness_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    ).data or []
except Exception:
    readiness_items = []


# ---------------------------------------------------------------------
# Existing Stage 28 run
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
# Metrics
# ---------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Validated Etapa 27", len(validated_items))
c2.metric(
    "Reviewer before",
    "—" if reviewer_before is None else f"{reviewer_before:g}/100",
)
c3.metric(
    "Compliance before",
    "—" if compliance_before is None else f"{compliance_before:g}/100",
)
c4.metric(
    "Readiness before",
    "—" if readiness_before is None else f"{readiness_before:g}/100",
)

if not proposal_context:
    st.warning(
        "Nu am găsit conținut în proposal_versions. Re-review-ul poate fi rulat, "
        "dar contextul propunerii va fi incomplet."
    )
else:
    st.success(
        f"Au fost încărcate {len(proposal_context)} secțiuni reale din proposal_versions."
    )

st.info(
    "Etapa 28 folosește acum evaluările reale anterioare și ultimele versiuni ale secțiunilor "
    "propunerii. Nu modifică automat documentul."
)


# ---------------------------------------------------------------------
# Build real context
# ---------------------------------------------------------------------
def validated_context():
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


def prior_context():
    return {
        "reviewer": {
            "overall_score": reviewer_before,
            "review_type": previous_reviewer.get("review_type"),
            "result": previous_reviewer.get("result") or {},
            "created_at": previous_reviewer.get("created_at"),
        },
        "compliance": {
            "score_if_available": compliance_before,
            "overall_status": previous_compliance.get("overall_status"),
            "eligibility_status": previous_compliance.get("eligibility_status"),
            "proposal_status": previous_compliance.get("proposal_status"),
            "budget_status": previous_compliance.get("budget_status"),
            "consortium_status": previous_compliance.get("consortium_status"),
            "trl_status": previous_compliance.get("trl_status"),
            "deadline_status": previous_compliance.get("deadline_status"),
            "mandatory_requirements": previous_compliance.get("mandatory_requirements") or [],
            "missing_information": previous_compliance.get("missing_information") or [],
            "critical_issues": previous_compliance.get("critical_issues") or [],
            "warnings": previous_compliance.get("warnings") or [],
            "recommendations": previous_compliance.get("recommendations") or [],
            "ai_summary": previous_compliance.get("ai_summary") or "",
            "result": previous_compliance.get("result") or {},
        },
        "readiness": {
            "readiness_score": readiness_before,
            "run": previous_readiness,
            "items": readiness_items,
        },
    }


def ai_rereview():
    prompt = f"""
You are performing a controlled re-review of an EU grant proposal after a validated correction.

You have:
1. The actual latest proposal sections from proposal_versions.
2. Actual previous Reviewer data from grant_reviews.
3. Actual previous Compliance data from grant_compliance_checks.
4. Actual previous Submission Readiness data and items.
5. The validated correction from Stage 27.

STRICT RULES:
- Use only supplied data.
- Never invent budgets, partners, eligibility, TRL, KPIs, legal status, deadlines, official confirmation, or evidence.
- A missing fact remains missing.
- Do not treat "User confirmed" as "Officially verified".
- Reviewer score_after, Compliance score_after and Readiness score_after must be 0-100.
- score_before must reflect the supplied real previous score when present; otherwise null.
- Reviewer: evaluate proposal quality based on actual proposal sections plus validated correction.
- Compliance: evaluate compliance based on actual prior compliance fields plus actual proposal content and correction.
- Readiness: evaluate readiness based on actual readiness run/items plus actual proposal content and correction.
- Do not penalize the project for a "missing project description" if a substantive project description is present in the supplied proposal sections.
- Do not claim an issue is fixed unless the supplied content actually fixes it.
- Return JSON only.

Return exactly:
{{
  "reviewer": {{
    "status": "Completed|Failed",
    "score_before": null,
    "score_after": 0,
    "summary": "",
    "resolved_issues": [],
    "remaining_issues": []
  }},
  "compliance": {{
    "status": "Completed|Failed",
    "score_before": null,
    "score_after": 0,
    "summary": "",
    "resolved_issues": [],
    "remaining_issues": []
  }},
  "readiness": {{
    "status": "Completed|Failed",
    "score_before": null,
    "score_after": 0,
    "summary": "",
    "resolved_issues": [],
    "remaining_issues": [],
    "submission_ready": false
  }},
  "overall_summary": ""
}}

PROJECT:
{compact_json(project)}

ACTUAL PROPOSAL SECTIONS:
{compact_json(proposal_context)}

ACTUAL PREVIOUS EVALUATIONS:
{compact_json(prior_context())}

VALIDATED STAGE 27 CHANGES:
{compact_json(validated_context())}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Use actual stored proposal/evaluation context. "
            "Never invent grant facts."
        ),
        input=prompt,
    )

    result = json.loads(clean_json(response.output_text))
    if not isinstance(result, dict):
        raise ValueError("Răspuns AI invalid.")
    return result


# ---------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------
if st.button(
    "🔄 Rulează Re-Review complet pe datele reale",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Reevaluăm Reviewer → Compliance → Readiness..."):
        try:
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
                        "context_source": "real_database_context",
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

            result = ai_rereview()

            modules = [
                ("Reviewer", "reviewer", reviewer_before),
                ("Compliance", "compliance", compliance_before),
                ("Readiness", "readiness", readiness_before),
            ]

            saved_results = {}

            for module_name, key, real_before in modules:
                module_result = result.get(key, {}) or {}

                module_status = str(module_result.get("status") or "Completed")
                if module_status not in ("Completed", "Failed"):
                    module_status = "Completed"

                score_after = safe_score(module_result.get("score_after"))
                score_before = (
                    real_before
                    if real_before is not None
                    else safe_score(module_result.get("score_before"))
                )

                module_result["score_before"] = score_before
                module_result["score_after"] = score_after

                for validated_row in validated_items:
                    (
                        supabase.table("rereview_orchestration_items")
                        .insert({
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
                            "error_message": (
                                None
                                if module_status == "Completed"
                                else str(module_result.get("summary") or "")
                            ),
                            "updated_at": now_iso(),
                        })
                        .execute()
                    )

                saved_results[key] = module_result

            reviewer_status = str(saved_results.get("reviewer", {}).get("status") or "Completed")
            compliance_status = str(saved_results.get("compliance", {}).get("status") or "Completed")
            readiness_status = str(saved_results.get("readiness", {}).get("status") or "Completed")

            if "Failed" in (reviewer_status, compliance_status, readiness_status):
                overall_status = "Failed"
            else:
                remaining = (
                    (saved_results.get("reviewer", {}).get("remaining_issues") or [])
                    + (saved_results.get("compliance", {}).get("remaining_issues") or [])
                    + (saved_results.get("readiness", {}).get("remaining_issues") or [])
                )
                overall_status = "Needs attention" if remaining else "Completed"

            (
                supabase.table("rereview_orchestration_runs")
                .update({
                    "reviewer_status": reviewer_status,
                    "compliance_status": compliance_status,
                    "readiness_status": readiness_status,
                    "overall_status": overall_status,
                    "reviewer_result": saved_results.get("reviewer", {}),
                    "compliance_result": saved_results.get("compliance", {}),
                    "readiness_result": saved_results.get("readiness", {}),
                    "summary": {
                        "stage": 28,
                        "text": result.get("overall_summary", ""),
                        "context_source": "proposal_versions + grant_reviews + grant_compliance_checks + submission_readiness",
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .eq("id", orchestration_run_id)
                .eq("user_id", user_id)
                .execute()
            )

            st.success("Re-Review complet finalizat pe datele reale.")
            st.rerun()

        except Exception as exc:
            st.error(f"Re-Review nu a putut fi executat: {exc}")


# ---------------------------------------------------------------------
# Reload / display
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

st.subheader("Rezultate Re-Review")

if not latest_run:
    st.caption("Nu există încă un Re-Review.")
else:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Overall", str(latest_run.get("overall_status") or "Pending"))
    r2.metric("Reviewer", str(latest_run.get("reviewer_status") or "Pending"))
    r3.metric("Compliance", str(latest_run.get("compliance_status") or "Pending"))
    r4.metric("Readiness", str(latest_run.get("readiness_status") or "Pending"))

    for title, result in [
        ("🧠 Reviewer", latest_run.get("reviewer_result") or {}),
        ("🛡️ Compliance", latest_run.get("compliance_result") or {}),
        ("🚦 Submission Readiness", latest_run.get("readiness_result") or {}),
    ]:
        with st.expander(title, expanded=True):
            s1, s2 = st.columns(2)

            before = result.get("score_before")
            after = result.get("score_after")

            s1.metric(
                "Score before",
                "—" if before is None else f"{before}/100",
            )
            s2.metric(
                "Score after",
                "—" if after is None else f"{after}/100",
            )

            if result.get("summary"):
                st.write(result.get("summary"))

            resolved = result.get("resolved_issues") or []
            if resolved:
                st.success("Probleme rezolvate:")
                for issue in resolved:
                    st.write(f"- {issue}")

            remaining = result.get("remaining_issues") or []
            if remaining:
                st.warning("Au rămas probleme de rezolvat:")
                for issue in remaining:
                    st.write(f"- {issue}")
            else:
                st.success("Nu au fost raportate probleme suplimentare.")

            if "submission_ready" in result:
                st.write(
                    f"**Submission ready:** "
                    f"{'YES' if bool(result.get('submission_ready')) else 'NO'}"
                )

    summary = latest_run.get("summary") or {}
    if isinstance(summary, dict) and summary.get("text"):
        st.info(str(summary.get("text")))


st.divider()

with st.expander("Context real folosit de Etapa 28"):
    st.write(f"**Proposal sections:** {len(proposal_context)}")
    st.write(
        f"**Reviewer score anterior:** "
        f"{'—' if reviewer_before is None else reviewer_before}"
    )
    st.write(
        f"**Compliance score anterior:** "
        f"{'—' if compliance_before is None else compliance_before}"
    )
    st.write(
        f"**Readiness score anterior:** "
        f"{'—' if readiness_before is None else readiness_before}"
    )

    if proposal_context:
        st.dataframe(
            [
                {
                    "section": row.get("section"),
                    "title": row.get("title"),
                    "ai_score": row.get("ai_score"),
                    "status": row.get("status"),
                    "created_at": row.get("created_at"),
                }
                for row in proposal_context
            ],
            use_container_width=True,
            hide_index=True,
        )


with st.expander("Istoric Re-Review Orchestration"):
    if existing_runs:
        st.dataframe(
            [
                {
                    "id": row.get("id"),
                    "validated_changes": row.get("validated_changes"),
                    "reviewer_status": row.get("reviewer_status"),
                    "compliance_status": row.get("compliance_status"),
                    "readiness_status": row.get("readiness_status"),
                    "overall_status": row.get("overall_status"),
                    "created_at": row.get("created_at"),
                    "completed_at": row.get("completed_at"),
                }
                for row in existing_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există încă rulări.")

st.caption(
    "Etapa 28 folosește acum proposal_versions, grant_reviews, grant_compliance_checks "
    "și submission_readiness pentru a realiza o reevaluare comparabilă înainte/după."
)
