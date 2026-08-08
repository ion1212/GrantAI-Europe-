import os
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Controlled Resolution Executor",
    page_icon="⚙️",
    layout="wide",
)

st.title("⚙️ Etapa 26 — Controlled Resolution Executor")
st.caption(
    "Execută numai modificările aprobate în Etapa 25. "
    "Păstrează audit trail, conținutul anterior și posibilitatea de rollback."
)


# ---------------------------------------------------------------------
# Secrets / authentication
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
# Approved execution plans from Stage 25
# ---------------------------------------------------------------------
try:
    plans = (
        supabase.table("resolution_execution_plans")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("plan_status", "Approved")
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca planurile aprobate din Etapa 25: {exc}")
    st.stop()

if not plans:
    st.warning(
        "Nu există planuri Approved în Etapa 25 pentru acest proiect. "
        "Aprobă mai întâi planul complet în AI Resolution Execution Planner."
    )
    st.stop()

plan_options = {
    f"{str(p.get('id'))[:8]} — {p.get('created_at', '')}": p
    for p in plans
}

selected_plan_label = st.selectbox(
    "Plan aprobat",
    list(plan_options.keys()),
)

plan = plan_options[selected_plan_label]
plan_id = str(plan["id"])
opportunity_identity = str(plan.get("opportunity_identity") or "")

st.text_input(
    "Oportunitate",
    value=opportunity_identity or "—",
    disabled=True,
)


# ---------------------------------------------------------------------
# Approved plan items
# ---------------------------------------------------------------------
try:
    plan_items = (
        supabase.table("resolution_execution_plan_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .eq("plan_id", plan_id)
        .order("created_at")
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca item-urile planului: {exc}")
    st.stop()

approved_items = [
    item for item in plan_items
    if bool(item.get("user_approved"))
    and norm(item.get("execution_status")) == "approved"
]

if not approved_items:
    st.warning(
        "Planul este Approved, dar nu există item-uri individuale aprobate pentru execuție."
    )
    st.stop()


# ---------------------------------------------------------------------
# Existing execution records
# ---------------------------------------------------------------------
try:
    execution_rows = (
        supabase.table("controlled_resolution_executions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .eq("execution_plan_id", plan_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    execution_rows = []

latest_execution_by_plan_item = {}
for row in execution_rows:
    pid = str(row.get("execution_plan_item_id") or "")
    if pid and pid not in latest_execution_by_plan_item:
        latest_execution_by_plan_item[pid] = row


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
applied_count = sum(
    1 for row in latest_execution_by_plan_item.values()
    if norm(row.get("execution_status")) == "applied"
)
rollback_count = sum(
    1 for row in latest_execution_by_plan_item.values()
    if norm(row.get("execution_status")) == "rolled back"
)
failed_count = sum(
    1 for row in latest_execution_by_plan_item.values()
    if norm(row.get("execution_status")) == "failed"
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Item-uri aprobate", len(approved_items))
c2.metric("Aplicate", applied_count)
c3.metric("Rollback", rollback_count)
c4.metric("Failed", failed_count)

st.info(
    "Etapa 26 nu execută nimic automat la încărcarea paginii. "
    "Fiecare modificare trebuie aplicată explicit prin butonul Apply."
)


# ---------------------------------------------------------------------
# Create / reuse run
# ---------------------------------------------------------------------
try:
    runs = (
        supabase.table("controlled_resolution_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .eq("execution_plan_id", plan_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    runs = []

active_run = None
for run in runs:
    if norm(run.get("run_status")) in ("pending", "running", "partially completed"):
        active_run = run
        break

if not active_run:
    try:
        run_insert = (
            supabase.table("controlled_resolution_runs")
            .insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_identity": opportunity_identity,
                "execution_plan_id": plan_id,
                "total_items": len(plan_items),
                "approved_items": len(approved_items),
                "executed_items": 0,
                "failed_items": 0,
                "rolled_back_items": 0,
                "run_status": "Pending",
                "summary": {
                    "stage": 26,
                    "plan_id": plan_id,
                },
            })
            .execute()
        )
        run_data = run_insert.data or []
        active_run = run_data[0] if run_data else None
    except Exception as exc:
        st.error(f"Nu am putut crea execution run: {exc}")
        st.stop()

if not active_run:
    st.error("Nu am putut inițializa execution run.")
    st.stop()

run_id = str(active_run["id"])


# ---------------------------------------------------------------------
# Apply helper
# ---------------------------------------------------------------------
def apply_change(original: str, approved_change: str, execution_type: str) -> str:
    original = original or ""
    approved_change = approved_change or ""
    execution_type = execution_type or "manual"

    if execution_type == "append":
        if not original.strip():
            return approved_change.strip()
        return original.rstrip() + "\n\n" + approved_change.strip()

    if execution_type in ("rewrite", "replace", "insert"):
        return approved_change.strip()

    return approved_change.strip()


# ---------------------------------------------------------------------
# Controlled execution UI
# ---------------------------------------------------------------------
st.subheader("Execuție controlată")

for pos, item in enumerate(approved_items):
    item_id = str(item.get("id"))
    latest_execution = latest_execution_by_plan_item.get(item_id, {})

    execution_status = str(
        latest_execution.get("execution_status") or "Pending"
    )

    category = str(item.get("category") or "Other")
    target_section = str(item.get("target_section") or "")
    execution_type = str(item.get("execution_type") or "manual")
    approved_change_default = str(
        item.get("proposed_change")
        or item.get("approved_resolution")
        or ""
    )
    original_default = str(item.get("current_content") or "")

    icon = {
        "Applied": "✅",
        "Rolled back": "↩️",
        "Failed": "❌",
        "Pending": "⏳",
    }.get(execution_status, "⏳")

    with st.expander(
        f"{icon} {category} — {target_section or 'Target section'} [{execution_status}]",
        expanded=(pos == 0),
    ):
        st.write(
            f"**Execution type:** `{execution_type}`  \n"
            f"**Resolution task:** `{str(item.get('resolution_task_id') or '')[:8]}`"
        )

        original_content = st.text_area(
            "Original content",
            value=str(
                latest_execution.get("original_content")
                if latest_execution
                else original_default
            ),
            height=170,
            key=f"stage26_original_{item_id}",
        )

        approved_change = st.text_area(
            "Approved change",
            value=str(
                latest_execution.get("approved_change")
                if latest_execution
                else approved_change_default
            ),
            height=200,
            key=f"stage26_change_{item_id}",
        )

        preview = apply_change(
            original_content,
            approved_change,
            execution_type,
        )

        st.text_area(
            "Preview applied content",
            value=preview,
            height=200,
            disabled=True,
            key=f"stage26_preview_{item_id}",
        )

        confirm = st.checkbox(
            "Confirm că vreau să aplic exact această modificare",
            key=f"stage26_confirm_{item_id}",
            disabled=execution_status == "Applied",
        )

        a1, a2 = st.columns(2)

        if a1.button(
            "✅ Apply",
            key=f"stage26_apply_{item_id}",
            use_container_width=True,
            disabled=(
                execution_status == "Applied"
                or not confirm
                or not approved_change.strip()
            ),
        ):
            try:
                applied_content = apply_change(
                    original_content,
                    approved_change,
                    execution_type,
                )

                payload = {
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "run_id": run_id,
                    "execution_plan_id": plan_id,
                    "execution_plan_item_id": item_id,
                    "resolution_task_id": item.get("resolution_task_id"),
                    "evidence_resolution_id": item.get("evidence_resolution_id"),
                    "category": category,
                    "target_section": target_section,
                    "execution_type": execution_type,
                    "original_content": original_content,
                    "approved_change": approved_change,
                    "applied_content": applied_content,
                    "execution_status": "Applied",
                    "error_message": None,
                    "rollback_content": original_content,
                    "user_approved": True,
                    "applied_at": now_iso(),
                    "metadata": {
                        "stage": 26,
                        "explicit_user_confirmation": True,
                    },
                    "updated_at": now_iso(),
                }

                if latest_execution and latest_execution.get("id"):
                    (
                        supabase.table("controlled_resolution_executions")
                        .update(payload)
                        .eq("id", latest_execution["id"])
                        .eq("user_id", user_id)
                        .execute()
                    )
                else:
                    (
                        supabase.table("controlled_resolution_executions")
                        .insert(payload)
                        .execute()
                    )

                try:
                    (
                        supabase.table("resolution_execution_plan_items")
                        .update({
                            "execution_status": "Executed",
                            "updated_at": now_iso(),
                        })
                        .eq("id", item_id)
                        .eq("user_id", user_id)
                        .execute()
                    )
                except Exception:
                    pass

                try:
                    if item.get("resolution_task_id"):
                        (
                            supabase.table("resolution_tasks")
                            .update({
                                "status": "Done",
                                "updated_at": now_iso(),
                            })
                            .eq("id", item["resolution_task_id"])
                            .eq("user_id", user_id)
                            .execute()
                        )
                except Exception:
                    pass

                st.success("Modificarea a fost aplicată și salvată în audit trail.")
                st.rerun()

            except Exception as exc:
                st.error(f"Aplicarea a eșuat: {exc}")

        can_rollback = execution_status == "Applied"

        if a2.button(
            "↩️ Rollback",
            key=f"stage26_rollback_{item_id}",
            use_container_width=True,
            disabled=not can_rollback,
        ):
            try:
                rollback_content = str(
                    latest_execution.get("rollback_content")
                    or latest_execution.get("original_content")
                    or ""
                )

                (
                    supabase.table("controlled_resolution_executions")
                    .update({
                        "applied_content": rollback_content,
                        "execution_status": "Rolled back",
                        "rolled_back_at": now_iso(),
                        "updated_at": now_iso(),
                    })
                    .eq("id", latest_execution["id"])
                    .eq("user_id", user_id)
                    .execute()
                )

                try:
                    (
                        supabase.table("resolution_execution_plan_items")
                        .update({
                            "execution_status": "Approved",
                            "updated_at": now_iso(),
                        })
                        .eq("id", item_id)
                        .eq("user_id", user_id)
                        .execute()
                    )
                except Exception:
                    pass

                try:
                    if item.get("resolution_task_id"):
                        (
                            supabase.table("resolution_tasks")
                            .update({
                                "status": "Proposed",
                                "updated_at": now_iso(),
                            })
                            .eq("id", item["resolution_task_id"])
                            .eq("user_id", user_id)
                            .execute()
                        )
                except Exception:
                    pass

                st.success("Rollback efectuat. Conținutul anterior a fost restaurat în audit trail.")
                st.rerun()

            except Exception as exc:
                st.error(f"Rollback-ul a eșuat: {exc}")


# ---------------------------------------------------------------------
# Recalculate run status
# ---------------------------------------------------------------------
try:
    fresh_execs = (
        supabase.table("controlled_resolution_executions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .eq("execution_plan_id", plan_id)
        .execute()
    ).data or []

    executed_items = sum(
        1 for r in fresh_execs
        if norm(r.get("execution_status")) == "applied"
    )
    failed_items = sum(
        1 for r in fresh_execs
        if norm(r.get("execution_status")) == "failed"
    )
    rolled_back_items = sum(
        1 for r in fresh_execs
        if norm(r.get("execution_status")) == "rolled back"
    )

    if executed_items == len(approved_items) and len(approved_items) > 0:
        run_status = "Completed"
        completed_at = now_iso()
    elif executed_items > 0:
        run_status = "Partially completed"
        completed_at = None
    elif failed_items > 0:
        run_status = "Failed"
        completed_at = None
    else:
        run_status = "Pending"
        completed_at = None

    update_payload = {
        "executed_items": executed_items,
        "failed_items": failed_items,
        "rolled_back_items": rolled_back_items,
        "run_status": run_status,
        "summary": {
            "stage": 26,
            "approved_items": len(approved_items),
            "executed_items": executed_items,
            "failed_items": failed_items,
            "rolled_back_items": rolled_back_items,
        },
    }

    if completed_at:
        update_payload["completed_at"] = completed_at

    (
        supabase.table("controlled_resolution_runs")
        .update(update_payload)
        .eq("id", run_id)
        .eq("user_id", user_id)
        .execute()
    )

except Exception:
    pass


# ---------------------------------------------------------------------
# Final status / history
# ---------------------------------------------------------------------
st.divider()
st.subheader("Execution status")

try:
    current_run = (
        supabase.table("controlled_resolution_runs")
        .select("*")
        .eq("id", run_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    ).data or []
    current_run = current_run[0] if current_run else active_run
except Exception:
    current_run = active_run

s1, s2, s3, s4 = st.columns(4)
s1.metric("Run status", str(current_run.get("run_status") or "Pending"))
s2.metric("Approved", int(current_run.get("approved_items") or 0))
s3.metric("Executed", int(current_run.get("executed_items") or 0))
s4.metric("Rolled back", int(current_run.get("rolled_back_items") or 0))

with st.expander("Istoric Controlled Executions"):
    try:
        history = (
            supabase.table("controlled_resolution_executions")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .eq("execution_plan_id", plan_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        ).data or []

        if history:
            display_cols = [
                "id",
                "category",
                "target_section",
                "execution_type",
                "execution_status",
                "applied_at",
                "rolled_back_at",
                "created_at",
            ]
            st.dataframe(
                [
                    {key: row.get(key) for key in display_cols}
                    for row in history
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Nu există încă execuții salvate.")

    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")

st.caption(
    "Etapa 26 execută numai item-uri aprobate în Etapa 25 și confirmate explicit în această pagină. "
    "Rollback-ul păstrează audit trail-ul și readuce task-ul la status Proposed."
)
