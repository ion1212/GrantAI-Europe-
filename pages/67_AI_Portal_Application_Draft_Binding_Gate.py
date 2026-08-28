import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Stage 67 v1.0 — Portal Application Draft Binding",
    page_icon="🧷",
    layout="wide",
)

st.title("🧷 Etapa 67 v1.0 — AI Portal Application / Draft Binding Gate")
st.caption(
    "Leagă sesiunea confirmată din Stage 66 de draftul/aplicația reală din Funding & Tenders Portal. "
    "Nu face upload și nu trimite aplicația."
)

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

def official_domain_ok(url: str) -> bool:
    try:
        parsed = urlparse(normalize_text(url))
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower().strip(".")
        return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES)
    except Exception:
        return False

def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Stage 67 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage67_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 67 BLOCKED: nu există opportunity lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")

stage66_candidates = rows(
    "stage66_portal_session_establishment_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)

stage66 = next(
    (
        r for r in stage66_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("session_outcome")).upper() == "PORTAL_SESSION_ESTABLISHED"
        and bool(r.get("portal_session_established"))
        and not bool(r.get("upload_started"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if stage66:
    stage66_run_id = str(stage66.get("id") or "")
    stage66_status = normalize_text(stage66.get("run_status")).upper()
    stage66_outcome = normalize_text(stage66.get("session_outcome")).upper()
    stage66_confirmation_fingerprint = normalize_text(stage66.get("confirmation_fingerprint"))
    stage66_session_evidence_sha256 = normalize_text(stage66.get("session_evidence_sha256"))
    stage66_run_fingerprint = normalize_text(stage66.get("run_fingerprint"))

    stage65_run_id = str(stage66.get("stage65_run_id") or "")
    stage64_run_id = str(stage66.get("stage64_run_id") or "")
    stage63_run_id = str(stage66.get("stage63_run_id") or "")
    stage62_run_id = str(stage66.get("stage62_run_id") or "")
    stage61_run_id = str(stage66.get("stage61_run_id") or "")
    stage60_run_id = str(stage66.get("stage60_run_id") or "")
    stage59_run_id = str(stage66.get("stage59_run_id") or "")
    stage57_run_id = str(stage66.get("stage57_run_id") or "")
else:
    stage66_run_id = ""
    stage66_status = "MISSING"
    stage66_outcome = "MISSING"
    stage66_confirmation_fingerprint = ""
    stage66_session_evidence_sha256 = ""
    stage66_run_fingerprint = ""
    stage65_run_id = stage64_run_id = stage63_run_id = stage62_run_id = ""
    stage61_run_id = stage60_run_id = stage59_run_id = stage57_run_id = ""

def get_bound(table: str, row_id: str):
    if not row_id:
        return None
    data = rows(
        table,
        {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
        "created_at",
        200,
    )
    return next((r for r in data if str(r.get("id") or "") == row_id), None)

stage65 = get_bound("stage65_external_execution_session_runs", stage65_run_id)
stage64 = get_bound("stage64_external_portal_preflight_runs", stage64_run_id)
stage63 = get_bound("stage63_external_execution_authorization_runs", stage63_run_id)
stage62 = get_bound("stage62_controlled_submission_handoff_runs", stage62_run_id)
stage61 = get_bound("stage61_human_approval_runs", stage61_run_id)
stage60 = get_bound("stage60_submission_package_runs", stage60_run_id)

stage66_confirmation_payload = as_dict(stage66.get("confirmation_payload")) if stage66 else {}
recomputed_stage66_confirmation_fingerprint = (
    stable_sha256(stage66_confirmation_payload) if stage66_confirmation_payload else ""
)

stage66_session_evidence = as_dict(stage66.get("session_evidence")) if stage66 else {}
recomputed_stage66_session_evidence_sha256 = (
    stable_sha256(stage66_session_evidence) if stage66_session_evidence else ""
)

stage66_run_payload = as_dict(stage66.get("run_payload")) if stage66 else {}
recomputed_stage66_run_fingerprint = (
    stable_sha256(stage66_run_payload) if stage66_run_payload else ""
)

stage65_outcome = normalize_text(stage65.get("session_outcome")).upper() if stage65 else "MISSING"
stage64_outcome = normalize_text(stage64.get("preflight_outcome")).upper() if stage64 else "MISSING"
stage63_outcome = normalize_text(stage63.get("authorization_outcome")).upper() if stage63 else "MISSING"
stage62_outcome = normalize_text(stage62.get("handoff_outcome")).upper() if stage62 else "MISSING"
stage61_outcome = normalize_text(stage61.get("approval_outcome")).upper() if stage61 else "MISSING"
stage60_outcome = normalize_text(stage60.get("package_outcome")).upper() if stage60 else "MISSING"

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})

add_check("ACTIVE lock", normalize_text(lock.get("lock_status")).upper() == "ACTIVE", normalize_text(lock.get("lock_status")).upper())
add_check("Workflow allowed", workflow_allowed, f"workflow_allowed={workflow_allowed}")
add_check("Deadline valid", future_deadline(deadline), str(deadline or "")[:10])
add_check("Stage 66 exists", bool(stage66), stage66_run_id or "MISSING")
add_check("Stage 66 COMPLETED", stage66_status == "COMPLETED", stage66_status)
add_check("Stage 66 PORTAL_SESSION_ESTABLISHED", stage66_outcome == "PORTAL_SESSION_ESTABLISHED", stage66_outcome)
add_check("Stage 66 portal session confirmed", bool(stage66) and bool(stage66.get("portal_session_established")), f"portal_session_established={bool(stage66.get('portal_session_established')) if stage66 else None}")
add_check(
    "Stage 66 confirmation fingerprint stable",
    bool(stage66_confirmation_fingerprint) and stage66_confirmation_fingerprint == recomputed_stage66_confirmation_fingerprint,
    f"stored={stage66_confirmation_fingerprint[:16]}..., recomputed={recomputed_stage66_confirmation_fingerprint[:16]}...",
)
add_check(
    "Stage 66 session evidence SHA256 stable",
    bool(stage66_session_evidence_sha256) and stage66_session_evidence_sha256 == recomputed_stage66_session_evidence_sha256,
    f"stored={stage66_session_evidence_sha256[:16]}..., recomputed={recomputed_stage66_session_evidence_sha256[:16]}...",
)
add_check(
    "Stage 66 run fingerprint stable",
    bool(stage66_run_fingerprint) and stage66_run_fingerprint == recomputed_stage66_run_fingerprint,
    f"stored={stage66_run_fingerprint[:16]}..., recomputed={recomputed_stage66_run_fingerprint[:16]}...",
)
add_check("Stage 65 READY_TO_OPEN_PORTAL", stage65_outcome == "READY_TO_OPEN_PORTAL", stage65_outcome)
add_check("Stage 64 EXTERNAL_PREFLIGHT_READY", stage64_outcome == "EXTERNAL_PREFLIGHT_READY", stage64_outcome)
add_check("Stage 63 READY_TO_EXECUTE", stage63_outcome == "READY_TO_EXECUTE", stage63_outcome)
add_check("Stage 62 HANDOFF_READY", stage62_outcome == "HANDOFF_READY", stage62_outcome)
add_check("Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF", stage61_outcome == "APPROVED_FOR_SUBMISSION_HANDOFF", stage61_outcome)
add_check("Stage 60 PACKAGE_READY", stage60_outcome == "PACKAGE_READY", stage60_outcome)

stage67_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

def load_existing_stage67():
    if not stage66_run_id:
        return None
    data = (
        supabase.table("stage67_portal_draft_binding_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage66_run_id", stage66_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []
    return data[0] if data else None

existing_stage67 = load_existing_stage67()

st.divider()
st.subheader("Stage 66 → Stage 67 draft-binding gate")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 66", stage66_outcome)
m2.metric("Portal session?", "YES" if stage66 and stage66.get("portal_session_established") else "NO")
m3.metric("Deadline", str(deadline or "—")[:10])
m4.metric("Integrity", "VERIFIED" if stage67_gate == "READY" else "FAILED")

with st.expander("Stage 67 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

if not existing_stage67:
    st.divider()
    st.subheader("Explicit portal application/draft binding")

    st.warning(
        "Nu introduce parole, cookie-uri, token-uri sau coduri MFA. "
        "Introdu doar referința vizibilă a aplicației/draftului și URL-ul oficial."
    )

    expected_portal_url = normalize_text(stage66.get("current_portal_url")) if stage66 else ""

    current_portal_url = st.text_input(
        "Current official portal URL",
        value=expected_portal_url,
        key="stage67_current_url",
    )
    official_url_ok = official_domain_ok(current_portal_url)

    application_reference = st.text_input(
        "Application / draft reference visible in portal",
        placeholder="Example: proposal number, draft ID, application reference",
        key="stage67_application_reference",
    )

    draft_title = st.text_input(
        "Draft title visible in portal",
        placeholder="Optional but recommended",
        key="stage67_draft_title",
    )

    confirmed_topic = st.checkbox(
        "The application/draft shown in the portal belongs to the locked opportunity shown above.",
        key="stage67_topic_match",
    )
    confirmed_editable = st.checkbox(
        "The application/draft is still editable and has not been submitted.",
        key="stage67_editable",
    )
    confirmed_same_session = st.checkbox(
        "This draft is visible in the authenticated portal session confirmed at Stage 66.",
        key="stage67_same_session",
    )

    confirmation_phrase = st.text_input(
        "Confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 67 DRAFT BINDING",
        key="stage67_phrase",
    )

    confirmation_note = st.text_area(
        "Optional binding note",
        placeholder="Optional non-sensitive note.",
        key="stage67_note",
    )

    application_reference_ok = len(normalize_text(application_reference)) >= 3

    all_confirmations = (
        official_url_ok
        and application_reference_ok
        and confirmed_topic
        and confirmed_editable
        and confirmed_same_session
        and normalize_text(confirmation_phrase) == "CONFIRM STAGE 67 DRAFT BINDING"
    )

    binding_payload = {
        "binding_version": "stage67-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10],
        "stage66_run_id": stage66_run_id,
        "stage66_confirmation_fingerprint": stage66_confirmation_fingerprint,
        "stage66_session_evidence_sha256": stage66_session_evidence_sha256,
        "current_portal_url": normalize_text(current_portal_url),
        "application_reference": normalize_text(application_reference),
        "draft_title": normalize_text(draft_title) or None,
        "confirmed_topic": bool(confirmed_topic),
        "confirmed_editable": bool(confirmed_editable),
        "confirmed_same_session": bool(confirmed_same_session),
        "confirmation_note": normalize_text(confirmation_note) or None,
        "credentials_collected": False,
        "cookies_collected": False,
        "tokens_collected": False,
        "mfa_codes_collected": False,
    }

    binding_fingerprint = stable_sha256(binding_payload)

    draft_binding_evidence = {
        "stage": 67,
        "evidence_type": "USER_CONFIRMED_PORTAL_DRAFT_BINDING",
        "binding_payload": binding_payload,
        "state": {
            "portal_session_established": True,
            "draft_bound": True,
            "upload_started": False,
            "external_submission_performed": False,
            "external_receipt_obtained": False,
        },
    }

    draft_binding_sha256 = stable_sha256(draft_binding_evidence)

    run_basis = {
        "stage": 67,
        "fingerprint_contract": "stage67-v1.0-portal-draft-binding",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage66_run_id": stage66_run_id,
        "stage66_session_evidence_sha256": stage66_session_evidence_sha256,
        "binding_fingerprint": binding_fingerprint,
        "draft_binding_sha256": draft_binding_sha256,
        "stage67_gate": stage67_gate,
    }
    run_fingerprint = stable_sha256(run_basis)

    if st.button(
        "🧷 Confirm & persist Stage 67 draft binding",
        type="primary",
        use_container_width=True,
        key="stage67_confirm",
        disabled=(stage67_gate != "READY" or not all_confirmations),
    ):
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
            "stage": 67,
            "binding_version": "stage67-v1.0",
            "opportunity_identity": identity,
            "official_deadline": str(deadline or "")[:10] or None,
            "current_portal_url": normalize_text(current_portal_url),
            "application_reference": normalize_text(application_reference),
            "draft_title": normalize_text(draft_title) or None,
            "run_status": "COMPLETED",
            "binding_outcome": "PORTAL_DRAFT_BOUND",
            "portal_session_established": True,
            "draft_bound": True,
            "upload_started": False,
            "external_submission_performed": False,
            "external_receipt_obtained": False,
            "credentials_collected": False,
            "cookies_collected": False,
            "tokens_collected": False,
            "mfa_codes_collected": False,
            "binding_fingerprint": binding_fingerprint,
            "draft_binding_sha256": draft_binding_sha256,
            "run_fingerprint": run_fingerprint,
            "binding_payload": binding_payload,
            "draft_binding_evidence": draft_binding_evidence,
            "run_payload": run_basis,
            "confirmed_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        try:
            rpc_result = supabase.rpc(
                "persist_stage67_portal_draft_binding",
                {"p_payload": payload},
            ).execute()

            if not rpc_result.data:
                supabase.table("stage67_portal_draft_binding_runs").insert(payload).execute()

            st.success("Stage 67 persisted — Outcome PORTAL_DRAFT_BOUND.")
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 67 persistence failed. Rulează mai întâi SQL-ul Stage 67 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )

existing_stage67 = load_existing_stage67()

if existing_stage67:
    st.divider()
    st.subheader("Stage 67 outcome")

    st.success(
        f"Stage 67 este deja persistată. Run ID: {existing_stage67.get('id')} — "
        f"Outcome: {existing_stage67.get('binding_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage67.get("binding_outcome"))
    o2.metric("Draft bound?", "YES" if bool(existing_stage67.get("draft_bound")) else "NO")
    o3.metric("Upload started?", "YES" if bool(existing_stage67.get("upload_started")) else "NO")
    o4.metric("Submitted?", "YES" if bool(existing_stage67.get("external_submission_performed")) else "NO")

    st.write(f"**Application reference:** `{existing_stage67.get('application_reference')}`")
    st.write(f"**Current portal URL:** `{existing_stage67.get('current_portal_url')}`")
    st.write(f"**Binding fingerprint:** `{existing_stage67.get('binding_fingerprint')}`")
    st.write(f"**Draft binding SHA256:** `{existing_stage67.get('draft_binding_sha256')}`")

    if normalize_text(existing_stage67.get("binding_outcome")).upper() == "PORTAL_DRAFT_BOUND":
        st.success(
            "Stage 67 PORTAL_DRAFT_BOUND. The authenticated session is now bound to an explicit portal "
            "application/draft reference. No upload has started and no submission has occurred. "
            "A future Stage 68 may consume this binding."
        )

st.caption(
    "Invariantă Stage 67 v1.0: PORTAL_DRAFT_BOUND requires explicit human confirmation of the exact "
    "application/draft visible in the authenticated portal session. Stage 67 collects no credentials "
    "and performs no upload or submission."
)
