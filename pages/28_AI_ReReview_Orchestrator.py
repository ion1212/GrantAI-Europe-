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
    "Reevaluează proiectul după corecțiile validate folosind snapshot-ul cel mai complet "
    "disponibil din Writer, Optimizer, Proposal Versions și Submission Pack."
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
    access_token = session.get("access_token") if isinstance(session, dict) else getattr(session, "access_token", None)
    refresh_token = session.get("refresh_token") if isinstance(session, dict) else getattr(session, "refresh_token", None)
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def clean_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```JSON", "", 1).replace("```", "").strip()
    return text


def compact_json(obj: Any, limit: int = 52000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def json_score(obj: Any) -> float | None:
    if not isinstance(obj, dict):
        return None
    for key in ("score", "overall_score", "compliance_score", "total_score", "readiness_score"):
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


def latest_rows(table: str, user_id: str, project_id: str, opportunity_identity: str = "", limit: int = 100):
    try:
        q = (
            supabase.table(table)
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
        )
        if opportunity_identity:
            try:
                q = q.eq("opportunity_identity", opportunity_identity)
            except Exception:
                pass
        return q.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception:
        return []


def pick_latest_per(rows: list[dict], key_fields: list[str]) -> list[dict]:
    seen = set()
    out = []
    for row in rows:
        key = None
        for field in key_fields:
            if row.get(field):
                key = str(row.get(field))
                break
        key = key or str(row.get("id") or "")
        if key and key not in seen:
            seen.add(key)
            out.append(row)
    return out


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


# Project
projects = latest_rows("projects", user_id, "", "", 100)
if not projects:
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

project_labels = {
    f"{p.get('name') or p.get('title') or 'Project'} — {str(p.get('id'))[:8]}": p
    for p in projects
}
selected = st.selectbox("Project", list(project_labels.keys()))
project = project_labels[selected]
project_id = str(project["id"])


# Validated stage 27
validated_items = latest_rows(
    "post_execution_validation_items",
    user_id,
    project_id,
    "",
    100,
)
validated_items = [r for r in validated_items if str(r.get("validation_status") or "") == "Validated"]
if not validated_items:
    st.warning("Nu există rezultate Validated în Etapa 27.")
    st.stop()

opportunity_identity = str(validated_items[0].get("opportunity_identity") or "")
validated_items = [
    r for r in validated_items
    if str(r.get("opportunity_identity") or "") == opportunity_identity
]
validated_items = pick_latest_per(validated_items, ["controlled_execution_id"])

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

post_validation_run_id = validated_items[0].get("validation_run_id")
execution_plan_id = validated_items[0].get("execution_plan_id")


# ---------------------------------------------------------------------
# Build best available proposal snapshot
# Priority:
# 1) grant_optimization_sections.optimized_content
# 2) grant_writer_sections.content
# 3) grant_writer_versions.content
# 4) proposal_versions.content
# 5) submission_packs excellence/impact/implementation
# ---------------------------------------------------------------------
writer_sections = latest_rows("grant_writer_sections", user_id, project_id, opportunity_identity, 200)
writer_versions = latest_rows("grant_writer_versions", user_id, project_id, opportunity_identity, 200)
optimization_sections = latest_rows("grant_optimization_sections", user_id, project_id, opportunity_identity, 200)
proposal_versions = latest_rows("proposal_versions", user_id, project_id, "", 200)
submission_packs = latest_rows("submission_packs", user_id, project_id, opportunity_identity, 20)

writer_sections = pick_latest_per(writer_sections, ["section_key", "section_title"])
writer_versions = pick_latest_per(writer_versions, ["section_key"])
optimization_sections = pick_latest_per(optimization_sections, ["section_key"])
proposal_versions = pick_latest_per(proposal_versions, ["section", "title"])

snapshot = {}
snapshot_source = {}

for row in proposal_versions:
    key = str(row.get("section") or row.get("title") or "General")
    content = str(row.get("content") or "").strip()
    if content:
        snapshot[key] = content
        snapshot_source[key] = "proposal_versions"

for row in writer_versions:
    key = str(row.get("section_key") or "General")
    content = str(row.get("content") or "").strip()
    if content:
        snapshot[key] = content
        snapshot_source[key] = "grant_writer_versions"

for row in writer_sections:
    key = str(row.get("section_key") or row.get("section_title") or "General")
    content = str(row.get("content") or "").strip()
    if content:
        snapshot[key] = content
        snapshot_source[key] = "grant_writer_sections"

for row in optimization_sections:
    key = str(row.get("section_key") or "General")
    content = str(row.get("optimized_content") or row.get("original_content") or "").strip()
    if content:
        snapshot[key] = content
        snapshot_source[key] = "grant_optimization_sections"

if submission_packs:
    pack = submission_packs[0]
    for key, field in [
        ("Excellence", "excellence_content"),
        ("Impact", "impact_content"),
        ("Implementation", "implementation_content"),
    ]:
        content = str(pack.get(field) or "").strip()
        if content and key not in snapshot:
            snapshot[key] = content
            snapshot_source[key] = "submission_packs"

# Apply validated Stage 27 content over target section in snapshot.
for item in validated_items:
    section = str(item.get("target_section") or item.get("category") or "General")
    content = str(item.get("applied_content") or "").strip()
    if content:
        snapshot[section] = content
        snapshot_source[section] = "Stage 27 validated correction"

proposal_snapshot = [
    {
        "section": section,
        "content": content,
        "source": snapshot_source.get(section, "unknown"),
    }
    for section, content in snapshot.items()
]


# Previous evaluations
reviewer_rows = latest_rows("grant_reviews", user_id, project_id, opportunity_identity, 20)
previous_reviewer = reviewer_rows[0] if reviewer_rows else {}
reviewer_before = safe_score(previous_reviewer.get("overall_score"))

compliance_rows = latest_rows("grant_compliance_checks", user_id, project_id, opportunity_identity, 20)
previous_compliance = compliance_rows[0] if compliance_rows else {}
compliance_before = json_score(previous_compliance.get("result") or {})

readiness_rows = latest_rows("submission_readiness_runs", user_id, project_id, opportunity_identity, 20)
previous_readiness = readiness_rows[0] if readiness_rows else {}
readiness_before = safe_score(previous_readiness.get("readiness_score"))

readiness_items = latest_rows("submission_readiness_items", user_id, project_id, opportunity_identity, 100)

existing_runs = latest_rows("rereview_orchestration_runs", user_id, project_id, opportunity_identity, 50)
latest_run = existing_runs[0] if existing_runs else None


# Metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Validated Etapa 27", len(validated_items))
c2.metric("Reviewer before", "—" if reviewer_before is None else f"{reviewer_before:g}/100")
c3.metric("Compliance before", "—" if compliance_before is None else f"{compliance_before:g}/100")
c4.metric("Readiness before", "—" if readiness_before is None else f"{readiness_before:g}/100")

if proposal_snapshot:
    st.success(f"Snapshot complet: {len(proposal_snapshot)} secțiuni încărcate.")
else:
    st.error("Nu am găsit conținut de propunere în Writer / Optimizer / Proposal Versions / Submission Pack.")

with st.expander("Surse snapshot"):
    st.dataframe(
        [
            {
                "section": row["section"],
                "source": row["source"],
                "characters": len(row["content"]),
            }
            for row in proposal_snapshot
        ],
        use_container_width=True,
        hide_index=True,
    )

st.info(
    "Etapa 28 folosește acum sursa cea mai completă pentru fiecare secțiune și suprapune "
    "corecțiile validate în Etapa 27 înainte de reevaluare."
)


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
You are performing a controlled re-review of an EU grant proposal after validated corrections.

You receive the best available full proposal snapshot assembled from the application's own databases.
The snapshot may combine optimized sections, writer sections, writer versions, proposal versions,
submission pack content, and Stage 27 validated corrections.

STRICT RULES:
- Use only supplied data.
- Never invent budget values, partners, eligibility, TRL, KPIs, legal status, deadlines or evidence.
- If a fact exists in the FULL PROPOSAL SNAPSHOT, do not claim it is missing.
- If a fact is absent from the FULL PROPOSAL SNAPSHOT and prior stored evidence, it remains missing.
- Do not confuse lack of a specific database table with lack of proposal content.
- score_before must reflect the real prior score supplied when available; otherwise null.
- Recalculate score_after on 0-100.
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

FULL PROPOSAL SNAPSHOT:
{compact_json(proposal_snapshot)}

ACTUAL PREVIOUS EVALUATIONS:
{compact_json(prior_context())}

VALIDATED STAGE 27 CHANGES:
{compact_json(validated_context())}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Evaluate the actual assembled proposal snapshot. "
            "Do not invent facts and do not claim present content is missing."
        ),
        input=prompt,
    )
    result = json.loads(clean_json(response.output_text))
    if not isinstance(result, dict):
        raise ValueError("Răspuns AI invalid.")
    return result


if st.button(
    "🔄 Rulează Re-Review complet pe snapshot",
    type="primary",
    use_container_width=True,
    disabled=not bool(proposal_snapshot),
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
                        "context_source": "assembled_full_snapshot",
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
                score_before = real_before if real_before is not None else safe_score(module_result.get("score_before"))

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
                            "error_message": None if module_status == "Completed" else str(module_result.get("summary") or ""),
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
                        "context_source": "writer + optimizer + proposal_versions + submission_pack + stage27",
                        "snapshot_sections": len(proposal_snapshot),
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .eq("id", orchestration_run_id)
                .eq("user_id", user_id)
                .execute()
            )

            st.success("Re-Review complet finalizat pe snapshot-ul asamblat.")
            st.rerun()

        except Exception as exc:
            st.error(f"Re-Review nu a putut fi executat: {exc}")


existing_runs = latest_rows("rereview_orchestration_runs", user_id, project_id, opportunity_identity, 50)
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
            s1.metric("Score before", "—" if before is None else f"{before}/100")
            s2.metric("Score after", "—" if after is None else f"{after}/100")

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
                st.write(f"**Submission ready:** {'YES' if bool(result.get('submission_ready')) else 'NO'}")

    summary = latest_run.get("summary") or {}
    if isinstance(summary, dict) and summary.get("text"):
        st.info(str(summary.get("text")))

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
    "Etapa 28 reconstruiește acum snapshot-ul propunerii din sursele reale disponibile "
    "și suprapune corecțiile validate înainte de reevaluare."
)
