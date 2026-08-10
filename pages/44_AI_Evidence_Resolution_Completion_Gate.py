import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Evidence Resolution Completion Gate",
    page_icon="🏁",
    layout="wide",
)

st.title("🏁 Etapa 44 — AI Evidence Resolution Completion Gate")
st.caption(
    "Confirmă finalizarea reală a task-urilor Etapei 43. "
    "Nu consideră WAITING/DISPATCHED drept RESOLVED și nu schimbă oportunitatea blocată."
)

NEXT_MODULE = "AI Evidence Resolution Finalization"


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows(table: str, filters=None, order="created_at", limit=1000):
    q = supabase.table(table).select("*")
    for key, value in (filters or {}).items():
        if value not in (None, ""):
            q = q.eq(key, value)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


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


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


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


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def project_label(p: dict) -> str:
    return f"{p.get('name') or 'Project'} — {str(p.get('id') or '')[:8]}"


def inspect_task_completion(task: dict) -> dict:
    task_status = normalize_text(task.get("task_status")).upper()
    completion_payload = as_dict(task.get("completion_payload"))

    explicit_status = normalize_text(task.get("completion_status")).upper()

    good = {"COMPLETED", "PASS", "RESOLVED", "VALID", "VERIFIED", "APPROVED"}
    blocked = {"BLOCKED", "INELIGIBLE", "REJECTED"}
    failed = {"FAILED", "ERROR"}

    downstream_status = explicit_status
    downstream_source = normalize_text(task.get("completion_source"))
    downstream_reference = normalize_text(task.get("completion_reference"))

    # Conservative fallback to completion payload.
    if not downstream_status:
        for key in (
            "completion_status",
            "result_status",
            "validation_status",
            "resolution_status",
            "run_status",
            "status",
        ):
            value = normalize_text(completion_payload.get(key)).upper()
            if value:
                downstream_status = value
                downstream_source = downstream_source or f"completion_payload.{key}"
                downstream_reference = downstream_reference or value
                break

    evidence_present = bool(
        completion_payload
        or normalize_text(task.get("completion_source"))
        or normalize_text(task.get("completion_reference"))
    )

    provenance_valid = bool(
        downstream_source
        or downstream_reference
        or completion_payload
    )

    contradiction = False
    completion_item_status = "WAITING"
    reason = ""
    next_action = ""

    if downstream_status in blocked or task_status == "BLOCKED":
        completion_item_status = "BLOCKED"
        reason = f"Downstream/blocking status confirmed: {downstream_status or task_status}."
        next_action = "Resolve blocker before continuation."

    elif downstream_status in failed or task_status == "FAILED":
        completion_item_status = "FAILED"
        reason = f"Downstream failure confirmed: {downstream_status or task_status}."
        next_action = "Repair failed downstream task."

    elif downstream_status in good:
        if provenance_valid:
            completion_item_status = "COMPLETED"
            reason = f"Explicit downstream completion confirmed: {downstream_status}."
        else:
            completion_item_status = "INVALID"
            contradiction = True
            reason = "Completion claimed without traceable provenance."
            next_action = "Provide traceable downstream completion evidence."

    elif task_status == "COMPLETED":
        # Etapa 43 may have set COMPLETED only when explicit downstream completion existed.
        if evidence_present and provenance_valid:
            completion_item_status = "COMPLETED"
            reason = "Stage 43 completion is supported by stored downstream evidence."
        else:
            completion_item_status = "INVALID"
            contradiction = True
            reason = "Task is marked COMPLETED but completion evidence is missing."
            next_action = "Re-run/repair downstream completion capture."

    elif task_status in {"WAITING_USER", "WAITING_OFFICIAL", "WAITING_RESOLVER", "IN_PROGRESS", "DISPATCHED", "PENDING"}:
        completion_item_status = "WAITING"
        reason = f"Task is still {task_status}; no explicit downstream completion exists."
        next_action = normalize_text(task.get("required_action")) or "Complete the downstream task."

    else:
        completion_item_status = "WAITING"
        reason = f"Task has no terminal completion state: {task_status or 'EMPTY'}."
        next_action = "Complete or repair downstream execution."

    return {
        "downstream_status": downstream_status,
        "downstream_source": downstream_source,
        "downstream_reference": downstream_reference,
        "evidence_present": evidence_present,
        "provenance_valid": provenance_valid,
        "contradiction_detected": contradiction,
        "completion_item_status": completion_item_status,
        "completion_reason": reason,
        "required_next_action": next_action,
        "completion_payload": completion_payload,
    }


supabase = get_supabase()
restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Nu am putut identifica utilizatorul autentificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_label = st.selectbox("Project", list(project_map.keys()))
project = project_map[selected_label]
project_id = str(project["id"])
project_name = normalize_text(project.get("name"))

locks = rows(
    "selected_opportunity_locks",
    {
        "user_id": user_id,
        "project_id": project_id,
        "lock_status": "ACTIVE",
    },
    "created_at",
    10,
)

if not locks:
    st.warning("Nu există lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

execution_runs = rows(
    "locked_evidence_execution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

execution_run = next(
    (
        r for r in execution_runs
        if normalize_text(r.get("execution_status")).upper()
        in {"WAITING", "COMPLETED", "BLOCKED", "FAILED", "DISPATCHED"}
    ),
    None,
)

if not execution_run:
    st.warning("Nu există rezultat Etapa 43 disponibil pentru Completion Gate.")
    st.stop()

execution_run_id = str(execution_run["id"])
orchestration_run_id = str(execution_run["orchestration_run_id"])
routing_run_id = str(execution_run["routing_run_id"])
validation_run_id = str(execution_run["validation_run_id"])
resolution_run_id = str(execution_run["resolution_run_id"])
requirement_run_id = str(execution_run["requirement_run_id"])

tasks = rows(
    "locked_evidence_execution_tasks",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
    },
    "created_at",
    500,
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 43", normalize_text(execution_run.get("execution_status")) or "—")
c4.metric("Tasks", len(tasks))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(tasks)
)

if not hard_gate_ok:
    st.error(
        "Etapa 44 este BLOCKED: se cere lock ACTIVE, workflow_allowed=true, "
        "deadline viitor și task-uri Etapa 43 pentru același lock."
    )
    st.stop()

st.success("Hard gate Etapa 44: PASS. Completion Gate poate evalua task-urile.")

preview = []
for task in tasks:
    result = inspect_task_completion(task)
    preview.append({
        "Requirement": task.get("requirement_label"),
        "Task status": task.get("task_status"),
        "Destination": task.get("destination_module"),
        "Downstream": result["downstream_status"] or "—",
        "Completion": result["completion_item_status"],
        "Evidence": "YES" if result["evidence_present"] else "NO",
        "Provenance": "YES" if result["provenance_valid"] else "NO",
    })

st.subheader("Completion preview")
st.dataframe(preview, use_container_width=True, hide_index=True)

prior_runs = rows(
    "locked_evidence_completion_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
    },
    "created_at",
    50,
)

if prior_runs:
    prior = prior_runs[0]
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Latest completion", prior.get("completion_status") or "—")
    p2.metric("Completed", prior.get("completed_tasks") or 0)
    p3.metric("Waiting", prior.get("waiting_tasks") or 0)
    p4.metric("Blocked/Invalid", (prior.get("blocked_tasks") or 0) + (prior.get("invalid_tasks") or 0))

confirm = st.checkbox(
    "Confirm că Etapa 44 trebuie să valideze finalizarea explicită a task-urilor fără să forțeze RESOLVED."
)

if st.button(
    "🏁 Run Completion Gate",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    completion_run_id = None

    try:
        run_insert = (
            supabase.table("locked_evidence_completion_runs")
            .insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "orchestration_run_id": orchestration_run_id,
                "execution_run_id": execution_run_id,
                "opportunity_identity": identity,
                "total_tasks": len(tasks),
                "completion_status": "RUNNING",
                "started_at": now_iso(),
                "summary": {
                    "stage": 44,
                    "project_name": project_name,
                    "stage43_run_id": execution_run_id,
                },
                "updated_at": now_iso(),
            })
            .execute()
        ).data or []

        if not run_insert:
            raise RuntimeError("Nu am putut crea completion run Etapa 44.")

        completion_run_id = str(run_insert[0]["id"])

        counts = {
            "completed_tasks": 0,
            "waiting_tasks": 0,
            "blocked_tasks": 0,
            "failed_tasks": 0,
            "invalid_tasks": 0,
        }

        progress = st.progress(0)

        for idx, task in enumerate(tasks, start=1):
            result = inspect_task_completion(task)
            status = result["completion_item_status"]

            if status == "COMPLETED":
                counts["completed_tasks"] += 1
            elif status == "WAITING":
                counts["waiting_tasks"] += 1
            elif status == "BLOCKED":
                counts["blocked_tasks"] += 1
            elif status == "FAILED":
                counts["failed_tasks"] += 1
            elif status == "INVALID":
                counts["invalid_tasks"] += 1

            supabase.table("locked_evidence_completion_items").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "orchestration_run_id": orchestration_run_id,
                "execution_run_id": execution_run_id,
                "completion_run_id": completion_run_id,
                "execution_task_id": task["id"],
                "requirement_id": task["requirement_id"],
                "opportunity_identity": identity,
                "requirement_key": task.get("requirement_key"),
                "requirement_category": task.get("requirement_category"),
                "requirement_label": task.get("requirement_label"),
                "task_status": task.get("task_status"),
                "destination_module": task.get("destination_module"),
                "route_type": task.get("route_type"),
                "downstream_status": result["downstream_status"],
                "downstream_source": result["downstream_source"],
                "downstream_reference": result["downstream_reference"],
                "evidence_present": result["evidence_present"],
                "provenance_valid": result["provenance_valid"],
                "contradiction_detected": result["contradiction_detected"],
                "completion_item_status": status,
                "completion_reason": result["completion_reason"],
                "required_next_action": result["required_next_action"],
                "completion_payload": result["completion_payload"],
                "metadata": {
                    "stage": 44,
                    "source_task_status": task.get("task_status"),
                },
                "completed_at": now_iso() if status == "COMPLETED" else None,
                "updated_at": now_iso(),
            }).execute()

            progress.progress(idx / len(tasks))

        if counts["blocked_tasks"] > 0:
            final_status = "BLOCKED"
        elif counts["failed_tasks"] > 0:
            final_status = "FAILED"
        elif counts["invalid_tasks"] > 0:
            final_status = "FAILED"
        elif counts["completed_tasks"] == len(tasks):
            final_status = "PASS"
        else:
            final_status = "WAITING"

        summary = {
            "stage": 44,
            "project_name": project_name,
            "opportunity_identity": identity,
            "stage43_run_id": execution_run_id,
            "next_action": (
                "CONTINUE_TO_FINALIZATION"
                if final_status == "PASS"
                else "STOP_AND_RESOLVE_BLOCKERS"
                if final_status == "BLOCKED"
                else "REPAIR_FAILED_COMPLETIONS"
                if final_status == "FAILED"
                else "WAIT_FOR_DOWNSTREAM_COMPLETION"
            ),
        }

        supabase.table("locked_evidence_completion_runs").update({
            **counts,
            "completion_status": final_status,
            "summary": summary,
            "completed_at": now_iso() if final_status in {"PASS", "BLOCKED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", completion_run_id).eq("user_id", user_id).execute()

        # Only PASS may create the next handoff.
        if final_status == "PASS":
            handoffs = rows(
                "selected_opportunity_handoffs",
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                },
                "created_at",
                1000,
            )

            existing = next(
                (
                    h for h in handoffs
                    if normalize_text(h.get("destination_module")) == NEXT_MODULE
                ),
                None,
            )

            payload = {
                "stage": 44,
                "completion_run_id": completion_run_id,
                "execution_run_id": execution_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "completion_status": final_status,
                "summary": summary,
                "created_at": now_iso(),
            }

            if existing:
                supabase.table("selected_opportunity_handoffs").update({
                    "handoff_status": "READY",
                    "payload": payload,
                    "consumed_at": None,
                    "updated_at": now_iso(),
                }).eq("id", existing["id"]).eq("user_id", user_id).execute()
            else:
                supabase.table("selected_opportunity_handoffs").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                    "opportunity_identity": identity,
                    "destination_module": NEXT_MODULE,
                    "handoff_status": "READY",
                    "payload": payload,
                    "updated_at": now_iso(),
                }).execute()

        st.success(
            f"Etapa 44 finalizată: {final_status}. "
            f"Completed={counts['completed_tasks']}, "
            f"Waiting={counts['waiting_tasks']}, "
            f"Blocked={counts['blocked_tasks']}, "
            f"Invalid={counts['invalid_tasks']}."
        )
        st.rerun()

    except Exception as exc:
        if completion_run_id:
            try:
                supabase.table("locked_evidence_completion_runs").update({
                    "completion_status": "FAILED",
                    "summary": {
                        "stage": 44,
                        "error": str(exc)[:4000],
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", completion_run_id).eq("user_id", user_id).execute()
            except Exception:
                pass

        st.error(f"Etapa 44 nu a putut finaliza Completion Gate: {exc}")

completion_runs = rows(
    "locked_evidence_completion_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
    },
    "created_at",
    50,
)

if completion_runs:
    latest = completion_runs[0]

    st.divider()
    st.subheader("Latest Stage 44 Result")

    a, b, c, d, e = st.columns(5)
    a.metric("Status", latest.get("completion_status") or "—")
    b.metric("Completed", latest.get("completed_tasks") or 0)
    c.metric("Waiting", latest.get("waiting_tasks") or 0)
    d.metric("Blocked", latest.get("blocked_tasks") or 0)
    e.metric("Invalid", latest.get("invalid_tasks") or 0)

    items = rows(
        "locked_evidence_completion_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "completion_run_id": str(latest["id"]),
        },
        "created_at",
        500,
    )

    if items:
        st.dataframe(
            [
                {
                    "Requirement": i.get("requirement_label"),
                    "Task": i.get("task_status"),
                    "Downstream": i.get("downstream_status"),
                    "Completion": i.get("completion_item_status"),
                    "Evidence": i.get("evidence_present"),
                    "Provenance": i.get("provenance_valid"),
                    "Next action": i.get("required_next_action"),
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Completion details")
        for item in items:
            with st.expander(
                f"{item.get('requirement_label')} — {item.get('completion_item_status')}"
            ):
                st.write(f"**Reason:** {item.get('completion_reason') or '—'}")
                st.write(f"**Downstream status:** {item.get('downstream_status') or '—'}")
                st.write(f"**Source:** {item.get('downstream_source') or '—'}")
                st.write(f"**Reference:** {item.get('downstream_reference') or '—'}")
                st.write(f"**Next action:** {item.get('required_next_action') or '—'}")

    status = normalize_text(latest.get("completion_status")).upper()
    if status == "PASS":
        st.success("Etapa 44 PASS: toate task-urile au finalizare explicită și trasabilă.")
    elif status == "WAITING":
        st.warning(
            "Etapa 44 WAITING: unul sau mai multe task-uri nu au încă finalizare downstream explicită."
        )
    elif status == "BLOCKED":
        st.error("Etapa 44 BLOCKED.")
    elif status == "FAILED":
        st.error("Etapa 44 FAILED/INVALID.")

with st.expander("Istoric Etapa 44"):
    if completion_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "status": r.get("completion_status"),
                    "total": r.get("total_tasks"),
                    "completed": r.get("completed_tasks"),
                    "waiting": r.get("waiting_tasks"),
                    "blocked": r.get("blocked_tasks"),
                    "failed": r.get("failed_tasks"),
                    "invalid": r.get("invalid_tasks"),
                }
                for r in completion_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 44.")

st.caption(
    "Invariantă Etapa 44: PASS este permis numai dacă toate task-urile au finalizare downstream explicită, "
    "cu evidence/provenance suficientă. WAITING nu este echivalent cu RESOLVED."
)
