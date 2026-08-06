import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client


# -----------------------------
# UI — ETAPA 20
# -----------------------------
st.set_page_config(page_title="AI Resolution Executor", page_icon="⚙️", layout="wide")

st.title("⚙️ Etapa 20 — AI Resolution Executor")
st.caption(
    "Transformă rezolvările propuse în modificări controlate. "
    "Nicio modificare nu este aplicată fără aprobare explicită."
)

# Reuse the authenticated session created by app.py.
def restore_auth_session(sb):
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

# Determine latest opportunity identity from resolution tasks/readiness.
opportunity_identity = ""
resolution_tasks = []
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
    if all_tasks:
        opportunity_identity = str(all_tasks[0].get("opportunity_identity") or "")
        resolution_tasks = [
            x for x in all_tasks
            if str(x.get("opportunity_identity") or "") == opportunity_identity
        ]
except Exception as exc:
    st.error(f"Nu am putut încărca resolution_tasks: {exc}")
    st.stop()

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

if not resolution_tasks:
    st.warning("Etapa 19 nu are încă task-uri de rezolvare pentru acest proiect.")
    st.stop()

def norm(v):
    return str(v or "").strip().lower()

def first_value(row, names, default=""):
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
    return default

def is_ai_executable(task):
    action = norm(first_value(task, [
        "resolution_type", "action_type", "task_type", "resolution_mode",
        "recommended_action", "action"
    ]))
    status = norm(first_value(task, ["status", "resolution_status"]))
    proposed = first_value(task, [
        "proposed_resolution", "proposed_text", "proposed_content",
        "resolution", "draft_resolution"
    ])
    # Stage 19 uses proposed/request_input/verify semantics. Keep this permissive
    # so existing rows remain usable across schema iterations.
    return (
        bool(str(proposed or "").strip())
        or "propos" in action
        or "draft" in action
        or "rewrite" in action
        or "generate" in action
        or "propos" in status
    ) and not (
        "input" in action or "verify" in action or "official" in action
        or "waiting input" in status
    )

eligible = [t for t in resolution_tasks if is_ai_executable(t)]
manual = [t for t in resolution_tasks if t not in eligible]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Task-uri Etapa 19", len(resolution_tasks))
c2.metric("AI executabile", len(eligible))
c3.metric("Necesită input/verificare", len(manual))

try:
    ex_resp = (
        supabase.table("resolution_executions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    )
    existing_executions = ex_resp.data or []
except Exception:
    existing_executions = []

c4.metric(
    "Aplicate",
    sum(1 for x in existing_executions if norm(x.get("execution_status")) == "applied")
)

if manual:
    st.info(
        f"{len(manual)} task-uri nu pot fi executate automat. "
        "Rămân blocate până la furnizarea sau verificarea datelor reale."
    )

st.subheader("Rezolvări executabile")

if not eligible:
    st.warning(
        "Nu există momentan rezolvări pe care AI le poate executa. "
        "Revino în Etapa 19 și generează planul de rezolvare."
    )
else:
    for pos, task in enumerate(eligible):
        task_id = str(task.get("id"))
        category = str(first_value(task, ["category"], "Other"))
        title = str(first_value(task, ["task", "title", "issue", "description"], "Rezolvare"))
        proposed_default = str(first_value(task, [
            "proposed_resolution", "proposed_text", "proposed_content",
            "resolution", "draft_resolution"
        ], ""))

        with st.expander(f"✨ {category} — {title}", expanded=(pos == 0)):
            target_section = st.text_input(
                "Secțiune țintă",
                value=str(first_value(task, ["target_section", "section", "category"], category)),
                key=f"target_{pos}_{task_id}",
            )

            original_content = st.text_area(
                "Conținut original",
                value="",
                height=160,
                help="Lipește aici textul actual al secțiunii înainte de aplicare.",
                key=f"original_{pos}_{task_id}",
            )

            proposed_content = st.text_area(
                "Rezolvare propusă",
                value=proposed_default,
                height=220,
                key=f"proposal_{pos}_{task_id}",
            )

            execution_type = st.selectbox(
                "Tip modificare",
                ["rewrite", "append", "replace"],
                key=f"type_{pos}_{task_id}",
            )

            st.warning(
                "Verifică textul înainte de aprobare. Executorul nu trebuie să "
                "inventeze bugete, parteneri, TRL, eligibilitate sau alte date factuale."
            )

            approve = st.checkbox(
                "Am verificat și aprob această modificare",
                key=f"approve_{pos}_{task_id}",
            )

            b1, b2 = st.columns(2)

            if b1.button(
                "💾 Salvează draftul",
                key=f"save_{pos}_{task_id}",
                use_container_width=True,
            ):
                payload = {
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "resolution_task_id": task_id,
                    "target_section": target_section,
                    "execution_type": execution_type,
                    "original_content": original_content,
                    "proposed_content": proposed_content,
                    "applied_content": "",
                    "execution_status": "Ready for approval" if proposed_content.strip() else "Draft",
                    "requires_confirmation": True,
                    "metadata": {
                        "category": category,
                        "task_title": title,
                        "stage": 20
                    },
                }
                try:
                    saved = supabase.table("resolution_executions").insert(payload).execute()
                    saved_id = (saved.data or [{}])[0].get("id", "")
                    st.success(f"Draft salvat. ID: {str(saved_id)[:8]}")
                except Exception as exc:
                    st.error(f"Nu am putut salva draftul: {exc}")

            if b2.button(
                "✅ Aprobă și aplică",
                key=f"apply_{pos}_{task_id}",
                use_container_width=True,
                disabled=not approve or not proposed_content.strip(),
            ):
                # Controlled execution: persist the approved change first.
                # Writer integration can consume Applied executions safely.
                payload = {
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "resolution_task_id": task_id,
                    "target_section": target_section,
                    "execution_type": execution_type,
                    "original_content": original_content,
                    "proposed_content": proposed_content,
                    "applied_content": proposed_content,
                    "execution_status": "Applied",
                    "requires_confirmation": True,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {
                        "category": category,
                        "task_title": title,
                        "stage": 20,
                        "explicit_user_approval": True
                    },
                }
                try:
                    saved = supabase.table("resolution_executions").insert(payload).execute()
                    saved_id = (saved.data or [{}])[0].get("id", "")

                    # Mark Stage 19 task done only after an approved execution is stored.
                    try:
                        supabase.table("resolution_tasks").update({
                            "status": "Done"
                        }).eq("id", task_id).eq("user_id", user_id).execute()
                    except Exception:
                        pass

                    st.success(
                        f"Modificarea a fost aprobată și înregistrată ca Applied. "
                        f"ID: {str(saved_id)[:8]}"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Aplicarea nu a putut fi salvată: {exc}")

st.divider()
st.subheader("Necesită input sau verificare")

if manual:
    for pos, task in enumerate(manual):
        category = str(first_value(task, ["category"], "Other"))
        title = str(first_value(task, ["task", "title", "issue", "description"], "Task"))
        with st.expander(f"🔒 {category} — {title}"):
            st.write(
                str(first_value(task, [
                    "required_input", "input_request", "reason",
                    "proposed_resolution", "description"
                ], "Este necesară informație reală sau verificare înainte de executare."))
            )
            st.caption(
                "Acest task nu este aplicat automat. Completează informația în Etapa 19 "
                "sau validează cerința în documentația oficială."
            )
else:
    st.success("Nu există task-uri care necesită input suplimentar.")

st.divider()
with st.expander("Istoric execuții"):
    try:
        hist = (
            supabase.table("resolution_executions")
            .select("id,target_section,execution_type,execution_status,created_at,applied_at")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        rows = hist.data or []
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Nu există încă execuții salvate.")
    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")

st.caption(
    "Etapa 20 aplică numai modificări aprobate explicit. "
    "Datele factuale neconfirmate rămân blocate."
)
