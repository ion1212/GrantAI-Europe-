import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 60 v1.0 — AI FINAL SUBMISSION PREPARATION PACKAGE
#
# Purpose:
#   Consume ONLY a persisted Stage 59 READY_FOR_SUBMISSION_PREP result
#   and assemble a final, immutable internal submission-preparation package.
#
# Stage 60 DOES NOT:
#   - log into Funding & Tenders
#   - submit externally
#   - sign legal declarations
#   - make financial commitments
#   - invent missing applicant/consortium facts
#
# Stage 60 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 59 COMPLETED + READY_FOR_SUBMISSION_PREP
#   - Stage 59 run/readiness fingerprints stable
#   - Stage 57 PASS chain still stable
#   - Stage 56 corrected draft integrity
#   - optional Stage 58 evidence-overlay integrity
#
# Stage 60 assembles:
#   - package manifest
#   - final proposal section snapshot(s)
#   - readiness summary
#   - evidence/provenance summary
#   - unresolved limitations / declarations
#   - immutable package SHA256
#
# Outcomes:
#   PACKAGE_READY
#   NOT_READY
#   BLOCKED
#
# Handoff:
#   Stage 61 may consume ONLY PACKAGE_READY.
# =====================================================================

st.set_page_config(
    page_title="Stage 60 v1.0 — Final Submission Preparation Package",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Etapa 60 v1.0 — AI Final Submission Preparation Package")
st.caption(
    "Asamblează pachetul intern final după Stage 59 READY_FOR_SUBMISSION_PREP. "
    "Nu efectuează submission extern."
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


def extract_latest(items: list, row_id: str):
    return next((r for r in items if str(r.get("id") or "") == row_id), None)


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
    st.error("Stage 60 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage60_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 60 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 59 READY_FOR_SUBMISSION_PREP
# ---------------------------------------------------------------------

stage59_candidates = rows(
    "stage59_submission_readiness_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage59 = next(
    (
        r for r in stage59_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("readiness_outcome")).upper() == "READY_FOR_SUBMISSION_PREP"
    ),
    None,
)

if stage59:
    stage59_run_id = str(stage59.get("id") or "")
    stage59_run_fingerprint = normalize_text(stage59.get("run_fingerprint"))
    stage59_readiness_fingerprint = normalize_text(stage59.get("readiness_fingerprint"))
    stage59_status = normalize_text(stage59.get("run_status")).upper()
    stage59_outcome = normalize_text(stage59.get("readiness_outcome")).upper()

    stage57_run_id = str(stage59.get("stage57_run_id") or "")
    stage58_run_id = str(stage59.get("stage58_run_id") or "")
    stage56_run_id = str(stage59.get("stage56_run_id") or "")
    stage55_run_id = str(stage59.get("stage55_run_id") or "")
    stage54_run_id = str(stage59.get("stage54_run_id") or "")
    stage52_run_id = str(stage59.get("stage52_run_id") or "")
else:
    stage59_run_id = ""
    stage59_run_fingerprint = ""
    stage59_readiness_fingerprint = ""
    stage59_status = "MISSING"
    stage59_outcome = "MISSING"

    stage57_run_id = ""
    stage58_run_id = ""
    stage56_run_id = ""
    stage55_run_id = ""
    stage54_run_id = ""
    stage52_run_id = ""


# ---------------------------------------------------------------------
# Load upstream chain
# ---------------------------------------------------------------------

stage57 = extract_latest(
    rows(
        "stage57_revalidation_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        200,
    ) if stage57_run_id else [],
    stage57_run_id,
)

stage58 = extract_latest(
    rows(
        "stage58_evidence_gap_resolution_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        100,
    ) if stage58_run_id else [],
    stage58_run_id,
)

stage56 = extract_latest(
    rows(
        "stage56_resolution_update_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        100,
    ) if stage56_run_id else [],
    stage56_run_id,
)

stage55 = extract_latest(
    rows(
        "stage55_confirmation_resolution_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        100,
    ) if stage55_run_id else [],
    stage55_run_id,
)

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

stage58_items = rows(
    "stage58_evidence_gap_resolution_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage58_run_id": stage58_run_id,
    },
    "claim_no",
    5000,
) if stage58_run_id else []

stage59_items = rows(
    "stage59_submission_readiness_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage59_run_id": stage59_run_id,
    },
    "created_at",
    1000,
) if stage59_run_id else []


# ---------------------------------------------------------------------
# Fingerprint verification
# ---------------------------------------------------------------------

stage59_run_payload = as_dict(stage59.get("run_payload")) if stage59 else {}
recomputed_stage59_run_fingerprint = stable_sha256(stage59_run_payload) if stage59_run_payload else ""

stage59_readiness_payload = as_dict(stage59.get("readiness_payload")) if stage59 else {}
recomputed_stage59_readiness_fingerprint = (
    stable_sha256(stage59_readiness_payload) if stage59_readiness_payload else ""
)

stage57_result_payload = as_dict(stage57.get("result_payload")) if stage57 else {}
stored_stage57_result_fingerprint = normalize_text(stage57.get("result_fingerprint")) if stage57 else ""
recomputed_stage57_result_fingerprint = (
    stable_sha256(stage57_result_payload) if stage57_result_payload else ""
)

stage56_update_payload = as_dict(stage56.get("update_payload")) if stage56 else {}
stored_stage56_update_fingerprint = normalize_text(stage56.get("update_fingerprint")) if stage56 else ""
recomputed_stage56_update_fingerprint = (
    stable_sha256(stage56_update_payload) if stage56_update_payload else ""
)

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

corrected_sha_ok = bool(stage56_corrected) and all(
    normalize_text(i.get("corrected_draft_sha256"))
    == text_sha256(normalize_text(i.get("corrected_text")))
    for i in stage56_corrected
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
    "Stage 59 exists",
    bool(stage59),
    stage59_run_id or "MISSING",
)

add_check(
    "Stage 59 COMPLETED",
    stage59_status == "COMPLETED",
    stage59_status,
)

add_check(
    "Stage 59 READY_FOR_SUBMISSION_PREP",
    stage59_outcome == "READY_FOR_SUBMISSION_PREP",
    stage59_outcome,
)

add_check(
    "Stage 59 run fingerprint stable",
    bool(stage59_run_fingerprint)
    and stage59_run_fingerprint == recomputed_stage59_run_fingerprint,
    f"stored={stage59_run_fingerprint[:16]}..., recomputed={recomputed_stage59_run_fingerprint[:16]}...",
)

add_check(
    "Stage 59 readiness fingerprint stable",
    bool(stage59_readiness_fingerprint)
    and stage59_readiness_fingerprint == recomputed_stage59_readiness_fingerprint,
    f"stored={stage59_readiness_fingerprint[:16]}..., recomputed={recomputed_stage59_readiness_fingerprint[:16]}...",
)

add_check(
    "Stage 59 all checks passed",
    bool(stage59_items) and all(bool(i.get("passed")) for i in stage59_items),
    f"checks={len(stage59_items)}, failed={sum(1 for i in stage59_items if not bool(i.get('passed')))}",
)

add_check(
    "Stage 57 exists",
    bool(stage57),
    stage57_run_id or "MISSING",
)

add_check(
    "Stage 57 PASS",
    normalize_text(stage57.get("global_verdict")).upper() == "PASS" if stage57 else False,
    normalize_text(stage57.get("global_verdict")).upper() if stage57 else "MISSING",
)

add_check(
    "Stage 57 result fingerprint stable",
    bool(stored_stage57_result_fingerprint)
    and stored_stage57_result_fingerprint == recomputed_stage57_result_fingerprint,
    f"stored={stored_stage57_result_fingerprint[:16]}..., recomputed={recomputed_stage57_result_fingerprint[:16]}...",
)

add_check(
    "Stage 57 no unresolved claims",
    all(
        normalize_text(c.get("classification")).upper() not in {"NEEDS_EVIDENCE", "CONTRADICTED"}
        for c in stage57_claims
    ),
    f"claims={len(stage57_claims)}",
)

add_check(
    "Stage 56 corrected drafts exist",
    bool(stage56_corrected),
    f"corrected_drafts={len(stage56_corrected)}",
)

add_check(
    "Stage 56 corrected SHA256 stable",
    corrected_sha_ok,
    f"sha_ok={corrected_sha_ok}",
)

add_check(
    "Stage 56 update fingerprint stable",
    bool(stored_stage56_update_fingerprint)
    and stored_stage56_update_fingerprint == recomputed_stage56_update_fingerprint,
    f"stored={stored_stage56_update_fingerprint[:16]}..., recomputed={recomputed_stage56_update_fingerprint[:16]}...",
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
        "Stage 58 result fingerprint stable",
        bool(stored_stage58_result_fingerprint)
        and stored_stage58_result_fingerprint == recomputed_stage58_result_fingerprint,
        f"stored={stored_stage58_result_fingerprint[:16]}..., recomputed={recomputed_stage58_result_fingerprint[:16]}...",
    )

stage60_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Stage 59 readiness chain is stable and the final corrected proposal snapshot is available."
    if stage60_gate == "READY"
    else "Stage 60 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Assemble package
# ---------------------------------------------------------------------

final_sections = []
for corrected in sorted(stage56_corrected, key=lambda x: normalize_text(x.get("section_key"))):
    section_key = normalize_text(corrected.get("section_key"))
    stage57_item = next(
        (i for i in stage57_items if normalize_text(i.get("section_key")) == section_key),
        None,
    )

    final_sections.append({
        "section_key": section_key,
        "section_title": normalize_text(corrected.get("section_title")),
        "final_text": normalize_text(corrected.get("corrected_text")),
        "corrected_draft_sha256": normalize_text(corrected.get("corrected_draft_sha256")),
        "stage57_section_verdict": normalize_text(stage57_item.get("section_verdict")).upper() if stage57_item else "",
        "stage57_audit_sha256": normalize_text(stage57_item.get("audit_sha256")) if stage57_item else "",
    })

evidence_summary = []
for claim in stage57_claims:
    evidence_summary.append({
        "section_key": normalize_text(claim.get("section_key")),
        "claim_no": int(claim.get("claim_no") or 0),
        "claim_text": normalize_text(claim.get("claim_text")),
        "classification": normalize_text(claim.get("classification")).upper(),
        "source_ids": as_list(claim.get("source_ids")),
        "violation_type": normalize_text(claim.get("violation_type")),
        "reason": normalize_text(claim.get("reason")),
    })

stage58_summary = []
for item in stage58_items:
    stage58_summary.append({
        "claim_no": int(item.get("claim_no") or 0),
        "claim_text": normalize_text(item.get("claim_text")),
        "resolution_status": normalize_text(item.get("resolution_status")).upper(),
        "resolution_basis": normalize_text(item.get("resolution_basis")).upper(),
        "resolved_source_ids": as_list(item.get("resolved_source_ids")),
        "resolution_value": normalize_text(item.get("resolution_value")),
        "resolution_note": normalize_text(item.get("resolution_note")),
    })

limitations = [
    "This package is an internal preparation artifact and is not an official European Commission submission.",
    "No portal login, legal signature, or financial commitment is performed by Stage 60.",
    "User-provided evidence is preserved as user-provided evidence and is not converted into independent official verification.",
]

manifest = {
    "package_version": "stage60-v1.0",
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
    "stage58_run_id": stage58_run_id or None,
    "stage59_run_id": stage59_run_id,

    "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
    "stage56_update_fingerprint": stored_stage56_update_fingerprint,
    "stage57_result_fingerprint": stored_stage57_result_fingerprint,
    "stage58_result_fingerprint": stored_stage58_result_fingerprint or None,
    "stage59_run_fingerprint": stage59_run_fingerprint,
    "stage59_readiness_fingerprint": stage59_readiness_fingerprint,

    "final_sections": final_sections,
    "evidence_summary": evidence_summary,
    "stage58_resolution_summary": stage58_summary,
    "readiness_checks": [
        {
            "check_key": normalize_text(i.get("check_key")),
            "check_label": normalize_text(i.get("check_label")),
            "passed": bool(i.get("passed")),
            "blocking": bool(i.get("blocking")),
        }
        for i in stage59_items
    ],
    "limitations": limitations,
}

package_sha256 = stable_sha256(manifest)

stage60_outcome = "PACKAGE_READY" if stage60_gate == "READY" else "BLOCKED"

run_basis = {
    "stage": 60,
    "fingerprint_contract": "stage60-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "stage59_run_id": stage59_run_id,
    "stage59_run_fingerprint": stage59_run_fingerprint,
    "stage59_readiness_fingerprint": stage59_readiness_fingerprint,
    "package_sha256": package_sha256,
    "stage60_gate": stage60_gate,
    "outcome": stage60_outcome,
}

stage60_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage60():
    data = (
        supabase.table("stage60_submission_package_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage59_run_id", stage59_run_id)
        .eq("run_fingerprint", stage60_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage60():
    if stage60_gate != "READY":
        raise RuntimeError("Stage 60 is BLOCKED.")

    existing = load_existing_stage60()
    if existing:
        return existing

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
        "stage59_run_id": stage59_run_id,

        "stage": 60,
        "assembler_version": "stage60-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "run_status": "COMPLETED",
        "package_outcome": stage60_outcome,

        "section_count": len(final_sections),
        "evidence_claim_count": len(evidence_summary),
        "stage58_resolution_count": len(stage58_summary),

        "run_fingerprint": stage60_run_fingerprint,
        "package_sha256": package_sha256,

        "manifest": manifest,
        "run_payload": run_basis,

        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = (
        supabase.table("stage60_submission_package_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 60 package.")

    run = data[0]
    run_id = str(run["id"])

    for idx, section in enumerate(final_sections, start=1):
        supabase.table("stage60_submission_package_sections").insert({
            "stage60_run_id": run_id,
            "stage59_run_id": stage59_run_id,
            "stage57_run_id": stage57_run_id,
            "stage56_run_id": stage56_run_id,

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "section_order": idx * 10,
            "section_key": section["section_key"],
            "section_title": section["section_title"],

            "final_text": section["final_text"],
            "final_text_sha256": text_sha256(section["final_text"]),

            "source_corrected_draft_sha256": section["corrected_draft_sha256"],
            "stage57_section_verdict": section["stage57_section_verdict"],
            "stage57_audit_sha256": section["stage57_audit_sha256"],

            "created_at": now_iso(),
            "updated_at": now_iso(),
        }).execute()

    for idx, limitation in enumerate(limitations, start=1):
        supabase.table("stage60_submission_package_limitations").insert({
            "stage60_run_id": run_id,

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "limitation_order": idx,
            "limitation_text": limitation,

            "created_at": now_iso(),
            "updated_at": now_iso(),
        }).execute()

    return run


existing_stage60 = load_existing_stage60()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 59 → Stage 60 package binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 59", stage59_outcome)
m2.metric("Final sections", len(final_sections))
m3.metric("Evidence claims", len(evidence_summary))
m4.metric("Integrity", "VERIFIED" if stage60_gate == "READY" else "FAILED")

with st.expander("Stage 60 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Final package manifest")

st.write(f"**Package SHA256:** `{package_sha256}`")
st.write(f"**Stage 60 run fingerprint:** `{stage60_run_fingerprint}`")

with st.expander("Manifest payload", expanded=False):
    st.json(manifest)

st.subheader("Final proposal sections")

if final_sections:
    st.dataframe(
        [
            {
                "Section": s["section_title"] or s["section_key"],
                "Stage 57 verdict": s["stage57_section_verdict"],
                "Corrected SHA256": s["corrected_draft_sha256"][:16] + "...",
                "Audit SHA256": s["stage57_audit_sha256"][:16] + "...",
            }
            for s in final_sections
        ],
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Package limitations")
for item in limitations:
    st.write(f"- {item}")

st.divider()
st.subheader("Stage 60 persistence")

if existing_stage60:
    st.success(
        f"Stage 60 este deja persistată. Run ID: {existing_stage60.get('id')} — "
        f"Outcome: {existing_stage60.get('package_outcome')}"
    )
else:
    st.info("Persistă pachetul final intern de submission preparation.")

if st.button(
    "📦 Assemble & persist Stage 60 final package",
    type="primary",
    use_container_width=True,
    key="stage60_persist",
    disabled=(stage60_gate != "READY"),
):
    try:
        saved = persist_stage60()
        st.success(
            f"Stage 60 persisted — Run ID {saved.get('id')} — "
            f"Outcome {saved.get('package_outcome')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 60 persistence failed. Rulează mai întâi SQL-ul Stage 60 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage60:
    st.divider()
    st.subheader("Stage 60 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage60.get("package_outcome"))
    o2.metric("Sections", existing_stage60.get("section_count"))
    o3.metric("Evidence claims", existing_stage60.get("evidence_claim_count"))
    o4.metric("Stage 58 resolutions", existing_stage60.get("stage58_resolution_count"))

    outcome = normalize_text(existing_stage60.get("package_outcome")).upper()

    if outcome == "PACKAGE_READY":
        st.success(
            "Stage 60 PACKAGE_READY. Internal final submission-preparation package is assembled and immutable. "
            "A future Stage 61 may perform final human approval / controlled submission handoff. "
            "External portal submission remains unauthorized at this stage."
        )
    else:
        st.warning("Stage 60 is not ready.")

st.caption(
    "Invariantă Stage 60 v1.0: package assembly is internal only; package SHA256 binds the exact final sections, "
    "evidence summary, readiness checks and limitations. No external submission is performed."
)
