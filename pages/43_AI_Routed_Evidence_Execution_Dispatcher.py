import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Routed Evidence Execution Dispatcher",
    page_icon="🚚",
    layout="wide",
)

st.title("🚚 Etapa 43 — AI Routed Evidence Execution Dispatcher")
st.caption(
    "Transformă rutele READY din Etapa 41/42 în task-uri de execuție controlate pentru "
    "modulele destinație. Nu declară dovezi rezolvate doar pentru că task-ul a fost dispatch-uit."
)

NEXT_MODULE = "AI Evidence Resolution Completion Gate"

ROUTE_WAITING_STATUS = {
    "OFFICIAL_VERIFICATION": "WAITING_OFFICIAL",
    "USER_EVIDENCE": "WAITING_USER",
    "EVIDENCE_RESOLVER": "WAITING_RESOLVER",
    "READY_NEXT": "COMPLETED",
    "RETURN_TO_STAGE39": "BLOCKED",
    "BLOCKED": "BLOCKED",
}


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


def task_instruction(route_item: dict) -> str:
    route_type = normalize_text(route_item.get("route_type")).upper()
    label = normalize_text(route_item.get("requirement_label"))
    action = normalize_text(route_item.get("required_action"))

    if route_type == "OFFICIAL_VERIFICATION":
        return (
            f"Verify the official call requirement for '{label}' using only official "
            f"call documentation/source data. Do not infer missing rules. {action}"
        )

    if route_type == "USER_EVIDENCE":
        return (
            f"Collect factual applicant evidence for '{label}'. Require user confirmation "
            f"before the evidence is accepted. {action}"
        )

    if route_type == "EVIDENCE_RESOLVER":
        return (
            f"Resolve the remaining evidence gap for '{label}' from stored evidence or "
            f"explicitly classify it as still unresolved. Do not invent facts. {action}"
        )

    if route_type == "READY_NEXT":
        return (
            f"Requirement '{label}' is already validated and may continue downstream."
        )

    if route_type == "RETURN_TO_STAGE39":
        return (
            f"Return '{label}' to Stage 39 because the prior resolution was invalid. {action}"
        )

    return f"Stop processing '{label}' because the route is blocked. {action}"


def downstream_completion_from_handoff(handoff: dict | None) -> tuple[bool, str, str, dict]:
    if handoff is None:
        return False, "", "", {}

    payload = as_dict(handoff.get("payload"))

    terminal_keys = (
        "completion_status",
        "result_status",
        "validation_status",
        "resolution_status",
        "run_status",
        "status",
    )
    terminal_good = {
        "COMPLETED",
        "PASS",
        "RESOLVED",
        "VALID",
        "VERIFIED",
        "APPROVED",
    }

    for key in terminal_keys:
        value = normalize_text(payload.get(key)).upper()
        if value in terminal_good:
            return True, f"handoff.payload.{key}", value, payload

    return False, "", "", payload


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

orchestration_runs = rows(
    "locked_evidence_orchestration_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

orchestration_run = next(
    (
        r for r in orchestration_runs
        if normalize_text(r.get("orchestration_status")).upper()
        in {"WAITING", "COMPLETED", "BLOCKED"}
    ),
    None,
)

if not orchestration_run:
    st.warning("Nu există rezultat Etapa 42 disponibil pentru dispatch.")
    st.stop()

orchestration_run_id = str(orchestration_run["id"])
routing_run_id = str(orchestration_run["routing_run_id"])
validation_run_id = str(orchestration_run["validation_run_id"])
resolution_run_id = str(orchestration_run["resolution_run_id"])
requirement_run_id = str(orchestration_run["requirement_run_id"])

routing_items = rows(
    "locked_evidence_routing_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "routing_run_id": routing_run_id,
    },
    "created_at",
    500,
)

orchestration_items = rows(
    "locked_evidence_orchestration_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "orchestration_run_id": orchestration_run_id,
    },
    "created_at",
    500,
)

orch_by_route = {
    str(x.get("routing_item_id")): x
    for x in orchestration_items
}

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

handoff_by_destination = {}
for h in handoffs:
    dest = normalize_text(h.get("destination_module"))
    if dest and dest not in handoff_by_destination:
        handoff_by_destination[dest] = h

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 42", normalize_text(orchestration_run.get("orchestration_status")) or "—")
c4.metric("Routes", len(routing_items))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(routing_items)
)

if not hard_gate_ok:
    st.error(
        "Etapa 43 este BLOCKED: se cere lock ACTIVE, workflow_allowed=true, "
        "deadline viitor și rute Etapa 41/42 pentru același lock."
    )
    st.stop()

st.success("Hard gate Etapa 43: PASS. Task-urile pot fi dispatch-uite controlat.")

preview = []
for item in routing_items:
    destination = normalize_text(item.get("destination_module"))
    handoff = handoff_by_destination.get(destination)
    route_type = normalize_text(item.get("route_type")).upper()
    task_status = ROUTE_WAITING_STATUS.get(route_type, "PENDING")
    completed, source, ref, _ = downstream_completion_from_handoff(handoff)

    if completed:
        task_status = "COMPLETED"

    preview.append({
        "Requirement": item.get("requirement_label"),
        "Route": route_type,
        "Destination": destination,
        "Handoff": normalize_text(handoff.get("handoff_status")) if handoff else "MISSING",
        "Task status": task_status,
        "Instruction": task_instruction(item),
    })

st.subheader("Execution preview")
st.dataframe(preview, use_container_width=True, hide_index=True)

prior_runs = rows(
    "locked_evidence_execution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "orchestration_run_id": orchestration_run_id,
    },
    "created_at",
    50,
)

if prior_runs:
    latest_prior = prior_runs[0]
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Latest execution", latest_prior.get("execution_status") or "—")
    p2.metric("Completed", latest_prior.get("completed_tasks") or 0)
    p3.metric("Official/User", (latest_prior.get("waiting_official_tasks") or 0) + (latest_prior.get("waiting_user_tasks") or 0))
    p4.metric("Resolver", latest_prior.get("waiting_resolver_tasks") or 0)

confirm = st.checkbox(
    "Confirm că Etapa 43 trebuie să transforme rutele READY în task-uri controlate pentru modulele destinație."
)

if st.button(
    "🚚 Dispatch routed evidence tasks",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    execution_run_id = None

    try:
        run_insert = (
            supabase.table("locked_evidence_execution_runs")
            .insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "orchestration_run_id": orchestration_run_id,
                "opportunity_identity": identity,
                "total_tasks": len(routing_items),
                "execution_status": "RUNNING",
                "started_at": now_iso(),
                "summary": {
                    "stage": 43,
                    "project_name": project_name,
                    "stage42_run_id": orchestration_run_id,
                },
                "updated_at": now_iso(),
            })
            .execute()
        ).data or []

        if not run_insert:
            raise RuntimeError("Nu am putut crea execution run Etapa 43.")

        execution_run_id = str(run_insert[0]["id"])

        counters = {
            "dispatched_tasks": 0,
            "completed_tasks": 0,
            "waiting_user_tasks": 0,
            "waiting_official_tasks": 0,
            "waiting_resolver_tasks": 0,
            "blocked_tasks": 0,
            "failed_tasks": 0,
        }

        progress = st.progress(0)

        for idx, item in enumerate(routing_items, start=1):
            destination = normalize_text(item.get("destination_module"))
            handoff = handoff_by_destination.get(destination)
            route_type = normalize_text(item.get("route_type")).upper()

            task_status = ROUTE_WAITING_STATUS.get(route_type, "PENDING")
            completed, completion_source, completion_reference, completion_payload = (
                downstream_completion_from_handoff(handoff)
            )

            if completed:
                task_status = "COMPLETED"

            if task_status == "COMPLETED":
                counters["completed_tasks"] += 1
            elif task_status == "WAITING_USER":
                counters["waiting_user_tasks"] += 1
            elif task_status == "WAITING_OFFICIAL":
                counters["waiting_official_tasks"] += 1
            elif task_status == "WAITING_RESOLVER":
                counters["waiting_resolver_tasks"] += 1
            elif task_status == "BLOCKED":
                counters["blocked_tasks"] += 1
            elif task_status == "FAILED":
                counters["failed_tasks"] += 1
            else:
                counters["dispatched_tasks"] += 1

            input_payload = {
                "stage": 43,
                "execution_run_id": execution_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "requirement": {
                    "id": item.get("requirement_id"),
                    "key": item.get("requirement_key"),
                    "category": item.get("requirement_category"),
                    "label": item.get("requirement_label"),
                },
                "route": {
                    "type": route_type,
                    "destination_module": destination,
                    "required_action": item.get("required_action"),
                },
                "task_instruction": task_instruction(item),
                "source_handoff": {
                    "id": handoff.get("id") if handoff else None,
                    "status": handoff.get("handoff_status") if handoff else None,
                },
                "created_at": now_iso(),
            }

            orch_item = orch_by_route.get(str(item.get("id")))

            supabase.table("locked_evidence_execution_tasks").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "orchestration_run_id": orchestration_run_id,
                "execution_run_id": execution_run_id,
                "routing_item_id": item["id"],
                "orchestration_item_id": orch_item.get("id") if orch_item else None,
                "requirement_id": item["requirement_id"],
                "opportunity_identity": identity,
                "requirement_key": item.get("requirement_key"),
                "requirement_category": item.get("requirement_category"),
                "requirement_label": item.get("requirement_label"),
                "route_type": route_type,
                "destination_module": destination,
                "task_status": task_status,
                "source_handoff_id": handoff.get("id") if handoff else None,
                "source_handoff_status": handoff.get("handoff_status") if handoff else None,
                "task_instruction": task_instruction(item),
                "required_action": item.get("required_action"),
                "input_payload": input_payload,
                "completion_payload": completion_payload if completed else {},
                "completion_status": completion_reference if completed else None,
                "completion_source": completion_source if completed else None,
                "completion_reference": completion_reference if completed else None,
                "dispatched_at": now_iso(),
                "completed_at": now_iso() if completed else None,
                "metadata": {
                    "stage": 43,
                    "route_status": item.get("route_status"),
                },
                "updated_at": now_iso(),
            }).execute()

            # Mark source route handoff as consumed only when it is actually READY.
            if handoff and normalize_text(handoff.get("handoff_status")).upper() == "READY":
                supabase.table("selected_opportunity_handoffs").update({
                    "handoff_status": "CONSUMED",
                    "consumed_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", handoff["id"]).eq("user_id", user_id).execute()

            progress.progress(idx / len(routing_items))

        waiting = (
            counters["waiting_user_tasks"]
            + counters["waiting_official_tasks"]
            + counters["waiting_resolver_tasks"]
            + counters["dispatched_tasks"]
        )

        if counters["blocked_tasks"] > 0:
            final_status = "BLOCKED"
        elif counters["failed_tasks"] > 0:
            final_status = "FAILED"
        elif counters["completed_tasks"] == len(routing_items):
            final_status = "COMPLETED"
        elif waiting > 0:
            final_status = "WAITING"
        else:
            final_status = "DISPATCHED"

        summary = {
            "stage": 43,
            "project_name": project_name,
            "opportunity_identity": identity,
            "stage42_run_id": orchestration_run_id,
            "next_action": (
                "STOP_AND_RESOLVE_BLOCKERS"
                if final_status == "BLOCKED"
                else "REPAIR_FAILED_TASKS"
                if final_status == "FAILED"
                else "CONTINUE_TO_COMPLETION_GATE"
                if final_status == "COMPLETED"
                else "WAIT_FOR_TASK_EXECUTION"
            ),
        }

        supabase.table("locked_evidence_execution_runs").update({
            **counters,
            "execution_status": final_status,
            "summary": summary,
            "completed_at": now_iso() if final_status in {"COMPLETED", "BLOCKED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", execution_run_id).eq("user_id", user_id).execute()

        # Stage 44 handoff is created only when all tasks have explicit completion.
        if final_status == "COMPLETED":
            existing = next(
                (
                    h for h in handoffs
                    if normalize_text(h.get("destination_module")) == NEXT_MODULE
                ),
                None,
            )

            handoff_payload = {
                "stage": 43,
                "execution_run_id": execution_run_id,
                "orchestration_run_id": orchestration_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "execution_status": final_status,
                "summary": summary,
                "created_at": now_iso(),
            }

            if existing:
                supabase.table("selected_opportunity_handoffs").update({
                    "handoff_status": "READY",
                    "payload": handoff_payload,
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
                    "payload": handoff_payload,
                    "updated_at": now_iso(),
                }).execute()

        st.success(
            f"Etapa 43 finalizată: {final_status}. "
            f"Completed={counters['completed_tasks']}, "
            f"Official={counters['waiting_official_tasks']}, "
            f"User={counters['waiting_user_tasks']}, "
            f"Resolver={counters['waiting_resolver_tasks']}."
        )
        st.rerun()

    except Exception as exc:
        if execution_run_id:
            try:
                supabase.table("locked_evidence_execution_runs").update({
                    "execution_status": "FAILED",
                    "summary": {
                        "stage": 43,
                        "error": str(exc)[:4000],
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", execution_run_id).eq("user_id", user_id).execute()
            except Exception:
                pass

        st.error(f"Etapa 43 nu a putut dispatch-ui task-urile: {exc}")


execution_runs = rows(
    "locked_evidence_execution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "orchestration_run_id": orchestration_run_id,
    },
    "created_at",
    50,
)

if execution_runs:
    latest = execution_runs[0]

    st.divider()
    st.subheader("Latest Stage 43 Result")

    a, b, c, d, e = st.columns(5)
    a.metric("Status", latest.get("execution_status") or "—")
    b.metric("Completed", latest.get("completed_tasks") or 0)
    c.metric("Official", latest.get("waiting_official_tasks") or 0)
    d.metric("User evidence", latest.get("waiting_user_tasks") or 0)
    e.metric("Resolver", latest.get("waiting_resolver_tasks") or 0)

    tasks = rows(
        "locked_evidence_execution_tasks",
        {
            "user_id": user_id,
            "project_id": project_id,
            "execution_run_id": str(latest["id"]),
        },
        "created_at",
        500,
    )

    if tasks:
        st.dataframe(
            [
                {
                    "Requirement": t.get("requirement_label"),
                    "Route": t.get("route_type"),
                    "Destination": t.get("destination_module"),
                    "Task status": t.get("task_status"),
                    "Source handoff": t.get("source_handoff_status"),
                    "Instruction": t.get("task_instruction"),
                }
                for t in tasks
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Execution details")
        for task in tasks:
            with st.expander(
                f"{task.get('requirement_label')} — {task.get('task_status')}"
            ):
                st.write(f"**Destination:** {task.get('destination_module')}")
                st.write(f"**Instruction:** {task.get('task_instruction') or '—'}")
                st.write(f"**Required action:** {task.get('required_action') or '—'}")
                st.write(f"**Completion:** {task.get('completion_status') or '—'}")

    status = normalize_text(latest.get("execution_status")).upper()
    if status == "COMPLETED":
        st.success("Toate task-urile au finalizare downstream explicită.")
    elif status == "WAITING":
        st.warning(
            "Etapa 43 WAITING: task-urile sunt dispatch-uite, dar necesită încă "
            "official verification / user evidence / resolver execution."
        )
    elif status == "BLOCKED":
        st.error("Etapa 43 BLOCKED.")
    elif status == "FAILED":
        st.error("Etapa 43 FAILED.")

with st.expander("Istoric Etapa 43"):
    if execution_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "status": r.get("execution_status"),
                    "total": r.get("total_tasks"),
                    "completed": r.get("completed_tasks"),
                    "official": r.get("waiting_official_tasks"),
                    "user": r.get("waiting_user_tasks"),
                    "resolver": r.get("waiting_resolver_tasks"),
                    "blocked": r.get("blocked_tasks"),
                    "failed": r.get("failed_tasks"),
                }
                for r in execution_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 43.")

st.caption(
    "Invariantă Etapa 43: DISPATCHED/WAITING nu înseamnă RESOLVED. "
    "Finalizarea cere un rezultat downstream explicit și trasabil."
)
