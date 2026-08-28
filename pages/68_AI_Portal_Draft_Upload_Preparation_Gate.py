import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 68 v1.0 — AI PORTAL DRAFT UPLOAD PREPARATION GATE
#
# Purpose:
#   Consume ONLY Stage 67 PORTAL_DRAFT_BOUND and prepare an immutable,
#   metadata-only manifest of the files intended for the exact EU portal
#   draft. No file is uploaded to the Funding & Tenders Portal here.
#
# Stage 68 DOES NOT:
#   - log into EU Login
#   - upload to Funding & Tenders
#   - edit Part A in the portal
#   - press Submit
#   - sign declarations
#   - create financial commitments
#   - claim portal receipt
#
# Stage 68 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 67 COMPLETED + PORTAL_DRAFT_BOUND
#   - Stage 67 binding fingerprint stable
#   - Stage 67 draft binding SHA256 stable
#   - Stage 66 PORTAL_SESSION_ESTABLISHED
#   - Stage 65 READY_TO_OPEN_PORTAL
#   - Stage 64 EXTERNAL_PREFLIGHT_READY
#   - Stage 63 READY_TO_EXECUTE
#   - Stage 62 HANDOFF_READY
#   - Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF
#   - Stage 60 PACKAGE_READY
#
# User preparation:
#   - selects the exact local files intended for later portal upload
#   - Stage 68 hashes the bytes locally/in-memory
#   - only metadata + SHA256 are persisted
#   - file bytes are NOT persisted by Stage 68
#
# Outcomes:
#   UPLOAD_PACKAGE_READY
#   BLOCKED
#
# Handoff:
#   Stage 69 may consume ONLY UPLOAD_PACKAGE_READY.
# =====================================================================


st.set_page_config(
    page_title="Stage 68 v1.0 — Portal Draft Upload Preparation",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Etapa 68 v1.0 — AI Portal Draft Upload Preparation Gate")
st.caption(
    "Pregătește și fixează manifestul fișierelor care vor fi încărcate ulterior în draftul oficial. "
    "Stage 68 NU face upload în portal și NU trimite propunerea."
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


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
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
# Supabase / authentication
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)

user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 68 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
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
        key="stage68_project",
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
    st.error("Stage 68 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Stage 67 — exact draft binding
# ---------------------------------------------------------------------

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

stage67 = next(
    (
        r for r in stage67_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("binding_outcome")).upper() == "PORTAL_DRAFT_BOUND"
        and bool(r.get("portal_session_established"))
        and bool(r.get("draft_bound"))
        and not bool(r.get("upload_started"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if stage67:
    stage67_run_id = str(stage67.get("id") or "")
    stage67_status = normalize_text(stage67.get("run_status")).upper()
    stage67_outcome = normalize_text(stage67.get("binding_outcome")).upper()
    stage67_binding_fingerprint = normalize_text(stage67.get("binding_fingerprint"))
    stage67_draft_binding_sha256 = normalize_text(stage67.get("draft_binding_sha256"))
    stage67_run_fingerprint = normalize_text(stage67.get("run_fingerprint"))

    application_reference = normalize_text(stage67.get("application_reference"))
    draft_title = normalize_text(stage67.get("draft_title"))
    current_portal_url = normalize_text(stage67.get("current_portal_url"))

    stage66_run_id = str(stage67.get("stage66_run_id") or "")
    stage65_run_id = str(stage67.get("stage65_run_id") or "")
    stage64_run_id = str(stage67.get("stage64_run_id") or "")
    stage63_run_id = str(stage67.get("stage63_run_id") or "")
    stage62_run_id = str(stage67.get("stage62_run_id") or "")
    stage61_run_id = str(stage67.get("stage61_run_id") or "")
    stage60_run_id = str(stage67.get("stage60_run_id") or "")
    stage59_run_id = str(stage67.get("stage59_run_id") or "")
    stage57_run_id = str(stage67.get("stage57_run_id") or "")
else:
    stage67_run_id = ""
    stage67_status = "MISSING"
    stage67_outcome = "MISSING"
    stage67_binding_fingerprint = ""
    stage67_draft_binding_sha256 = ""
    stage67_run_fingerprint = ""
    application_reference = ""
    draft_title = ""
    current_portal_url = ""
    stage66_run_id = stage65_run_id = stage64_run_id = stage63_run_id = ""
    stage62_run_id = stage61_run_id = stage60_run_id = ""
    stage59_run_id = stage57_run_id = ""


# ---------------------------------------------------------------------
# Bound upstream chain
# ---------------------------------------------------------------------

stage66 = get_bound("stage66_portal_session_establishment_runs", stage66_run_id)
stage65 = get_bound("stage65_external_execution_session_runs", stage65_run_id)
stage64 = get_bound("stage64_external_portal_preflight_runs", stage64_run_id)
stage63 = get_bound("stage63_external_execution_authorization_runs", stage63_run_id)
stage62 = get_bound("stage62_controlled_submission_handoff_runs", stage62_run_id)
stage61 = get_bound("stage61_human_approval_runs", stage61_run_id)
stage60 = get_bound("stage60_submission_package_runs", stage60_run_id)


# ---------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------

stage67_binding_payload = as_dict(stage67.get("binding_payload")) if stage67 else {}
recomputed_stage67_binding_fingerprint = (
    stable_sha256(stage67_binding_payload)
    if stage67_binding_payload
    else ""
)

stage67_draft_binding_evidence = as_dict(stage67.get("draft_binding_evidence")) if stage67 else {}
recomputed_stage67_draft_binding_sha256 = (
    stable_sha256(stage67_draft_binding_evidence)
    if stage67_draft_binding_evidence
    else ""
)

stage67_run_payload = as_dict(stage67.get("run_payload")) if stage67 else {}
recomputed_stage67_run_fingerprint = (
    stable_sha256(stage67_run_payload)
    if stage67_run_payload
    else ""
)

stage66_outcome = (
    normalize_text(stage66.get("session_outcome")).upper()
    if stage66 else "MISSING"
)
stage65_outcome = (
    normalize_text(stage65.get("session_outcome")).upper()
    if stage65 else "MISSING"
)
stage64_outcome = (
    normalize_text(stage64.get("preflight_outcome")).upper()
    if stage64 else "MISSING"
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

stage60_package_sha256 = ""
if stage60:
    for key in (
        "package_sha256",
        "submission_package_sha256",
        "final_package_sha256",
        "package_fingerprint",
    ):
        value = normalize_text(stage60.get(key))
        if value:
            stage60_package_sha256 = value
            break


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
    "Stage 67 exists",
    bool(stage67),
    stage67_run_id or "MISSING",
)

add_check(
    "Stage 67 COMPLETED",
    stage67_status == "COMPLETED",
    stage67_status,
)

add_check(
    "Stage 67 PORTAL_DRAFT_BOUND",
    stage67_outcome == "PORTAL_DRAFT_BOUND",
    stage67_outcome,
)

add_check(
    "Stage 67 exact application reference",
    len(application_reference) >= 3,
    application_reference or "MISSING",
)

add_check(
    "Stage 67 binding fingerprint stable",
    bool(stage67_binding_fingerprint)
    and stage67_binding_fingerprint == recomputed_stage67_binding_fingerprint,
    f"stored={stage67_binding_fingerprint[:16]}..., recomputed={recomputed_stage67_binding_fingerprint[:16]}...",
)

add_check(
    "Stage 67 draft binding SHA256 stable",
    bool(stage67_draft_binding_sha256)
    and stage67_draft_binding_sha256 == recomputed_stage67_draft_binding_sha256,
    f"stored={stage67_draft_binding_sha256[:16]}..., recomputed={recomputed_stage67_draft_binding_sha256[:16]}...",
)

add_check(
    "Stage 67 run fingerprint stable",
    bool(stage67_run_fingerprint)
    and stage67_run_fingerprint == recomputed_stage67_run_fingerprint,
    f"stored={stage67_run_fingerprint[:16]}..., recomputed={recomputed_stage67_run_fingerprint[:16]}...",
)

add_check(
    "Stage 66 PORTAL_SESSION_ESTABLISHED",
    stage66_outcome == "PORTAL_SESSION_ESTABLISHED",
    stage66_outcome,
)

add_check(
    "Stage 65 READY_TO_OPEN_PORTAL",
    stage65_outcome == "READY_TO_OPEN_PORTAL",
    stage65_outcome,
)

add_check(
    "Stage 64 EXTERNAL_PREFLIGHT_READY",
    stage64_outcome == "EXTERNAL_PREFLIGHT_READY",
    stage64_outcome,
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

stage68_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)

gate_reason = (
    "Stage 67 draft binding and the complete upstream submission-readiness chain are valid."
    if stage68_gate == "READY"
    else "Stage 68 fail-closed gate failed: "
    + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Existing Stage 68
# ---------------------------------------------------------------------

def load_existing_stage68():
    if not stage67_run_id:
        return None

    data = (
        supabase
        .table("stage68_upload_package_preparation_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage67_run_id", stage67_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage68 = load_existing_stage68()


# ---------------------------------------------------------------------
# Gate UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 67 → Stage 68 upload-package preparation")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Stage 67", stage67_outcome)
m2.metric("Draft reference", application_reference or "—")
m3.metric("Deadline", str(deadline or "—")[:10])
m4.metric("Integrity", "VERIFIED" if stage68_gate == "READY" else "FAILED")

with st.expander("Stage 68 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.write(f"**Gate:** `{stage68_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Bound draft:** `{application_reference or '—'}`")
if draft_title:
    st.write(f"**Draft title:** `{draft_title}`")
st.write(f"**Stage 67 draft binding SHA256:** `{stage67_draft_binding_sha256}`")

if stage60_package_sha256:
    st.write(f"**Stage 60 package hash/fingerprint:** `{stage60_package_sha256}`")


# ---------------------------------------------------------------------
# Upload-package manifest preparation
# ---------------------------------------------------------------------

if not existing_stage68:
    st.divider()
    st.subheader("Local file manifest for later portal upload")

    st.info(
        "Selectează aici fișierele exacte pe care intenționezi să le încarci ulterior în portal. "
        "Stage 68 calculează SHA256 local și persistă numai metadata + hash; nu trimite fișierele către portal."
    )

    uploaded_files = st.file_uploader(
        "Select proposal files",
        accept_multiple_files=True,
        key="stage68_files",
        help="Select the final local files intended for later portal upload. File bytes are not persisted by Stage 68.",
    )

    manifest_files = []

    if uploaded_files:
        for index, uploaded in enumerate(uploaded_files):
            data = uploaded.getvalue()
            file_sha256 = bytes_sha256(data)

            role_default = "PART_B" if uploaded.name.lower().endswith(".pdf") and index == 0 else "OTHER"

            role = st.selectbox(
                f"Portal role — {uploaded.name}",
                [
                    "PART_B",
                    "ANNEX",
                    "ETHICS",
                    "SECURITY",
                    "CLINICAL_STUDIES",
                    "OTHER",
                ],
                index=[
                    "PART_B",
                    "ANNEX",
                    "ETHICS",
                    "SECURITY",
                    "CLINICAL_STUDIES",
                    "OTHER",
                ].index(role_default),
                key=f"stage68_role_{index}_{uploaded.name}",
            )

            required_for_upload = st.checkbox(
                f"Required for intended upload — {uploaded.name}",
                value=True,
                key=f"stage68_required_{index}_{uploaded.name}",
            )

            manifest_files.append(
                {
                    "ordinal": index + 1,
                    "filename": uploaded.name,
                    "size_bytes": len(data),
                    "mime_type": normalize_text(uploaded.type) or "application/octet-stream",
                    "sha256": file_sha256,
                    "portal_role": role,
                    "required_for_upload": bool(required_for_upload),
                    "bytes_persisted": False,
                }
            )

    if manifest_files:
        st.subheader("Prepared file manifest")
        st.dataframe(
            [
                {
                    "File": item["filename"],
                    "Role": item["portal_role"],
                    "Size": item["size_bytes"],
                    "SHA256": item["sha256"],
                    "Required": item["required_for_upload"],
                }
                for item in manifest_files
            ],
            use_container_width=True,
            hide_index=True,
        )

    part_b_files = [
        item for item in manifest_files
        if item["portal_role"] == "PART_B"
    ]

    duplicate_hashes = (
        len({item["sha256"] for item in manifest_files})
        != len(manifest_files)
    )

    has_files = len(manifest_files) > 0
    has_exactly_one_part_b = len(part_b_files) == 1
    all_nonempty = all(item["size_bytes"] > 0 for item in manifest_files)
    all_hashes_valid = all(len(item["sha256"]) == 64 for item in manifest_files)

    if duplicate_hashes:
        st.error("Duplicate file content detected: two or more selected files have the same SHA256.")

    if has_files and not has_exactly_one_part_b:
        st.warning(
            "Stage 68 requires exactly one file designated PART_B in this preparation manifest. "
            "Additional annexes may be designated separately."
        )

    confirmed_exact_files = st.checkbox(
        "I confirm these are the exact local files intended for the bound portal draft.",
        key="stage68_confirm_exact",
    )

    confirmed_not_uploaded = st.checkbox(
        "I confirm Stage 68 is preparation only; no portal upload has been performed by this stage.",
        key="stage68_confirm_no_upload",
    )

    confirmed_bound_draft = st.checkbox(
        f"I confirm this manifest is for draft {application_reference or '—'} only.",
        key="stage68_confirm_draft",
    )

    confirmation_phrase = st.text_input(
        "Confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 68 UPLOAD PACKAGE",
        key="stage68_phrase",
    )

    preparation_note = st.text_area(
        "Optional preparation note",
        placeholder="Optional non-sensitive note about this file package.",
        key="stage68_note",
    )

    file_manifest = {
        "manifest_version": "stage68-v1.0",
        "draft_reference": application_reference,
        "draft_title": draft_title or None,
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10],
        "file_count": len(manifest_files),
        "files": manifest_files,
        "bytes_persisted": False,
        "portal_upload_performed": False,
    }

    file_manifest_sha256 = stable_sha256(file_manifest)

    preparation_payload = {
        "preparation_version": "stage68-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage67_run_id": stage67_run_id,
        "stage67_binding_fingerprint": stage67_binding_fingerprint,
        "stage67_draft_binding_sha256": stage67_draft_binding_sha256,
        "stage60_run_id": stage60_run_id,
        "stage60_package_sha256": stage60_package_sha256 or None,
        "application_reference": application_reference,
        "draft_title": draft_title or None,
        "current_portal_url": current_portal_url,
        "file_manifest_sha256": file_manifest_sha256,
        "file_count": len(manifest_files),
        "part_b_count": len(part_b_files),
        "confirmation_note": normalize_text(preparation_note) or None,
        "portal_upload_performed": False,
        "external_submission_performed": False,
        "external_receipt_obtained": False,
    }

    preparation_fingerprint = stable_sha256(preparation_payload)

    run_basis = {
        "stage": 68,
        "fingerprint_contract": "stage68-v1.0-upload-package-preparation",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage67_run_id": stage67_run_id,
        "stage67_draft_binding_sha256": stage67_draft_binding_sha256,
        "stage60_run_id": stage60_run_id,
        "file_manifest_sha256": file_manifest_sha256,
        "preparation_fingerprint": preparation_fingerprint,
        "stage68_gate": stage68_gate,
    }

    run_fingerprint = stable_sha256(run_basis)

    package_ready = (
        stage68_gate == "READY"
        and has_files
        and has_exactly_one_part_b
        and all_nonempty
        and all_hashes_valid
        and not duplicate_hashes
        and confirmed_exact_files
        and confirmed_not_uploaded
        and confirmed_bound_draft
        and normalize_text(confirmation_phrase) == "CONFIRM STAGE 68 UPLOAD PACKAGE"
    )

    if st.button(
        "📦 Confirm & persist Stage 68 upload package",
        type="primary",
        use_container_width=True,
        key="stage68_confirm",
        disabled=not package_ready,
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

                "stage": 68,
                "preparation_version": "stage68-v1.0",

                "opportunity_identity": identity,
                "official_deadline": str(deadline or "")[:10] or None,

                "application_reference": application_reference,
                "draft_title": draft_title or None,
                "current_portal_url": current_portal_url,

                "run_status": "COMPLETED",
                "preparation_outcome": "UPLOAD_PACKAGE_READY",

                "file_count": len(manifest_files),
                "part_b_count": len(part_b_files),

                "portal_upload_started": False,
                "portal_upload_performed": False,
                "external_submission_performed": False,
                "external_receipt_obtained": False,

                "file_bytes_persisted": False,

                "file_manifest_sha256": file_manifest_sha256,
                "preparation_fingerprint": preparation_fingerprint,
                "run_fingerprint": run_fingerprint,

                "file_manifest": file_manifest,
                "preparation_payload": preparation_payload,
                "run_payload": run_basis,

                "prepared_at": now_iso(),
                "completed_at": now_iso(),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                rpc_result = supabase.rpc(
                    "persist_stage68_upload_package",
                    {"p_payload": payload},
                ).execute()

                if not rpc_result.data:
                    (
                        supabase
                        .table("stage68_upload_package_preparation_runs")
                        .insert(payload)
                        .execute()
                    )

            except Exception:
                (
                    supabase
                    .table("stage68_upload_package_preparation_runs")
                    .insert(payload)
                    .execute()
                )

            st.success("Stage 68 persisted — Outcome UPLOAD_PACKAGE_READY.")
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 68 persistence failed. Rulează mai întâi SQL-ul Stage 68 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage68 = load_existing_stage68()

if existing_stage68:
    st.divider()
    st.subheader("Stage 68 outcome")

    st.success(
        f"Stage 68 este deja persistată. Run ID: {existing_stage68.get('id')} — "
        f"Outcome: {existing_stage68.get('preparation_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage68.get("preparation_outcome"),
    )
    o2.metric(
        "Files",
        existing_stage68.get("file_count"),
    )
    o3.metric(
        "Portal upload?",
        "YES" if bool(existing_stage68.get("portal_upload_performed")) else "NO",
    )
    o4.metric(
        "Submitted?",
        "YES" if bool(existing_stage68.get("external_submission_performed")) else "NO",
    )

    st.write(
        f"**Application reference:** `{existing_stage68.get('application_reference')}`"
    )
    st.write(
        f"**File manifest SHA256:** `{existing_stage68.get('file_manifest_sha256')}`"
    )
    st.write(
        f"**Preparation fingerprint:** `{existing_stage68.get('preparation_fingerprint')}`"
    )

    manifest = as_dict(existing_stage68.get("file_manifest"))
    if manifest.get("files"):
        with st.expander("Persisted Stage 68 metadata-only manifest", expanded=False):
            st.dataframe(
                manifest["files"],
                use_container_width=True,
                hide_index=True,
            )

    if normalize_text(existing_stage68.get("preparation_outcome")).upper() == "UPLOAD_PACKAGE_READY":
        st.success(
            "Stage 68 UPLOAD_PACKAGE_READY. The exact local file set is bound by SHA256 to the portal draft. "
            "No file bytes were persisted by Stage 68, no portal upload occurred, and no submission occurred. "
            "A future Stage 69 may consume this immutable upload manifest."
        )


st.caption(
    "Invariantă Stage 68 v1.0: UPLOAD_PACKAGE_READY means only that the exact intended local file set "
    "has been hashed and bound to the Stage 67 portal draft. Stage 68 persists metadata and SHA256 only; "
    "it does not upload files to Funding & Tenders and does not submit."
)
