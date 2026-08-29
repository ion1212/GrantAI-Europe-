import os
import json
import hashlib
from datetime import datetime, timezone, date
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 75 v1.0 — CONTROLLED FINAL SUBMISSION EXECUTION HANDOFF
#
# Purpose:
#   Consume ONLY Stage 74 FINAL_PRE_SUBMISSION_INTEGRITY_CONFIRMED and
#   create a single-use execution handoff for the final EU portal submit.
#
# IMPORTANT:
#   Stage 75 DOES NOT press Submit.
#   Stage 75 DOES NOT claim a receipt.
#   Stage 75 only freezes the exact target draft + integrity evidence
#   and records explicit human approval for a separate execution step.
#
# Success outcome:
#   FINAL_SUBMISSION_EXECUTION_HANDOFF_READY
#
# A future Stage 76 can consume this handoff to confirm actual portal
# submission execution and/or receipt.
# =====================================================================


st.set_page_config(
    page_title="Stage 75 v1.0 — Final Submission Execution Handoff",
    page_icon="🚦",
    layout="wide",
)

st.title("🚦 Etapa 75 v1.0 — AI Controlled Final Submission Execution Handoff")
st.caption(
    "Pregătește handoff-ul final pentru submit-ul controlat. "
    "Stage 75 NU apasă Submit și NU înregistrează receipt."
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


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)

user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 75 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("No projects.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[
    st.selectbox("Project", list(project_map.keys()), key="stage75_project")
]
project_id = str(project["id"])

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
    st.error("Stage 75 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")


# ---------------------------------------------------------------------
# Resolve Stage 74
# ---------------------------------------------------------------------

stage74_candidates = rows(
    "stage74_final_pre_submission_integrity_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage74 = next(
    (
        r for r in stage74_candidates
        if norm(r.get("run_status")).upper() == "COMPLETED"
        and norm(r.get("integrity_outcome")).upper()
        == "FINAL_PRE_SUBMISSION_INTEGRITY_CONFIRMED"
        and bool(r.get("production_data_final"))
        and bool(r.get("no_test_or_provisional_data"))
        and bool(r.get("proposal_editable"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if not stage74:
    st.error(
        "Stage 75 BLOCKED: no valid Stage 74 "
        "FINAL_PRE_SUBMISSION_INTEGRITY_CONFIRMED run found."
    )
    st.stop()

stage74_run_id = str(stage74["id"])
stage73_run_id = norm(stage74.get("stage73_run_id"))
application_reference = norm(stage74.get("application_reference"))
current_portal_url = norm(stage74.get("current_portal_url"))
stage74_integrity_sha = norm(stage74.get("final_integrity_evidence_sha256"))
stage74_run_fingerprint = norm(stage74.get("run_fingerprint"))


# ---------------------------------------------------------------------
# Hard-gate checks
# ---------------------------------------------------------------------

checks = [
    ("ACTIVE lock", norm(lock.get("lock_status")).upper() == "ACTIVE"),
    ("Workflow allowed", bool(lock.get("workflow_allowed"))),
    ("Deadline valid", deadline_ok(deadline)),
    ("Stage 74 COMPLETED", norm(stage74.get("run_status")).upper() == "COMPLETED"),
    (
        "Stage 74 integrity confirmed",
        norm(stage74.get("integrity_outcome")).upper()
        == "FINAL_PRE_SUBMISSION_INTEGRITY_CONFIRMED",
    ),
    ("Production data final", bool(stage74.get("production_data_final"))),
    ("No test/provisional data", bool(stage74.get("no_test_or_provisional_data"))),
    ("Proposal editable", bool(stage74.get("proposal_editable"))),
    ("Not submitted", not bool(stage74.get("external_submission_performed"))),
    ("No receipt", not bool(stage74.get("external_receipt_obtained"))),
    ("Application reference present", len(application_reference) >= 3),
    ("Official portal URL valid", official_domain_ok(current_portal_url)),
    ("Stage 74 integrity SHA present", len(stage74_integrity_sha) == 64),
    ("Stage 74 run fingerprint present", len(stage74_run_fingerprint) == 64),
]

base_ready = all(v for _, v in checks)

st.divider()
st.subheader("Stage 74 → Stage 75 execution handoff gate")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Stage 74", norm(stage74.get("integrity_outcome")))
c2.metric("Application", application_reference)
c3.metric("Deadline", str(deadline or "—")[:10])
c4.metric("Handoff gate", "READY" if base_ready else "BLOCKED")

with st.expander("Stage 75 hard-gate checks"):
    st.dataframe(
        [{"Check": n, "PASS": v} for n, v in checks],
        use_container_width=True,
        hide_index=True,
    )

if base_ready:
    st.success("Stage 75 base handoff gate: READY")
else:
    st.error("Stage 75 base handoff gate: BLOCKED")


def load_existing():
    data = rows(
        "stage75_final_submission_execution_handoffs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage74_run_id": stage74_run_id,
        },
        "created_at",
        1,
    )
    return data[0] if data else None


existing = load_existing()

if not existing:
    st.divider()
    st.subheader("Explicit controlled execution handoff")

    st.warning(
        "Stage 75 only creates a single-use execution handoff. "
        "It does NOT press Submit in Funding & Tenders Portal."
    )

    displayed_url = st.text_input(
        "Current official portal URL",
        value=current_portal_url,
        key="stage75_url",
    )

    displayed_reference = st.text_input(
        "Draft reference currently visible in portal",
        value=application_reference,
        key="stage75_ref",
    )

    exact_target = (
        official_domain_ok(displayed_url)
        and norm(displayed_reference) == application_reference
    )

    still_not_submitted = st.checkbox(
        "The proposal is still NOT finally submitted.",
        key="stage75_not_submitted",
    )

    still_no_receipt = st.checkbox(
        "No final submission receipt has been issued.",
        key="stage75_no_receipt",
    )

    final_integrity_unchanged = st.checkbox(
        "No proposal/package/budget/participant data has changed since Stage 74.",
        key="stage75_integrity_unchanged",
    )

    understand_handoff = st.checkbox(
        "I understand Stage 75 creates a controlled execution handoff only and does NOT press Submit.",
        key="stage75_understand",
    )

    phrase = st.text_input(
        "Authorization phrase",
        placeholder="Type exactly: AUTHORIZE STAGE 75 EXECUTION HANDOFF",
        key="stage75_phrase",
    )

    note = st.text_area(
        "Optional handoff note",
        placeholder="Optional non-sensitive note.",
        key="stage75_note",
    )

    handoff_ready = (
        base_ready
        and exact_target
        and still_not_submitted
        and still_no_receipt
        and final_integrity_unchanged
        and understand_handoff
        and norm(phrase) == "AUTHORIZE STAGE 75 EXECUTION HANDOFF"
    )

    handoff_payload = {
        "handoff_version": "stage75-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage74_run_id": stage74_run_id,
        "stage73_run_id": stage73_run_id or None,
        "application_reference": application_reference,
        "current_portal_url": norm(displayed_url),
        "stage74_final_integrity_evidence_sha256": stage74_integrity_sha,
        "stage74_run_fingerprint": stage74_run_fingerprint,
        "exact_target_confirmed": exact_target,
        "proposal_not_submitted": bool(still_not_submitted),
        "no_receipt": bool(still_no_receipt),
        "final_integrity_unchanged": bool(final_integrity_unchanged),
        "handoff_note": norm(note) or None,
        "external_submission_performed": False,
        "external_receipt_obtained": False,
    }

    handoff_evidence_sha256 = sha(handoff_payload)

    run_basis = {
        "stage": 75,
        "fingerprint_contract": "stage75-v1.0-controlled-final-submission-execution-handoff",
        "stage74_run_id": stage74_run_id,
        "stage74_final_integrity_evidence_sha256": stage74_integrity_sha,
        "stage74_run_fingerprint": stage74_run_fingerprint,
        "handoff_evidence_sha256": handoff_evidence_sha256,
        "application_reference": application_reference,
    }

    run_fingerprint = sha(run_basis)

    if st.button(
        "🚦 Create & persist Stage 75 controlled execution handoff",
        type="primary",
        use_container_width=True,
        disabled=not handoff_ready,
        key="stage75_confirm",
    ):
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage74_run_id": stage74_run_id,
            "stage73_run_id": stage73_run_id or None,

            "stage": 75,
            "handoff_version": "stage75-v1.0",

            "opportunity_identity": identity,
            "official_deadline": str(deadline or "")[:10] or None,
            "application_reference": application_reference,
            "current_portal_url": norm(displayed_url),

            "run_status": "COMPLETED",
            "handoff_outcome": "FINAL_SUBMISSION_EXECUTION_HANDOFF_READY",

            "single_use": True,
            "consumed": False,

            "external_submission_performed": False,
            "external_receipt_obtained": False,

            "stage74_final_integrity_evidence_sha256": stage74_integrity_sha,
            "stage74_run_fingerprint": stage74_run_fingerprint,
            "handoff_evidence_sha256": handoff_evidence_sha256,
            "run_fingerprint": run_fingerprint,

            "handoff_payload": handoff_payload,
            "run_payload": run_basis,

            "authorized_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        try:
            same = (
                supabase
                .table("stage75_final_submission_execution_handoffs")
                .select("*")
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .eq("opportunity_lock_id", lock_id)
                .eq("stage74_run_id", stage74_run_id)
                .eq("run_fingerprint", run_fingerprint)
                .limit(1)
                .execute()
            ).data or []

            if not same:
                supabase.table(
                    "stage75_final_submission_execution_handoffs"
                ).insert(payload).execute()

            st.success(
                "Stage 75 persisted — "
                "FINAL_SUBMISSION_EXECUTION_HANDOFF_READY."
            )
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 75 persistence failed. Run Stage 75 SQL first. "
                f"{type(exc).__name__}: {str(exc)[:1600]}"
            )


existing = load_existing()

if existing:
    st.divider()
    st.subheader("Stage 75 outcome")

    st.success(
        f"Run ID: {existing.get('id')} — "
        f"Outcome: {existing.get('handoff_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing.get("handoff_outcome"))
    o2.metric("Single-use?", "YES" if existing.get("single_use") else "NO")
    o3.metric("Submitted?", "YES" if existing.get("external_submission_performed") else "NO")
    o4.metric("Receipt?", "YES" if existing.get("external_receipt_obtained") else "NO")

    st.write(
        f"**Application reference:** `{existing.get('application_reference')}`"
    )
    st.write(
        f"**Stage 74 integrity SHA256:** "
        f"`{existing.get('stage74_final_integrity_evidence_sha256')}`"
    )
    st.write(
        f"**Handoff evidence SHA256:** "
        f"`{existing.get('handoff_evidence_sha256')}`"
    )
    st.write(
        f"**Run fingerprint:** `{existing.get('run_fingerprint')}`"
    )

    st.success(
        "Stage 75 handoff is ready. "
        "No submission or receipt occurred at Stage 75."
    )


st.caption(
    "Invariantă Stage 75 v1.0: FINAL_SUBMISSION_EXECUTION_HANDOFF_READY "
    "is a single-use execution handoff only. It is not evidence of submission."
)
