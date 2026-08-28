import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 69 v1.0 — AI PORTAL UPLOAD EXECUTION CONFIRMATION GATE
#
# Purpose:
#   Consume ONLY Stage 68 UPLOAD_PACKAGE_READY and record explicit
#   user-confirmed evidence that the exact manifest-bound file set was
#   uploaded to the exact Funding & Tenders draft.
#
# IMPORTANT:
#   Stage 69 DOES NOT automate browser actions.
#   The user performs the upload manually in the official portal and then
#   confirms the observed portal state here.
#
# Stage 69 DOES NOT:
#   - collect credentials, MFA, cookies or tokens
#   - press Submit
#   - sign declarations
#   - create financial commitments
#   - claim a submission receipt
#
# Stage 69 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 68 COMPLETED + UPLOAD_PACKAGE_READY
#   - Stage 68 manifest SHA256 stable
#   - Stage 68 preparation fingerprint stable
#   - Stage 68 run fingerprint stable
#   - Stage 67 PORTAL_DRAFT_BOUND
#   - Stage 66 PORTAL_SESSION_ESTABLISHED
#   - Stage 60 PACKAGE_READY
#
# Human evidence requires:
#   - upload completed manually in Funding & Tenders
#   - exact draft reference match
#   - exact displayed uploaded filename(s) match Stage 68 manifest
#   - portal shows no final submission occurred
#   - exact phrase: CONFIRM STAGE 69 PORTAL UPLOAD
#
# Outcomes:
#   PORTAL_UPLOAD_CONFIRMED
#   BLOCKED
#
# Handoff:
#   Stage 70 may consume ONLY PORTAL_UPLOAD_CONFIRMED.
# =====================================================================

st.set_page_config(
    page_title="Stage 69 v1.0 — Portal Upload Execution Confirmation",
    page_icon="⬆️",
    layout="wide",
)

st.title("⬆️ Etapa 69 v1.0 — AI Portal Upload Execution Confirmation Gate")
st.caption(
    "Înregistrează confirmarea explicită că fișierele legate prin Stage 68 au fost încărcate manual "
    "în draftul oficial Funding & Tenders. Stage 69 NU face Submit."
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
    st.error("Stage 69 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
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
        key="stage69_project",
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
    st.error("Stage 69 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Stage 68
# ---------------------------------------------------------------------

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

stage68 = next(
    (
        r for r in stage68_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("preparation_outcome")).upper() == "UPLOAD_PACKAGE_READY"
        and not bool(r.get("portal_upload_performed"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if stage68:
    stage68_run_id = str(stage68.get("id") or "")
    stage68_status = normalize_text(stage68.get("run_status")).upper()
    stage68_outcome = normalize_text(stage68.get("preparation_outcome")).upper()

    stage68_file_manifest_sha256 = normalize_text(stage68.get("file_manifest_sha256"))
    stage68_preparation_fingerprint = normalize_text(stage68.get("preparation_fingerprint"))
    stage68_run_fingerprint = normalize_text(stage68.get("run_fingerprint"))

    application_reference = normalize_text(stage68.get("application_reference"))
    draft_title = normalize_text(stage68.get("draft_title"))
    current_portal_url = normalize_text(stage68.get("current_portal_url"))

    stage67_run_id = str(stage68.get("stage67_run_id") or "")
    stage66_run_id = str(stage68.get("stage66_run_id") or "")
    stage65_run_id = str(stage68.get("stage65_run_id") or "")
    stage64_run_id = str(stage68.get("stage64_run_id") or "")
    stage63_run_id = str(stage68.get("stage63_run_id") or "")
    stage62_run_id = str(stage68.get("stage62_run_id") or "")
    stage61_run_id = str(stage68.get("stage61_run_id") or "")
    stage60_run_id = str(stage68.get("stage60_run_id") or "")
    stage59_run_id = str(stage68.get("stage59_run_id") or "")
    stage57_run_id = str(stage68.get("stage57_run_id") or "")
else:
    stage68_run_id = ""
    stage68_status = "MISSING"
    stage68_outcome = "MISSING"
    stage68_file_manifest_sha256 = ""
    stage68_preparation_fingerprint = ""
    stage68_run_fingerprint = ""
    application_reference = ""
    draft_title = ""
    current_portal_url = ""
    stage67_run_id = stage66_run_id = stage65_run_id = stage64_run_id = ""
    stage63_run_id = stage62_run_id = stage61_run_id = stage60_run_id = ""
    stage59_run_id = stage57_run_id = ""


# ---------------------------------------------------------------------
# Upstream chain
# ---------------------------------------------------------------------

stage67 = get_bound("stage67_portal_draft_binding_runs", stage67_run_id)
stage66 = get_bound("stage66_portal_session_establishment_runs", stage66_run_id)
stage60 = get_bound("stage60_submission_package_runs", stage60_run_id)

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
# Integrity verification
# ---------------------------------------------------------------------

stage68_file_manifest = as_dict(stage68.get("file_manifest")) if stage68 else {}
recomputed_stage68_file_manifest_sha256 = (
    stable_sha256(stage68_file_manifest)
    if stage68_file_manifest
    else ""
)

stage68_preparation_payload = as_dict(stage68.get("preparation_payload")) if stage68 else {}
recomputed_stage68_preparation_fingerprint = (
    stable_sha256(stage68_preparation_payload)
    if stage68_preparation_payload
    else ""
)

stage68_run_payload = as_dict(stage68.get("run_payload")) if stage68 else {}
recomputed_stage68_run_fingerprint = (
    stable_sha256(stage68_run_payload)
    if stage68_run_payload
    else ""
)

manifest_files = stage68_file_manifest.get("files") if isinstance(stage68_file_manifest, dict) else []
if not isinstance(manifest_files, list):
    manifest_files = []

expected_filenames = [
    normalize_text(item.get("filename"))
    for item in manifest_files
    if isinstance(item, dict) and normalize_text(item.get("filename"))
]

expected_file_hashes = [
    normalize_text(item.get("sha256"))
    for item in manifest_files
    if isinstance(item, dict) and normalize_text(item.get("sha256"))
]


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
    "Stage 68 exists",
    bool(stage68),
    stage68_run_id or "MISSING",
)

add_check(
    "Stage 68 COMPLETED",
    stage68_status == "COMPLETED",
    stage68_status,
)

add_check(
    "Stage 68 UPLOAD_PACKAGE_READY",
    stage68_outcome == "UPLOAD_PACKAGE_READY",
    stage68_outcome,
)

add_check(
    "Stage 68 file manifest SHA256 stable",
    bool(stage68_file_manifest_sha256)
    and stage68_file_manifest_sha256 == recomputed_stage68_file_manifest_sha256,
    f"stored={stage68_file_manifest_sha256[:16]}..., recomputed={recomputed_stage68_file_manifest_sha256[:16]}...",
)

add_check(
    "Stage 68 preparation fingerprint stable",
    bool(stage68_preparation_fingerprint)
    and stage68_preparation_fingerprint == recomputed_stage68_preparation_fingerprint,
    f"stored={stage68_preparation_fingerprint[:16]}..., recomputed={recomputed_stage68_preparation_fingerprint[:16]}...",
)

add_check(
    "Stage 68 run fingerprint stable",
    bool(stage68_run_fingerprint)
    and stage68_run_fingerprint == recomputed_stage68_run_fingerprint,
    f"stored={stage68_run_fingerprint[:16]}..., recomputed={recomputed_stage68_run_fingerprint[:16]}...",
)

add_check(
    "Stage 68 manifest has files",
    len(manifest_files) > 0,
    f"file_count={len(manifest_files)}",
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

stage69_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)

gate_reason = (
    "Stage 68 immutable upload package and bound draft are valid."
    if stage69_gate == "READY"
    else "Stage 69 fail-closed gate failed: "
    + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Existing Stage 69
# ---------------------------------------------------------------------

def load_existing_stage69():
    if not stage68_run_id:
        return None

    data = (
        supabase
        .table("stage69_portal_upload_confirmation_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage68_run_id", stage68_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage69 = load_existing_stage69()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 68 → Stage 69 portal-upload confirmation")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 68", stage68_outcome)
m2.metric("Draft reference", application_reference or "—")
m3.metric("Files expected", len(expected_filenames))
m4.metric("Integrity", "VERIFIED" if stage69_gate == "READY" else "FAILED")

with st.expander("Stage 69 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.write(f"**Gate:** `{stage69_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Bound draft:** `{application_reference or '—'}`")
st.write(f"**Stage 68 file manifest SHA256:** `{stage68_file_manifest_sha256}`")

if expected_filenames:
    st.write("**Expected uploaded files:**")
    for filename in expected_filenames:
        st.write(f"- `{filename}`")


# ---------------------------------------------------------------------
# Human upload confirmation
# ---------------------------------------------------------------------

if not existing_stage69:
    st.divider()
    st.subheader("Explicit portal upload confirmation")

    st.warning(
        "Fă upload-ul manual în Funding & Tenders Portal. "
        "Nu introduce aici parole, cookie-uri, token-uri sau coduri MFA. "
        "Stage 69 persistă doar confirmarea stării observate după upload."
    )

    portal_url = st.text_input(
        "Current official portal URL",
        value=current_portal_url,
        key="stage69_portal_url",
    )

    portal_url_ok = official_domain_ok(portal_url)

    if portal_url:
        if portal_url_ok:
            st.success("Current URL is on an accepted official EU domain.")
        else:
            st.error("Current URL must be HTTPS and on europa.eu / ec.europa.eu.")

    displayed_draft_reference = st.text_input(
        "Draft reference visible in portal",
        value=application_reference,
        key="stage69_draft_reference",
    )

    uploaded_names_text = st.text_area(
        "Uploaded filename(s) visible in portal",
        value="\n".join(expected_filenames),
        help="One filename per line. Must match the Stage 68 manifest exactly.",
        key="stage69_uploaded_names",
    )

    displayed_uploaded_filenames = [
        line.strip()
        for line in uploaded_names_text.splitlines()
        if line.strip()
    ]

    filenames_match = (
        sorted(displayed_uploaded_filenames)
        == sorted(expected_filenames)
    )

    if displayed_uploaded_filenames:
        if filenames_match:
            st.success("Displayed uploaded filenames match the Stage 68 manifest.")
        else:
            st.error("Displayed uploaded filenames do not exactly match the Stage 68 manifest.")

    confirmed_uploaded = st.checkbox(
        "I confirm the Stage 68 manifest file set has been uploaded to the bound draft in the official portal.",
        key="stage69_confirm_uploaded",
    )

    confirmed_draft = st.checkbox(
        f"I confirm the upload is attached to draft {application_reference or '—'} only.",
        key="stage69_confirm_draft",
    )

    confirmed_no_submit = st.checkbox(
        "I confirm the proposal has NOT been finally submitted.",
        key="stage69_confirm_no_submit",
    )

    confirmed_no_receipt = st.checkbox(
        "I confirm no final submission receipt has been issued.",
        key="stage69_confirm_no_receipt",
    )

    confirmation_phrase = st.text_input(
        "Confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 69 PORTAL UPLOAD",
        key="stage69_phrase",
    )

    confirmation_note = st.text_area(
        "Optional upload confirmation note",
        placeholder="Optional non-sensitive note about the observed portal upload state.",
        key="stage69_note",
    )

    draft_reference_matches = (
        normalize_text(displayed_draft_reference)
        == application_reference
        and len(application_reference) >= 3
    )

    all_confirmations = (
        stage69_gate == "READY"
        and portal_url_ok
        and draft_reference_matches
        and filenames_match
        and confirmed_uploaded
        and confirmed_draft
        and confirmed_no_submit
        and confirmed_no_receipt
        and normalize_text(confirmation_phrase) == "CONFIRM STAGE 69 PORTAL UPLOAD"
    )

    upload_confirmation_payload = {
        "confirmation_version": "stage69-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage68_run_id": stage68_run_id,
        "stage68_file_manifest_sha256": stage68_file_manifest_sha256,

        "application_reference": application_reference,
        "draft_title": draft_title or None,

        "portal_url": normalize_text(portal_url),
        "displayed_draft_reference": normalize_text(displayed_draft_reference),

        "expected_filenames": expected_filenames,
        "displayed_uploaded_filenames": displayed_uploaded_filenames,

        "filenames_match": filenames_match,
        "draft_reference_matches": draft_reference_matches,

        "confirmed_uploaded": bool(confirmed_uploaded),
        "confirmed_no_submit": bool(confirmed_no_submit),
        "confirmed_no_receipt": bool(confirmed_no_receipt),

        "confirmation_note": normalize_text(confirmation_note) or None,

        "credentials_collected": False,
        "cookies_collected": False,
        "tokens_collected": False,
        "mfa_codes_collected": False,
    }

    upload_confirmation_sha256 = stable_sha256(upload_confirmation_payload)

    upload_evidence = {
        "stage": 69,
        "evidence_type": "USER_CONFIRMED_PORTAL_UPLOAD_STATE",
        "upload_confirmation_payload": upload_confirmation_payload,
        "manifest_file_hashes": expected_file_hashes,
        "state": {
            "portal_upload_started": True,
            "portal_upload_performed": True,
            "external_submission_performed": False,
            "external_receipt_obtained": False,
        },
    }

    upload_evidence_sha256 = stable_sha256(upload_evidence)

    run_basis = {
        "stage": 69,
        "fingerprint_contract": "stage69-v1.0-portal-upload-confirmation",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage68_run_id": stage68_run_id,
        "stage68_file_manifest_sha256": stage68_file_manifest_sha256,
        "upload_confirmation_sha256": upload_confirmation_sha256,
        "upload_evidence_sha256": upload_evidence_sha256,
        "stage69_gate": stage69_gate,
    }

    run_fingerprint = stable_sha256(run_basis)

    if st.button(
        "⬆️ Confirm & persist Stage 69 portal upload",
        type="primary",
        use_container_width=True,
        key="stage69_confirm",
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

                "stage": 69,
                "confirmation_version": "stage69-v1.0",

                "opportunity_identity": identity,
                "official_deadline": str(deadline or "")[:10] or None,

                "application_reference": application_reference,
                "draft_title": draft_title or None,
                "current_portal_url": normalize_text(portal_url),

                "run_status": "COMPLETED",
                "upload_outcome": "PORTAL_UPLOAD_CONFIRMED",

                "file_count": len(expected_filenames),

                "portal_upload_started": True,
                "portal_upload_performed": True,
                "external_submission_performed": False,
                "external_receipt_obtained": False,

                "credentials_collected": False,
                "cookies_collected": False,
                "tokens_collected": False,
                "mfa_codes_collected": False,

                "stage68_file_manifest_sha256": stage68_file_manifest_sha256,
                "upload_confirmation_sha256": upload_confirmation_sha256,
                "upload_evidence_sha256": upload_evidence_sha256,
                "run_fingerprint": run_fingerprint,

                "upload_confirmation_payload": upload_confirmation_payload,
                "upload_evidence": upload_evidence,
                "run_payload": run_basis,

                "confirmed_at": now_iso(),
                "completed_at": now_iso(),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                rpc_result = supabase.rpc(
                    "persist_stage69_portal_upload_confirmation",
                    {"p_payload": payload},
                ).execute()

                if not rpc_result.data:
                    (
                        supabase
                        .table("stage69_portal_upload_confirmation_runs")
                        .insert(payload)
                        .execute()
                    )
            except Exception:
                (
                    supabase
                    .table("stage69_portal_upload_confirmation_runs")
                    .insert(payload)
                    .execute()
                )

            st.success("Stage 69 persisted — Outcome PORTAL_UPLOAD_CONFIRMED.")
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 69 persistence failed. Rulează mai întâi SQL-ul Stage 69 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage69 = load_existing_stage69()

if existing_stage69:
    st.divider()
    st.subheader("Stage 69 outcome")

    st.success(
        f"Stage 69 este deja persistată. Run ID: {existing_stage69.get('id')} — "
        f"Outcome: {existing_stage69.get('upload_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage69.get("upload_outcome"),
    )
    o2.metric(
        "Portal upload?",
        "YES" if bool(existing_stage69.get("portal_upload_performed")) else "NO",
    )
    o3.metric(
        "Submitted?",
        "YES" if bool(existing_stage69.get("external_submission_performed")) else "NO",
    )
    o4.metric(
        "Receipt?",
        "YES" if bool(existing_stage69.get("external_receipt_obtained")) else "NO",
    )

    st.write(
        f"**Application reference:** `{existing_stage69.get('application_reference')}`"
    )
    st.write(
        f"**Stage 68 manifest SHA256:** `{existing_stage69.get('stage68_file_manifest_sha256')}`"
    )
    st.write(
        f"**Upload evidence SHA256:** `{existing_stage69.get('upload_evidence_sha256')}`"
    )
    st.write(
        f"**Run fingerprint:** `{existing_stage69.get('run_fingerprint')}`"
    )

    if normalize_text(existing_stage69.get("upload_outcome")).upper() == "PORTAL_UPLOAD_CONFIRMED":
        st.success(
            "Stage 69 PORTAL_UPLOAD_CONFIRMED. The exact Stage 68 file set is recorded as uploaded "
            "to the bound portal draft. No final submission or receipt is recorded. "
            "A future Stage 70 may perform post-upload portal validation."
        )


st.caption(
    "Invariantă Stage 69 v1.0: PORTAL_UPLOAD_CONFIRMED is explicit user-confirmed evidence of the "
    "observed portal upload state for the exact Stage 68 manifest. Stage 69 does not automate browser "
    "upload and never marks the proposal as submitted."
)
