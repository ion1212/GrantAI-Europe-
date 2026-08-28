import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 71 v1.4 — AI FINAL PORTAL SUBMISSION READINESS GATE
#
# Purpose:
#   Consume ONLY Stage 70 PORTAL_POST_UPLOAD_VALIDATED and establish
#   whether the exact portal draft is ready to enter a future explicit
#   submission-authorization stage.
#
# Stage 71 DOES NOT:
#   - press Submit
#   - sign declarations
#   - create financial commitments
#   - claim European Commission receipt
#   - collect credentials, MFA, cookies or tokens
#
# Stage 71 verifies:
#   - ACTIVE opportunity lock
#   - valid deadline
#   - Stage 70 COMPLETED + PORTAL_POST_UPLOAD_VALIDATED
#   - Stage 70 evidence SHA256 stable
#   - Stage 70 run fingerprint stable
#   - Stage 69 PORTAL_UPLOAD_CONFIRMED
#   - Stage 68 UPLOAD_PACKAGE_READY
#   - Stage 67 PORTAL_DRAFT_BOUND
#   - Stage 66 PORTAL_SESSION_ESTABLISHED
#   - Stage 60 PACKAGE_READY
#
# Human readiness confirmation requires:
#   - exact draft reference still visible
#   - no blocking validation errors
#   - uploaded file set still present
#   - proposal remains editable
#   - proposal has NOT been submitted
#   - no final receipt exists
#   - user explicitly understands Stage 71 does NOT submit
#   - exact phrase: CONFIRM STAGE 71 SUBMISSION READINESS
#
# Outcomes:
#   READY_FOR_SUBMISSION_AUTHORIZATION
#   BLOCKED
#
# Handoff:
#   Stage 72 may consume ONLY READY_FOR_SUBMISSION_AUTHORIZATION.
# =====================================================================


st.set_page_config(
    page_title="Stage 71 v1.4 — Final Portal Submission Readiness",
    page_icon="✅",
    layout="wide",
)

st.title("✅ Etapa 71 v1.4 — AI Final Portal Submission Readiness Gate")
st.caption(
    "Ultimul control de readiness înainte de o eventuală etapă separată de autorizare explicită a trimiterii. "
    "Stage 71 NU apasă Submit și NU trimite propunerea."
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

OFFICIAL_HOST_SUFFIXES = ("ec.europa.eu", "europa.eu")


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


def official_domain_ok(url: str) -> bool:
    try:
        parsed = urlparse(normalize_text(url))
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower().strip(".")
        return any(
            host == suffix or host.endswith("." + suffix)
            for suffix in OFFICIAL_HOST_SUFFIXES
        )
    except Exception:
        return False


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


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
        300,
    )

    return next(
        (r for r in data if str(r.get("id") or "") == row_id),
        None,
    )


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
    st.error("Stage 71 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
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
        key="stage71_project",
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
    st.error("Stage 71 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Stage 70 + deterministic upstream fallback resolution
# ---------------------------------------------------------------------

# Candidate sets for the same exact user/project/opportunity lock.
stage70_candidates = rows(
    "stage70_post_upload_portal_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage69_candidates = rows(
    "stage69_portal_upload_confirmation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage68_candidates = rows(
    "stage68_upload_package_preparation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage67_candidates = rows(
    "stage67_portal_draft_binding_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)


def valid_stage70_row(r: dict) -> bool:
    return (
        normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("validation_outcome")).upper()
        == "PORTAL_POST_UPLOAD_VALIDATED"
        and bool(r.get("portal_upload_performed"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
        and not bool(r.get("blocking_errors_present"))
    )


def valid_stage69_row(r: dict) -> bool:
    return (
        normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("upload_outcome")).upper()
        == "PORTAL_UPLOAD_CONFIRMED"
        and bool(r.get("portal_upload_performed"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    )


def valid_stage68_row(r: dict) -> bool:
    return (
        normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("preparation_outcome")).upper()
        == "UPLOAD_PACKAGE_READY"
        and not bool(r.get("portal_upload_performed"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    )


def valid_stage67_row(r: dict) -> bool:
    return (
        normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("binding_outcome")).upper()
        == "PORTAL_DRAFT_BOUND"
        and bool(r.get("draft_bound"))
        and bool(r.get("portal_session_established"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    )


# Resolve Stage 70 directly from its own table.
stage70 = next((r for r in stage70_candidates if valid_stage70_row(r)), None)

if stage70:
    stage70_run_id = str(stage70.get("id") or "")
    stage70_status = normalize_text(stage70.get("run_status")).upper()
    stage70_outcome = normalize_text(stage70.get("validation_outcome")).upper()

    stage70_evidence_sha256 = normalize_text(
        stage70.get("post_upload_evidence_sha256")
        or stage70.get("validation_evidence_sha256")
        or stage70.get("evidence_sha256")
    )
    stage70_run_fingerprint = normalize_text(stage70.get("run_fingerprint"))

    stage70_application_reference = normalize_text(stage70.get("application_reference"))
    stage70_draft_title = normalize_text(stage70.get("draft_title"))
    stage70_current_portal_url = normalize_text(stage70.get("current_portal_url"))

    stage69_run_id = str(stage70.get("stage69_run_id") or "")
    stage68_run_id = str(stage70.get("stage68_run_id") or "")
    stage67_run_id = str(stage70.get("stage67_run_id") or "")
    stage66_run_id = str(stage70.get("stage66_run_id") or "")
    stage65_run_id = str(stage70.get("stage65_run_id") or "")
    stage64_run_id = str(stage70.get("stage64_run_id") or "")
    stage63_run_id = str(stage70.get("stage63_run_id") or "")
    stage62_run_id = str(stage70.get("stage62_run_id") or "")
    stage61_run_id = str(stage70.get("stage61_run_id") or "")
    stage60_run_id = str(stage70.get("stage60_run_id") or "")
    stage59_run_id = str(stage70.get("stage59_run_id") or "")
    stage57_run_id = str(stage70.get("stage57_run_id") or "")
else:
    stage70_run_id = ""
    stage70_status = "MISSING"
    stage70_outcome = "MISSING"
    stage70_evidence_sha256 = ""
    stage70_run_fingerprint = ""

    stage70_application_reference = ""
    stage70_draft_title = ""
    stage70_current_portal_url = ""

    stage69_run_id = ""
    stage68_run_id = ""
    stage67_run_id = ""
    stage66_run_id = ""
    stage65_run_id = ""
    stage64_run_id = ""
    stage63_run_id = ""
    stage62_run_id = ""
    stage61_run_id = ""
    stage60_run_id = ""
    stage59_run_id = ""
    stage57_run_id = ""


# ---------------------------------------------------------------------
# Stage 69 deterministic resolution
# ---------------------------------------------------------------------

stage69 = get_bound(
    "stage69_portal_upload_confirmation_runs",
    stage69_run_id,
)

if not stage69 or not valid_stage69_row(stage69):
    stage69 = next((r for r in stage69_candidates if valid_stage69_row(r)), None)

if stage69:
    stage69_run_id = str(stage69.get("id") or "")

    # Recover missing upstream IDs from Stage 69.
    if not stage68_run_id:
        stage68_run_id = str(stage69.get("stage68_run_id") or "")
    if not stage67_run_id:
        stage67_run_id = str(stage69.get("stage67_run_id") or "")
    if not stage66_run_id:
        stage66_run_id = str(stage69.get("stage66_run_id") or "")
    if not stage65_run_id:
        stage65_run_id = str(stage69.get("stage65_run_id") or "")
    if not stage64_run_id:
        stage64_run_id = str(stage69.get("stage64_run_id") or "")
    if not stage63_run_id:
        stage63_run_id = str(stage69.get("stage63_run_id") or "")
    if not stage62_run_id:
        stage62_run_id = str(stage69.get("stage62_run_id") or "")
    if not stage61_run_id:
        stage61_run_id = str(stage69.get("stage61_run_id") or "")
    if not stage60_run_id:
        stage60_run_id = str(stage69.get("stage60_run_id") or "")
    if not stage59_run_id:
        stage59_run_id = str(stage69.get("stage59_run_id") or "")
    if not stage57_run_id:
        stage57_run_id = str(stage69.get("stage57_run_id") or "")


# ---------------------------------------------------------------------
# Stage 68 deterministic resolution
# ---------------------------------------------------------------------

stage68 = get_bound(
    "stage68_upload_package_preparation_runs",
    stage68_run_id,
)

if not stage68 or not valid_stage68_row(stage68):
    # Prefer a row matching the Stage 69 application reference when available.
    stage69_ref = normalize_text(stage69.get("application_reference")) if stage69 else ""
    matching_stage68 = [
        r for r in stage68_candidates
        if valid_stage68_row(r)
        and (
            not stage69_ref
            or not normalize_text(r.get("application_reference"))
            or normalize_text(r.get("application_reference")) == stage69_ref
        )
    ]
    stage68 = matching_stage68[0] if matching_stage68 else None

if stage68:
    stage68_run_id = str(stage68.get("id") or "")

    if not stage67_run_id:
        stage67_run_id = str(stage68.get("stage67_run_id") or "")
    if not stage66_run_id:
        stage66_run_id = str(stage68.get("stage66_run_id") or "")
    if not stage65_run_id:
        stage65_run_id = str(stage68.get("stage65_run_id") or "")
    if not stage64_run_id:
        stage64_run_id = str(stage68.get("stage64_run_id") or "")
    if not stage63_run_id:
        stage63_run_id = str(stage68.get("stage63_run_id") or "")
    if not stage62_run_id:
        stage62_run_id = str(stage68.get("stage62_run_id") or "")
    if not stage61_run_id:
        stage61_run_id = str(stage68.get("stage61_run_id") or "")
    if not stage60_run_id:
        stage60_run_id = str(stage68.get("stage60_run_id") or "")
    if not stage59_run_id:
        stage59_run_id = str(stage68.get("stage59_run_id") or "")
    if not stage57_run_id:
        stage57_run_id = str(stage68.get("stage57_run_id") or "")


# ---------------------------------------------------------------------
# Stage 67 canonical draft binding
# ---------------------------------------------------------------------

stage67 = get_bound(
    "stage67_portal_draft_binding_runs",
    stage67_run_id,
)

if not stage67 or not valid_stage67_row(stage67):
    # Prefer exact application reference compatibility with downstream rows.
    downstream_reference = (
        normalize_text(stage70.get("application_reference")) if stage70 else ""
    ) or (
        normalize_text(stage69.get("application_reference")) if stage69 else ""
    ) or (
        normalize_text(stage68.get("application_reference")) if stage68 else ""
    )

    matching_stage67 = [
        r for r in stage67_candidates
        if valid_stage67_row(r)
        and (
            not downstream_reference
            or normalize_text(r.get("application_reference")) == downstream_reference
        )
    ]
    stage67 = matching_stage67[0] if matching_stage67 else None

if stage67:
    stage67_run_id = str(stage67.get("id") or "")

    if not stage66_run_id:
        stage66_run_id = str(stage67.get("stage66_run_id") or "")
    if not stage60_run_id:
        stage60_run_id = str(stage67.get("stage60_run_id") or "")
    if not stage59_run_id:
        stage59_run_id = str(stage67.get("stage59_run_id") or "")
    if not stage57_run_id:
        stage57_run_id = str(stage67.get("stage57_run_id") or "")


# ---------------------------------------------------------------------
# Remaining bound upstream rows
# ---------------------------------------------------------------------

stage66 = get_bound(
    "stage66_portal_session_establishment_runs",
    stage66_run_id,
)

stage60 = get_bound(
    "stage60_submission_package_runs",
    stage60_run_id,
)


# ---------------------------------------------------------------------
# Cross-stage deterministic compatibility checks
# ---------------------------------------------------------------------

stage70_ref = normalize_text(stage70.get("application_reference")) if stage70 else ""
stage69_ref = normalize_text(stage69.get("application_reference")) if stage69 else ""
stage68_ref = normalize_text(stage68.get("application_reference")) if stage68 else ""
stage67_ref = normalize_text(stage67.get("application_reference")) if stage67 else ""

resolved_refs = [r for r in (stage70_ref, stage69_ref, stage68_ref, stage67_ref) if r]
cross_stage_reference_consistent = (
    len(set(resolved_refs)) <= 1
    if resolved_refs
    else False
)

stage69_manifest_sha = normalize_text(
    stage69.get("stage68_file_manifest_sha256")
) if stage69 else ""

stage68_manifest_sha = normalize_text(
    stage68.get("file_manifest_sha256")
) if stage68 else ""

stage69_stage68_manifest_consistent = (
    bool(stage69_manifest_sha)
    and bool(stage68_manifest_sha)
    and stage69_manifest_sha == stage68_manifest_sha
)

stage70_stage69_link_consistent = (
    bool(stage70)
    and bool(stage69)
    and (
        not normalize_text(stage70.get("stage69_run_id"))
        or normalize_text(stage70.get("stage69_run_id")) == str(stage69.get("id") or "")
    )
)

stage69_stage68_link_consistent = (
    bool(stage69)
    and bool(stage68)
    and (
        not normalize_text(stage69.get("stage68_run_id"))
        or normalize_text(stage69.get("stage68_run_id")) == str(stage68.get("id") or "")
    )
)

stage69_outcome = (
    normalize_text(stage69.get("upload_outcome")).upper()
    if stage69 else "MISSING"
)
stage68_outcome = (
    normalize_text(stage68.get("preparation_outcome")).upper()
    if stage68 else "MISSING"
)
stage67_outcome = (
    normalize_text(stage67.get("binding_outcome")).upper()
    if stage67 else "MISSING"
)
stage66_outcome = (
    normalize_text(stage66.get("session_outcome")).upper()
    if stage66 else "MISSING"
)
stage60_outcome = (
    normalize_text(stage60.get("package_outcome")).upper()
    if stage60 else "MISSING"
)


# ---------------------------------------------------------------------
# Canonical portal draft identity — Stage 67 is authoritative
# ---------------------------------------------------------------------

stage67_application_reference = (
    normalize_text(stage67.get("application_reference"))
    if stage67 else ""
)

stage67_current_portal_url = (
    normalize_text(stage67.get("current_portal_url"))
    if stage67 else ""
)

stage67_draft_title = (
    normalize_text(stage67.get("draft_title"))
    if stage67 else ""
)

application_reference = (
    stage67_application_reference
    or stage70_application_reference
)

current_portal_url = (
    stage67_current_portal_url
    or stage70_current_portal_url
)

draft_title = (
    stage67_draft_title
    or stage70_draft_title
)

stage67_stage70_reference_consistent = (
    bool(stage67_application_reference)
    and (
        not stage70_application_reference
        or stage70_application_reference == stage67_application_reference
    )
)


# ---------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------

stage70_evidence = (
    as_dict(stage70.get("post_upload_evidence"))
    or as_dict(stage70.get("validation_evidence"))
    or as_dict(stage70.get("evidence_payload"))
    if stage70 else {}
)

recomputed_stage70_evidence_sha256 = (
    stable_sha256(stage70_evidence)
    if stage70_evidence
    else ""
)

stage70_run_payload = as_dict(stage70.get("run_payload")) if stage70 else {}
recomputed_stage70_run_fingerprint = (
    stable_sha256(stage70_run_payload)
    if stage70_run_payload
    else ""
)

stage69_upload_evidence_sha256 = normalize_text(
    stage69.get("upload_evidence_sha256")
) if stage69 else ""

stage68_file_manifest_sha256 = normalize_text(
    stage68.get("file_manifest_sha256")
) if stage68 else ""


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
    "Stage 70 exists",
    bool(stage70),
    stage70_run_id or "MISSING",
)

add_check(
    "Stage 70 COMPLETED",
    stage70_status == "COMPLETED",
    stage70_status,
)

add_check(
    "Stage 70 PORTAL_POST_UPLOAD_VALIDATED",
    stage70_outcome == "PORTAL_POST_UPLOAD_VALIDATED",
    stage70_outcome,
)

add_check(
    "Stage 70 no blocking errors",
    bool(stage70) and not bool(stage70.get("blocking_errors_present")),
    f"blocking_errors_present={bool(stage70.get('blocking_errors_present')) if stage70 else None}",
)

add_check(
    "Stage 70 portal upload still present",
    bool(stage70) and bool(stage70.get("portal_upload_performed")),
    f"portal_upload_performed={bool(stage70.get('portal_upload_performed')) if stage70 else None}",
)

add_check(
    "Stage 70 not submitted",
    bool(stage70) and not bool(stage70.get("external_submission_performed")),
    f"external_submission_performed={bool(stage70.get('external_submission_performed')) if stage70 else None}",
)

add_check(
    "Stage 70 no receipt",
    bool(stage70) and not bool(stage70.get("external_receipt_obtained")),
    f"external_receipt_obtained={bool(stage70.get('external_receipt_obtained')) if stage70 else None}",
)

add_check(
    "Stage 70 evidence SHA256 stable",
    bool(stage70_evidence_sha256)
    and stage70_evidence_sha256 == recomputed_stage70_evidence_sha256,
    f"stored={stage70_evidence_sha256[:16]}..., recomputed={recomputed_stage70_evidence_sha256[:16]}...",
)

add_check(
    "Stage 70 run fingerprint stable",
    bool(stage70_run_fingerprint)
    and stage70_run_fingerprint == recomputed_stage70_run_fingerprint,
    f"stored={stage70_run_fingerprint[:16]}..., recomputed={recomputed_stage70_run_fingerprint[:16]}...",
)

add_check(
    "Stage 69 PORTAL_UPLOAD_CONFIRMED",
    stage69_outcome == "PORTAL_UPLOAD_CONFIRMED",
    stage69_outcome,
)

add_check(
    "Stage 68 UPLOAD_PACKAGE_READY",
    stage68_outcome == "UPLOAD_PACKAGE_READY",
    stage68_outcome,
)


add_check(
    "Stage 70 → Stage 69 link consistent",
    stage70_stage69_link_consistent,
    (
        f"stage70.stage69_run_id={normalize_text(stage70.get('stage69_run_id')) if stage70 else 'MISSING'}, "
        f"resolved_stage69={stage69_run_id or 'MISSING'}"
    ),
)

add_check(
    "Stage 69 → Stage 68 link consistent",
    stage69_stage68_link_consistent,
    (
        f"stage69.stage68_run_id={normalize_text(stage69.get('stage68_run_id')) if stage69 else 'MISSING'}, "
        f"resolved_stage68={stage68_run_id or 'MISSING'}"
    ),
)

add_check(
    "Stage 69 / Stage 68 manifest SHA256 consistent",
    stage69_stage68_manifest_consistent,
    (
        f"stage69={stage69_manifest_sha[:16]}..., "
        f"stage68={stage68_manifest_sha[:16]}..."
    ),
)

add_check(
    "Cross-stage application reference consistent",
    cross_stage_reference_consistent,
    " | ".join(resolved_refs) if resolved_refs else "MISSING",
)

add_check(
    "Stage 67 PORTAL_DRAFT_BOUND",
    stage67_outcome == "PORTAL_DRAFT_BOUND",
    stage67_outcome,
)

add_check(
    "Stage 66 PORTAL_SESSION_ESTABLISHED",
    stage66_outcome == "PORTAL_SESSION_ESTABLISHED",
    stage66_outcome,
)

add_check(
    "Stage 60 PACKAGE_READY",
    stage60_outcome == "PACKAGE_READY",
    stage60_outcome,
)

add_check(
    "Stage 67 canonical draft reference present",
    len(stage67_application_reference) >= 3,
    stage67_application_reference or "MISSING",
)

add_check(
    "Stage 70 consistent with Stage 67 draft",
    stage67_stage70_reference_consistent,
    (
        f"stage67={stage67_application_reference or 'MISSING'}, "
        f"stage70={stage70_application_reference or 'MISSING'}"
    ),
)

add_check(
    "Application reference present",
    len(application_reference) >= 3,
    application_reference or "MISSING",
)

add_check(
    "Canonical portal URL present",
    bool(current_portal_url),
    current_portal_url or "MISSING",
)

add_check(
    "Canonical portal URL official",
    official_domain_ok(current_portal_url),
    current_portal_url or "MISSING",
)

stage71_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)

gate_reason = (
    "Stage 70→69→68→67 chain is deterministically resolved, integrity-compatible, and submission readiness is valid."
    if stage71_gate == "READY"
    else "Stage 71 fail-closed gate failed: "
    + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Existing Stage 71
# ---------------------------------------------------------------------

def load_existing_stage71():
    if not stage70_run_id:
        return None

    data = (
        supabase
        .table("stage71_final_submission_readiness_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage70_run_id", stage70_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage71 = load_existing_stage71()


# ---------------------------------------------------------------------
# UI — gate
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 70 → Stage 71 final submission-readiness gate")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Stage 70", stage70_outcome)
m2.metric("Draft reference", application_reference or "—")
m3.metric("Deadline", str(deadline or "—")[:10])
m4.metric("Integrity", "VERIFIED" if stage71_gate == "READY" else "FAILED")

with st.expander("Stage 71 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.write(f"**Base gate:** `{stage71_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Bound draft:** `{application_reference or '—'}`")

with st.expander("Resolved draft identity chain", expanded=False):
    st.write(f"Stage 70 run: `{stage70_run_id or 'MISSING'}`")
    st.write(f"Stage 69 run: `{stage69_run_id or 'MISSING'}`")
    st.write(f"Stage 68 run: `{stage68_run_id or 'MISSING'}`")
    st.write(f"Stage 67 run: `{stage67_run_id or 'MISSING'}`")
    st.write(f"Stage 67 reference: `{stage67_application_reference or 'MISSING'}`")
    st.write(f"Stage 70 reference: `{stage70_application_reference or 'MISSING'}`")
    st.write(f"Stage 69 reference: `{stage69_ref or 'MISSING'}`")
    st.write(f"Stage 68 reference: `{stage68_ref or 'MISSING'}`")
    st.write(f"Canonical reference: `{application_reference or 'MISSING'}`")
    st.write(f"Canonical URL: `{current_portal_url or 'MISSING'}`")
    st.write(f"Cross-stage reference consistent: `{cross_stage_reference_consistent}`")
    st.write(f"Stage69/68 manifest consistent: `{stage69_stage68_manifest_consistent}`")

if stage70_evidence_sha256:
    st.write(f"**Stage 70 evidence SHA256:** `{stage70_evidence_sha256}`")

if stage69_upload_evidence_sha256:
    st.write(f"**Stage 69 upload evidence SHA256:** `{stage69_upload_evidence_sha256}`")

if stage68_file_manifest_sha256:
    st.write(f"**Stage 68 manifest SHA256:** `{stage68_file_manifest_sha256}`")


# ---------------------------------------------------------------------
# Explicit readiness confirmation
# ---------------------------------------------------------------------

if not existing_stage71:
    st.divider()
    st.subheader("Explicit final readiness confirmation")

    st.warning(
        "Stage 71 este numai un readiness gate. Nu apasă Submit și nu creează o depunere oficială. "
        "Confirmă doar starea reală observată în portal."
    )

    portal_url = st.text_input(
        "Current official portal URL",
        value=current_portal_url,
        key="stage71_portal_url",
    )

    portal_url_ok = official_domain_ok(portal_url)

    if portal_url:
        if portal_url_ok:
            st.success("Current URL is on an accepted official EU domain.")
        else:
            st.error("Current URL must be HTTPS and on europa.eu / ec.europa.eu.")

    displayed_reference = st.text_input(
        "Draft reference currently visible in portal",
        value=application_reference,
        key="stage71_draft_reference",
    )

    reference_matches = (
        normalize_text(displayed_reference) == application_reference
        and len(application_reference) >= 3
    )

    if displayed_reference:
        if reference_matches:
            st.success("Draft reference matches the bound application reference.")
        else:
            st.error("Draft reference does not match the bound application reference.")

    confirmed_upload_present = st.checkbox(
        "I confirm the uploaded Part B/package is still attached to this exact draft.",
        key="stage71_upload_present",
    )

    confirmed_no_errors = st.checkbox(
        "I confirm the portal shows no blocking validation errors.",
        key="stage71_no_errors",
    )

    confirmed_editable = st.checkbox(
        "I confirm the proposal remains editable and has NOT been finally submitted.",
        key="stage71_editable",
    )

    confirmed_no_receipt = st.checkbox(
        "I confirm no final submission receipt has been issued.",
        key="stage71_no_receipt",
    )

    confirmed_no_submit_action = st.checkbox(
        "I understand Stage 71 does NOT press Submit and only records readiness for a future explicit authorization stage.",
        key="stage71_understand",
    )

    confirmation_phrase = st.text_input(
        "Confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 71 SUBMISSION READINESS",
        key="stage71_phrase",
    )

    readiness_note = st.text_area(
        "Optional readiness note",
        placeholder="Optional non-sensitive note about the observed final readiness state.",
        key="stage71_note",
    )

    all_confirmations = (
        stage71_gate == "READY"
        and portal_url_ok
        and reference_matches
        and confirmed_upload_present
        and confirmed_no_errors
        and confirmed_editable
        and confirmed_no_receipt
        and confirmed_no_submit_action
        and normalize_text(confirmation_phrase) == "CONFIRM STAGE 71 SUBMISSION READINESS"
    )

    readiness_payload = {
        "readiness_version": "stage71-v1.4",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage70_run_id": stage70_run_id,
        "stage70_evidence_sha256": stage70_evidence_sha256,
        "stage69_run_id": stage69_run_id,
        "stage69_upload_evidence_sha256": stage69_upload_evidence_sha256,
        "stage68_run_id": stage68_run_id,
        "stage68_file_manifest_sha256": stage68_file_manifest_sha256,

        "application_reference": application_reference,
        "draft_title": draft_title or None,

        "current_portal_url": normalize_text(portal_url),
        "displayed_reference": normalize_text(displayed_reference),
        "reference_matches": reference_matches,

        "confirmed_upload_present": bool(confirmed_upload_present),
        "confirmed_no_errors": bool(confirmed_no_errors),
        "confirmed_editable": bool(confirmed_editable),
        "confirmed_no_receipt": bool(confirmed_no_receipt),
        "confirmed_no_submit_action": bool(confirmed_no_submit_action),

        "readiness_note": normalize_text(readiness_note) or None,

        "external_submission_performed": False,
        "external_receipt_obtained": False,
    }

    readiness_evidence_sha256 = stable_sha256(readiness_payload)

    run_basis = {
        "stage": 71,
        "fingerprint_contract": "stage71-v1.4-final-submission-readiness",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage70_run_id": stage70_run_id,
        "stage70_evidence_sha256": stage70_evidence_sha256,
        "readiness_evidence_sha256": readiness_evidence_sha256,
        "stage71_gate": stage71_gate,
    }

    run_fingerprint = stable_sha256(run_basis)

    if st.button(
        "✅ Confirm & persist Stage 71 submission readiness",
        type="primary",
        use_container_width=True,
        key="stage71_confirm",
        disabled=not all_confirmations,
    ):
        try:
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
                "stage65_run_id": stage65_run_id,
                "stage66_run_id": stage66_run_id,
                "stage67_run_id": stage67_run_id,
                "stage68_run_id": stage68_run_id,
                "stage69_run_id": stage69_run_id,
                "stage70_run_id": stage70_run_id,

                "stage": 71,
                "readiness_version": "stage71-v1.4",

                "opportunity_identity": identity,
                "official_deadline": str(deadline or "")[:10] or None,

                "application_reference": application_reference,
                "draft_title": draft_title or None,
                "current_portal_url": normalize_text(portal_url),

                "run_status": "COMPLETED",
                "readiness_outcome": "READY_FOR_SUBMISSION_AUTHORIZATION",

                "blocking_errors_present": False,
                "portal_upload_present": True,
                "proposal_editable": True,

                "external_submission_performed": False,
                "external_receipt_obtained": False,

                "stage70_evidence_sha256": stage70_evidence_sha256,
                "readiness_evidence_sha256": readiness_evidence_sha256,
                "run_fingerprint": run_fingerprint,

                "readiness_payload": readiness_payload,
                "run_payload": run_basis,

                "confirmed_at": now_iso(),
                "completed_at": now_iso(),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                rpc_result = supabase.rpc(
                    "persist_stage71_submission_readiness",
                    {"p_payload": payload},
                ).execute()

                if not rpc_result.data:
                    (
                        supabase
                        .table("stage71_final_submission_readiness_runs")
                        .insert(payload)
                        .execute()
                    )

            except Exception:
                (
                    supabase
                    .table("stage71_final_submission_readiness_runs")
                    .insert(payload)
                    .execute()
                )

            st.success(
                "Stage 71 persisted — Outcome READY_FOR_SUBMISSION_AUTHORIZATION."
            )
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 71 persistence failed. Rulează mai întâi SQL-ul Stage 71 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage71 = load_existing_stage71()

if existing_stage71:
    st.divider()
    st.subheader("Stage 71 outcome")

    st.success(
        f"Stage 71 este deja persistată. Run ID: {existing_stage71.get('id')} — "
        f"Outcome: {existing_stage71.get('readiness_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage71.get("readiness_outcome"),
    )
    o2.metric(
        "Blocking errors?",
        "YES" if bool(existing_stage71.get("blocking_errors_present")) else "NO",
    )
    o3.metric(
        "Submitted?",
        "YES" if bool(existing_stage71.get("external_submission_performed")) else "NO",
    )
    o4.metric(
        "Receipt?",
        "YES" if bool(existing_stage71.get("external_receipt_obtained")) else "NO",
    )

    st.write(
        f"**Application reference:** `{existing_stage71.get('application_reference')}`"
    )
    st.write(
        f"**Stage 70 evidence SHA256:** `{existing_stage71.get('stage70_evidence_sha256')}`"
    )
    st.write(
        f"**Readiness evidence SHA256:** `{existing_stage71.get('readiness_evidence_sha256')}`"
    )
    st.write(
        f"**Run fingerprint:** `{existing_stage71.get('run_fingerprint')}`"
    )

    if normalize_text(existing_stage71.get("readiness_outcome")).upper() == "READY_FOR_SUBMISSION_AUTHORIZATION":
        st.success(
            "Stage 71 READY_FOR_SUBMISSION_AUTHORIZATION. The draft is recorded as ready to enter a future "
            "explicit submission-authorization stage. No submission or receipt occurred at Stage 71."
        )


st.caption(
    "Invariantă Stage 71 v1.4: READY_FOR_SUBMISSION_AUTHORIZATION is a readiness state only. "
    "It is not evidence of submission, signature, financial commitment or European Commission receipt."
)
