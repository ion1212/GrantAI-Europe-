import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Post-Execution Validator",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 Etapa 27 — AI Post-Execution Validator")
st.caption(
    "Validează execuțiile Applied din Etapa 26 înainte ca proiectul să fie retrimis "
    "către Reviewer, Compliance și Submission Readiness. Nu modifică automat conținutul."
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


def compact_json(obj: Any, limit: int = 30000) -> str:
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

selected_label = st.selectbox("Project", list(project_labels.keys()))
project = project_labels[selected_label]
project_id = str(project["id"])


# ---------------------------------------------------------------------
# Applied executions from Stage 26
# ---------------------------------------------------------------------
try:
    executions = (
        supabase.table("controlled_resolution_executions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("execution_status", "Applied")
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca execuțiile Applied din Etapa 26: {exc}")
    st.stop()

if not executions:
    st.warning(
        "Nu există execuții Applied în Etapa 26 pentru acest proiect. "
        "Aplică mai întâi o modificare în Controlled Resolution Executor."
    )
    st.stop()

opportunity_identity = str(executions[0].get("opportunity_identity") or "")
executions = [
    row for row in executions
    if str(row.get("opportunity_identity") or "") == opportunity_identity
]

# Deduplicate latest applied execution per execution_plan_item_id.
dedup = {}
for row in executions:
    key = str(row.get("execution_plan_item_id") or row.get("id") or "")
    if key and key not in dedup:
        dedup[key] = row
executions = list(dedup.values())

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)


# ---------------------------------------------------------------------
# Existing validations
# ---------------------------------------------------------------------
try:
    validation_items = (
        supabase.table("post_execution_validation_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    validation_items = []

latest_validation_by_execution = {}
for row in validation_items:
    eid = str(row.get("controlled_execution_id") or "")
    if eid and eid not in latest_validation_by_execution:
        latest_validation_by_execution[eid] = row


# ---------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------
def deterministic_check(execution: dict) -> tuple[bool, dict]:
    approved_change = str(execution.get("approved_change") or "").strip()
    applied_content = str(execution.get("applied_content") or "").strip()
    original_content = str(execution.get("original_content") or "").strip()
    execution_type = norm(execution.get("execution_type"))

    checks = {
        "has_approved_change": bool(approved_change),
        "has_applied_content": bool(applied_content),
        "user_approved": bool(execution.get("user_approved")),
        "execution_is_applied": norm(execution.get("execution_status")) == "applied",
        "no_error_message": not bool(str(execution.get("error_message") or "").strip()),
    }

    if execution_type in ("replace", "rewrite"):
        checks["content_matches_approval"] = applied_content == approved_change
    elif execution_type in ("append", "insert"):
        checks["content_matches_approval"] = approved_change in applied_content
    else:
        checks["content_matches_approval"] = bool(applied_content)

    # original_content is allowed to be empty, so it is not a blocker by itself
    # if the approved change and applied content are consistent.
    ok = all(checks.values())

    return ok, {
        "checks": checks,
        "original_content_empty": not bool(original_content),
    }


prechecks = []
for execution in executions:
    ok, details = deterministic_check(execution)
    prechecks.append((execution, ok, details))

local_ok = sum(1 for _, ok, _ in prechecks if ok)
local_attention = len(prechecks) - local_ok

validated_count = sum(
    1
    for row in latest_validation_by_execution.values()
    if norm(row.get("validation_status")) == "validated"
)
needs_attention_count = sum(
    1
    for row in latest_validation_by_execution.values()
    if norm(row.get("validation_status")) == "needs attention"
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Applied Etapa 26", len(executions))
c2.metric("Pre-check OK", local_ok)
c3.metric("Validate", validated_count)
c4.metric("Necesită atenție", needs_attention_count)

st.info(
    "Etapa 27 verifică faptul că execuția Applied corespunde exact modificării aprobate "
    "și nu conține adăugiri nesusținute. Nu schimbă automat documentul sau task-urile."
)


# ---------------------------------------------------------------------
# AI validation
# ---------------------------------------------------------------------
def build_ai_payload():
    payload = []

    for execution, precheck_ok, details in prechecks:
        payload.append({
            "controlled_execution_id": str(execution.get("id") or ""),
            "execution_plan_id": str(execution.get("execution_plan_id") or ""),
            "execution_plan_item_id": str(execution.get("execution_plan_item_id") or ""),
            "resolution_task_id": str(execution.get("resolution_task_id") or ""),
            "evidence_resolution_id": str(execution.get("evidence_resolution_id") or ""),
            "category": str(execution.get("category") or "Other"),
            "target_section": str(execution.get("target_section") or ""),
            "execution_type": str(execution.get("execution_type") or ""),
            "original_content": str(execution.get("original_content") or ""),
            "approved_change": str(execution.get("approved_change") or ""),
            "applied_content": str(execution.get("applied_content") or ""),
            "user_approved": bool(execution.get("user_approved")),
            "error_message": str(execution.get("error_message") or ""),
            "deterministic_precheck_ok": precheck_ok,
            "deterministic_details": details,
        })

    return payload


def ai_validate(payload):
    prompt = f"""
You are a strict post-execution validator for an EU grant workflow.

You are reviewing changes that have already been applied by a controlled executor.
Your job is to validate whether each applied result faithfully corresponds to the approved change.

STRICT RULES:
- Do not invent or add any facts.
- Do not upgrade verification status.
- Do not infer eligibility, budget, partners, TRL, KPIs or official compliance beyond the supplied data.
- "Validated" means the applied result faithfully reflects the approved change and contains no unsupported additions.
- If applied_content contains information not present in approved_change/original_content, mark Needs attention.
- If the execution is inconsistent with the execution type, mark Needs attention.
- If the applied content clearly contradicts the approved change, mark Rejected.
- Return JSON only.
- Return exactly one result per input execution.

Return:
{{
  "summary": "",
  "items": [
    {{
      "controlled_execution_id": "",
      "validation_status": "Validated|Needs attention|Rejected",
      "content_matches_approval": true,
      "no_unsupported_additions": true,
      "target_section_valid": true,
      "validation_reason": "",
      "confidence": "High|Medium|Low"
    }}
  ]
}}

INPUT:
{compact_json(payload)}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Validate conservatively. "
            "Never invent facts and never change the applied content."
        ),
        input=prompt,
    )

    result = json.loads(clean_json(response.output_text))
    if not isinstance(result, dict):
        raise ValueError("Răspunsul AI nu este un obiect JSON.")

    result.setdefault("summary", "")
    result.setdefault("items", [])
    return result


if st.button(
    "🧪 Validează execuțiile Applied",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Validăm execuțiile aplicate..."):
        try:
            payload = build_ai_payload()
            result = ai_validate(payload)

            controlled_run_id = None
            execution_plan_id = None

            if executions:
                controlled_run_id = executions[0].get("run_id")
                execution_plan_id = executions[0].get("execution_plan_id")

            run_insert = (
                supabase.table("post_execution_validation_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "execution_plan_id": execution_plan_id,
                    "controlled_run_id": controlled_run_id,
                    "total_executions": len(executions),
                    "validated": 0,
                    "needs_attention": 0,
                    "failed": 0,
                    "overall_status": "Pending",
                    "summary": {
                        "stage": 27,
                        "text": result.get("summary", ""),
                    },
                })
                .execute()
            )

            run_data = run_insert.data or []
            if not run_data:
                raise RuntimeError("Nu am putut salva validation run.")

            validation_run_id = str(run_data[0]["id"])

            execution_by_id = {
                str(row.get("id")): row
                for row in executions
                if row.get("id")
            }

            validated = 0
            needs_attention = 0
            failed = 0

            for item in result.get("items", []):
                execution_id = str(item.get("controlled_execution_id") or "")
                execution = execution_by_id.get(execution_id)
                if not execution:
                    continue

                status = str(item.get("validation_status") or "Needs attention")
                if status not in ("Validated", "Needs attention", "Rejected"):
                    status = "Needs attention"

                confidence = str(item.get("confidence") or "Low")
                if confidence not in ("High", "Medium", "Low"):
                    confidence = "Low"

                if status == "Validated":
                    validated += 1
                elif status == "Rejected":
                    failed += 1
                else:
                    needs_attention += 1

                supabase.table("post_execution_validation_items").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "validation_run_id": validation_run_id,
                    "execution_plan_id": execution.get("execution_plan_id"),
                    "execution_plan_item_id": execution.get("execution_plan_item_id"),
                    "controlled_execution_id": execution.get("id"),
                    "resolution_task_id": execution.get("resolution_task_id"),
                    "evidence_resolution_id": execution.get("evidence_resolution_id"),
                    "category": execution.get("category"),
                    "target_section": execution.get("target_section"),
                    "original_content": str(execution.get("original_content") or ""),
                    "approved_change": str(execution.get("approved_change") or ""),
                    "applied_content": str(execution.get("applied_content") or ""),
                    "validation_status": status,
                    "content_matches_approval": bool(item.get("content_matches_approval")),
                    "no_unsupported_additions": bool(item.get("no_unsupported_additions")),
                    "target_section_valid": bool(item.get("target_section_valid")),
                    "validation_reason": str(item.get("validation_reason") or ""),
                    "confidence": confidence,
                    "updated_at": now_iso(),
                }).execute()

            overall_status = (
                "Validated"
                if validated == len(executions) and validated > 0
                else "Failed"
                if failed > 0 and validated == 0
                else "Needs attention"
            )

            (
                supabase.table("post_execution_validation_runs")
                .update({
                    "validated": validated,
                    "needs_attention": needs_attention,
                    "failed": failed,
                    "overall_status": overall_status,
                })
                .eq("id", validation_run_id)
                .eq("user_id", user_id)
                .execute()
            )

            st.success(
                f"Validare finalizată: {validated} Validated, "
                f"{needs_attention} Needs attention, {failed} Rejected."
            )
            st.rerun()

        except Exception as exc:
            st.error(f"Validarea nu a putut fi executată: {exc}")


# ---------------------------------------------------------------------
# Reload latest validation items
# ---------------------------------------------------------------------
try:
    validation_items = (
        supabase.table("post_execution_validation_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    validation_items = []

latest_validation_by_execution = {}
for row in validation_items:
    eid = str(row.get("controlled_execution_id") or "")
    if eid and eid not in latest_validation_by_execution:
        latest_validation_by_execution[eid] = row


# ---------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------
st.subheader("Rezultate Post-Execution Validation")

for pos, execution in enumerate(executions):
    eid = str(execution.get("id") or "")
    validation = latest_validation_by_execution.get(eid)

    status = str(validation.get("validation_status")) if validation else "Not validated"

    if status == "Validated":
        icon = "✅"
    elif status == "Rejected":
        icon = "❌"
    else:
        icon = "⚠️"

    with st.expander(
        f"{icon} {execution.get('category') or 'Other'} — "
        f"{execution.get('target_section') or 'Target section'} [{status}]",
        expanded=(pos == 0),
    ):
        st.write("**Approved change:**")
        st.text_area(
            "Approved change",
            value=str(execution.get("approved_change") or ""),
            height=170,
            disabled=True,
            key=f"stage27_approved_{eid}",
        )

        st.write("**Applied content:**")
        st.text_area(
            "Applied content",
            value=str(execution.get("applied_content") or ""),
            height=190,
            disabled=True,
            key=f"stage27_applied_{eid}",
        )

        if validation:
            v1, v2, v3 = st.columns(3)
            v1.metric(
                "Matches approval",
                "YES" if validation.get("content_matches_approval") else "NO",
            )
            v2.metric(
                "No unsupported additions",
                "YES" if validation.get("no_unsupported_additions") else "NO",
            )
            v3.metric(
                "Target section valid",
                "YES" if validation.get("target_section_valid") else "NO",
            )

            if status == "Validated":
                st.success(
                    "Execuția a fost validată și poate fi retrimisă către Reviewer / Compliance / Readiness."
                )
            elif status == "Rejected":
                st.error(
                    "Execuția a fost respinsă. Nu trebuie propagată mai departe."
                )
            else:
                st.warning(
                    "Execuția necesită verificare înainte de a continua."
                )

            if validation.get("validation_reason"):
                st.write(f"**Motiv:** {validation.get('validation_reason')}")

            st.caption(
                f"Încredere validare: {validation.get('confidence') or 'Low'}"
            )

        else:
            ok, details = deterministic_check(execution)

            if ok:
                st.caption(
                    "Pre-check local OK. Rulează validarea AI pentru confirmarea finală."
                )
            else:
                st.warning(
                    "Pre-check-ul local a identificat o posibilă neconcordanță."
                )
                st.json(details)


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()

with st.expander("Istoric Post-Execution Validation"):
    try:
        runs = (
            supabase.table("post_execution_validation_runs")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        ).data or []

        if runs:
            st.dataframe(
                runs,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Nu există încă rulări Etapa 27.")

    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")


st.caption(
    "Etapa 27 validează rezultatul execuției, dar nu modifică documentul și nu declară "
    "automat proiectul submission-ready. După Validated, proiectul poate fi retrimis "
    "către Reviewer, Compliance Checker și Submission Readiness."
)
