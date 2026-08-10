import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Evidence Resolution Routing",
    page_icon="🧭",
    layout="wide",
)

st.title("🧭 Etapa 41 — AI Evidence Resolution Routing")
st.caption(
    "Rutează rezultatele validate în Etapa 40 către modulul corect, "
    "fără să schimbe lock-ul, oportunitatea sau verdictul factual."
)

HANDOFF_FROM_STAGE40 = "AI Evidence Resolution Routing"

ROUTE_DESTINATIONS = {
    "OFFICIAL_VERIFICATION": "AI Official Call Verification",
    "USER_EVIDENCE": "AI Evidence Input Manager",
    "EVIDENCE_RESOLVER": "AI Evidence Resolver",
    "READY_NEXT": "AI Grant Writer",
    "RETURN_TO_STAGE39": "AI Locked Opportunity Evidence Gap Resolver",
    "BLOCKED": "AI Resolution Revalidation",
}


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


def determine_route(item: dict) -> tuple[str, str, str]:
    """
    Deterministic routing only.
    Returns (route_type, reason, required_action).
    """
    validation_status = normalize_text(item.get("validation_status")).upper()
    resolution_status = normalize_text(item.get("resolution_status")).upper()
    resolution_route = normalize_text(item.get("resolution_route")).upper()

    if validation_status == "BLOCKED":
        return (
            "BLOCKED",
            "Etapa 40 a validat acest item ca BLOCKED.",
            normalize_text(item.get("required_next_action")) or "Resolve blocker before continuation.",
        )

    if validation_status == "INVALID":
        return (
            "RETURN_TO_STAGE39",
            "Etapa 40 a invalidat rezultatul Etapei 39.",
            normalize_text(item.get("required_next_action")) or "Correct Stage 39 resolution/evidence.",
        )

    if validation_status == "VALID":
        return (
            "READY_NEXT",
            "Rezoluția este validă și nu mai necesită evidence routing.",
            "Continue downstream using the validated evidence.",
        )

    if validation_status == "VALID_NEEDS_ATTENTION":
        if resolution_status == "NEEDS_OFFICIAL_VERIFICATION" or resolution_route == "OFFICIAL_SOURCE":
            return (
                "OFFICIAL_VERIFICATION",
                "Etapa 40 a validat necesitatea unei verificări oficiale.",
                normalize_text(item.get("required_next_action"))
                or "Verify the requirement against official call documentation.",
            )

        if resolution_status == "NEEDS_USER_EVIDENCE" or resolution_route == "USER_EVIDENCE":
            return (
                "USER_EVIDENCE",
                "Etapa 40 a validat necesitatea unei dovezi furnizate de utilizator.",
                normalize_text(item.get("required_next_action"))
                or "Request factual applicant evidence/documentation.",
            )

        if resolution_status in {"PARTIAL", "UNRESOLVED"}:
            return (
                "EVIDENCE_RESOLVER",
                "Rezoluția este validă ca parțială/nerezolvată și necesită o nouă rezolvare controlată.",
                normalize_text(item.get("required_next_action"))
                or "Resolve remaining evidence gap without assumptions.",
            )

        return (
            "EVIDENCE_RESOLVER",
            "Itemul necesită atenție, dar nu are o rută mai specifică.",
            normalize_text(item.get("required_next_action"))
            or "Review the unresolved evidence route.",
        )

    return (
        "RETURN_TO_STAGE39",
        f"Validation status neacceptat pentru routing: {validation_status or 'EMPTY'}.",
        "Return to Stage 39/40 and correct the item.",
    )


# ---------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------
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

validation_runs = rows(
    "locked_evidence_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

validation_run = next(
    (
        r for r in validation_runs
        if normalize_text(r.get("validation_status")).upper()
        in {"PASS", "NEEDS_ATTENTION", "BLOCKED"}
    ),
    None,
)

if not validation_run:
    st.warning("Nu există un rezultat Etapa 40 disponibil pentru routing.")
    st.stop()

validation_run_id = str(validation_run["id"])
requirement_run_id = str(validation_run["requirement_run_id"])
resolution_run_id = str(validation_run["resolution_run_id"])

validation_items = rows(
    "locked_evidence_validation_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "validation_run_id": validation_run_id,
    },
    "created_at",
    500,
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 40", normalize_text(validation_run.get("validation_status")) or "—")
c4.metric("Items", len(validation_items))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(validation_items)
)

if not hard_gate_ok:
    st.error(
        "Etapa 41 este BLOCKED: se cere lock ACTIVE, workflow_allowed=true, "
        "deadline viitor și rezultat Etapa 40 pentru același lock."
    )
    st.stop()

st.success("Hard gate Etapa 41: PASS. Routing-ul poate fi construit.")

preview = []
for item in validation_items:
    route_type, reason, action = determine_route(item)
    preview.append({
        "Requirement": item.get("requirement_label"),
        "Validation": item.get("validation_status"),
        "Stage 39": item.get("resolution_status"),
        "Route": route_type,
        "Destination": ROUTE_DESTINATIONS[route_type],
        "Required action": action,
    })

st.subheader("Routing preview")
st.dataframe(preview, use_container_width=True, hide_index=True)

prior_runs = rows(
    "locked_evidence_routing_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "validation_run_id": validation_run_id,
    },
    "created_at",
    50,
)

if prior_runs:
    latest_prior = prior_runs[0]
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Latest routing", latest_prior.get("routing_status") or "—")
    p2.metric("Official", latest_prior.get("official_routes") or 0)
    p3.metric("User evidence", latest_prior.get("user_evidence_routes") or 0)
    p4.metric("Resolver", latest_prior.get("resolver_routes") or 0)

confirm = st.checkbox(
    "Confirm că Etapa 41 trebuie să ruteze rezultatele validate fără să schimbe oportunitatea."
)

if st.button(
    "🧭 Build evidence routes",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    routing_run_id = None

    try:
        run_insert = (
            supabase.table("locked_evidence_routing_runs")
            .insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "opportunity_identity": identity,
                "total_items": len(validation_items),
                "routing_status": "RUNNING",
                "started_at": now_iso(),
                "summary": {
                    "stage": 41,
                    "project_name": project_name,
                    "stage40_run_id": validation_run_id,
                },
                "updated_at": now_iso(),
            })
            .execute()
        ).data or []

        if not run_insert:
            raise RuntimeError("Nu am putut crea routing run Etapa 41.")

        routing_run_id = str(run_insert[0]["id"])

        counts = {
            "official_routes": 0,
            "user_evidence_routes": 0,
            "resolver_routes": 0,
            "ready_routes": 0,
            "blocked_routes": 0,
            "invalid_routes": 0,
        }

        handoff_groups = {}

        progress = st.progress(0)

        for idx, item in enumerate(validation_items, start=1):
            route_type, reason, action = determine_route(item)
            destination = ROUTE_DESTINATIONS[route_type]

            if route_type == "OFFICIAL_VERIFICATION":
                counts["official_routes"] += 1
            elif route_type == "USER_EVIDENCE":
                counts["user_evidence_routes"] += 1
            elif route_type == "EVIDENCE_RESOLVER":
                counts["resolver_routes"] += 1
            elif route_type == "READY_NEXT":
                counts["ready_routes"] += 1
            elif route_type == "BLOCKED":
                counts["blocked_routes"] += 1
            elif route_type == "RETURN_TO_STAGE39":
                counts["invalid_routes"] += 1

            payload = {
                "stage": 41,
                "routing_run_id": routing_run_id,
                "validation_run_id": validation_run_id,
                "resolution_run_id": resolution_run_id,
                "requirement_run_id": requirement_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "requirement": {
                    "id": item.get("requirement_id"),
                    "key": item.get("requirement_key"),
                    "category": item.get("requirement_category"),
                    "label": item.get("requirement_label"),
                },
                "validation": {
                    "item_id": item.get("id"),
                    "status": item.get("validation_status"),
                    "reason": item.get("validation_reason"),
                },
                "resolution": {
                    "item_id": item.get("resolution_item_id"),
                    "status": item.get("resolution_status"),
                    "route": item.get("resolution_route"),
                },
                "route": {
                    "type": route_type,
                    "destination_module": destination,
                    "reason": reason,
                    "required_action": action,
                },
                "created_at": now_iso(),
            }

            supabase.table("locked_evidence_routing_items").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "routing_run_id": routing_run_id,
                "validation_item_id": item["id"],
                "resolution_item_id": item["resolution_item_id"],
                "requirement_id": item["requirement_id"],
                "opportunity_identity": identity,
                "requirement_key": item.get("requirement_key"),
                "requirement_category": item.get("requirement_category"),
                "requirement_label": item.get("requirement_label"),
                "validation_status": item.get("validation_status"),
                "resolution_status": item.get("resolution_status"),
                "resolution_route": item.get("resolution_route"),
                "route_type": route_type,
                "destination_module": destination,
                "route_status": "BLOCKED" if route_type == "BLOCKED" else "READY",
                "route_reason": reason,
                "required_action": action,
                "payload": payload,
                "metadata": {
                    "stage": 41,
                    "is_critical": as_dict(item.get("metadata")).get("is_critical"),
                },
                "routed_at": now_iso(),
                "updated_at": now_iso(),
            }).execute()

            handoff_groups.setdefault(destination, []).append(payload)
            progress.progress(idx / len(validation_items))

        if counts["blocked_routes"] > 0:
            final_status = "BLOCKED"
        elif counts["official_routes"] + counts["user_evidence_routes"] + counts["resolver_routes"] + counts["invalid_routes"] > 0:
            final_status = "NEEDS_ATTENTION"
        else:
            final_status = "ROUTED"

        summary = {
            "stage": 41,
            "project_name": project_name,
            "opportunity_identity": identity,
            "stage40_run_id": validation_run_id,
            "next_action": (
                "STOP_AND_RESOLVE_BLOCKERS"
                if final_status == "BLOCKED"
                else "EXECUTE_EVIDENCE_ROUTES"
                if final_status == "NEEDS_ATTENTION"
                else "CONTINUE"
            ),
        }

        supabase.table("locked_evidence_routing_runs").update({
            **counts,
            "routing_status": final_status,
            "summary": summary,
            "completed_at": now_iso(),
            "updated_at": now_iso(),
        }).eq("id", routing_run_id).eq("user_id", user_id).execute()

        # Create/re-arm one canonical handoff per destination module.
        existing_handoffs = rows(
            "selected_opportunity_handoffs",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
            },
            "created_at",
            1000,
        )

        for destination, route_payloads in handoff_groups.items():
            existing = next(
                (
                    h for h in existing_handoffs
                    if normalize_text(h.get("destination_module")) == destination
                ),
                None,
            )

            handoff_payload = {
                "stage": 41,
                "routing_run_id": routing_run_id,
                "validation_run_id": validation_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "destination_module": destination,
                "routes": route_payloads,
                "routing_status": final_status,
                "created_at": now_iso(),
            }

            handoff_status = (
                "BLOCKED"
                if destination == ROUTE_DESTINATIONS["BLOCKED"]
                else "READY"
            )

            if existing:
                supabase.table("selected_opportunity_handoffs").update({
                    "handoff_status": handoff_status,
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
                    "destination_module": destination,
                    "handoff_status": handoff_status,
                    "payload": handoff_payload,
                    "updated_at": now_iso(),
                }).execute()

        # Consume the Stage 40 -> Stage 41 handoff if present.
        stage40_handoff = next(
            (
                h for h in existing_handoffs
                if normalize_text(h.get("destination_module")) == HANDOFF_FROM_STAGE40
                and normalize_text(h.get("handoff_status")).upper() == "READY"
            ),
            None,
        )
        if stage40_handoff:
            supabase.table("selected_opportunity_handoffs").update({
                "handoff_status": "CONSUMED",
                "consumed_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", stage40_handoff["id"]).eq("user_id", user_id).execute()

        st.success(
            f"Etapa 41 finalizată: {final_status}. "
            f"Official={counts['official_routes']}, "
            f"User evidence={counts['user_evidence_routes']}, "
            f"Resolver={counts['resolver_routes']}, "
            f"Ready={counts['ready_routes']}."
        )
        st.rerun()

    except Exception as exc:
        if routing_run_id:
            try:
                supabase.table("locked_evidence_routing_runs").update({
                    "routing_status": "FAILED",
                    "summary": {
                        "stage": 41,
                        "error": str(exc)[:4000],
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", routing_run_id).eq("user_id", user_id).execute()
            except Exception:
                pass

        st.error(f"Etapa 41 nu a putut construi routing-ul: {exc}")

# ---------------------------------------------------------------------
# Latest result
# ---------------------------------------------------------------------
routing_runs = rows(
    "locked_evidence_routing_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "validation_run_id": validation_run_id,
    },
    "created_at",
    50,
)

if routing_runs:
    latest = routing_runs[0]

    st.divider()
    st.subheader("Latest Stage 41 Result")

    a, b, c, d, e = st.columns(5)
    a.metric("Status", latest.get("routing_status") or "—")
    b.metric("Official", latest.get("official_routes") or 0)
    c.metric("User evidence", latest.get("user_evidence_routes") or 0)
    d.metric("Resolver", latest.get("resolver_routes") or 0)
    e.metric("Ready", latest.get("ready_routes") or 0)

    items = rows(
        "locked_evidence_routing_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "routing_run_id": str(latest["id"]),
        },
        "created_at",
        500,
    )

    if items:
        st.dataframe(
            [
                {
                    "Requirement": r.get("requirement_label"),
                    "Validation": r.get("validation_status"),
                    "Route": r.get("route_type"),
                    "Destination": r.get("destination_module"),
                    "Status": r.get("route_status"),
                    "Action": r.get("required_action"),
                }
                for r in items
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Routing details")
        for item in items:
            with st.expander(
                f"{item.get('requirement_label')} → {item.get('destination_module')}"
            ):
                st.write(f"**Route:** {item.get('route_type')}")
                st.write(f"**Reason:** {item.get('route_reason') or '—'}")
                st.write(f"**Required action:** {item.get('required_action') or '—'}")
                st.write(f"**Status:** {item.get('route_status')}")

    status = normalize_text(latest.get("routing_status")).upper()
    if status == "ROUTED":
        st.success("Toate rezultatele validate au fost rutate pentru continuare.")
    elif status == "NEEDS_ATTENTION":
        st.warning(
            "Etapa 41 NEEDS_ATTENTION: există rute către official verification, "
            "user evidence sau evidence resolver."
        )
    elif status == "BLOCKED":
        st.error("Etapa 41 BLOCKED: există un blocker validat care oprește fluxul.")

with st.expander("Istoric Etapa 41"):
    if routing_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "status": r.get("routing_status"),
                    "official": r.get("official_routes"),
                    "user_evidence": r.get("user_evidence_routes"),
                    "resolver": r.get("resolver_routes"),
                    "ready": r.get("ready_routes"),
                    "blocked": r.get("blocked_routes"),
                    "invalid": r.get("invalid_routes"),
                }
                for r in routing_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 41.")

st.caption(
    "Invariantă Etapa 41: routing-ul nu modifică opportunity_lock_id, opportunity_identity "
    "sau verdictul validat în Etapa 40. Creează doar traseul controlat către modulul următor."
)
