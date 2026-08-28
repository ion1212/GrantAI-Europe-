import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 62 v1.0 — AI CONTROLLED SUBMISSION HANDOFF EXECUTION
#
# Purpose:
#   Consume ONLY a persisted Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF
#   decision and create an immutable controlled handoff package for a
#   future external submission executor.
#
# Stage 62 DOES NOT:
#   - log into the Funding & Tenders Portal
#   - submit the proposal externally
#   - click final Submit
#   - sign declarations
#   - create financial commitments
#   - claim European Commission receipt
#
# Stage 62 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 61 COMPLETED + APPROVED_FOR_SUBMISSION_HANDOFF
#   - Stage 61 run/approval fingerprints are stable
#   - Stage 60 PACKAGE_READY and package SHA256 still match
#   - Stage 59 READY_FOR_SUBMISSION_PREP remains bound
#   - Stage 57 PASS remains bound
#   - exact Stage 60 package sections are preserved
#
# Stage 62 produces:
#   - immutable handoff manifest
#   - handoff_fingerprint / handoff_sha256
#   - destination metadata
#   - execution constraints
#   - final section inventory
#   - controlled external-execution authorization state
#
# Outcomes:
#   HANDOFF_READY
#   BLOCKED
#
# Handoff:
#   Stage 63 may consume ONLY HANDOFF_READY.
# =====================================================================

st.set_page_config(
    page_title="Stage 62 v1.0 — Controlled Submission Handoff Execution",
    page_icon="🚦",
    layout="wide",
)

st.title("🚦 Etapa 62 v1.0 — AI Controlled Submission Handoff Execution")
st.caption(
    "Transformă aprobarea Stage 61 într-un handoff intern, imuabil, pentru un viitor executor extern controlat. "
    "Nu efectuează submission pe portal."
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


def text_sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


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
    st.error("Stage 62 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage62_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 62 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 61 approval
# ---------------------------------------------------------------------

stage61_candidates = rows(
    "stage61_human_approval_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage61 = next(
    (
        r for r in stage61_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("approval_outcome")).upper() == "APPROVED_FOR_SUBMISSION_HANDOFF"
    ),
    None,
)

if stage61:
    stage61_run_id = str(stage61.get("id") or "")
    stage61_status = normalize_text(stage61.get("run_status")).upper()
    stage61_outcome = normalize_text(stage61.get("approval_outcome")).upper()
    stage61_run_fingerprint = normalize_text(stage61.get("run_fingerprint"))
    stage61_approval_fingerprint = normalize_text(stage61.get("approval_fingerprint"))

    stage60_run_id = str(stage61.get("stage60_run_id") or "")
    stage59_run_id = str(stage61.get("stage59_run_id") or "")
    stage58_run_id = str(stage61.get("stage58_run_id") or "")
    stage57_run_id = str(stage61.get("stage57_run_id") or "")
else:
    stage61_run_id = ""
    stage61_status = "MISSING"
    stage61_outcome = "MISSING"
    stage61_run_fingerprint = ""
    stage61_approval_fingerprint = ""

    stage60_run_id = ""
    stage59_run_id = ""
    stage58_run_id = ""
    stage57_run_id = ""


# ---------------------------------------------------------------------
# Load bound Stage 60 / 59 / 57
# ---------------------------------------------------------------------

stage60 = next(
    (
        r for r in rows(
            "stage60_submission_package_runs",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
            },
            "created_at",
            100,
        )
        if str(r.get("id") or "") == stage60_run_id
    ),
    None,
) if stage60_run_id else None

stage59 = next(
    (
        r for r in rows(
            "stage59_submission_readiness_runs",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
            },
            "created_at",
            100,
        )
        if str(r.get("id") or "") == stage59_run_id
    ),
    None,
) if stage59_run_id else None

stage57 = next(
    (
        r for r in rows(
            "stage57_revalidation_runs",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
            },
            "created_at",
            200,
        )
        if str(r.get("id") or "") == stage57_run_id
    ),
    None,
) if stage57_run_id else None

stage60_sections = rows(
    "stage60_submission_package_sections",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage60_run_id": stage60_run_id,
    },
    "section_order",
    1000,
) if stage60_run_id else []

stage60_limitations = rows(
    "stage60_submission_package_limitations",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage60_run_id": stage60_run_id,
    },
    "limitation_order",
    1000,
) if stage60_run_id else []


# ---------------------------------------------------------------------
# Fingerprint verification
# ---------------------------------------------------------------------

stage61_run_payload = as_dict(stage61.get("run_payload")) if stage61 else {}
recomputed_stage61_run_fingerprint = stable_sha256(stage61_run_payload) if stage61_run_payload else ""

stage61_decision_payload = as_dict(stage61.get("decision_payload")) if stage61 else {}
recomputed_stage61_approval_fingerprint = (
    stable_sha256(stage61_decision_payload) if stage61_decision_payload else ""
)

stage60_run_payload = as_dict(stage60.get("run_payload")) if stage60 else {}
stored_stage60_run_fingerprint = normalize_text(stage60.get("run_fingerprint")) if stage60 else ""
recomputed_stage60_run_fingerprint = (
    stable_sha256(stage60_run_payload) if stage60_run_payload else ""
)

stage60_manifest = as_dict(stage60.get("manifest")) if stage60 else {}
stored_stage60_package_sha256 = normalize_text(stage60.get("package_sha256")) if stage60 else ""
recomputed_stage60_package_sha256 = (
    stable_sha256(stage60_manifest) if stage60_manifest else ""
)

stage59_outcome = normalize_text(stage59.get("readiness_outcome")).upper() if stage59 else "MISSING"
stage57_outcome = normalize_text(stage57.get("global_verdict")).upper() if stage57 else "MISSING"

section_sha_ok = bool(stage60_sections) and all(
    normalize_text(s.get("final_text_sha256"))
    == text_sha256(normalize_text(s.get("final_text")))
    for s in stage60_sections
)


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
    "Stage 61 exists",
    bool(stage61),
    stage61_run_id or "MISSING",
)

add_check(
    "Stage 61 COMPLETED",
    stage61_status == "COMPLETED",
    stage61_status,
)

add_check(
    "Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF",
    stage61_outcome == "APPROVED_FOR_SUBMISSION_HANDOFF",
    stage61_outcome,
)

add_check(
    "Stage 61 run fingerprint stable",
    bool(stage61_run_fingerprint)
    and stage61_run_fingerprint == recomputed_stage61_run_fingerprint,
    f"stored={stage61_run_fingerprint[:16]}..., recomputed={recomputed_stage61_run_fingerprint[:16]}...",
)

add_check(
    "Stage 61 approval fingerprint stable",
    bool(stage61_approval_fingerprint)
    and stage61_approval_fingerprint == recomputed_stage61_approval_fingerprint,
    f"stored={stage61_approval_fingerprint[:16]}..., recomputed={recomputed_stage61_approval_fingerprint[:16]}...",
)

add_check(
    "Stage 60 exists",
    bool(stage60),
    stage60_run_id or "MISSING",
)

add_check(
    "Stage 60 PACKAGE_READY",
    normalize_text(stage60.get("package_outcome")).upper() == "PACKAGE_READY" if stage60 else False,
    normalize_text(stage60.get("package_outcome")).upper() if stage60 else "MISSING",
)

add_check(
    "Stage 60 run fingerprint stable",
    bool(stored_stage60_run_fingerprint)
    and stored_stage60_run_fingerprint == recomputed_stage60_run_fingerprint,
    f"stored={stored_stage60_run_fingerprint[:16]}..., recomputed={recomputed_stage60_run_fingerprint[:16]}...",
)

add_check(
    "Stage 60 package SHA256 stable",
    bool(stored_stage60_package_sha256)
    and stored_stage60_package_sha256 == recomputed_stage60_package_sha256,
    f"stored={stored_stage60_package_sha256[:16]}..., recomputed={recomputed_stage60_package_sha256[:16]}...",
)

add_check(
    "Stage 60 final section SHA256 stable",
    section_sha_ok,
    f"sections={len(stage60_sections)}, sha_ok={section_sha_ok}",
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

stage62_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Stage 61 human approval and Stage 60 package are stable and eligible for controlled handoff creation."
    if stage62_gate == "READY"
    else "Stage 62 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Controlled handoff manifest
# ---------------------------------------------------------------------

execution_constraints = [
    "External portal login is not performed by Stage 62.",
    "Final submission is not performed by Stage 62.",
    "Legal declarations are not signed by Stage 62.",
    "Financial commitments are not created by Stage 62.",
    "Any future external executor must verify the same opportunity identity, deadline, lock ID, package SHA256, Stage 61 approval fingerprint and handoff SHA256 before acting.",
    "Any mutation of the proposal package invalidates this handoff and requires a new readiness / approval chain.",
]

handoff_manifest = {
    "handoff_version": "stage62-v1.0",
    "handoff_type": "CONTROLLED_EXTERNAL_SUBMISSION_HANDOFF",
    "destination": {
        "system": "EU Funding & Tenders Portal",
        "mode": "FUTURE_CONTROLLED_EXECUTOR",
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

    "stage60_run_fingerprint": stored_stage60_run_fingerprint,
    "stage60_package_sha256": stored_stage60_package_sha256,
    "stage61_run_fingerprint": stage61_run_fingerprint,
    "stage61_approval_fingerprint": stage61_approval_fingerprint,

    "final_sections": [
        {
            "section_order": int(s.get("section_order") or 0),
            "section_key": normalize_text(s.get("section_key")),
            "section_title": normalize_text(s.get("section_title")),
            "final_text_sha256": normalize_text(s.get("final_text_sha256")),
            "source_corrected_draft_sha256": normalize_text(s.get("source_corrected_draft_sha256")),
            "stage57_section_verdict": normalize_text(s.get("stage57_section_verdict")).upper(),
            "stage57_audit_sha256": normalize_text(s.get("stage57_audit_sha256")),
        }
        for s in stage60_sections
    ],

    "limitations": [
        normalize_text(i.get("limitation_text"))
        for i in stage60_limitations
    ],

    "execution_constraints": execution_constraints,
}

handoff_sha256 = stable_sha256(handoff_manifest)

run_basis = {
    "stage": 62,
    "fingerprint_contract": "stage62-v1.0-controlled-handoff",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "stage61_run_id": stage61_run_id,
    "stage61_approval_fingerprint": stage61_approval_fingerprint,
    "stage60_run_id": stage60_run_id,
    "stage60_package_sha256": stored_stage60_package_sha256,
    "handoff_sha256": handoff_sha256,
    "stage62_gate": stage62_gate,
}

stage62_run_fingerprint = stable_sha256(run_basis)
stage62_outcome = "HANDOFF_READY" if stage62_gate == "READY" else "BLOCKED"


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage62():
    data = (
        supabase.table("stage62_controlled_submission_handoff_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage61_run_id", stage61_run_id)
        .eq("run_fingerprint", stage62_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage62():
    if stage62_gate != "READY":
        raise RuntimeError("Stage 62 is BLOCKED.")

    existing = load_existing_stage62()
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

        "stage": 62,
        "handoff_version": "stage62-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage60_package_sha256": stored_stage60_package_sha256,
        "stage61_approval_fingerprint": stage61_approval_fingerprint,

        "run_status": "COMPLETED",
        "handoff_outcome": stage62_outcome,

        "destination_system": "EU Funding & Tenders Portal",
        "external_submission_performed": False,

        "section_count": len(stage60_sections),
        "constraint_count": len(execution_constraints),

        "run_fingerprint": stage62_run_fingerprint,
        "handoff_sha256": handoff_sha256,

        "handoff_manifest": handoff_manifest,
        "run_payload": run_basis,

        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    # Prefer atomic RPC when available.
    try:
        rpc_result = supabase.rpc(
            "persist_stage62_controlled_handoff",
            {"p_payload": payload},
        ).execute()

        if rpc_result.data:
            if isinstance(rpc_result.data, list):
                return rpc_result.data[0]
            if isinstance(rpc_result.data, dict):
                return rpc_result.data
    except Exception:
        pass

    # Fallback direct insert.
    data = (
        supabase.table("stage62_controlled_submission_handoff_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 62 controlled handoff.")

    return data[0]


existing_stage62 = load_existing_stage62()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 61 → Stage 62 controlled handoff binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 61", stage61_outcome)
m2.metric("Stage 60 package", normalize_text(stage60.get("package_outcome")).upper() if stage60 else "MISSING")
m3.metric("Sections", len(stage60_sections))
m4.metric("Integrity", "VERIFIED" if stage62_gate == "READY" else "FAILED")

with st.expander("Stage 62 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.write(f"**Gate:** `{stage62_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Stage 60 package SHA256:** `{stored_stage60_package_sha256}`")
st.write(f"**Stage 61 approval fingerprint:** `{stage61_approval_fingerprint}`")
st.write(f"**Stage 62 handoff SHA256:** `{handoff_sha256}`")
st.write(f"**Stage 62 run fingerprint:** `{stage62_run_fingerprint}`")


st.divider()
st.subheader("Controlled handoff manifest")

with st.expander("Handoff manifest payload", expanded=False):
    st.json(handoff_manifest)

st.subheader("Execution constraints")
for item in execution_constraints:
    st.write(f"- {item}")

st.divider()
st.subheader("Stage 62 persistence")

if existing_stage62:
    st.success(
        f"Stage 62 este deja persistată. Run ID: {existing_stage62.get('id')} — "
        f"Outcome: {existing_stage62.get('handoff_outcome')}"
    )
else:
    st.info(
        "Persistă handoff-ul controlat. Această acțiune nu deschide portalul și nu trimite proiectul."
    )

if st.button(
    "🚦 Create & persist controlled submission handoff",
    type="primary",
    use_container_width=True,
    key="stage62_persist",
    disabled=(stage62_gate != "READY"),
):
    try:
        saved = persist_stage62()
        st.success(
            f"Stage 62 persisted — Run ID {saved.get('id')} — "
            f"Outcome {saved.get('handoff_outcome')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 62 persistence failed. Rulează mai întâi SQL-ul Stage 62 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage62:
    st.divider()
    st.subheader("Stage 62 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage62.get("handoff_outcome"))
    o2.metric("Sections", existing_stage62.get("section_count"))
    o3.metric("Constraints", existing_stage62.get("constraint_count"))
    o4.metric(
        "External submitted?",
        "YES" if bool(existing_stage62.get("external_submission_performed")) else "NO",
    )

    if normalize_text(existing_stage62.get("handoff_outcome")).upper() == "HANDOFF_READY":
        st.success(
            "Stage 62 HANDOFF_READY. Controlled handoff is immutable and ready for a future Stage 63 external-execution gate. "
            "No external submission has occurred."
        )

st.caption(
    "Invariantă Stage 62 v1.0: HANDOFF_READY authorizes only the next controlled workflow stage. "
    "It is not evidence of portal login, submission, signature, financial commitment, or European Commission receipt."
)
