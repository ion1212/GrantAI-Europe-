import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 59 v1.0 — AI SUBMISSION READINESS FINALIZATION GATE
#
# Purpose:
#   Consume the FINAL persisted Stage 57 PASS generation and produce a
#   fail-closed submission-readiness decision.
#
# Stage 59 DOES NOT:
#   - submit to any external portal
#   - log in to Funding & Tenders
#   - sign legal declarations
#   - create financial commitments
#   - fabricate consortium partners, legal, technical, or financial facts
#
# Stage 59 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 57 PASS, COMPLETED
#   - Stage 57 result fingerprint stable
#   - Stage 56 update fingerprint stable
#   - Stage 55 resolution fingerprint stable
#   - optional Stage 58 handoff fingerprint stable
#   - no NEEDS_EVIDENCE / CONTRADICTED claims in final Stage 57
#   - all Stage 57 audited sections PASS
#   - corrected draft SHA256 chain retained
#   - final readiness checklist
#
# Outcomes:
#   READY_FOR_SUBMISSION_PREP
#   NOT_READY
#   BLOCKED
#
# Handoff:
#   Stage 60 may consume ONLY READY_FOR_SUBMISSION_PREP.
# =====================================================================

st.set_page_config(
    page_title="Stage 59 v1.0 — Submission Readiness Finalization",
    page_icon="✅",
    layout="wide",
)

st.title("✅ Etapa 59 v1.0 — AI Submission Readiness Finalization Gate")
st.caption(
    "Finalizează readiness-ul intern după Stage 57 PASS. "
    "Nu efectuează submission extern și nu inventează fapte."
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
    st.error("Stage 59 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage59_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 59 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load FINAL Stage 57 PASS generation
# ---------------------------------------------------------------------

stage57_candidates = rows(
    "stage57_revalidation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    200,
)

stage57 = next(
    (
        r for r in stage57_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("global_verdict")).upper() == "PASS"
    ),
    None,
)

if stage57:
    stage57_run_id = str(stage57.get("id") or "")
    stage57_status = normalize_text(stage57.get("run_status")).upper()
    stage57_verdict = normalize_text(stage57.get("global_verdict")).upper()
    stage57_run_fingerprint = normalize_text(stage57.get("run_fingerprint"))
    stage57_result_fingerprint = normalize_text(stage57.get("result_fingerprint"))

    stage56_run_id = str(stage57.get("stage56_run_id") or "")
    stage55_run_id = str(stage57.get("stage55_run_id") or "")
    stage54_run_id = str(stage57.get("stage54_run_id") or "")
    stage52_run_id = str(stage57.get("stage52_run_id") or "")

    stage58_run_id = str(stage57.get("stage58_run_id") or "")
    stage58_result_fingerprint = normalize_text(stage57.get("stage58_result_fingerprint"))
    parent_stage57_run_id = str(stage57.get("parent_stage57_run_id") or "")
    revalidation_generation = int(stage57.get("revalidation_generation") or 0)
else:
    stage57_run_id = ""
    stage57_status = "MISSING"
    stage57_verdict = "MISSING"
    stage57_run_fingerprint = ""
    stage57_result_fingerprint = ""

    stage56_run_id = ""
    stage55_run_id = ""
    stage54_run_id = ""
    stage52_run_id = ""

    stage58_run_id = ""
    stage58_result_fingerprint = ""
    parent_stage57_run_id = ""
    revalidation_generation = 0


# ---------------------------------------------------------------------
# Load final Stage 57 section/claim audits
# ---------------------------------------------------------------------

stage57_items = rows(
    "stage57_revalidation_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage57_run_id": stage57_run_id,
    },
    "created_at",
    1000,
) if stage57_run_id else []

stage57_claims = rows(
    "stage57_claim_audits",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage57_run_id": stage57_run_id,
    },
    "claim_no",
    5000,
) if stage57_run_id else []

needs_evidence_claims = [
    c for c in stage57_claims
    if normalize_text(c.get("classification")).upper() == "NEEDS_EVIDENCE"
]

contradicted_claims = [
    c for c in stage57_claims
    if normalize_text(c.get("classification")).upper() == "CONTRADICTED"
]

nonpass_sections = [
    i for i in stage57_items
    if normalize_text(i.get("section_verdict")).upper() != "PASS"
]


# ---------------------------------------------------------------------
# Load Stage 56 / 55 / optional 58
# ---------------------------------------------------------------------

stage56_candidates = rows(
    "stage56_resolution_update_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage56_run_id else []
stage56 = next((r for r in stage56_candidates if str(r.get("id") or "") == stage56_run_id), None)

stage55_candidates = rows(
    "stage55_confirmation_resolution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage55_run_id else []
stage55 = next((r for r in stage55_candidates if str(r.get("id") or "") == stage55_run_id), None)

if stage58_run_id:
    stage58_candidates = rows(
        "stage58_evidence_gap_resolution_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        100,
    )
    stage58 = next((r for r in stage58_candidates if str(r.get("id") or "") == stage58_run_id), None)
else:
    stage58 = None

stage56_corrected = rows(
    "stage56_corrected_drafts",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage56_run_id": stage56_run_id,
    },
    "created_at",
    500,
) if stage56_run_id else []


# ---------------------------------------------------------------------
# Fingerprints / integrity
# ---------------------------------------------------------------------

stage57_run_payload = as_dict(stage57.get("run_payload")) if stage57 else {}
recomputed_stage57_run_fingerprint = stable_sha256(stage57_run_payload) if stage57_run_payload else ""

stage57_result_payload = as_dict(stage57.get("result_payload")) if stage57 else {}
recomputed_stage57_result_fingerprint = stable_sha256(stage57_result_payload) if stage57_result_payload else ""

stage56_update_payload = as_dict(stage56.get("update_payload")) if stage56 else {}
stored_stage56_update_fingerprint = normalize_text(stage56.get("update_fingerprint")) if stage56 else ""
recomputed_stage56_update_fingerprint = stable_sha256(stage56_update_payload) if stage56_update_payload else ""

stage55_resolution_payload = as_dict(stage55.get("resolution_payload")) if stage55 else {}
stored_stage55_resolution_fingerprint = normalize_text(stage55.get("resolution_fingerprint")) if stage55 else ""
recomputed_stage55_resolution_fingerprint = (
    stable_sha256(stage55_resolution_payload) if stage55_resolution_payload else ""
)

if stage58:
    stage58_result_payload = as_dict(stage58.get("result_payload"))
    stored_stage58_result_fingerprint = normalize_text(stage58.get("result_fingerprint"))
    recomputed_stage58_result_fingerprint = (
        stable_sha256(stage58_result_payload) if stage58_result_payload else ""
    )
else:
    stage58_result_payload = {}
    stored_stage58_result_fingerprint = ""
    recomputed_stage58_result_fingerprint = ""

corrected_draft_sha_ok = all(
    normalize_text(i.get("corrected_draft_sha256"))
    == text_sha256(normalize_text(i.get("corrected_text")))
    for i in stage56_corrected
) if stage56_corrected else False


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
    "Stage 57 PASS run exists",
    bool(stage57),
    stage57_run_id or "MISSING",
)

add_check(
    "Stage 57 COMPLETED",
    stage57_status == "COMPLETED",
    stage57_status,
)

add_check(
    "Stage 57 verdict PASS",
    stage57_verdict == "PASS",
    stage57_verdict,
)

add_check(
    "Stage 57 run fingerprint stable",
    bool(stage57_run_fingerprint)
    and stage57_run_fingerprint == recomputed_stage57_run_fingerprint,
    f"stored={stage57_run_fingerprint[:16]}..., recomputed={recomputed_stage57_run_fingerprint[:16]}...",
)

add_check(
    "Stage 57 result fingerprint stable",
    bool(stage57_result_fingerprint)
    and stage57_result_fingerprint == recomputed_stage57_result_fingerprint,
    f"stored={stage57_result_fingerprint[:16]}..., recomputed={recomputed_stage57_result_fingerprint[:16]}...",
)

add_check(
    "Stage 57 all sections PASS",
    bool(stage57_items) and len(nonpass_sections) == 0,
    f"sections={len(stage57_items)}, nonpass={len(nonpass_sections)}",
)

add_check(
    "Stage 57 NEEDS_EVIDENCE = 0",
    len(needs_evidence_claims) == 0,
    f"needs_evidence={len(needs_evidence_claims)}",
)

add_check(
    "Stage 57 CONTRADICTED = 0",
    len(contradicted_claims) == 0,
    f"contradicted={len(contradicted_claims)}",
)

add_check(
    "Stage 56 exists",
    bool(stage56),
    stage56_run_id or "MISSING",
)

add_check(
    "Stage 56 update fingerprint stable",
    bool(stored_stage56_update_fingerprint)
    and stored_stage56_update_fingerprint == recomputed_stage56_update_fingerprint,
    f"stored={stored_stage56_update_fingerprint[:16]}..., recomputed={recomputed_stage56_update_fingerprint[:16]}...",
)

add_check(
    "Stage 56 corrected drafts exist",
    len(stage56_corrected) > 0,
    f"corrected_drafts={len(stage56_corrected)}",
)

add_check(
    "Stage 56 corrected SHA256 stable",
    corrected_draft_sha_ok,
    f"sha_ok={corrected_draft_sha_ok}",
)

add_check(
    "Stage 55 exists",
    bool(stage55),
    stage55_run_id or "MISSING",
)

add_check(
    "Stage 55 resolution fingerprint stable",
    bool(stored_stage55_resolution_fingerprint)
    and stored_stage55_resolution_fingerprint == recomputed_stage55_resolution_fingerprint,
    f"stored={stored_stage55_resolution_fingerprint[:16]}..., recomputed={recomputed_stage55_resolution_fingerprint[:16]}...",
)

if stage58_run_id:
    add_check(
        "Stage 58 exists",
        bool(stage58),
        stage58_run_id or "MISSING",
    )

    add_check(
        "Stage 58 READY_FOR_STAGE57_REAUDIT",
        normalize_text(stage58.get("resolution_outcome")).upper() == "READY_FOR_STAGE57_REAUDIT" if stage58 else False,
        normalize_text(stage58.get("resolution_outcome")).upper() if stage58 else "MISSING",
    )

    add_check(
        "Stage 58 result fingerprint stable",
        bool(stored_stage58_result_fingerprint)
        and stored_stage58_result_fingerprint == recomputed_stage58_result_fingerprint
        and stored_stage58_result_fingerprint == stage58_result_fingerprint,
        f"stage57_bound={stage58_result_fingerprint[:16]}..., stored={stored_stage58_result_fingerprint[:16]}..., recomputed={recomputed_stage58_result_fingerprint[:16]}...",
    )

stage59_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Final Stage 57 PASS chain is stable and contains zero unresolved or contradicted claims."
    if stage59_gate == "READY"
    else "Stage 59 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Submission-readiness checklist
# ---------------------------------------------------------------------

# These are internal readiness checks, not official Horizon portal approval.
readiness_checks = [
    {
        "key": "final_stage57_pass",
        "label": "Final Stage 57 verdict is PASS",
        "passed": stage57_verdict == "PASS",
        "blocking": True,
    },
    {
        "key": "zero_needs_evidence",
        "label": "No NEEDS_EVIDENCE claims remain",
        "passed": len(needs_evidence_claims) == 0,
        "blocking": True,
    },
    {
        "key": "zero_contradictions",
        "label": "No CONTRADICTED claims remain",
        "passed": len(contradicted_claims) == 0,
        "blocking": True,
    },
    {
        "key": "all_sections_pass",
        "label": "All audited proposal sections are PASS",
        "passed": bool(stage57_items) and len(nonpass_sections) == 0,
        "blocking": True,
    },
    {
        "key": "corrected_draft_integrity",
        "label": "Corrected draft SHA256 integrity is valid",
        "passed": corrected_draft_sha_ok,
        "blocking": True,
    },
    {
        "key": "deadline_valid",
        "label": "Official call deadline is still valid",
        "passed": future_deadline(deadline),
        "blocking": True,
    },
    {
        "key": "active_lock",
        "label": "Opportunity lock remains ACTIVE",
        "passed": normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
        "blocking": True,
    },
]

blocking_failures = [c for c in readiness_checks if c["blocking"] and not c["passed"]]

readiness_outcome = (
    "READY_FOR_SUBMISSION_PREP"
    if stage59_gate == "READY" and not blocking_failures
    else "NOT_READY"
)


# ---------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------

run_basis = {
    "stage": 59,
    "fingerprint_contract": "stage59-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage52_run_id": stage52_run_id,
    "stage54_run_id": stage54_run_id,
    "stage55_run_id": stage55_run_id,
    "stage56_run_id": stage56_run_id,
    "stage57_run_id": stage57_run_id,
    "parent_stage57_run_id": parent_stage57_run_id or None,
    "stage58_run_id": stage58_run_id or None,

    "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
    "stage56_update_fingerprint": stored_stage56_update_fingerprint,
    "stage57_run_fingerprint": stage57_run_fingerprint,
    "stage57_result_fingerprint": stage57_result_fingerprint,
    "stage58_result_fingerprint": stage58_result_fingerprint or None,

    "revalidation_generation": revalidation_generation,

    "final_section_inventory": [
        {
            "id": str(i.get("id") or ""),
            "section_key": normalize_text(i.get("section_key")),
            "section_verdict": normalize_text(i.get("section_verdict")).upper(),
            "audit_sha256": normalize_text(i.get("audit_sha256")),
            "corrected_draft_sha256": normalize_text(i.get("corrected_draft_sha256")),
        }
        for i in sorted(stage57_items, key=lambda x: normalize_text(x.get("section_key")))
    ],

    "corrected_draft_inventory": [
        {
            "id": str(i.get("id") or ""),
            "section_key": normalize_text(i.get("section_key")),
            "source_draft_sha256": normalize_text(i.get("source_draft_sha256")),
            "corrected_draft_sha256": normalize_text(i.get("corrected_draft_sha256")),
            "item_fingerprint": normalize_text(i.get("item_fingerprint")),
        }
        for i in sorted(stage56_corrected, key=lambda x: normalize_text(x.get("section_key")))
    ],

    "readiness_checks": readiness_checks,
    "stage59_gate": stage59_gate,
    "readiness_outcome": readiness_outcome,
}

stage59_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage59():
    data = (
        supabase.table("stage59_submission_readiness_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage57_run_id", stage57_run_id)
        .eq("run_fingerprint", stage59_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage59():
    if stage59_gate != "READY":
        raise RuntimeError("Stage 59 is BLOCKED.")

    existing = load_existing_stage59()
    if existing:
        return existing

    readiness_payload = {
        "stage59_run_fingerprint": stage59_run_fingerprint,
        "stage57_run_id": stage57_run_id,
        "stage57_result_fingerprint": stage57_result_fingerprint,
        "stage58_run_id": stage58_run_id or None,
        "stage58_result_fingerprint": stage58_result_fingerprint or None,
        "readiness_checks": readiness_checks,
        "readiness_outcome": readiness_outcome,
    }

    readiness_fingerprint = stable_sha256(readiness_payload)

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage52_run_id": stage52_run_id,
        "stage54_run_id": stage54_run_id,
        "stage55_run_id": stage55_run_id,
        "stage56_run_id": stage56_run_id,
        "stage57_run_id": stage57_run_id,
        "stage58_run_id": stage58_run_id or None,

        "stage": 59,
        "finalizer_version": "stage59-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
        "stage56_update_fingerprint": stored_stage56_update_fingerprint,
        "stage57_run_fingerprint": stage57_run_fingerprint,
        "stage57_result_fingerprint": stage57_result_fingerprint,
        "stage58_result_fingerprint": stage58_result_fingerprint or None,

        "run_status": "COMPLETED",
        "readiness_outcome": readiness_outcome,

        "total_checks": len(readiness_checks),
        "passed_checks": sum(1 for c in readiness_checks if c["passed"]),
        "failed_checks": sum(1 for c in readiness_checks if not c["passed"]),

        "run_fingerprint": stage59_run_fingerprint,
        "readiness_fingerprint": readiness_fingerprint,

        "run_payload": run_basis,
        "readiness_payload": readiness_payload,

        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = (
        supabase.table("stage59_submission_readiness_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 59 readiness result.")

    run = data[0]
    run_id = str(run["id"])

    for check in readiness_checks:
        supabase.table("stage59_submission_readiness_items").insert({
            "stage59_run_id": run_id,
            "stage57_run_id": stage57_run_id,

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "check_key": check["key"],
            "check_label": check["label"],
            "passed": bool(check["passed"]),
            "blocking": bool(check["blocking"]),

            "created_at": now_iso(),
            "updated_at": now_iso(),
        }).execute()

    return run


existing_stage59 = load_existing_stage59()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 57 → Stage 59 final binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 57", stage57_verdict)
m2.metric("Needs evidence", len(needs_evidence_claims))
m3.metric("Contradicted", len(contradicted_claims))
m4.metric("Integrity", "VERIFIED" if stage59_gate == "READY" else "FAILED")

with st.expander("Stage 59 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Submission readiness checklist")

st.dataframe(
    [
        {
            "Check": c["label"],
            "Passed": c["passed"],
            "Blocking": c["blocking"],
        }
        for c in readiness_checks
    ],
    use_container_width=True,
    hide_index=True,
)

c1, c2, c3 = st.columns(3)
c1.metric("Gate", stage59_gate)
c2.metric("Checks passed", f"{sum(1 for c in readiness_checks if c['passed'])}/{len(readiness_checks)}")
c3.metric("Readiness", readiness_outcome)

if stage59_gate == "READY":
    st.success("Stage 59 gate is READY.")
else:
    st.error("Stage 59 is BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage59_run_fingerprint, language=None)

st.divider()
st.subheader("Stage 59 persistence")

if existing_stage59:
    st.success(
        f"Stage 59 este deja persistată. Run ID: {existing_stage59.get('id')} — "
        f"Outcome: {existing_stage59.get('readiness_outcome')}"
    )
else:
    st.info("Persistă verdictul final de submission readiness.")

if st.button(
    "✅ Finalize & persist Stage 59 submission readiness",
    type="primary",
    use_container_width=True,
    key="stage59_persist",
    disabled=(stage59_gate != "READY"),
):
    try:
        saved = persist_stage59()
        st.success(
            f"Stage 59 persisted — Run ID {saved.get('id')} — "
            f"Outcome {saved.get('readiness_outcome')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 59 persistence failed. Rulează mai întâi SQL-ul Stage 59 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage59:
    st.divider()
    st.subheader("Stage 59 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage59.get("readiness_outcome"))
    o2.metric("Total checks", existing_stage59.get("total_checks"))
    o3.metric("Passed", existing_stage59.get("passed_checks"))
    o4.metric("Failed", existing_stage59.get("failed_checks"))

    outcome = normalize_text(existing_stage59.get("readiness_outcome")).upper()

    if outcome == "READY_FOR_SUBMISSION_PREP":
        st.success(
            "Stage 59 READY_FOR_SUBMISSION_PREP. Internal readiness is complete. "
            "A future Stage 60 may assemble the final submission-preparation package, "
            "but external submission, portal login, legal signature and financial commitment remain unauthorized."
        )
    else:
        st.warning(
            "Stage 59 NOT_READY. One or more blocking readiness checks failed."
        )

st.caption(
    "Invariantă Stage 59 v1.0: READY_FOR_SUBMISSION_PREP is an internal workflow state, "
    "not an official Horizon Europe eligibility or submission confirmation. "
    "External submission remains unauthorized."
)
