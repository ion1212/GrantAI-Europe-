import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 66 v1.0 — AI CONTROLLED PORTAL SESSION ESTABLISHMENT GATE
#
# Purpose:
#   Consume ONLY Stage 65 READY_TO_OPEN_PORTAL and record, with explicit
#   human confirmation, that a real authenticated Funding & Tenders portal
#   session has been established OUTSIDE this app.
#
# Security design:
#   - NEVER asks for EU Login username/password
#   - NEVER stores credentials, cookies, MFA codes or session tokens
#   - NEVER claims login succeeded without explicit human confirmation
#   - NEVER uploads or submits anything
#
# Stage 66 verifies:
#   - ACTIVE lock + deadline still valid
#   - Stage 65 COMPLETED + READY_TO_OPEN_PORTAL
#   - Stage 65 run fingerprint stable
#   - Stage 65 session SHA256 stable
#   - Stage 64 EXTERNAL_PREFLIGHT_READY
#   - Stage 63 READY_TO_EXECUTE
#   - Stage 62 HANDOFF_READY
#   - Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF
#   - Stage 60 PACKAGE_READY
#
# Human confirmation requires:
#   - confirmation that the user opened the official portal
#   - confirmation that EU Login/authenticated session is active
#   - confirmation that the displayed topic matches the locked opportunity
#   - current official portal URL
#   - exact phrase: CONFIRM STAGE 66 PORTAL SESSION
#
# Outcomes:
#   PORTAL_SESSION_ESTABLISHED
#   BLOCKED
#
# Handoff:
#   Stage 67 may consume ONLY PORTAL_SESSION_ESTABLISHED.
# =====================================================================

st.set_page_config(
    page_title="Stage 66 v1.0 — Controlled Portal Session Establishment",
    page_icon="🔑",
    layout="wide",
)

st.title("🔑 Etapa 66 v1.0 — AI Controlled Portal Session Establishment Gate")
st.caption(
    "Înregistrează numai o sesiune reală de portal confirmată explicit de utilizator. "
    "Nu colectează parole, cookie-uri, coduri MFA sau token-uri și nu trimite proiectul."
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

OFFICIAL_HOST_SUFFIXES = (
    "ec.europa.eu",
    "europa.eu",
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
    st.error("Stage 66 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
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
        key="stage66_project",
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
    st.error("Stage 66 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 65 READY_TO_OPEN_PORTAL
# ---------------------------------------------------------------------

stage65_candidates = rows(
    "stage65_external_execution_session_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage65 = next(
    (
        r for r in stage65_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("session_outcome")).upper() == "READY_TO_OPEN_PORTAL"
        and not bool(r.get("portal_login_started"))
        and not bool(r.get("portal_session_established"))
        and not bool(r.get("upload_started"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if stage65:
    stage65_run_id = str(stage65.get("id") or "")
    stage65_status = normalize_text(stage65.get("run_status")).upper()
    stage65_outcome = normalize_text(stage65.get("session_outcome")).upper()
    stage65_run_fingerprint = normalize_text(stage65.get("run_fingerprint"))
    stage65_session_sha256 = normalize_text(stage65.get("session_sha256"))

    stage64_run_id = str(stage65.get("stage64_run_id") or "")
    stage63_run_id = str(stage65.get("stage63_run_id") or "")
    stage62_run_id = str(stage65.get("stage62_run_id") or "")
    stage61_run_id = str(stage65.get("stage61_run_id") or "")
    stage60_run_id = str(stage65.get("stage60_run_id") or "")
    stage59_run_id = str(stage65.get("stage59_run_id") or "")
    stage57_run_id = str(stage65.get("stage57_run_id") or "")
else:
    stage65_run_id = ""
    stage65_status = "MISSING"
    stage65_outcome = "MISSING"
    stage65_run_fingerprint = ""
    stage65_session_sha256 = ""

    stage64_run_id = ""
    stage63_run_id = ""
    stage62_run_id = ""
    stage61_run_id = ""
    stage60_run_id = ""
    stage59_run_id = ""
    stage57_run_id = ""


# ---------------------------------------------------------------------
# Bound upstream chain
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


stage64 = get_bound(
    "stage64_external_portal_preflight_runs",
    stage64_run_id,
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

stage65_run_payload = as_dict(stage65.get("run_payload")) if stage65 else {}
recomputed_stage65_run_fingerprint = (
    stable_sha256(stage65_run_payload)
    if stage65_run_payload
    else ""
)

stage65_session_manifest = as_dict(stage65.get("session_manifest")) if stage65 else {}
recomputed_stage65_session_sha256 = (
    stable_sha256(stage65_session_manifest)
    if stage65_session_manifest
    else ""
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
    "Stage 65 exists",
    bool(stage65),
    stage65_run_id or "MISSING",
)

add_check(
    "Stage 65 COMPLETED",
    stage65_status == "COMPLETED",
    stage65_status,
)

add_check(
    "Stage 65 READY_TO_OPEN_PORTAL",
    stage65_outcome == "READY_TO_OPEN_PORTAL",
    stage65_outcome,
)

add_check(
    "Stage 65 run fingerprint stable",
    bool(stage65_run_fingerprint)
    and stage65_run_fingerprint == recomputed_stage65_run_fingerprint,
    f"stored={stage65_run_fingerprint[:16]}..., recomputed={recomputed_stage65_run_fingerprint[:16]}...",
)

add_check(
    "Stage 65 session SHA256 stable",
    bool(stage65_session_sha256)
    and stage65_session_sha256 == recomputed_stage65_session_sha256,
    f"stored={stage65_session_sha256[:16]}..., recomputed={recomputed_stage65_session_sha256[:16]}...",
)

add_check(
    "Stage 64 EXTERNAL_PREFLIGHT_READY",
    stage64_outcome == "EXTERNAL_PREFLIGHT_READY",
    stage64_outcome,
)

add_check(
    "Stage 64 portal verified",
    bool(stage64) and bool(stage64.get("portal_state_verified")),
    f"portal_state_verified={bool(stage64.get('portal_state_verified')) if stage64 else None}",
)

add_check(
    "Stage 64 topic identity verified",
    bool(stage64) and bool(stage64.get("topic_identity_verified_live")),
    f"topic_identity_verified_live={bool(stage64.get('topic_identity_verified_live')) if stage64 else None}",
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

stage66_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)

gate_reason = (
    "Stage 65 session envelope and all upstream portal-readiness states are valid."
    if stage66_gate == "READY"
    else "Stage 66 fail-closed gate failed: "
    + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Existing Stage 66
# ---------------------------------------------------------------------

def load_existing_stage66():
    if not stage65_run_id:
        return None

    data = (
        supabase
        .table("stage66_portal_session_establishment_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage65_run_id", stage65_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage66 = load_existing_stage66()


# ---------------------------------------------------------------------
# UI — gate
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 65 → Stage 66 controlled portal-session binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 65", stage65_outcome)
m2.metric("Stage 64", stage64_outcome)
m3.metric("Deadline", str(deadline or "—")[:10])
m4.metric("Integrity", "VERIFIED" if stage66_gate == "READY" else "FAILED")

with st.expander("Stage 66 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.write(f"**Gate:** `{stage66_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Stage 65 session SHA256:** `{stage65_session_sha256}`")


# ---------------------------------------------------------------------
# Human portal-session confirmation
# ---------------------------------------------------------------------

if not existing_stage66:
    st.divider()
    st.subheader("Explicit human portal-session confirmation")

    st.warning(
        "Nu introduce aici parola EU Login, coduri MFA, cookie-uri, token-uri sau alte credențiale. "
        "Stage 66 înregistrează doar confirmarea că sesiunea a fost deschisă de tine în portalul oficial."
    )

    expected_portal_url = normalize_text(
        stage65.get("verified_portal_url")
    ) if stage65 else ""

    st.write(f"**Expected official portal URL:** `{expected_portal_url}`")

    confirmed_opened = st.checkbox(
        "I opened the official EU Funding & Tenders Portal in my browser.",
        key="stage66_opened",
    )

    confirmed_authenticated = st.checkbox(
        "I am authenticated in the portal with my own EU Login session.",
        key="stage66_authenticated",
    )

    confirmed_topic = st.checkbox(
        "The topic/opportunity displayed in the portal matches the locked opportunity shown above.",
        key="stage66_topic",
    )

    current_portal_url = st.text_input(
        "Current official portal URL",
        value=expected_portal_url,
        placeholder="https://ec.europa.eu/info/funding-tenders/...",
        key="stage66_current_url",
    )

    official_url_ok = official_domain_ok(current_portal_url)

    if current_portal_url:
        if official_url_ok:
            st.success("Current URL is on an accepted official EU domain.")
        else:
            st.error("Current URL must be HTTPS and on europa.eu / ec.europa.eu.")

    session_reference = st.text_input(
        "Optional non-secret portal reference",
        placeholder="Optional: application/draft reference visible in portal. Do NOT paste tokens/cookies.",
        key="stage66_reference",
    )

    confirmation_phrase = st.text_input(
        "Confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 66 PORTAL SESSION",
        key="stage66_phrase",
    )

    confirmation_note = st.text_area(
        "Optional confirmation note",
        placeholder="Optional non-sensitive note about the portal session.",
        key="stage66_note",
    )

    all_confirmations = (
        confirmed_opened
        and confirmed_authenticated
        and confirmed_topic
        and official_url_ok
        and normalize_text(confirmation_phrase) == "CONFIRM STAGE 66 PORTAL SESSION"
    )

    confirmation_payload = {
        "confirmation_version": "stage66-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10],
        "stage65_run_id": stage65_run_id,
        "stage65_session_sha256": stage65_session_sha256,
        "confirmed_opened": bool(confirmed_opened),
        "confirmed_authenticated": bool(confirmed_authenticated),
        "confirmed_topic": bool(confirmed_topic),
        "current_portal_url": normalize_text(current_portal_url),
        "session_reference": normalize_text(session_reference) or None,
        "confirmation_note": normalize_text(confirmation_note) or None,
        "credentials_collected": False,
        "cookies_collected": False,
        "tokens_collected": False,
        "mfa_codes_collected": False,
    }

    confirmation_fingerprint = stable_sha256(confirmation_payload)

    session_evidence = {
        "stage": 66,
        "evidence_type": "USER_CONFIRMED_AUTHENTICATED_PORTAL_SESSION",
        "confirmation_payload": confirmation_payload,
        "upstream": {
            "stage65_run_id": stage65_run_id,
            "stage65_run_fingerprint": stage65_run_fingerprint,
            "stage65_session_sha256": stage65_session_sha256,
            "stage64_run_id": stage64_run_id,
        },
        "state": {
            "portal_login_started": True,
            "portal_session_established": True,
            "upload_started": False,
            "external_submission_performed": False,
            "external_receipt_obtained": False,
        },
    }

    session_evidence_sha256 = stable_sha256(session_evidence)

    run_basis = {
        "stage": 66,
        "fingerprint_contract": "stage66-v1.0-user-confirmed-portal-session",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage65_run_id": stage65_run_id,
        "stage65_session_sha256": stage65_session_sha256,
        "confirmation_fingerprint": confirmation_fingerprint,
        "session_evidence_sha256": session_evidence_sha256,
        "stage66_gate": stage66_gate,
    }

    run_fingerprint = stable_sha256(run_basis)

    if st.button(
        "🔑 Confirm & persist authenticated portal session",
        type="primary",
        use_container_width=True,
        key="stage66_confirm",
        disabled=(stage66_gate != "READY" or not all_confirmations),
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

                "stage": 66,
                "confirmation_version": "stage66-v1.0",

                "opportunity_identity": identity,
                "official_deadline": str(deadline or "")[:10] or None,

                "stage65_session_sha256": stage65_session_sha256,

                "run_status": "COMPLETED",
                "session_outcome": "PORTAL_SESSION_ESTABLISHED",

                "current_portal_url": normalize_text(current_portal_url),
                "session_reference": normalize_text(session_reference) or None,

                "portal_login_started": True,
                "portal_session_established": True,
                "upload_started": False,
                "external_submission_performed": False,
                "external_receipt_obtained": False,

                "credentials_collected": False,
                "cookies_collected": False,
                "tokens_collected": False,
                "mfa_codes_collected": False,

                "confirmation_fingerprint": confirmation_fingerprint,
                "session_evidence_sha256": session_evidence_sha256,
                "run_fingerprint": run_fingerprint,

                "confirmation_payload": confirmation_payload,
                "session_evidence": session_evidence,
                "run_payload": run_basis,

                "confirmed_at": now_iso(),
                "completed_at": now_iso(),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                rpc_result = supabase.rpc(
                    "persist_stage66_portal_session",
                    {"p_payload": payload},
                ).execute()

                if not rpc_result.data:
                    (
                        supabase
                        .table("stage66_portal_session_establishment_runs")
                        .insert(payload)
                        .execute()
                    )

            except Exception:
                (
                    supabase
                    .table("stage66_portal_session_establishment_runs")
                    .insert(payload)
                    .execute()
                )

            st.success(
                "Stage 66 persisted — Outcome PORTAL_SESSION_ESTABLISHED."
            )
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 66 persistence failed. Rulează mai întâi SQL-ul Stage 66 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage66 = load_existing_stage66()

if existing_stage66:
    st.divider()
    st.subheader("Stage 66 outcome")

    st.success(
        f"Stage 66 este deja persistată. Run ID: {existing_stage66.get('id')} — "
        f"Outcome: {existing_stage66.get('session_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage66.get("session_outcome"),
    )

    o2.metric(
        "Portal session?",
        "YES" if bool(existing_stage66.get("portal_session_established")) else "NO",
    )

    o3.metric(
        "Upload started?",
        "YES" if bool(existing_stage66.get("upload_started")) else "NO",
    )

    o4.metric(
        "Submitted?",
        "YES" if bool(existing_stage66.get("external_submission_performed")) else "NO",
    )

    st.write(
        f"**Current portal URL:** `{existing_stage66.get('current_portal_url')}`"
    )

    if existing_stage66.get("session_reference"):
        st.write(
            f"**Portal reference:** `{existing_stage66.get('session_reference')}`"
        )

    st.write(
        f"**Confirmation fingerprint:** `{existing_stage66.get('confirmation_fingerprint')}`"
    )

    st.write(
        f"**Session evidence SHA256:** `{existing_stage66.get('session_evidence_sha256')}`"
    )

    if normalize_text(existing_stage66.get("session_outcome")).upper() == "PORTAL_SESSION_ESTABLISHED":
        st.success(
            "Stage 66 PORTAL_SESSION_ESTABLISHED. The authenticated portal session is recorded as "
            "explicit user-confirmed evidence. No credentials were collected, no upload has started, "
            "and no submission has occurred. A future Stage 67 may consume this session."
        )


st.caption(
    "Invariantă Stage 66 v1.0: PORTAL_SESSION_ESTABLISHED is based on explicit human confirmation "
    "of a real authenticated portal session. The app must never collect EU Login passwords, MFA codes, "
    "cookies or session tokens. Stage 66 does not upload or submit."
)
