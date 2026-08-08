import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Resolution Execution Planner",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ Etapa 25 — AI Resolution Execution Planner")
st.caption(
    "Construiește un plan de execuție numai pentru rezolvările validate în Etapa 24 "
    "ca Ready for execution. Nu aplică automat modificări și cere aprobare explicită."
)


# ---------------------------------------------------------------------
# Secrets / auth
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


def first_value(row: dict, names, default=""):
    if not isinstance(row, dict):
        return default
    for name in names:
        value = row.get(name)
        if value not in (None, "", [], {}):
            return value
    return default


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


def compact_json(obj: Any, limit: int = 26000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


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
# Stage 24: Ready for execution
# ---------------------------------------------------------------------
try:
    ready_rows = (
        supabase.table("resolution_revalidation_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("validation_status", "Ready for execution")
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca rezultatele Etapei 24: {exc}")
    st.stop()

if not ready_rows:
    st.warning(
        "Nu există rezolvări Ready for execution în Etapa 24. "
        "Rulează și finalizează mai întâi AI Resolution Revalidation."
    )
    st.stop()

opportunity_identity = str(ready_rows[0].get("opportunity_identity") or "")
ready_rows = [
    row for row in ready_rows
    if str(row.get("opportunity_identity") or "") == opportunity_identity
]

# Deduplicate by resolution_task_id, keeping newest row.
dedup = {}
for row in ready_rows:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in dedup:
        dedup[tid] = row
ready_rows = list(dedup.values())

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)


# ---------------------------------------------------------------------
# Load related task / Stage 23 resolution
# ---------------------------------------------------------------------
try:
    resolution_tasks = (
        supabase.table("resolution_tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca resolution_tasks: {exc}")
    st.stop()

task_by_id = {
    str(row.get("id")): row
    for row in resolution_tasks
    if row.get("id")
}

try:
    approved_resolutions = (
        supabase.table("evidence_resolution_inputs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .eq("resolution_status", "Approved")
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    approved_resolutions = []

resolution_by_id = {
    str(row.get("id")): row
    for row in approved_resolutions
    if row.get("id")
}


# ---------------------------------------------------------------------
# Existing plan data
# ---------------------------------------------------------------------
try:
    plans = (
        supabase.table("resolution_execution_plans")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    plans = []

latest_plan = plans[0] if plans else None

try:
    plan_items = (
        supabase.table("resolution_execution_plan_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    plan_items = []

latest_item_by_task = {}
for item in plan_items:
    tid = str(item.get("resolution_task_id") or "")
    if tid and tid not in latest_item_by_task:
        latest_item_by_task[tid] = item


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
ready_count = len(ready_rows)
planned_count = len(latest_item_by_task)
approved_count = sum(
    1 for item in latest_item_by_task.values()
    if bool(item.get("user_approved"))
    or norm(item.get("execution_status")) == "approved"
)
blocked_count = sum(
    1 for item in latest_item_by_task.values()
    if norm(item.get("execution_status")) == "needs attention"
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Ready Etapa 24", ready_count)
c2.metric("Planificate", planned_count)
c3.metric("Aprobate", approved_count)
c4.metric("Necesită atenție", blocked_count)

st.info(
    "Etapa 25 creează doar planul de execuție. "
    "Nicio modificare nu este aplicată în propunere fără aprobarea explicită a utilizatorului."
)


# ---------------------------------------------------------------------
# AI execution planning
# ---------------------------------------------------------------------
def build_input():
    items = []

    for row in ready_rows:
        task_id = str(row.get("resolution_task_id") or "")
        task = task_by_id.get(task_id, {})
        evidence_resolution_id = str(row.get("evidence_resolution_id") or "")
        evidence_resolution = resolution_by_id.get(evidence_resolution_id, {})

        approved_resolution = str(
            first_value(
                evidence_resolution,
                ["proposed_resolution"],
                row.get("proposed_resolution") or task.get("proposed_resolution") or "",
            )
        )

        items.append({
            "revalidation_item_id": str(row.get("id") or ""),
            "resolution_task_id": task_id,
            "evidence_resolution_id": evidence_resolution_id,
            "category": str(
                row.get("category")
                or evidence_resolution.get("category")
                or task.get("category")
                or "Other"
            ),
            "task_status": str(task.get("status") or ""),
            "task_title": str(
                first_value(
                    task,
                    ["issue_title", "task", "title", "issue", "description"],
                    "Task",
                )
            ),
            "task_description": str(
                first_value(task, ["issue_description", "description"], "")
            ),
            "target_section": str(
                first_value(task, ["target_section"], "")
            ),
            "approved_resolution": approved_resolution,
            "verification_status": str(
                evidence_resolution.get("verification_status") or ""
            ),
            "source_reference": str(
                evidence_resolution.get("source_reference") or ""
            ),
            "revalidation_reason": str(row.get("validation_reason") or ""),
            "revalidation_confidence": str(row.get("confidence") or ""),
        })

    return items


def ai_plan_execution(items):
    prompt = f"""
You are an EU grant controlled execution planning assistant.

The input contains resolutions already approved by the user in Stage 23 and
revalidated as "Ready for execution" in Stage 24.

Your task is only to prepare an execution plan.

STRICT RULES:
- Do not invent facts, numbers, partners, budgets, TRL, eligibility, KPIs,
  official rules or evidence.
- Do not alter the approved factual meaning.
- Do not mark anything as executed.
- Do not declare final submission readiness.
- If no clear target section can be identified from the supplied context,
  set execution_type="manual" and execution_status="Needs attention".
- proposed_change may reformat the approved resolution for insertion, but
  may not add new facts.
- The plan must require explicit user approval.
- Return JSON only.
- Return exactly one item per input item.

Return:
{{
  "summary": "",
  "items": [
    {{
      "revalidation_item_id": "",
      "resolution_task_id": "",
      "evidence_resolution_id": "",
      "category": "",
      "target_section": "",
      "current_content": "",
      "approved_resolution": "",
      "proposed_change": "",
      "execution_type": "rewrite|append|replace|insert|manual",
      "execution_status": "Ready|Needs attention",
      "validation_notes": "",
      "confidence": "High|Medium|Low"
    }}
  ]
}}

INPUT:
{compact_json(items)}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Build a conservative controlled execution plan. "
            "Never invent facts."
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
    "🗺️ Generează planul de execuție",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Construim planul de execuție controlată..."):
        try:
            source_items = build_input()
            result = ai_plan_execution(source_items)

            ready_items = 0
            attention_items = 0

            plan_insert = (
                supabase.table("resolution_execution_plans")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "total_ready": len(source_items),
                    "executable": 0,
                    "blocked": 0,
                    "plan_status": "Draft",
                    "summary": {
                        "stage": 25,
                        "text": result.get("summary", ""),
                    },
                    "updated_at": now_iso(),
                })
                .execute()
            )

            plan_data = plan_insert.data or []
            if not plan_data:
                raise RuntimeError("Planul principal nu a fost salvat.")

            plan_id = str(plan_data[0]["id"])

            for item in result.get("items", []):
                status = str(item.get("execution_status") or "Needs attention")
                if status not in ("Ready", "Needs attention"):
                    status = "Needs attention"

                execution_type = str(item.get("execution_type") or "manual")
                if execution_type not in ("rewrite", "append", "replace", "insert", "manual"):
                    execution_type = "manual"

                confidence = str(item.get("confidence") or "Low")
                if confidence not in ("High", "Medium", "Low"):
                    confidence = "Low"

                if status == "Ready":
                    ready_items += 1
                else:
                    attention_items += 1

                supabase.table("resolution_execution_plan_items").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "plan_id": plan_id,
                    "resolution_task_id": item.get("resolution_task_id"),
                    "evidence_resolution_id": item.get("evidence_resolution_id"),
                    "revalidation_item_id": item.get("revalidation_item_id"),
                    "category": str(item.get("category") or "Other"),
                    "target_section": str(item.get("target_section") or ""),
                    "current_content": str(item.get("current_content") or ""),
                    "approved_resolution": str(item.get("approved_resolution") or ""),
                    "proposed_change": str(item.get("proposed_change") or ""),
                    "execution_type": execution_type,
                    "execution_status": status,
                    "requires_user_approval": True,
                    "user_approved": False,
                    "validation_notes": str(item.get("validation_notes") or ""),
                    "confidence": confidence,
                    "updated_at": now_iso(),
                }).execute()

            plan_status = (
                "Ready"
                if ready_items == len(source_items) and ready_items > 0
                else "Needs attention"
            )

            supabase.table("resolution_execution_plans").update({
                "executable": ready_items,
                "blocked": attention_items,
                "plan_status": plan_status,
                "updated_at": now_iso(),
            }).eq("id", plan_id).eq("user_id", user_id).execute()

            st.success(
                f"Plan creat: {ready_items} Ready, "
                f"{attention_items} Needs attention."
            )
            st.rerun()

        except Exception as exc:
            st.error(f"Planul de execuție nu a putut fi creat: {exc}")


# ---------------------------------------------------------------------
# Reload current plan/items
# ---------------------------------------------------------------------
try:
    plans = (
        supabase.table("resolution_execution_plans")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    plans = []

latest_plan = plans[0] if plans else None

current_items = []
if latest_plan:
    try:
        current_items = (
            supabase.table("resolution_execution_plan_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .eq("plan_id", latest_plan["id"])
            .order("created_at")
            .execute()
        ).data or []
    except Exception:
        current_items = []


# ---------------------------------------------------------------------
# Review / approval
# ---------------------------------------------------------------------
st.subheader("Plan de execuție")

if not latest_plan:
    st.caption("Nu există încă un plan de execuție.")
else:
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Plan status", str(latest_plan.get("plan_status") or "Draft"))
    p2.metric("Total", int(latest_plan.get("total_ready") or 0))
    p3.metric("Executable", int(latest_plan.get("executable") or 0))
    p4.metric("Blocked", int(latest_plan.get("blocked") or 0))

    if not current_items:
        st.warning("Planul nu conține item-uri.")
    else:
        for pos, item in enumerate(current_items):
            task = task_by_id.get(str(item.get("resolution_task_id") or ""), {})
            status = str(item.get("execution_status") or "Planned")
            icon = "✅" if status == "Ready" else "⚠️"

            with st.expander(
                f"{icon} {item.get('category') or 'Other'} — "
                f"{first_value(task, ['issue_title','task','title','issue','description'], 'Task')} "
                f"[{status}]",
                expanded=(pos == 0),
            ):
                st.write("**Approved resolution:**")
                st.write(str(item.get("approved_resolution") or ""))

                proposed_change = st.text_area(
                    "Proposed change",
                    value=str(item.get("proposed_change") or ""),
                    height=180,
                    key=f"stage25_change_{item['id']}",
                )

                target_section = st.text_input(
                    "Target section",
                    value=str(item.get("target_section") or ""),
                    key=f"stage25_target_{item['id']}",
                )

                st.write(
                    f"**Execution type:** {item.get('execution_type') or 'manual'}  \n"
                    f"**Confidence:** {item.get('confidence') or 'Low'}"
                )

                if item.get("validation_notes"):
                    st.caption(str(item.get("validation_notes")))

                approve = st.checkbox(
                    "Am verificat planul și aprob acest item pentru execuția controlată",
                    value=bool(item.get("user_approved")),
                    key=f"stage25_approve_{item['id']}",
                    disabled=status != "Ready",
                )

                if st.button(
                    "💾 Salvează aprobarea",
                    key=f"stage25_save_{item['id']}",
                    use_container_width=True,
                    disabled=status != "Ready",
                ):
                    try:
                        supabase.table("resolution_execution_plan_items").update({
                            "target_section": target_section.strip(),
                            "proposed_change": proposed_change.strip(),
                            "user_approved": bool(approve),
                            "execution_status": "Approved" if approve else "Ready",
                            "updated_at": now_iso(),
                        }).eq("id", item["id"]).eq("user_id", user_id).execute()

                        st.success(
                            "Item-ul a fost aprobat pentru execuția controlată."
                            if approve
                            else "Aprobarea item-ului a fost eliminată."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Nu am putut salva aprobarea: {exc}")

    approved_items = sum(
        1 for item in current_items
        if bool(item.get("user_approved"))
        or norm(item.get("execution_status")) == "approved"
    )

    if current_items and approved_items == len(current_items):
        if st.button(
            "✅ Aprobă planul complet",
            type="primary",
            use_container_width=True,
        ):
            try:
                supabase.table("resolution_execution_plans").update({
                    "plan_status": "Approved",
                    "updated_at": now_iso(),
                }).eq("id", latest_plan["id"]).eq("user_id", user_id).execute()

                st.success(
                    "Planul complet a fost aprobat. "
                    "Poate fi preluat de etapa de execuție."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Nu am putut aproba planul: {exc}")
    else:
        st.caption(
            f"Item-uri aprobate: {approved_items}/{len(current_items)}. "
            "Planul complet poate fi aprobat numai după aprobarea tuturor item-urilor."
        )


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()

with st.expander("Istoric Execution Plans"):
    if plans:
        st.dataframe(
            plans,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există încă planuri Stage 25.")

st.caption(
    "Etapa 25 nu aplică modificările. Ea construiește și aprobă planul care va putea "
    "fi executat controlat într-o etapă ulterioară."
)
