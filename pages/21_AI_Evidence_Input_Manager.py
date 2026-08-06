import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="AI Evidence & Input Manager", page_icon="🧾", layout="wide")

st.title("🧾 Etapa 21 — AI Evidence & Input Manager")
st.caption(
    "Colectează și validează datele reale necesare pentru task-urile blocate. "
    "Nu confirmă automat eligibilitatea și nu inventează bugete, parteneri, TRL sau dovezi."
)

# ---------------------------------------------------------------------
# Secrets / Supabase / shared authentication bootstrap
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


def restore_auth_session(sb) -> None:
    """Restore the Supabase JWT session created on the main app page."""
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


# Load user's projects.
try:
    projects_resp = (
        supabase.table("projects")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    projects = projects_resp.data or []
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

def norm(v):
    return str(v or "").strip().lower()

def first_value(row, names, default=""):
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
    return default

# Determine latest opportunity from Stage 19.
try:
    rt = (
        supabase.table("resolution_tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .execute()
    )
    all_tasks = rt.data or []
except Exception as exc:
    st.error(f"Nu am putut încărca resolution_tasks: {exc}")
    st.stop()

if not all_tasks:
    st.warning("Etapa 19 nu are task-uri pentru acest proiect.")
    st.stop()

opportunity_identity = str(all_tasks[0].get("opportunity_identity") or "")
tasks = [
    x for x in all_tasks
    if str(x.get("opportunity_identity") or "") == opportunity_identity
]
st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

def needs_real_input(task):
    action = norm(first_value(task, [
        "resolution_type", "action_type", "task_type", "resolution_mode",
        "recommended_action", "action"
    ]))
    status = norm(first_value(task, ["status", "resolution_status"]))
    proposed = str(first_value(task, [
        "proposed_resolution", "proposed_text", "proposed_content",
        "resolution", "draft_resolution"
    ], "") or "").strip()

    return (
        "input" in action
        or "verify" in action
        or "official" in action
        or "waiting input" in status
        or (not proposed and norm(status) != "done")
    )

blocked_tasks = [t for t in tasks if needs_real_input(t)]

# Existing evidence for this opportunity.
try:
    ev_resp = (
        supabase.table("project_evidence_inputs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    )
    evidence_rows = ev_resp.data or []
except Exception as exc:
    st.error(f"Nu am putut încărca project_evidence_inputs: {exc}")
    st.stop()

latest_by_task = {}
for row in evidence_rows:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_by_task:
        latest_by_task[tid] = row

completed = 0
verified = 0
for task in blocked_tasks:
    row = latest_by_task.get(str(task.get("id")))
    if row and str(row.get("value_text") or "").strip():
        completed += 1
        if row.get("verification_status") in ("User confirmed", "Officially verified"):
            verified += 1

c1, c2, c3, c4 = st.columns(4)
c1.metric("Necesită input/verificare", len(blocked_tasks))
c2.metric("Cu date salvate", completed)
c3.metric("Confirmate/verificate", verified)
c4.metric("Lipsă", max(len(blocked_tasks) - completed, 0))

st.info(
    "Completează numai informații reale. Pentru date provenite din documente sau surse oficiale, "
    "notează sursa. «User confirmed» înseamnă că tu confirmi informația; "
    "«Officially verified» trebuie folosit numai când ai verificat-o într-o sursă oficială."
)

st.subheader("Cerințe de completat")

if not blocked_tasks:
    st.success("Nu există task-uri care necesită input sau verificare.")
else:
    for pos, task in enumerate(blocked_tasks):
        task_id = str(task.get("id"))
        category = str(first_value(task, ["category"], "Other"))
        title = str(first_value(task, ["task", "title", "issue", "description"], "Cerință"))
        request = str(first_value(task, [
            "required_input", "input_request", "reason", "description",
            "proposed_resolution"
        ], "Furnizează informația reală necesară pentru această cerință."))

        previous = latest_by_task.get(task_id, {})
        with st.expander(f"📌 {category} — {title}", expanded=(pos == 0)):
            st.write(request)

            field_name = st.text_input(
                "Câmp / tip informație",
                value=str(previous.get("field_name") or category),
                key=f"field_{pos}_{task_id}",
            )
            value_text = st.text_area(
                "Informația / dovada",
                value=str(previous.get("value_text") or ""),
                height=180,
                placeholder="Introdu aici informația reală. Nu folosi valori presupuse.",
                key=f"value_{pos}_{task_id}",
            )
            source_type = st.selectbox(
                "Tip sursă",
                ["user_input", "document", "official_source", "project_data", "manual"],
                index=(
                    ["user_input", "document", "official_source", "project_data", "manual"].index(
                        previous.get("source_type")
                    )
                    if previous.get("source_type") in
                    ["user_input", "document", "official_source", "project_data", "manual"]
                    else 0
                ),
                key=f"source_type_{pos}_{task_id}",
            )
            source_reference = st.text_input(
                "Referință sursă",
                value=str(previous.get("source_reference") or ""),
                placeholder="Ex.: numele documentului, pagina, portalul oficial sau nota internă",
                key=f"source_ref_{pos}_{task_id}",
            )
            verification_status = st.selectbox(
                "Status verificare",
                ["Unverified", "User confirmed", "Officially verified", "Rejected"],
                index=(
                    ["Unverified", "User confirmed", "Officially verified", "Rejected"].index(
                        previous.get("verification_status")
                    )
                    if previous.get("verification_status") in
                    ["Unverified", "User confirmed", "Officially verified", "Rejected"]
                    else 0
                ),
                key=f"verify_{pos}_{task_id}",
            )
            confidence = st.selectbox(
                "Încredere",
                ["High", "Medium", "Low"],
                index=(
                    ["High", "Medium", "Low"].index(previous.get("confidence"))
                    if previous.get("confidence") in ["High", "Medium", "Low"]
                    else 1
                ),
                key=f"confidence_{pos}_{task_id}",
            )
            notes = st.text_area(
                "Note",
                value=str(previous.get("notes") or ""),
                height=90,
                key=f"notes_{pos}_{task_id}",
            )

            if st.button("💾 Salvează informația", key=f"save_{pos}_{task_id}", use_container_width=True):
                if not value_text.strip():
                    st.error("Introdu informația sau dovada înainte de salvare.")
                elif verification_status == "Officially verified" and not source_reference.strip():
                    st.error("Pentru «Officially verified» trebuie să introduci referința sursei oficiale.")
                else:
                    payload = {
                        "user_id": user_id,
                        "project_id": project_id,
                        "opportunity_identity": opportunity_identity,
                        "resolution_task_id": task_id,
                        "category": category,
                        "field_name": field_name.strip() or category,
                        "field_label": title,
                        "value_text": value_text.strip(),
                        "value_json": {},
                        "source_type": source_type,
                        "source_reference": source_reference.strip(),
                        "verification_status": verification_status,
                        "confidence": confidence,
                        "notes": notes.strip(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    try:
                        # Keep an audit trail by inserting a new evidence version.
                        saved = supabase.table("project_evidence_inputs").insert(payload).execute()
                        saved_id = (saved.data or [{}])[0].get("id", "")
                        st.success(f"Informație salvată. ID: {str(saved_id)[:8]}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Nu am putut salva informația: {exc}")

            if previous:
                st.caption(
                    f"Ultima versiune: {previous.get('verification_status', 'Unverified')} · "
                    f"sursă: {previous.get('source_type', '—')}"
                )

st.divider()
st.subheader("Sincronizare cu Etapa 19")

st.write(
    "Task-urile pot fi deblocate numai dacă există informație salvată și aceasta este "
    "confirmată sau verificată. Datele neconfirmate rămân blocate."
)

if st.button("🔄 Actualizează task-urile confirmate", use_container_width=True):
    changed = 0
    try:
        fresh = (
            supabase.table("project_evidence_inputs")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .execute()
        ).data or []

        newest = {}
        for row in fresh:
            tid = str(row.get("resolution_task_id") or "")
            if tid and tid not in newest:
                newest[tid] = row

        for task in blocked_tasks:
            tid = str(task.get("id"))
            row = newest.get(tid)
            if not row:
                continue
            if (
                str(row.get("value_text") or "").strip()
                and row.get("verification_status") in ("User confirmed", "Officially verified")
            ):
                # Preserve the original task; only move it out of Waiting input.
                supabase.table("resolution_tasks").update({
                    "status": "Input provided"
                }).eq("id", tid).eq("user_id", user_id).execute()
                changed += 1

        run_payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "total_requirements": len(blocked_tasks),
            "completed_requirements": sum(
                1 for t in blocked_tasks
                if str((newest.get(str(t.get("id"))) or {}).get("value_text") or "").strip()
            ),
            "verified_requirements": sum(
                1 for t in blocked_tasks
                if (newest.get(str(t.get("id"))) or {}).get("verification_status")
                in ("User confirmed", "Officially verified")
            ),
            "missing_requirements": sum(
                1 for t in blocked_tasks
                if not str((newest.get(str(t.get("id"))) or {}).get("value_text") or "").strip()
            ),
            "summary": {"stage": 21, "tasks_updated": changed},
        }
        supabase.table("evidence_input_runs").insert(run_payload).execute()
        st.success(f"Sincronizare finalizată. {changed} task-uri au primit status «Input provided».")
        st.rerun()
    except Exception as exc:
        st.error(f"Sincronizarea nu a putut fi finalizată: {exc}")

st.divider()
with st.expander("Istoric Evidence/Input"):
    try:
        hist = (
            supabase.table("project_evidence_inputs")
            .select("id,category,field_label,source_type,verification_status,confidence,created_at")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        ).data or []
        if hist:
            st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.caption("Nu există încă informații salvate.")
    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")

st.caption(
    "Etapa 21 este un registru de dovezi și inputuri. Nu transformă automat o informație "
    "neconfirmată într-un fapt și nu declară eligibilitatea fără verificare."
)
