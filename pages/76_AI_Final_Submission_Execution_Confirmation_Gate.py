import os
import json
import hashlib
from datetime import datetime, timezone, date
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 76 v1.0 — FINAL SUBMISSION EXECUTION CONFIRMATION GATE
#
# Purpose:
#   Consume ONLY Stage 75 FINAL_SUBMISSION_EXECUTION_HANDOFF_READY and
#   record explicit human-confirmed evidence that the user manually
#   executed the final Submit action in the official EU portal.
#
# CRITICAL:
#   Stage 76 DOES NOT automate browser submission.
#   Stage 76 DOES NOT collect credentials, MFA, cookies or tokens.
#   Stage 76 DOES NOT invent a receipt.
#
# Success outcome:
#   FINAL_SUBMISSION_EXECUTED_CONFIRMED
#
# Receipt remains separate:
#   external_receipt_obtained = false
#
# A future Stage 77 can capture and verify the official submission receipt.
# =====================================================================


st.set_page_config(
    page_title="Stage 76 v1.0 — Final Submission Execution Confirmation",
    page_icon="📨",
    layout="wide",
)

st.title("📨 Etapa 76 v1.0 — AI Final Submission Execution Confirmation Gate")
st.caption(
    "Înregistrează numai faptul că utilizatorul a apăsat manual Submit în portalul oficial. "
    "Stage 76 NU automatizează trimiterea."
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


def norm(v):
    return str(v or "").strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rows(table, filters=None, order="created_at", limit=100):
    q = supabase.table(table).select("*")
    for k, v in (filters or {}).items():
        if v not in (None, ""):
            q = q.eq(k, v)
    if order:
        q = q.order(order, desc=True)
    return q.limit(limit).execute().data or []


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


def project_label(project):
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


def official_domain_ok(url):
    try:
        p = urlparse(norm(url))
        host = (p.hostname or "").lower().strip(".")
        return p.scheme.lower() == "https" and (
            host == "europa.eu"
            or host.endswith(".europa.eu")
        )
    except Exception:
        return False


def deadline_ok(value):
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


# ---------------------------------------------------------------------
# Supabase / auth
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)

user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 76 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("No projects.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[
    st.selectbox("Project", list(project_map.keys()), key="stage76_project")
]
project_id = str(project["id"])


# ---------------------------------------------------------------------
# Active lock
# ---------------------------------------------------------------------

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
    st.error("Stage 76 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")


# ---------------------------------------------------------------------
# Resolve Stage 75
# ---------------------------------------------------------------------

stage75_candidates = rows(
    "stage75_final_submission_execution_handoffs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage75 = next(
    (
        r for r in stage75_candidates
        if norm(r.get("run_status")).upper() == "COMPLETED"
        and norm(r.get("handoff_outcome")).upper()
        == "FINAL_SUBMISSION_EXECUTION_HANDOFF_READY"
        and bool(r.get("single_use"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if not stage75:
    st.error(
        "Stage 76 BLOCKED: no valid Stage 75 "
        "FINAL_SUBMISSION_EXECUTION_HANDOFF_READY run found."
    )
    st.stop()

stage75_run_id = str(stage75["id"])
stage74_run_id = norm(stage75.get("stage74_run_id"))
application_reference = norm(stage75.get("application_reference"))
current_portal_url = norm(stage75.get("current_portal_url"))

stage75_handoff_sha = norm(stage75.get("handoff_evidence_sha256"))
stage75_run_fingerprint = norm(stage75.get("run_fingerprint"))


# ---------------------------------------------------------------------
# Single-use protection
# ---------------------------------------------------------------------

existing_stage76 = rows(
    "stage76_final_submission_execution_confirmations",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage75_run_id": stage75_run_id,
    },
    "created_at",
    1,
)
existing_stage76 = existing_stage76[0] if existing_stage76 else None


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = [
    ("ACTIVE lock", norm(lock.get("lock_status")).upper() == "ACTIVE"),
    ("Workflow allowed", bool(lock.get("workflow_allowed"))),
    ("Deadline valid", deadline_ok(deadline)),
    ("Stage 75 COMPLETED", norm(stage75.get("run_status")).upper() == "COMPLETED"),
    (
        "Stage 75 handoff ready",
        norm(stage75.get("handoff_outcome")).upper()
        == "FINAL_SUBMISSION_EXECUTION_HANDOFF_READY",
    ),
    ("Stage 75 single-use", bool(stage75.get("single_use"))),
    ("Stage 75 handoff SHA present", len(stage75_handoff_sha) == 64),
    ("Stage 75 run fingerprint present", len(stage75_run_fingerprint) == 64),
    ("Application reference present", len(application_reference) >= 3),
    ("Official portal URL valid", official_domain_ok(current_portal_url)),
    ("No prior Stage 76 for this handoff", existing_stage76 is None),
]

base_ready = all(v for _, v in checks) or existing_stage76 is not None

st.divider()
st.subheader("Stage 75 → Stage 76 final execution confirmation")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Stage 75", norm(stage75.get("handoff_outcome")))
c2.metric("Application", application_reference)
c3.metric("Deadline", str(deadline or "—")[:10])
c4.metric("Execution gate", "READY" if base_ready else "BLOCKED")

with st.expander("Stage 76 hard-gate checks"):
    st.dataframe(
        [{"Check": n, "PASS": v} for n, v in checks],
        use_container_width=True,
        hide_index=True,
    )


# ---------------------------------------------------------------------
# Explicit human-confirmed execution
# ---------------------------------------------------------------------

if not existing_stage76:
    if base_ready:
        st.success("Stage 76 base execution-confirmation gate: READY")
    else:
        st.error("Stage 76 base execution-confirmation gate: BLOCKED")

    st.divider()
    st.subheader("Explicit portal submission execution confirmation")

    st.error(
        "Do NOT confirm this stage until you have personally pressed the final Submit button "
        "in the official Funding & Tenders Portal and the portal shows the proposal as submitted."
    )

    portal_url_now = st.text_input(
        "Current official portal URL",
        value=current_portal_url,
        key="stage76_url",
    )

    visible_reference = st.text_input(
        "Application / proposal reference visible in portal",
        value=application_reference,
        key="stage76_reference",
    )

    portal_shows_submitted = st.checkbox(
        "The official portal now shows this exact proposal as finally SUBMITTED.",
        key="stage76_submitted",
    )

    manual_submit_confirmed = st.checkbox(
        "I personally executed/approved the final Submit action in the official portal.",
        key="stage76_manual_submit",
    )

    exact_draft_confirmed = st.checkbox(
        "The submitted proposal is exactly the draft bound to Stage 75.",
        key="stage76_exact_draft",
    )

    no_changes_after_handoff = st.checkbox(
        "No proposal/package/budget/participant data changed between Stage 75 and the final Submit action.",
        key="stage76_no_changes",
    )

    receipt_not_captured_here = st.checkbox(
        "I understand Stage 76 records submission execution only; official receipt verification is deferred to Stage 77.",
        key="stage76_receipt_later",
    )

    phrase = st.text_input(
        "Execution confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 76 FINAL SUBMISSION EXECUTED",
        key="stage76_phrase",
    )

    note = st.text_area(
        "Optional execution note",
        placeholder="Optional non-sensitive note about the observed submitted state.",
        key="stage76_note",
    )

    exact_target = (
        official_domain_ok(portal_url_now)
        and norm(visible_reference) == application_reference
    )

    execution_ready = (
        base_ready
        and exact_target
        and portal_shows_submitted
        and manual_submit_confirmed
        and exact_draft_confirmed
        and no_changes_after_handoff
        and receipt_not_captured_here
        and norm(phrase) == "CONFIRM STAGE 76 FINAL SUBMISSION EXECUTED"
    )

    execution_payload = {
        "execution_version": "stage76-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage75_run_id": stage75_run_id,
        "stage74_run_id": stage74_run_id or None,
        "application_reference": application_reference,
        "current_portal_url": norm(portal_url_now),
        "stage75_handoff_evidence_sha256": stage75_handoff_sha,
        "stage75_run_fingerprint": stage75_run_fingerprint,

        "portal_shows_submitted": bool(portal_shows_submitted),
        "manual_submit_confirmed": bool(manual_submit_confirmed),
        "exact_draft_confirmed": bool(exact_draft_confirmed),
        "no_changes_after_handoff": bool(no_changes_after_handoff),

        "execution_note": norm(note) or None,

        "external_submission_performed": True,
        "external_receipt_obtained": False,
    }

    execution_evidence_sha256 = sha(execution_payload)

    run_basis = {
        "stage": 76,
        "fingerprint_contract": "stage76-v1.0-final-submission-execution-confirmation",
        "stage75_run_id": stage75_run_id,
        "stage75_handoff_evidence_sha256": stage75_handoff_sha,
        "stage75_run_fingerprint": stage75_run_fingerprint,
        "execution_evidence_sha256": execution_evidence_sha256,
        "application_reference": application_reference,
    }

    run_fingerprint = sha(run_basis)

    if st.button(
        "📨 Persist Stage 76 final submission execution confirmation",
        type="primary",
        use_container_width=True,
        disabled=not execution_ready,
        key="stage76_confirm",
    ):
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "stage75_run_id": stage75_run_id,
            "stage74_run_id": stage74_run_id or None,

            "stage": 76,
            "execution_version": "stage76-v1.0",

            "opportunity_identity": identity,
            "official_deadline": str(deadline or "")[:10] or None,

            "application_reference": application_reference,
            "current_portal_url": norm(portal_url_now),

            "run_status": "COMPLETED",
            "execution_outcome": "FINAL_SUBMISSION_EXECUTED_CONFIRMED",

            "stage75_handoff_consumed": True,

            "external_submission_performed": True,
            "external_receipt_obtained": False,

            "stage75_handoff_evidence_sha256": stage75_handoff_sha,
            "stage75_run_fingerprint": stage75_run_fingerprint,
            "execution_evidence_sha256": execution_evidence_sha256,
            "run_fingerprint": run_fingerprint,

            "execution_payload": execution_payload,
            "run_payload": run_basis,

            "submitted_at": now_iso(),
            "confirmed_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        try:
            same = (
                supabase
                .table("stage76_final_submission_execution_confirmations")
                .select("*")
                .eq("stage75_run_id", stage75_run_id)
                .limit(1)
                .execute()
            ).data or []

            if not same:
                supabase.table(
                    "stage76_final_submission_execution_confirmations"
                ).insert(payload).execute()

            st.success(
                "Stage 76 persisted — FINAL_SUBMISSION_EXECUTED_CONFIRMED."
            )
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 76 persistence failed. Run Stage 76 SQL first. "
                f"{type(exc).__name__}: {str(exc)[:1600]}"
            )


# ---------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------

existing_stage76 = rows(
    "stage76_final_submission_execution_confirmations",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage75_run_id": stage75_run_id,
    },
    "created_at",
    1,
)
existing_stage76 = existing_stage76[0] if existing_stage76 else None

if existing_stage76:
    st.divider()
    st.subheader("Stage 76 outcome")

    st.success(
        f"Run ID: {existing_stage76.get('id')} — "
        f"Outcome: {existing_stage76.get('execution_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage76.get("execution_outcome"))
    o2.metric(
        "Stage 75 consumed?",
        "YES" if existing_stage76.get("stage75_handoff_consumed") else "NO",
    )
    o3.metric(
        "Submitted?",
        "YES" if existing_stage76.get("external_submission_performed") else "NO",
    )
    o4.metric(
        "Receipt?",
        "YES" if existing_stage76.get("external_receipt_obtained") else "NO",
    )

    st.write(
        f"**Application reference:** `{existing_stage76.get('application_reference')}`"
    )
    st.write(
        f"**Stage 75 handoff SHA256:** "
        f"`{existing_stage76.get('stage75_handoff_evidence_sha256')}`"
    )
    st.write(
        f"**Execution evidence SHA256:** "
        f"`{existing_stage76.get('execution_evidence_sha256')}`"
    )
    st.write(
        f"**Run fingerprint:** `{existing_stage76.get('run_fingerprint')}`"
    )

    st.success(
        "Stage 76 records human-confirmed final submission execution. "
        "Official receipt is still pending Stage 77 verification."
    )


st.caption(
    "Invariantă Stage 76 v1.0: FINAL_SUBMISSION_EXECUTED_CONFIRMED means "
    "the user explicitly confirmed that the final portal Submit action was executed. "
    "It is not itself an official European Commission receipt."
)
