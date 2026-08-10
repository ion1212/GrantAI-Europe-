import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Routed Evidence Resolution Orchestrator",
    page_icon="🛰️",
    layout="wide",
)

st.title("🛰️ Etapa 42 — AI Routed Evidence Resolution Orchestrator")
st.caption(
    "Coordonează rutele create în Etapa 41 și urmărește dacă fiecare gap a ajuns "
    "la modulul corect. Nu rezolvă singur dovezile și nu schimbă oportunitatea blocată."
)

HANDOFF_FROM_STAGE41 = "AI Evidence Resolution Routing"
NEXT_MODULE = "AI Evidence Resolution Completion Gate"


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


def route_state_from_handoff(route_item: dict, handoff: dict | None) -> tuple[str, str]:
    route_status = normalize_text(route_item.get("route_status")).upper()

    if route_status == "BLOCKED":
        return "BLOCKED", "Etapa 41 a marcat ruta ca BLOCKED."

    if route_status == "FAILED":
        return "FAILED", "Etapa 41 a marcat ruta ca FAILED."

    if handoff is None:
        return "PENDING", "Nu există încă handoff pentru destinația rutei."

    handoff_status = normalize_text(handoff.get("handoff_status")).upper()

    if handoff_status in {"READY", "CREATED", "PENDING", "AVAILABLE"}:
        return "READY", f"Handoff {handoff_status} către modulul destinație."

    if handoff_status == "CONSUMED":
        return "IN_PROGRESS", "Handoff-ul a fost consumat de modulul destinație."

    if handoff_status == "BLOCKED":
        return "BLOCKED", "Handoff-ul destinației este BLOCKED."

    if handoff_status in {"SUPERSEDED", "CANCELLED", "FAILED"}:
        return "FAILED", f"Handoff-ul destinației este {handoff_status}."

    return "PENDING", f"Status handoff necunoscut/nefinal: {handoff_status or 'EMPTY'}."


def detect_completion(route_item: dict, handoff: dict | None) -> tuple[bool, str, str]:
    """
    Conservative completion detection.
    We do NOT claim route completion just because handoff was consumed.
    Completion is accepted only when downstream payload explicitly reports a
    completed/pass/resolved state.
    """
    if handoff is None:
        return False, "", ""

    payload = as_dict(handoff.get("payload"))

    candidate_values = []
    for key in (
        "status",
        "run_status",
        "validation_status",
        "resolution_status",
        "routing_status",
        "result_status",
        "completion_status",
    ):
        value = normalize_text(payload.get(key)).upper()
        if value:
            candidate_values.append((key, value))

    terminal_good = {
        "COMPLETED",
        "PASS",
        "RESOLVED",
        "VALID",
        "ROUTED",
        "READY_NEXT",
        "VERIFIED",
    }

    for key, value in candidate_values:
        if value in terminal_good:
            return True, f"selected_opportunity_handoffs.payload.{key}", value

    return False, "", ""


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

routing_runs = rows(
    "locked_evidence_routing_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

routing_run = next(
    (
        r for r in routing_runs
        if normalize_text(r.get("routing_status")).upper()
        in {"ROUTED", "NEEDS_ATTENTION", "BLOCKED"}
    ),
    None,
)

if not routing_run:
    st.warning("Nu există rezultat Etapa 41 disponibil pentru orchestrare.")
    st.stop()

routing_run_id = str(routing_run["id"])
validation_run_id = str(routing_run["validation_run_id"])
resolution_run_id = str(routing_run["resolution_run_id"])
requirement_run_id = str(routing_run["requirement_run_id"])

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
    destination = normalize_text(h.get("destination_module"))
    if destination and destination not in handoff_by_destination:
        handoff_by_destination[destination] = h

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 41", normalize_text(routing_run.get("routing_status")) or "—")
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
        "Etapa 42 este BLOCKED: se cere lock ACTIVE, workflow_allowed=true, "
        "deadline viitor și rute Etapa 41 pentru același lock."
    )
    st.stop()

st.success("Hard gate Etapa 42: PASS. Rutele pot fi orchestrate.")

preview = []
for item in routing_items:
    destination = normalize_text(item.get("destination_module"))
    handoff = handoff_by_destination.get(destination)
    state, reason = route_state_from_handoff(item, handoff)
    completed, completion_source, completion_reference = detect_completion(item, handoff)

    if completed:
        state = "COMPLETED"
        reason = f"Downstream completion confirmat prin {completion_source}={completion_reference}."

    preview.append({
        "Requirement": item.get("requirement_label"),
        "Route": item.get("route_type"),
        "Destination": destination,
        "Route status": item.get("route_status"),
        "Handoff": normalize_text(handoff.get("handoff_status")) if handoff else "MISSING",
        "Orchestration": state,
        "Reason": reason,
    })

st.subheader("Orchestration preview")
st.dataframe(preview, use_container_width=True, hide_index=True)

prior_runs = rows(
    "locked_evidence_orchestration_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "routing_run_id": routing_run_id,
    },
    "created_at",
    50,
)

if prior_runs:
    latest_prior = prior_runs[0]
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Latest orchestration", latest_prior.get("orchestration_status") or "—")
    p2.metric("Completed", latest_prior.get("completed_routes") or 0)
    p3.metric("Pending", latest_prior.get("pending_routes") or 0)
    p4.metric("Blocked/Failed", (latest_prior.get("blocked_routes") or 0) + (latest_prior.get("failed_routes") or 0))

confirm = st.checkbox(
    "Confirm că Etapa 42 trebuie să orchestreze rutele Etapei 41 fără să inventeze rezultate downstream."
)

if st.button(
    "🛰️ Orchestrate evidence routes",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    orchestration_run_id = None

    try:
        run_insert = (
            supabase.table("locked_evidence_orchestration_runs")
            .insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "opportunity_identity": identity,
                "total_routes": len(routing_items),
                "orchestration_status": "RUNNING",
                "started_at": now_iso(),
                "summary": {
                    "stage": 42,
                    "project_name": project_name,
                    "stage41_run_id": routing_run_id,
                },
                "updated_at": now_iso(),
            })
            .execute()
        ).data or []

        if not run_insert:
            raise RuntimeError("Nu am putut crea orchestration run Etapa 42.")

        orchestration_run_id = str(run_insert[0]["id"])

        counts = {
            "ready_routes": 0,
            "consumed_routes": 0,
            "completed_routes": 0,
            "blocked_routes": 0,
            "failed_routes": 0,
            "pending_routes": 0,
        }

        progress = st.progress(0)

        for idx, item in enumerate(routing_items, start=1):
            destination = normalize_text(item.get("destination_module"))
            handoff = handoff_by_destination.get(destination)

            state, reason = route_state_from_handoff(item, handoff)
            completed, completion_source, completion_reference = detect_completion(item, handoff)

            if completed:
                state = "COMPLETED"
                reason = (
                    f"Downstream completion confirmat prin "
                    f"{completion_source}={completion_reference}."
                )

            if state == "READY":
                counts["ready_routes"] += 1
            elif state == "IN_PROGRESS":
                counts["consumed_routes"] += 1
            elif state == "COMPLETED":
                counts["completed_routes"] += 1
            elif state == "BLOCKED":
                counts["blocked_routes"] += 1
            elif state == "FAILED":
                counts["failed_routes"] += 1
            else:
                counts["pending_routes"] += 1

            payload = {
                "stage": 42,
                "routing_run_id": routing_run_id,
                "routing_item_id": item.get("id"),
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "requirement": {
                    "id": item.get("requirement_id"),
                    "key": item.get("requirement_key"),
                    "category": item.get("requirement_category"),
                    "label": item.get("requirement_label"),
                },
                "route": {
                    "type": item.get("route_type"),
                    "destination_module": destination,
                    "required_action": item.get("required_action"),
                },
                "handoff": {
                    "id": handoff.get("id") if handoff else None,
                    "status": handoff.get("handoff_status") if handoff else None,
                },
                "orchestration": {
                    "status": state,
                    "reason": reason,
                    "completion_source": completion_source,
                    "completion_reference": completion_reference,
                },
                "created_at": now_iso(),
            }

            supabase.table("locked_evidence_orchestration_items").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "orchestration_run_id": orchestration_run_id,
                "routing_item_id": item["id"],
                "requirement_id": item["requirement_id"],
                "opportunity_identity": identity,
                "requirement_key": item.get("requirement_key"),
                "requirement_category": item.get("requirement_category"),
                "requirement_label": item.get("requirement_label"),
                "route_type": item.get("route_type"),
                "destination_module": destination,
                "orchestration_status": state,
                "handoff_id": handoff.get("id") if handoff else None,
                "handoff_status": handoff.get("handoff_status") if handoff else None,
                "completion_source": completion_source,
                "completion_reference": completion_reference,
                "required_action": item.get("required_action"),
                "orchestration_reason": reason,
                "payload": payload,
                "metadata": {
                    "stage": 42,
                    "route_status": item.get("route_status"),
                },
                "completed_at": now_iso() if state == "COMPLETED" else None,
                "updated_at": now_iso(),
            }).execute()

            progress.progress(idx / len(routing_items))

        if counts["blocked_routes"] > 0:
            final_status = "BLOCKED"
        elif counts["failed_routes"] > 0:
            final_status = "FAILED"
        elif counts["completed_routes"] == len(routing_items):
            final_status = "COMPLETED"
        else:
            final_status = "WAITING"

        summary = {
            "stage": 42,
            "project_name": project_name,
            "opportunity_identity": identity,
            "stage41_run_id": routing_run_id,
            "next_action": (
                "STOP_AND_RESOLVE_BLOCKERS"
                if final_status == "BLOCKED"
                else "REPAIR_FAILED_ROUTES"
                if final_status == "FAILED"
                else "CONTINUE_TO_COMPLETION_GATE"
                if final_status == "COMPLETED"
                else "WAIT_FOR_ROUTED_MODULES"
            ),
        }

        supabase.table("locked_evidence_orchestration_runs").update({
            **counts,
            "orchestration_status": final_status,
            "summary": summary,
            "completed_at": now_iso() if final_status in {"COMPLETED", "BLOCKED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", orchestration_run_id).eq("user_id", user_id).execute()

        # Create/re-arm Stage 43 handoff only when ALL routes are completed.
        if final_status == "COMPLETED":
            existing = next(
                (
                    h for h in handoffs
                    if normalize_text(h.get("destination_module")) == NEXT_MODULE
                ),
                None,
            )

            handoff_payload = {
                "stage": 42,
                "orchestration_run_id": orchestration_run_id,
                "routing_run_id": routing_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "orchestration_status": final_status,
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

        # Consume Stage 41 -> Stage 42 handoff if it exists.
        source_handoff = next(
            (
                h for h in handoffs
                if normalize_text(h.get("destination_module")) == HANDOFF_FROM_STAGE41
                and normalize_text(h.get("handoff_status")).upper() == "READY"
            ),
            None,
        )
        if source_handoff:
            supabase.table("selected_opportunity_handoffs").update({
                "handoff_status": "CONSUMED",
                "consumed_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", source_handoff["id"]).eq("user_id", user_id).execute()

        st.success(
            f"Etapa 42 finalizată: {final_status}. "
            f"Completed={counts['completed_routes']}, "
            f"Ready={counts['ready_routes']}, "
            f"In progress={counts['consumed_routes']}, "
            f"Pending={counts['pending_routes']}."
        )
        st.rerun()

    except Exception as exc:
        if orchestration_run_id:
            try:
                supabase.table("locked_evidence_orchestration_runs").update({
                    "orchestration_status": "FAILED",
                    "summary": {
                        "stage": 42,
                        "error": str(exc)[:4000],
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", orchestration_run_id).eq("user_id", user_id).execute()
            except Exception:
                pass

        st.error(f"Etapa 42 nu a putut finaliza orchestrarea: {exc}")

latest_runs = rows(
    "locked_evidence_orchestration_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "routing_run_id": routing_run_id,
    },
    "created_at",
    50,
)

if latest_runs:
    latest = latest_runs[0]

    st.divider()
    st.subheader("Latest Stage 42 Result")

    a, b, c, d, e = st.columns(5)
    a.metric("Status", latest.get("orchestration_status") or "—")
    b.metric("Completed", latest.get("completed_routes") or 0)
    c.metric("Ready", latest.get("ready_routes") or 0)
    d.metric("In progress", latest.get("consumed_routes") or 0)
    e.metric("Pending", latest.get("pending_routes") or 0)

    items = rows(
        "locked_evidence_orchestration_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "orchestration_run_id": str(latest["id"]),
        },
        "created_at",
        500,
    )

    if items:
        st.dataframe(
            [
                {
                    "Requirement": r.get("requirement_label"),
                    "Route": r.get("route_type"),
                    "Destination": r.get("destination_module"),
                    "Handoff": r.get("handoff_status"),
                    "Orchestration": r.get("orchestration_status"),
                    "Reason": r.get("orchestration_reason"),
                }
                for r in items
            ],
            use_container_width=True,
            hide_index=True,
        )

    status = normalize_text(latest.get("orchestration_status")).upper()
    if status == "COMPLETED":
        st.success("Toate rutele au finalizare downstream confirmată.")
    elif status == "WAITING":
        st.warning(
            "Etapa 42 WAITING: rutele sunt create, dar unul sau mai multe module "
            "downstream nu au confirmat încă finalizarea."
        )
    elif status == "BLOCKED":
        st.error("Etapa 42 BLOCKED: cel puțin o rută este blocată.")
    elif status == "FAILED":
        st.error("Etapa 42 FAILED: există rute/handoff-uri eșuate.")

with st.expander("Istoric Etapa 42"):
    if latest_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "status": r.get("orchestration_status"),
                    "total": r.get("total_routes"),
                    "completed": r.get("completed_routes"),
                    "ready": r.get("ready_routes"),
                    "in_progress": r.get("consumed_routes"),
                    "pending": r.get("pending_routes"),
                    "blocked": r.get("blocked_routes"),
                    "failed": r.get("failed_routes"),
                }
                for r in latest_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 42.")

st.caption(
    "Invariantă Etapa 42: orchestratorul nu declară o rută COMPLETED doar pentru că "
    "handoff-ul a fost consumat. Este necesară o stare downstream explicită de finalizare."
)
