import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 65 v1.0 — AI EXTERNAL EXECUTION SESSION PREPARATION /
# PORTAL ACTION GATE
#
# Purpose:
#   Consume ONLY a persisted Stage 64 EXTERNAL_PREFLIGHT_READY result and
#   prepare an immutable execution-session envelope for a future portal
#   interaction stage.
#
# Stage 65 DOES NOT:
#   - log into the EU Funding & Tenders Portal
#   - upload proposal documents
#   - press Submit
#   - sign declarations
#   - create financial commitments
#   - claim a submission receipt
#
# Stage 65 verifies:
#   - ACTIVE lock + deadline still valid
#   - Stage 64 COMPLETED + EXTERNAL_PREFLIGHT_READY
#   - Stage 64 portal/topic verification was positive
#   - Stage 64 verification fingerprint stable
#   - Stage 63 READY_TO_EXECUTE remains bound
#   - Stage 62 HANDOFF_READY remains bound
#   - Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF remains bound
#   - Stage 60 PACKAGE_READY remains bound
#
# Stage 65 produces:
#   - session_manifest
#   - session_sha256
#   - session_fingerprint
#   - explicit "no external action yet" state
#
# Outcomes:
#   READY_TO_OPEN_PORTAL
#   BLOCKED
#
# Handoff:
#   Stage 66 may consume ONLY READY_TO_OPEN_PORTAL.
# =====================================================================

st.set_page_config(
    page_title="Stage 65 v1.0 — External Execution Session Preparation",
    page_icon="🧭",
    layout="wide",
)

st.title("🧭 Etapa 65 v1.0 — AI External Execution Session Preparation / Portal Action Gate")
st.caption(
    "Pregătește sesiunea controlată pentru un viitor pas de interacțiune cu portalul. "
    "Nu face login, upload sau submission."
)


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


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


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
    st.error("Stage 65 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[
    st.selectbox(
        "Project",
        list(project_map.keys()),
        key="stage65_project",
    )
]
project_id = str(project["id"])


# ---------------------------------------------------------------------
# Opportunity lock
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
    st.error("Stage 65 BLOCKED: nu există opportunity lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")


# ---------------------------------------------------------------------
# Load Stage 64 EXTERNAL_PREFLIGHT_READY
# ---------------------------------------------------------------------

stage64_candidates = rows(
    "stage64_external_portal_preflight_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage64 = next(
    (
        r for r in stage64_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("preflight_outcome")).upper() == "EXTERNAL_PREFLIGHT_READY"
        and bool(r.get("portal_state_verified"))
        and bool(r.get("topic_identity_verified_live"))
        and not bool(r.get("external_execution_started"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if stage64:
    stage64_run_id = str(stage64.get("id") or "")
    stage64_status = normalize_text(stage64.get("run_status")).upper()
    stage64_outcome = normalize_text(stage64.get("preflight_outcome")).upper()
    stage64_run_fingerprint = normalize_text(stage64.get("run_fingerprint"))
    stage64_verification_fingerprint = normalize_text(stage64.get("verification_fingerprint"))

    stage63_run_id = str(stage64.get("stage63_run_id") or "")
    stage62_run_id = str(stage64.get("stage62_run_id") or "")
    stage61_run_id = str(stage64.get("stage61_run_id") or "")
    stage60_run_id = str(stage64.get("stage60_run_id") or "")
    stage59_run_id = str(stage64.get("stage59_run_id") or "")
    stage57_run_id = str(stage64.get("stage57_run_id") or "")
else:
    stage64_run_id = ""
    stage64_status = "MISSING"
    stage64_outcome = "MISSING"
    stage64_run_fingerprint = ""
    stage64_verification_fingerprint = ""

    stage63_run_id = ""
    stage62_run_id = ""
    stage61_run_id = ""
    stage60_run_id = ""
    stage59_run_id = ""
    stage57_run_id = ""


# ---------------------------------------------------------------------
# Load bound upstream chain
# ---------------------------------------------------------------------

def get_bound(table: str, row_id: str):
    if not row_id:
        return None

    data = rows(
        table,
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        200,
    )

    return next(
        (r for r in data if str(r.get("id") or "") == row_id),
        None,
    )


stage63 = get_bound(
    "stage63_external_execution_authorization_runs",
    stage63_run_id,
)
stage62 = get_bound(
    "stage62_controlled_submission_handoff_runs",
    stage62_run_id,
)
stage61 = get_bound(
    "stage61_human_approval_runs",
    stage61_run_id,
)
stage60 = get_bound(
    "stage60_submission_package_runs",
    stage60_run_id,
)


# ---------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------

stage64_run_payload = as_dict(stage64.get("run_payload")) if stage64 else {}
recomputed_stage64_run_fingerprint = (
    stable_sha256(stage64_run_payload)
    if stage64_run_payload
    else ""
)

stage64_verification_payload = as_dict(
    stage64.get("verification_payload")
) if stage64 else {}
recomputed_stage64_verification_fingerprint = (
    stable_sha256(stage64_verification_payload)
    if stage64_verification_payload
    else ""
)

stage63_outcome = (
    normalize_text(stage63.get("authorization_outcome")).upper()
    if stage63 else "MISSING"
)

stage62_outcome = (
    normalize_text(stage62.get("handoff_outcome")).upper()
    if stage62 else "MISSING"
)

stage61_outcome = (
    normalize_text(stage61.get("approval_outcome")).upper()
    if stage61 else "MISSING"
)

stage60_outcome = (
    normalize_text(stage60.get("package_outcome")).upper()
    if stage60 else "MISSING"
)


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []


def add_check(name: str, passed: bool, detail: str):
    checks.append(
        {
            "Check": name,
            "PASS": bool(passed),
            "Detail": detail,
        }
    )


add_check(
    "ACTIVE lock",
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
    normalize_text(lock.get("lock_status")).upper(),
)

add_check(
    "Workflow allowed",
    workflow_allowed,
    f"workflow_allowed={workflow_allowed}",
)

add_check(
    "Deadline valid",
    future_deadline(deadline),
    str(deadline or "")[:10],
)

add_check(
    "Stage 64 exists",
    bool(stage64),
    stage64_run_id or "MISSING",
)

add_check(
    "Stage 64 COMPLETED",
    stage64_status == "COMPLETED",
    stage64_status,
)

add_check(
    "Stage 64 EXTERNAL_PREFLIGHT_READY",
    stage64_outcome == "EXTERNAL_PREFLIGHT_READY",
    stage64_outcome,
)

add_check(
    "Stage 64 portal state verified",
    bool(stage64) and bool(stage64.get("portal_state_verified")),
    f"portal_state_verified={bool(stage64.get('portal_state_verified')) if stage64 else None}",
)

add_check(
    "Stage 64 topic identity verified",
    bool(stage64) and bool(stage64.get("topic_identity_verified_live")),
    f"topic_identity_verified_live={bool(stage64.get('topic_identity_verified_live')) if stage64 else None}",
)

add_check(
    "Stage 64 run fingerprint stable",
    bool(stage64_run_fingerprint)
    and stage64_run_fingerprint == recomputed_stage64_run_fingerprint,
    f"stored={stage64_run_fingerprint[:16]}..., recomputed={recomputed_stage64_run_fingerprint[:16]}...",
)

add_check(
    "Stage 64 verification fingerprint stable",
    bool(stage64_verification_fingerprint)
    and stage64_verification_fingerprint == recomputed_stage64_verification_fingerprint,
    f"stored={stage64_verification_fingerprint[:16]}..., recomputed={recomputed_stage64_verification_fingerprint[:16]}...",
)

add_check(
    "Stage 64 external execution not started",
    bool(stage64) and not bool(stage64.get("external_execution_started")),
    f"external_execution_started={bool(stage64.get('external_execution_started')) if stage64 else None}",
)

add_check(
    "Stage 64 submission not performed",
    bool(stage64) and not bool(stage64.get("external_submission_performed")),
    f"external_submission_performed={bool(stage64.get('external_submission_performed')) if stage64 else None}",
)

add_check(
    "Stage 64 receipt not obtained",
    bool(stage64) and not bool(stage64.get("external_receipt_obtained")),
    f"external_receipt_obtained={bool(stage64.get('external_receipt_obtained')) if stage64 else None}",
)

add_check(
    "Stage 63 READY_TO_EXECUTE",
    stage63_outcome == "READY_TO_EXECUTE",
    stage63_outcome,
)

add_check(
    "Stage 62 HANDOFF_READY",
    stage62_outcome == "HANDOFF_READY",
    stage62_outcome,
)

add_check(
    "Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF",
    stage61_outcome == "APPROVED_FOR_SUBMISSION_HANDOFF",
    stage61_outcome,
)

add_check(
    "Stage 60 PACKAGE_READY",
    stage60_outcome == "PACKAGE_READY",
    stage60_outcome,
)


stage65_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)

gate_reason = (
    "Stage 64 external preflight and all bound upstream authorization states are valid."
    if stage65_gate == "READY"
    else "Stage 65 fail-closed gate failed: "
    + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Session manifest
# ---------------------------------------------------------------------

session_constraints = [
    "Stage 65 does not log in to the EU Funding & Tenders Portal.",
    "Stage 65 does not upload proposal files.",
    "Stage 65 does not press Submit.",
    "Stage 65 does not sign declarations or create financial commitments.",
    "Any future portal-execution stage must re-check the live topic identity and deadline before taking action.",
    "Any package mutation invalidates this session and requires a new upstream readiness/approval chain.",
    "SUBMITTED may only be recorded after real external confirmation from the portal.",
]

session_manifest = {
    "session_version": "stage65-v1.0",
    "session_type": "EXTERNAL_EXECUTION_SESSION_PREPARATION",

    "target_system": "EU Funding & Tenders Portal",

    "state": {
        "portal_login_started": False,
        "portal_session_established": False,
        "upload_started": False,
        "external_submission_performed": False,
        "external_receipt_obtained": False,
    },

    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage57_run_id": stage57_run_id,
    "stage59_run_id": stage59_run_id,
    "stage60_run_id": stage60_run_id,
    "stage61_run_id": stage61_run_id,
    "stage62_run_id": stage62_run_id,
    "stage63_run_id": stage63_run_id,
    "stage64_run_id": stage64_run_id,

    "stage64_run_fingerprint": stage64_run_fingerprint,
    "stage64_verification_fingerprint": stage64_verification_fingerprint,

    "verified_portal_url": normalize_text(
        stage64.get("portal_final_url") or stage64.get("portal_url")
    ) if stage64 else "",

    "verified_portal_page_sha256": normalize_text(
        stage64.get("portal_page_sha256")
    ) if stage64 else "",

    "constraints": session_constraints,
}

session_sha256 = stable_sha256(session_manifest)

run_basis = {
    "stage": 65,
    "fingerprint_contract": "stage65-v1.0-external-session-preparation",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "stage64_run_id": stage64_run_id,
    "stage64_verification_fingerprint": stage64_verification_fingerprint,
    "session_sha256": session_sha256,
    "stage65_gate": stage65_gate,
}

stage65_run_fingerprint = stable_sha256(run_basis)
stage65_outcome = (
    "READY_TO_OPEN_PORTAL"
    if stage65_gate == "READY"
    else "BLOCKED"
)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage65():
    if not stage64_run_id:
        return None

    data = (
        supabase
        .table("stage65_external_execution_session_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage64_run_id", stage64_run_id)
        .eq("run_fingerprint", stage65_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage65():
    if stage65_gate != "READY":
        raise RuntimeError("Stage 65 is BLOCKED.")

    existing = load_existing_stage65()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage57_run_id": stage57_run_id,
        "stage59_run_id": stage59_run_id,
        "stage60_run_id": stage60_run_id,
        "stage61_run_id": stage61_run_id,
        "stage62_run_id": stage62_run_id,
        "stage63_run_id": stage63_run_id,
        "stage64_run_id": stage64_run_id,

        "stage": 65,
        "session_version": "stage65-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage64_verification_fingerprint": stage64_verification_fingerprint,
        "verified_portal_url": normalize_text(
            stage64.get("portal_final_url") or stage64.get("portal_url")
        ),
        "verified_portal_page_sha256": normalize_text(
            stage64.get("portal_page_sha256")
        ),

        "run_status": "COMPLETED",
        "session_outcome": stage65_outcome,

        "portal_login_started": False,
        "portal_session_established": False,
        "upload_started": False,
        "external_submission_performed": False,
        "external_receipt_obtained": False,

        "constraint_count": len(session_constraints),

        "run_fingerprint": stage65_run_fingerprint,
        "session_sha256": session_sha256,

        "session_manifest": session_manifest,
        "run_payload": run_basis,

        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    try:
        rpc_result = supabase.rpc(
            "persist_stage65_external_session",
            {"p_payload": payload},
        ).execute()

        if rpc_result.data:
            if isinstance(rpc_result.data, list):
                return rpc_result.data[0]
            if isinstance(rpc_result.data, dict):
                return rpc_result.data
    except Exception:
        pass

    data = (
        supabase
        .table("stage65_external_execution_session_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 65 execution session.")

    return data[0]


existing_stage65 = load_existing_stage65()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 64 → Stage 65 session preparation binding")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Stage 64", stage64_outcome)
m2.metric("Portal verified?", "YES" if stage64 and stage64.get("portal_state_verified") else "NO")
m3.metric("Topic identity?", "YES" if stage64 and stage64.get("topic_identity_verified_live") else "NO")
m4.metric("Integrity", "VERIFIED" if stage65_gate == "READY" else "FAILED")

with st.expander("Stage 65 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.write(f"**Gate:** `{stage65_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Stage 64 verification fingerprint:** `{stage64_verification_fingerprint}`")
st.write(f"**Stage 65 session SHA256:** `{session_sha256}`")
st.write(f"**Stage 65 run fingerprint:** `{stage65_run_fingerprint}`")


st.divider()
st.subheader("Execution session manifest")

with st.expander("Session manifest payload", expanded=False):
    st.json(session_manifest)

st.subheader("Session constraints")
for item in session_constraints:
    st.write(f"- {item}")


st.divider()
st.subheader("Stage 65 persistence")

if existing_stage65:
    st.success(
        f"Stage 65 este deja persistată. Run ID: {existing_stage65.get('id')} — "
        f"Outcome: {existing_stage65.get('session_outcome')}"
    )
else:
    st.info(
        "Persistă sesiunea controlată. "
        "Această acțiune NU deschide portalul și NU începe execuția externă."
    )

if st.button(
    "🧭 Prepare & persist Stage 65 portal action session",
    type="primary",
    use_container_width=True,
    key="stage65_persist",
    disabled=(stage65_gate != "READY"),
):
    try:
        saved = persist_stage65()

        st.success(
            f"Stage 65 persisted — Run ID {saved.get('id')} — "
            f"Outcome {saved.get('session_outcome')}"
        )

        st.rerun()

    except Exception as exc:
        st.error(
            "Stage 65 persistence failed. Rulează mai întâi SQL-ul Stage 65 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )


if existing_stage65:
    st.divider()
    st.subheader("Stage 65 outcome")

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage65.get("session_outcome"),
    )

    o2.metric(
        "Portal login started?",
        "YES" if bool(existing_stage65.get("portal_login_started")) else "NO",
    )

    o3.metric(
        "Upload started?",
        "YES" if bool(existing_stage65.get("upload_started")) else "NO",
    )

    o4.metric(
        "Submitted?",
        "YES" if bool(existing_stage65.get("external_submission_performed")) else "NO",
    )

    if normalize_text(existing_stage65.get("session_outcome")).upper() == "READY_TO_OPEN_PORTAL":
        st.success(
            "Stage 65 READY_TO_OPEN_PORTAL. The execution session is prepared and immutable. "
            "A future Stage 66 may begin controlled portal-session establishment. "
            "No login, upload, or submission has occurred at Stage 65."
        )


st.caption(
    "Invariantă Stage 65 v1.0: READY_TO_OPEN_PORTAL means only that a verified, immutable "
    "execution-session envelope exists. Stage 65 itself does not log in, upload, submit, "
    "sign, create financial commitments, or claim an external receipt."
)
