import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(page_title="AI Resolution Revalidation", page_icon="🔁", layout="wide")

st.title("🔁 Etapa 24 — AI Resolution Revalidation")
st.caption(
    "Revalidează rezolvările aprobate în Etapa 23 înainte de execuție. "
    "Verifică dacă task-ul, dovada și rezolvarea aprobată sunt coerente și pot fi trimise "
    "către AI Resolution Executor fără inventarea de date."
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

def restore_auth_session(sb):
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

def current_user_id(sb):
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

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def compact_json(obj: Any, limit: int = 26000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]

def clean_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```JSON", "", 1).replace("```", "").strip()
    return text

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

try:
    approved_rows = (
        supabase.table("evidence_resolution_inputs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("resolution_status", "Approved")
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca rezolvările aprobate din Etapa 23: {exc}")
    st.stop()

if not approved_rows:
    st.warning("Nu există rezolvări Approved în Etapa 23 pentru acest proiect.")
    st.stop()

opportunity_identity = str(approved_rows[0].get("opportunity_identity") or "")
approved_rows = [r for r in approved_rows if str(r.get("opportunity_identity") or "") == opportunity_identity]
st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

try:
    all_tasks = (
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

task_by_id = {str(row.get("id")): row for row in all_tasks if row.get("id")}

def deterministic_check(resolution_row, task_row):
    issues = []
    if not task_row:
        return False, ["Resolution task not found."]
    proposed = str(resolution_row.get("proposed_resolution") or "").strip()
    if not proposed:
        issues.append("Approved resolution has no proposed_resolution.")
    if norm(resolution_row.get("resolution_status")) != "approved":
        issues.append("Stage 23 resolution is not Approved.")
    task_status = norm(task_row.get("status"))
    if task_status not in ("proposed", "open", "waiting input", "in progress"):
        issues.append(f"Unexpected resolution task status: {task_row.get('status')}")
    if str(task_row.get("proposed_resolution") or "").strip() != proposed:
        issues.append("Approved resolution and resolution_tasks.proposed_resolution are not identical.")
    if str(task_row.get("project_id") or "") != project_id:
        issues.append("Task belongs to another project.")
    if str(task_row.get("opportunity_identity") or "") != opportunity_identity:
        issues.append("Task belongs to another opportunity.")
    return len(issues) == 0, issues

prechecks = []
for res in approved_rows:
    task = task_by_id.get(str(res.get("resolution_task_id") or ""))
    ok, issues = deterministic_check(res, task)
    prechecks.append((res, task, ok, issues))

ready_local = sum(1 for _, _, ok, _ in prechecks if ok)
needs_attention_local = len(prechecks) - ready_local

try:
    existing_items = (
        supabase.table("resolution_revalidation_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    existing_items = []

latest_item_by_resolution = {}
for row in existing_items:
    rid = str(row.get("evidence_resolution_id") or "")
    if rid and rid not in latest_item_by_resolution:
        latest_item_by_resolution[rid] = row

c1, c2, c3, c4 = st.columns(4)
c1.metric("Approved Etapa 23", len(approved_rows))
c2.metric("Pre-check OK", ready_local)
c3.metric("Necesită atenție", needs_attention_local)
c4.metric(
    "Ready for execution",
    sum(1 for r in latest_item_by_resolution.values() if norm(r.get("validation_status")) == "ready for execution"),
)

st.info(
    "Etapa 24 nu modifică documentul și nu execută task-uri. "
    "Doar verifică dacă rezolvarea aprobată poate fi predată în siguranță către Resolution Executor."
)

def build_ai_payload():
    items = []
    for resolution_row, task_row, ok, issues in prechecks:
        items.append({
            "evidence_resolution_id": str(resolution_row.get("id") or ""),
            "resolution_task_id": str(resolution_row.get("resolution_task_id") or ""),
            "category": str(
                resolution_row.get("category")
                or resolution_row.get("requirement_category")
                or (task_row or {}).get("category")
                or "Other"
            ),
            "approved_resolution": str(resolution_row.get("proposed_resolution") or ""),
            "verification_status": str(resolution_row.get("verification_status") or ""),
            "source_reference": str(resolution_row.get("source_reference") or ""),
            "confidence": str(resolution_row.get("confidence") or ""),
            "task_status": str((task_row or {}).get("status") or ""),
            "task_proposed_resolution": str((task_row or {}).get("proposed_resolution") or ""),
            "task_text": str(first_value(task_row or {}, ["task", "title", "issue", "description", "issue_description"], "")),
            "deterministic_precheck_ok": ok,
            "deterministic_issues": issues,
        })
    return items

def ai_revalidate(payload):
    prompt = f'''
You are a strict EU grant workflow revalidation assistant.

Review Stage 23 resolutions explicitly approved by the user.
Do NOT rewrite them and do NOT invent information.

Rules:
- Never invent facts, budgets, partners, eligibility, TRL, KPIs, official rules or evidence.
- Do not upgrade verification status.
- User confirmed is not officially verified.
- If deterministic_precheck_ok=false, normally mark Needs attention.
- If evidence is insufficient or contradictory, mark Needs attention.
- "Ready for execution" only means ready for the controlled executor, not final submission ready.
- Return JSON only and exactly one item per input.

Return:
{{
  "summary": "",
  "items": [
    {{
      "evidence_resolution_id": "",
      "resolution_task_id": "",
      "category": "",
      "validation_status": "Ready for execution|Needs attention|Rejected",
      "validation_reason": "",
      "confidence": "High|Medium|Low"
    }}
  ]
}}

INPUT:
{compact_json(payload)}
'''
    response = get_openai().responses.create(
        model=model_name(),
        instructions="Return JSON only. Revalidate conservatively. Never invent facts.",
        input=prompt,
    )
    result = json.loads(clean_json(response.output_text))
    result.setdefault("summary", "")
    result.setdefault("items", [])
    return result

if st.button("🔁 Revalidează rezolvările aprobate", type="primary", use_container_width=True):
    with st.spinner("Revalidăm rezolvările aprobate..."):
        try:
            payload = build_ai_payload()
            result = ai_revalidate(payload)
            ready_count = 0
            attention_count = 0
            approved_by_id = {str(row.get("id")): row for row in approved_rows}

            for item in result.get("items", []):
                resolution_id = str(item.get("evidence_resolution_id") or "")
                resolution_row = approved_by_id.get(resolution_id)
                if not resolution_row:
                    continue

                status = str(item.get("validation_status") or "Needs attention")
                if status not in ("Ready for execution", "Needs attention", "Rejected"):
                    status = "Needs attention"

                confidence = str(item.get("confidence") or "Low")
                if confidence not in ("High", "Medium", "Low"):
                    confidence = "Low"

                if status == "Ready for execution":
                    ready_count += 1
                else:
                    attention_count += 1

                task_row = task_by_id.get(str(resolution_row.get("resolution_task_id") or ""), {})

                supabase.table("resolution_revalidation_items").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "resolution_task_id": resolution_row.get("resolution_task_id"),
                    "evidence_resolution_id": resolution_row.get("id"),
                    "category": str(
                        item.get("category")
                        or resolution_row.get("category")
                        or resolution_row.get("requirement_category")
                        or "Other"
                    ),
                    "proposed_resolution": str(resolution_row.get("proposed_resolution") or ""),
                    "task_status": str(task_row.get("status") or ""),
                    "validation_status": status,
                    "validation_reason": str(item.get("validation_reason") or ""),
                    "confidence": confidence,
                    "updated_at": now_iso(),
                }).execute()

            overall_status = "Ready" if ready_count == len(approved_rows) and ready_count > 0 else "Needs attention"

            supabase.table("resolution_revalidation_runs").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_identity": opportunity_identity,
                "total_approved": len(approved_rows),
                "valid_for_execution": ready_count,
                "needs_attention": attention_count,
                "overall_status": overall_status,
                "summary": {"stage": 24, "text": result.get("summary", "")},
            }).execute()

            st.success(f"Revalidare finalizată: {ready_count} Ready for execution, {attention_count} Needs attention.")
            st.rerun()

        except Exception as exc:
            st.error(f"Revalidarea nu a putut fi executată: {exc}")

try:
    revalidation_items = (
        supabase.table("resolution_revalidation_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    revalidation_items = []

latest_item_by_resolution = {}
for row in revalidation_items:
    rid = str(row.get("evidence_resolution_id") or "")
    if rid and rid not in latest_item_by_resolution:
        latest_item_by_resolution[rid] = row

st.subheader("Rezultate revalidare")

if not latest_item_by_resolution:
    st.caption("Nu există încă o rulare de revalidare pentru aceste rezolvări.")

for resolution_row in approved_rows:
    rid = str(resolution_row.get("id") or "")
    task_id = str(resolution_row.get("resolution_task_id") or "")
    task_row = task_by_id.get(task_id, {})
    validation = latest_item_by_resolution.get(rid)

    category = str(
        resolution_row.get("category")
        or resolution_row.get("requirement_category")
        or task_row.get("category")
        or "Other"
    )
    status = str(validation.get("validation_status")) if validation else "Not revalidated"
    icon = "✅" if status == "Ready for execution" else "⚠️"

    with st.expander(
        f"{icon} {category} — {first_value(task_row, ['task','title','issue','description'], 'Task')} [{status}]",
        expanded=True,
    ):
        st.write("**Rezolvare aprobată în Etapa 23:**")
        st.write(str(resolution_row.get("proposed_resolution") or ""))

        st.write(
            f"**Task status:** {task_row.get('status') or '—'}  \n"
            f"**Verificare dovadă:** {resolution_row.get('verification_status') or '—'}  \n"
            f"**Sursă:** {resolution_row.get('source_reference') or '—'}"
        )

        if validation:
            if status == "Ready for execution":
                st.success("Rezolvarea este validată pentru Resolution Executor.")
            else:
                st.warning("Rezolvarea necesită atenție înainte de execuție.")

            if validation.get("validation_reason"):
                st.write(f"**Motiv:** {validation.get('validation_reason')}")
            st.caption(f"Încredere revalidare: {validation.get('confidence') or 'Low'}")
        else:
            _, issues = deterministic_check(resolution_row, task_row)
            if issues:
                for issue in issues:
                    st.warning(issue)
            else:
                st.caption("Pre-check local OK; rulează revalidarea AI.")

st.divider()

with st.expander("Istoric Resolution Revalidation"):
    try:
        runs = (
            supabase.table("resolution_revalidation_runs")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        ).data or []

        if runs:
            st.dataframe(runs, use_container_width=True, hide_index=True)
        else:
            st.caption("Nu există încă rulări Stage 24.")
    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")

st.caption(
    "Etapa 24 nu execută task-uri și nu modifică propunerea. "
    "Ea decide doar dacă o rezolvare Approved din Etapa 23 poate fi transmisă "
    "în siguranță către Resolution Executor."
)
