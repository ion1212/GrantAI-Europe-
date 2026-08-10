import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Selected Opportunity Lock & Workflow Handoff",
    page_icon="🔒",
    layout="wide",
)

st.title("🔒 Etapa 37 — Selected Opportunity Lock & Workflow Handoff")
st.caption(
    "Blochează oportunitatea selectată și verificată în Etapa 36 pentru proiectul curent "
    "și creează handoff-uri controlate către modulele următoare. "
    "Oportunitatea nu se schimbă automat la refresh/scoring ulterior."
)


# ---------------------------------------------------------------------
# Config / auth helpers
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
    try:
        q = supabase.table(table).select("*")
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                q = q.eq(key, value)
        if order:
            q = q.order(order, desc=True)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


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


def safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


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
# Project selector
# ---------------------------------------------------------------------
projects = rows("projects", {"user_id": user_id}, "updated_at", 100)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_options = {
    f"{p.get('name') or 'Project'} — {str(p.get('id'))[:8]}": p
    for p in projects
}

selected_project_label = st.selectbox("Project", list(project_options.keys()))
project = project_options[selected_project_label]
project_id = str(project["id"])
project_name = project.get("name") or "—"
project_data = as_dict(project.get("data"))

st.write(f"**Project:** {project_name}")


# ---------------------------------------------------------------------
# Latest Etapa 36 run and selected result
# ---------------------------------------------------------------------
selection_runs = rows(
    "opportunity_selection_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    100,
)

latest_selection_run = next(
    (
        r for r in selection_runs
        if str(r.get("run_status") or "") in ("Completed", "Needs attention")
        and r.get("selected_result_id")
        and r.get("selected_opportunity_identity")
    ),
    None,
)

if not latest_selection_run:
    st.warning(
        "Nu există încă o oportunitate selectată explicit în Etapa 36 pentru acest proiect."
    )
    st.info(
        "Revino în Etapa 36, alege o oportunitate SELECTABLE și apasă "
        "„Selectează oportunitatea verificată”."
    )
    st.stop()

selection_run_id = str(latest_selection_run["id"])
selected_result_id = str(latest_selection_run["selected_result_id"])
selected_identity = str(latest_selection_run["selected_opportunity_identity"])

selection_results = rows(
    "opportunity_selection_results",
    {
        "user_id": user_id,
        "project_id": project_id,
        "selection_run_id": selection_run_id,
    },
    "created_at",
    100,
)

selected_result = next(
    (
        r for r in selection_results
        if str(r.get("id")) == selected_result_id
        and r.get("user_selected") is True
    ),
    None,
)

if not selected_result:
    st.error(
        "Run-ul Etapei 36 indică o selecție, dar rezultatul selectat nu poate fi validat."
    )
    st.stop()


# ---------------------------------------------------------------------
# Hard gate validation
# ---------------------------------------------------------------------
selection_status = str(selected_result.get("selection_status") or "")
identity_status = str(selected_result.get("identity_status") or "")
deadline_verified = bool(selected_result.get("deadline_verified"))
user_selected = bool(selected_result.get("user_selected"))
official_deadline = selected_result.get("official_deadline")
official_identity = str(
    selected_result.get("official_identity")
    or selected_result.get("opportunity_identity")
    or ""
)

today = date.today()
deadline_is_future = True
if official_deadline:
    try:
        deadline_is_future = date.fromisoformat(str(official_deadline)) >= today
    except Exception:
        deadline_is_future = False
else:
    deadline_is_future = False

gate_ok = (
    selection_status == "SELECTABLE"
    and identity_status == "MATCH"
    and deadline_verified
    and user_selected
    and deadline_is_future
    and official_identity == selected_identity
)

g1, g2, g3, g4 = st.columns(4)
g1.metric("Etapa 36 status", selection_status or "—")
g2.metric("Identity", identity_status or "—")
g3.metric("Deadline verified", "YES" if deadline_verified else "NO")
g4.metric("User selected", "YES" if user_selected else "NO")

st.write(f"**Selected identity:** {selected_identity}")
st.write(f"**Official identity:** {official_identity or '—'}")
st.write(f"**Official deadline:** {official_deadline or '—'}")

if not gate_ok:
    st.error(
        "Etapa 37 este blocată. Se cere simultan: "
        "SELECTABLE + MATCH + deadline verificat și viitor + user_selected=true "
        "+ official_identity identic cu oportunitatea selectată."
    )
    st.stop()


# ---------------------------------------------------------------------
# Resolve scoring snapshot and opportunity snapshot
# ---------------------------------------------------------------------
scoring_result = None
scoring_result_id = selected_result.get("scoring_result_id")
if scoring_result_id:
    scoring_rows = rows(
        "opportunity_scoring_results",
        {"user_id": user_id, "project_id": project_id},
        "created_at",
        1000,
    )
    scoring_result = next(
        (r for r in scoring_rows if str(r.get("id")) == str(scoring_result_id)),
        None,
    )

opportunity_rows = rows(
    "opportunities",
    {"user_id": user_id, "identity": selected_identity},
    "updated_at",
    20,
)
opportunity_row = opportunity_rows[0] if opportunity_rows else None
opportunity_snapshot = as_dict(opportunity_row.get("data")) if opportunity_row else {}


# ---------------------------------------------------------------------
# Current active lock
# ---------------------------------------------------------------------
active_locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    20,
)

active_lock = active_locks[0] if active_locks else None

st.divider()
st.subheader("Workflow Lock")

if active_lock:
    same_identity = (
        str(active_lock.get("opportunity_identity") or "") == selected_identity
    )

    l1, l2, l3 = st.columns(3)
    l1.metric("Current lock", "ACTIVE")
    l2.metric(
        "Workflow allowed",
        "YES" if active_lock.get("workflow_allowed") else "NO",
    )
    l3.metric(
        "Same opportunity",
        "YES" if same_identity else "NO",
    )

    st.write(
        f"**Locked opportunity:** {active_lock.get('opportunity_identity') or '—'}"
    )
    st.write(
        f"**Locked at:** {active_lock.get('locked_at') or active_lock.get('created_at') or '—'}"
    )

    if same_identity and active_lock.get("workflow_allowed"):
        st.success(
            "Această oportunitate este deja blocată și workflow-ul este permis."
        )
    else:
        st.warning(
            "Există deja un lock ACTIVE pentru proiect. "
            "Pentru schimbarea oportunității trebuie creat un lock nou, "
            "iar cel vechi va fi marcat SUPERSEDED."
        )
else:
    st.info("Nu există încă un lock ACTIVE pentru acest proiect.")


# ---------------------------------------------------------------------
# Handoff destination configuration
# ---------------------------------------------------------------------
default_destinations = [
    "AI Opportunity Fit Gate",
    "AI Evidence Requirement Resolver",
    "AI Official Call Verification",
    "AI Opportunity Validity Gate",
    "AI Grant Writer",
]

destination_options = st.multiselect(
    "Module pentru handoff",
    options=[
        "AI Opportunity Fit Gate",
        "AI Evidence Requirement Resolver",
        "AI Official Call Verification",
        "AI Opportunity Validity Gate",
        "AI Grant Writer",
        "AI Proposal Reviewer",
        "AI Compliance Checker",
        "AI Submission Readiness",
    ],
    default=default_destinations,
)

st.info(
    "Etapa 37 nu rescrie proiectul și nu schimbă automat oportunitatea. "
    "Ea creează un lock canonic și payload-uri de handoff pentru modulele selectate."
)


# ---------------------------------------------------------------------
# Lock + handoff creation
# ---------------------------------------------------------------------
confirm = st.checkbox(
    "Confirm că vreau să blochez această oportunitate pentru proiect și să permit workflow-ul."
)

if st.button(
    "🔒 Lock opportunity & continue",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    try:
        # If the same active lock already exists, reuse it.
        lock = None
        if (
            active_lock
            and str(active_lock.get("opportunity_identity") or "") == selected_identity
        ):
            supabase.table("selected_opportunity_locks").update({
                "workflow_allowed": True,
                "updated_at": now_iso(),
            }).eq("id", active_lock["id"]).eq("user_id", user_id).execute()

            lock = {
                **active_lock,
                "workflow_allowed": True,
            }

        else:
            # Supersede prior active lock first to satisfy the unique partial index.
            if active_lock:
                supabase.table("selected_opportunity_locks").update({
                    "lock_status": "SUPERSEDED",
                    "workflow_allowed": False,
                    "released_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", active_lock["id"]).eq("user_id", user_id).execute()

            verification_snapshot = {
                "selection_run": latest_selection_run,
                "selection_result": selected_result,
                "gate": {
                    "selection_status": selection_status,
                    "identity_status": identity_status,
                    "deadline_verified": deadline_verified,
                    "user_selected": user_selected,
                    "deadline_is_future": deadline_is_future,
                },
            }

            scoring_snapshot = scoring_result or {}

            lock_insert = (
                supabase.table("selected_opportunity_locks")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "selection_run_id": selection_run_id,
                    "selection_result_id": selected_result_id,
                    "scoring_result_id": (
                        scoring_result.get("id") if scoring_result else None
                    ),

                    "opportunity_identity": selected_identity,
                    "opportunity_title": selected_result.get("opportunity_title"),

                    "official_identity": official_identity,
                    "official_title": selected_result.get("official_title"),
                    "official_deadline": official_deadline,

                    "programme": selected_result.get("programme"),

                    "official_source_title": selected_result.get("official_source_title"),
                    "official_source_url": selected_result.get("official_source_url"),
                    "official_source_reference": selected_result.get("official_source_reference"),

                    "scoring_score": (
                        scoring_result.get("overall_score")
                        if scoring_result
                        else selected_result.get("scoring_score")
                    ),
                    "scoring_verdict": (
                        scoring_result.get("verdict")
                        if scoring_result
                        else selected_result.get("scoring_verdict")
                    ),

                    "lock_status": "ACTIVE",
                    "workflow_allowed": True,

                    "verification_snapshot": verification_snapshot,
                    "scoring_snapshot": scoring_snapshot,
                    "opportunity_snapshot": opportunity_snapshot,

                    "locked_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .execute()
            ).data or []

            if not lock_insert:
                raise RuntimeError("Nu am putut crea opportunity lock.")

            lock = lock_insert[0]

        lock_id = str(lock["id"])

        # Supersede stale READY handoffs for previous locks of this project.
        old_handoffs = rows(
            "selected_opportunity_handoffs",
            {"user_id": user_id, "project_id": project_id},
            "created_at",
            1000,
        )
        for h in old_handoffs:
            if (
                str(h.get("opportunity_lock_id") or "") != lock_id
                and str(h.get("handoff_status") or "") == "READY"
            ):
                try:
                    supabase.table("selected_opportunity_handoffs").update({
                        "handoff_status": "SUPERSEDED",
                        "updated_at": now_iso(),
                    }).eq("id", h["id"]).eq("user_id", user_id).execute()
                except Exception:
                    pass

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

        # Canonical destinations required by downstream workflow.
        # Do not rely only on the Streamlit multiselect/session state: Etapa 38
        # requires at minimum the Evidence Requirement Resolver handoff.
        required_destinations = {
            "AI Evidence Requirement Resolver",
        }
        effective_destinations = list(
            dict.fromkeys(list(destination_options) + list(required_destinations))
        )

        # A destination may already exist for this lock but be CONSUMED,
        # SUPERSEDED, FAILED, etc. In that case we reactivate that row instead
        # of assuming the destination is available merely because a row exists.
        existing_by_destination = {
            str(h.get("destination_module") or ""): h
            for h in existing_handoffs
            if h.get("destination_module")
        }

        created = 0
        reactivated = 0

        for destination in effective_destinations:
            existing = existing_by_destination.get(destination)
            if existing and str(existing.get("handoff_status") or "") == "READY":
                continue

            payload = {
                "stage": 37,
                "project": {
                    "id": project_id,
                    "name": project_name,
                    "data": project_data,
                },
                "opportunity_lock": {
                    "id": lock_id,
                    "identity": selected_identity,
                    "official_identity": official_identity,
                    "official_title": selected_result.get("official_title"),
                    "official_deadline": official_deadline,
                    "programme": selected_result.get("programme"),
                    "official_source_title": selected_result.get("official_source_title"),
                    "official_source_url": selected_result.get("official_source_url"),
                    "official_source_reference": selected_result.get("official_source_reference"),
                    "workflow_allowed": True,
                },
                "selection": {
                    "run_id": selection_run_id,
                    "result_id": selected_result_id,
                    "selection_status": selection_status,
                    "identity_status": identity_status,
                },
                "scoring": {
                    "result_id": scoring_result.get("id") if scoring_result else None,
                    "score": (
                        scoring_result.get("overall_score")
                        if scoring_result
                        else selected_result.get("scoring_score")
                    ),
                    "verdict": (
                        scoring_result.get("verdict")
                        if scoring_result
                        else selected_result.get("scoring_verdict")
                    ),
                },
                "destination_module": destination,
                "created_at": now_iso(),
            }

            handoff_values = {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": selected_identity,
                "destination_module": destination,
                "handoff_status": "READY",
                "payload": payload,
                "updated_at": now_iso(),
            }

            if existing:
                # Re-arm the handoff for the same canonical lock. Clear the
                # consumption timestamp so Etapa 38 can consume it once.
                update_values = {
                    "handoff_status": "READY",
                    "payload": payload,
                    "consumed_at": None,
                    "updated_at": now_iso(),
                }
                supabase.table("selected_opportunity_handoffs").update(
                    update_values
                ).eq("id", existing["id"]).eq("user_id", user_id).execute()
                reactivated += 1
            else:
                supabase.table("selected_opportunity_handoffs").insert(
                    handoff_values
                ).execute()
                created += 1

        # Hard postcondition: never report success unless the mandatory
        # Evidence Requirement Resolver handoff is READY for this exact lock.
        post_handoffs = rows(
            "selected_opportunity_handoffs",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
            },
            "created_at",
            1000,
        )
        evidence_ready = any(
            str(h.get("destination_module") or "") == "AI Evidence Requirement Resolver"
            and str(h.get("handoff_status") or "") == "READY"
            for h in post_handoffs
        )
        if not evidence_ready:
            raise RuntimeError(
                "Handoff-ul obligatoriu către AI Evidence Requirement Resolver "
                "nu este READY pentru lock-ul activ."
            )

        st.success(
            f"Oportunitatea {selected_identity} este LOCKED și workflow_allowed=true. "
            f"Handoff-uri noi create: {created}; reactivate: {reactivated}. "
            "Evidence Requirement Resolver: READY."
        )
        st.rerun()

    except Exception as exc:
        st.error(f"Etapa 37 nu a putut crea lock-ul/handoff-ul: {exc}")


# ---------------------------------------------------------------------
# Latest active lock + handoffs
# ---------------------------------------------------------------------
st.divider()
st.subheader("Active Opportunity Lock")

active_locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    20,
)

active_lock = active_locks[0] if active_locks else None

if not active_lock:
    st.caption("Nu există încă un lock ACTIVE.")
else:
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Lock", "ACTIVE")
    a2.metric(
        "Workflow",
        "ALLOWED" if active_lock.get("workflow_allowed") else "BLOCKED",
    )
    a3.metric(
        "Identity",
        active_lock.get("opportunity_identity") or "—",
    )
    a4.metric(
        "Score",
        (
            f"{float(active_lock.get('scoring_score')):.1f}"
            if active_lock.get("scoring_score") is not None
            else "—"
        ),
    )

    st.write(
        f"**Official title:** {active_lock.get('official_title') or active_lock.get('opportunity_title') or '—'}"
    )
    st.write(
        f"**Official deadline:** {active_lock.get('official_deadline') or '—'}"
    )
    st.write(
        f"**Official source:** {active_lock.get('official_source_title') or '—'}"
    )

    handoffs = rows(
        "selected_opportunity_handoffs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": str(active_lock["id"]),
        },
        "created_at",
        100,
    )

    st.subheader("Workflow Handoffs")

    if handoffs:
        st.dataframe(
            [
                {
                    "Destination": h.get("destination_module"),
                    "Status": h.get("handoff_status"),
                    "Identity": h.get("opportunity_identity"),
                    "Created": h.get("created_at"),
                    "Consumed": h.get("consumed_at"),
                }
                for h in handoffs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există handoff-uri pentru lock-ul activ.")


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
with st.expander("Istoric Etapa 37"):
    locks = rows(
        "selected_opportunity_locks",
        {"user_id": user_id, "project_id": project_id},
        "created_at",
        100,
    )

    if locks:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "identity": r.get("opportunity_identity"),
                    "official_deadline": r.get("official_deadline"),
                    "score": r.get("scoring_score"),
                    "status": r.get("lock_status"),
                    "workflow_allowed": r.get("workflow_allowed"),
                    "locked_at": r.get("locked_at"),
                    "released_at": r.get("released_at"),
                }
                for r in locks
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există lock-uri Etapa 37.")

st.caption(
    "Etapa 37 este sursa canonică pentru oportunitatea selectată a proiectului. "
    "Modulele următoare trebuie să accepte numai lock_status=ACTIVE și "
    "workflow_allowed=true și să consume handoff-ul corespunzător."
)
