import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 63 v1.0 — AI EXTERNAL SUBMISSION EXECUTION AUTHORIZATION GATE
#
# Purpose:
#   Consume ONLY a persisted Stage 62 HANDOFF_READY result and create an
#   immutable authorization envelope for a FUTURE external submission
#   executor.
#
# Stage 63 DOES NOT:
#   - log into the EU Funding & Tenders Portal
#   - press Submit
#   - upload files to the portal
#   - sign legal declarations
#   - create financial commitments
#   - claim that the proposal was submitted or received
#
# Stage 63 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 62 COMPLETED + HANDOFF_READY
#   - Stage 62 run fingerprint stable
#   - Stage 62 handoff SHA256 stable
#   - Stage 61 approval still valid and immutable
#   - Stage 60 package SHA256 still bound
#   - Stage 59 READY_FOR_SUBMISSION_PREP
#   - Stage 57 PASS
#   - exact handoff manifest and package identity
#
# Stage 63 creates:
#   - execution authorization manifest
#   - executor constraints
#   - exact target-system metadata
#   - authorization_fingerprint
#   - authorization_sha256
#
# Outcomes:
#   READY_TO_EXECUTE
#   BLOCKED
#
# Handoff:
#   Stage 64 may consume ONLY READY_TO_EXECUTE.
#   Stage 64 is the first stage that may be designed to interact with an
#   external execution mechanism, but it must still verify real portal
#   state and must never mark SUBMITTED without real confirmation.
# =====================================================================

st.set_page_config(
    page_title="Stage 63 v1.0 — External Submission Execution Authorization",
    page_icon="🔐",
    layout="wide",
)

st.title("🔐 Etapa 63 v1.0 — AI External Submission Execution Authorization Gate")
st.caption(
    "Autorizează numai următorul executor controlat. "
    "Nu efectuează login, upload sau submission extern."
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


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


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
    st.error("Stage 63 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage63_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 63 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 62 HANDOFF_READY
# ---------------------------------------------------------------------

stage62_candidates = rows(
    "stage62_controlled_submission_handoff_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage62 = next(
    (
        r for r in stage62_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("handoff_outcome")).upper() == "HANDOFF_READY"
        and not bool(r.get("external_submission_performed"))
    ),
    None,
)

if stage62:
    stage62_run_id = str(stage62.get("id") or "")
    stage62_status = normalize_text(stage62.get("run_status")).upper()
    stage62_outcome = normalize_text(stage62.get("handoff_outcome")).upper()
    stage62_run_fingerprint = normalize_text(stage62.get("run_fingerprint"))
    stage62_handoff_sha256 = normalize_text(stage62.get("handoff_sha256"))

    stage61_run_id = str(stage62.get("stage61_run_id") or "")
    stage60_run_id = str(stage62.get("stage60_run_id") or "")
    stage59_run_id = str(stage62.get("stage59_run_id") or "")
    stage58_run_id = str(stage62.get("stage58_run_id") or "")
    stage57_run_id = str(stage62.get("stage57_run_id") or "")
else:
    stage62_run_id = ""
    stage62_status = "MISSING"
    stage62_outcome = "MISSING"
    stage62_run_fingerprint = ""
    stage62_handoff_sha256 = ""

    stage61_run_id = ""
    stage60_run_id = ""
    stage59_run_id = ""
    stage58_run_id = ""
    stage57_run_id = ""


# ---------------------------------------------------------------------
# Load bound upstream rows
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
    return next((r for r in data if str(r.get("id") or "") == row_id), None)


stage61 = get_bound("stage61_human_approval_runs", stage61_run_id)
stage60 = get_bound("stage60_submission_package_runs", stage60_run_id)
stage59 = get_bound("stage59_submission_readiness_runs", stage59_run_id)
stage57 = get_bound("stage57_revalidation_runs", stage57_run_id)


# ---------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------

stage62_run_payload = as_dict(stage62.get("run_payload")) if stage62 else {}
recomputed_stage62_run_fingerprint = (
    stable_sha256(stage62_run_payload) if stage62_run_payload else ""
)

stage62_handoff_manifest = as_dict(stage62.get("handoff_manifest")) if stage62 else {}
recomputed_stage62_handoff_sha256 = (
    stable_sha256(stage62_handoff_manifest) if stage62_handoff_manifest else ""
)

stage61_run_payload = as_dict(stage61.get("run_payload")) if stage61 else {}
stored_stage61_run_fingerprint = normalize_text(stage61.get("run_fingerprint")) if stage61 else ""
recomputed_stage61_run_fingerprint = (
    stable_sha256(stage61_run_payload) if stage61_run_payload else ""
)

stage61_decision_payload = as_dict(stage61.get("decision_payload")) if stage61 else {}
stored_stage61_approval_fingerprint = normalize_text(stage61.get("approval_fingerprint")) if stage61 else ""
recomputed_stage61_approval_fingerprint = (
    stable_sha256(stage61_decision_payload) if stage61_decision_payload else ""
)

stage60_manifest = as_dict(stage60.get("manifest")) if stage60 else {}
stored_stage60_package_sha256 = normalize_text(stage60.get("package_sha256")) if stage60 else ""
recomputed_stage60_package_sha256 = (
    stable_sha256(stage60_manifest) if stage60_manifest else ""
)

stage59_outcome = normalize_text(stage59.get("readiness_outcome")).upper() if stage59 else "MISSING"
stage57_outcome = normalize_text(stage57.get("global_verdict")).upper() if stage57 else "MISSING"


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})


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
    "Stage 62 exists",
    bool(stage62),
    stage62_run_id or "MISSING",
)

add_check(
    "Stage 62 COMPLETED",
    stage62_status == "COMPLETED",
    stage62_status,
)

add_check(
    "Stage 62 HANDOFF_READY",
    stage62_outcome == "HANDOFF_READY",
    stage62_outcome,
)

add_check(
    "Stage 62 external_submission_performed = false",
    bool(stage62) and not bool(stage62.get("external_submission_performed")),
    f"external_submission_performed={bool(stage62.get('external_submission_performed')) if stage62 else None}",
)

add_check(
    "Stage 62 run fingerprint stable",
    bool(stage62_run_fingerprint)
    and stage62_run_fingerprint == recomputed_stage62_run_fingerprint,
    f"stored={stage62_run_fingerprint[:16]}..., recomputed={recomputed_stage62_run_fingerprint[:16]}...",
)

add_check(
    "Stage 62 handoff SHA256 stable",
    bool(stage62_handoff_sha256)
    and stage62_handoff_sha256 == recomputed_stage62_handoff_sha256,
    f"stored={stage62_handoff_sha256[:16]}..., recomputed={recomputed_stage62_handoff_sha256[:16]}...",
)

add_check(
    "Stage 61 approval exists",
    bool(stage61),
    stage61_run_id or "MISSING",
)

add_check(
    "Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF",
    normalize_text(stage61.get("approval_outcome")).upper() == "APPROVED_FOR_SUBMISSION_HANDOFF" if stage61 else False,
    normalize_text(stage61.get("approval_outcome")).upper() if stage61 else "MISSING",
)

add_check(
    "Stage 61 run fingerprint stable",
    bool(stored_stage61_run_fingerprint)
    and stored_stage61_run_fingerprint == recomputed_stage61_run_fingerprint,
    f"stored={stored_stage61_run_fingerprint[:16]}..., recomputed={recomputed_stage61_run_fingerprint[:16]}...",
)

add_check(
    "Stage 61 approval fingerprint stable",
    bool(stored_stage61_approval_fingerprint)
    and stored_stage61_approval_fingerprint == recomputed_stage61_approval_fingerprint,
    f"stored={stored_stage61_approval_fingerprint[:16]}..., recomputed={recomputed_stage61_approval_fingerprint[:16]}...",
)

add_check(
    "Stage 60 PACKAGE_READY",
    normalize_text(stage60.get("package_outcome")).upper() == "PACKAGE_READY" if stage60 else False,
    normalize_text(stage60.get("package_outcome")).upper() if stage60 else "MISSING",
)

add_check(
    "Stage 60 package SHA256 stable",
    bool(stored_stage60_package_sha256)
    and stored_stage60_package_sha256 == recomputed_stage60_package_sha256,
    f"stored={stored_stage60_package_sha256[:16]}..., recomputed={recomputed_stage60_package_sha256[:16]}...",
)

add_check(
    "Stage 59 READY_FOR_SUBMISSION_PREP",
    stage59_outcome == "READY_FOR_SUBMISSION_PREP",
    stage59_outcome,
)

add_check(
    "Stage 57 PASS",
    stage57_outcome == "PASS",
    stage57_outcome,
)

stage63_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Stage 62 handoff and all bound authorization/package fingerprints are stable."
    if stage63_gate == "READY"
    else "Stage 63 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Execution authorization manifest
# ---------------------------------------------------------------------

executor_constraints = [
    "The executor must verify opportunity_identity, lock_id, deadline and Stage 63 authorization_sha256 before any external action.",
    "The executor must verify the live portal opportunity/topic identity against the locked opportunity before upload or submission.",
    "The executor must not alter proposal content without invalidating this authorization and returning to the appropriate upstream stage.",
    "The executor must not sign legal declarations unless separately authorized by the human user in the external system.",
    "The executor must not create or accept financial commitments automatically.",
    "The executor must not mark SUBMITTED unless a real external portal confirmation/receipt is obtained.",
    "If portal state, deadline, package hash or opportunity identity differs, execution must stop with BLOCKED.",
]

execution_authorization_manifest = {
    "authorization_version": "stage63-v1.0",
    "authorization_type": "EXTERNAL_SUBMISSION_EXECUTION_AUTHORIZATION",

    "target": {
        "system": "EU Funding & Tenders Portal",
        "action_scope": [
            "VERIFY_PORTAL_STATE",
            "VERIFY_TOPIC_IDENTITY",
            "PREPARE_EXTERNAL_EXECUTION",
        ],
        "submission_authorized_at_stage63": False,
        "external_submission_performed": False,
    },

    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage57_run_id": stage57_run_id,
    "stage58_run_id": stage58_run_id or None,
    "stage59_run_id": stage59_run_id,
    "stage60_run_id": stage60_run_id,
    "stage61_run_id": stage61_run_id,
    "stage62_run_id": stage62_run_id,

    "stage60_package_sha256": stored_stage60_package_sha256,
    "stage61_approval_fingerprint": stored_stage61_approval_fingerprint,
    "stage62_run_fingerprint": stage62_run_fingerprint,
    "stage62_handoff_sha256": stage62_handoff_sha256,

    "handoff_manifest_sha256": recomputed_stage62_handoff_sha256,
    "executor_constraints": executor_constraints,
}

authorization_sha256 = stable_sha256(execution_authorization_manifest)

run_basis = {
    "stage": 63,
    "fingerprint_contract": "stage63-v1.0-external-execution-authorization",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "stage62_run_id": stage62_run_id,
    "stage62_handoff_sha256": stage62_handoff_sha256,
    "stage61_approval_fingerprint": stored_stage61_approval_fingerprint,
    "stage60_package_sha256": stored_stage60_package_sha256,
    "authorization_sha256": authorization_sha256,
    "stage63_gate": stage63_gate,
}

stage63_run_fingerprint = stable_sha256(run_basis)
stage63_outcome = "READY_TO_EXECUTE" if stage63_gate == "READY" else "BLOCKED"


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage63():
    data = (
        supabase.table("stage63_external_execution_authorization_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage62_run_id", stage62_run_id)
        .eq("run_fingerprint", stage63_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage63():
    if stage63_gate != "READY":
        raise RuntimeError("Stage 63 is BLOCKED.")

    existing = load_existing_stage63()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage57_run_id": stage57_run_id,
        "stage58_run_id": stage58_run_id or None,
        "stage59_run_id": stage59_run_id,
        "stage60_run_id": stage60_run_id,
        "stage61_run_id": stage61_run_id,
        "stage62_run_id": stage62_run_id,

        "stage": 63,
        "authorization_version": "stage63-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage60_package_sha256": stored_stage60_package_sha256,
        "stage61_approval_fingerprint": stored_stage61_approval_fingerprint,
        "stage62_handoff_sha256": stage62_handoff_sha256,

        "run_status": "COMPLETED",
        "authorization_outcome": stage63_outcome,

        "target_system": "EU Funding & Tenders Portal",

        "portal_state_verified": False,
        "topic_identity_verified_live": False,
        "external_execution_started": False,
        "external_submission_performed": False,
        "external_receipt_obtained": False,

        "constraint_count": len(executor_constraints),

        "run_fingerprint": stage63_run_fingerprint,
        "authorization_sha256": authorization_sha256,

        "authorization_manifest": execution_authorization_manifest,
        "run_payload": run_basis,

        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    try:
        rpc_result = supabase.rpc(
            "persist_stage63_execution_authorization",
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
        supabase.table("stage63_external_execution_authorization_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 63 execution authorization.")

    return data[0]


existing_stage63 = load_existing_stage63()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 62 → Stage 63 execution authorization binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 62", stage62_outcome)
m2.metric("Stage 61", normalize_text(stage61.get("approval_outcome")).upper() if stage61 else "MISSING")
m3.metric("Constraints", len(executor_constraints))
m4.metric("Integrity", "VERIFIED" if stage63_gate == "READY" else "FAILED")

with st.expander("Stage 63 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.write(f"**Gate:** `{stage63_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Stage 60 package SHA256:** `{stored_stage60_package_sha256}`")
st.write(f"**Stage 61 approval fingerprint:** `{stored_stage61_approval_fingerprint}`")
st.write(f"**Stage 62 handoff SHA256:** `{stage62_handoff_sha256}`")
st.write(f"**Stage 63 authorization SHA256:** `{authorization_sha256}`")
st.write(f"**Stage 63 run fingerprint:** `{stage63_run_fingerprint}`")

st.divider()
st.subheader("Execution authorization manifest")

with st.expander("Authorization manifest payload", expanded=False):
    st.json(execution_authorization_manifest)

st.subheader("Executor constraints")
for item in executor_constraints:
    st.write(f"- {item}")

st.divider()
st.subheader("Stage 63 persistence")

if existing_stage63:
    st.success(
        f"Stage 63 este deja persistată. Run ID: {existing_stage63.get('id')} — "
        f"Outcome: {existing_stage63.get('authorization_outcome')}"
    )
else:
    st.info(
        "Persistă autorizația controlată pentru următorul executor. "
        "Această acțiune nu deschide portalul și nu trimite aplicația."
    )

if st.button(
    "🔐 Authorize & persist Stage 63 external execution gate",
    type="primary",
    use_container_width=True,
    key="stage63_persist",
    disabled=(stage63_gate != "READY"),
):
    try:
        saved = persist_stage63()
        st.success(
            f"Stage 63 persisted — Run ID {saved.get('id')} — "
            f"Outcome {saved.get('authorization_outcome')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 63 persistence failed. Rulează mai întâi SQL-ul Stage 63 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage63:
    st.divider()
    st.subheader("Stage 63 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage63.get("authorization_outcome"))
    o2.metric("Portal verified?", "YES" if bool(existing_stage63.get("portal_state_verified")) else "NO")
    o3.metric("Execution started?", "YES" if bool(existing_stage63.get("external_execution_started")) else "NO")
    o4.metric("Submitted?", "YES" if bool(existing_stage63.get("external_submission_performed")) else "NO")

    if normalize_text(existing_stage63.get("authorization_outcome")).upper() == "READY_TO_EXECUTE":
        st.success(
            "Stage 63 READY_TO_EXECUTE. The next controlled stage may verify live portal state. "
            "No portal login, external execution, or submission has occurred at Stage 63."
        )

st.caption(
    "Invariantă Stage 63 v1.0: READY_TO_EXECUTE authorizes only the next controlled workflow stage. "
    "It is not evidence of portal login, upload, submission, signature, financial commitment, or European Commission receipt."
)
