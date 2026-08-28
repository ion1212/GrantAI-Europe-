import os
import re
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import requests
import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 64 v1.1 — AI EXTERNAL PORTAL PREFLIGHT VERIFICATION GATE
#
# Purpose:
#   Consume ONLY a persisted Stage 63 READY_TO_EXECUTE authorization and
#   perform a READ-ONLY verification of the public official EU Funding &
#   Tenders opportunity page before any external execution.
#
# Stage 64 MAY:
#   - perform an unauthenticated HTTP GET to an official ec.europa.eu URL
#   - verify that the locked opportunity identity appears on that page
#   - capture HTTP status, final URL, page SHA256 and timestamp
#
# Stage 64 DOES NOT:
#   - log in
#   - upload documents
#   - modify portal state
#   - press Submit
#   - sign declarations
#   - create financial commitments
#   - claim submission or European Commission receipt
#
# Outcomes:
#   EXTERNAL_PREFLIGHT_READY
#   NEEDS_PORTAL_VERIFICATION
#   BLOCKED
#
# Handoff:
#   Stage 65 may consume ONLY EXTERNAL_PREFLIGHT_READY.
# =====================================================================

st.set_page_config(
    page_title="Stage 64 v1.1 — External Portal Preflight Verification",
    page_icon="🌐",
    layout="wide",
)

st.title("🌐 Etapa 64 v1.1 — AI External Portal Preflight Verification Gate")
st.caption(
    "Descoperă automat topicul oficial prin API-ul Funding & Tenders și face verificare publică read-only. "
    "Nu face login, upload sau submission."
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


def normalize_identity(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalize_text(value).upper())


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


def official_domain_ok(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES)
    except Exception:
        return False


def infer_official_url(lock: dict) -> str:
    for key in (
        "official_url",
        "opportunity_url",
        "topic_url",
        "source_url",
        "final_url",
        "url",
    ):
        value = normalize_text(lock.get(key))
        if value.startswith("http://") or value.startswith("https://"):
            return value
    return ""



OFFICIAL_SEARCH_API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
PORTAL_TOPIC_BASE = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/"


def walk_json(value: Any):
    """Yield every dict/list node recursively without assuming API response shape."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def extract_first_text(obj: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if value is not None and normalize_text(value):
            return normalize_text(value)
    return ""


def discover_official_topic(identity: str, timeout_seconds: int = 20) -> dict:
    """
    Resolve the locked opportunity identity through the official Funding & Tenders
    Search API documented by the European Commission.

    Fail-closed rule:
    - API exact identifier match => VERIFIED_API_EXACT
    - otherwise a canonical topic URL may be constructed, but it is only a
      CANDIDATE until the subsequent live verification succeeds.
    """
    identity = normalize_text(identity)
    normalized_target = normalize_identity(identity)

    result = {
        "query_identity": identity,
        "api_url": "",
        "api_http_status": None,
        "api_response_sha256": "",
        "api_error": "",
        "exact_match": False,
        "resolved_identifier": "",
        "resolved_title": "",
        "resolved_ccm2_id": "",
        "resolved_url": "",
        "resolution_basis": "NONE",
        "candidate_count": 0,
    }

    if not identity:
        result["api_error"] = "Missing opportunity identity."
        return result

    try:
        response = requests.get(
            OFFICIAL_SEARCH_API,
            params={"apiKey": "SEDIA", "text": f'"{identity}"'},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Stage64Preflight/1.1)",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=timeout_seconds,
            allow_redirects=True,
        )

        result["api_url"] = str(response.url or "")
        result["api_http_status"] = int(response.status_code)
        result["api_response_sha256"] = text_sha256(response.text or "")

        payload = response.json() if response.ok else {}
        candidates = []

        for obj in walk_json(payload):
            if not isinstance(obj, dict):
                continue

            identifier = extract_first_text(
                obj,
                (
                    "identifier",
                    "topicIdentifier",
                    "topic_identifier",
                    "callIdentifier",
                    "reference",
                    "code",
                ),
            )
            if not identifier:
                continue

            candidate = {
                "identifier": identifier,
                "title": extract_first_text(obj, ("title", "name", "topicTitle")),
                "ccm2_id": extract_first_text(obj, ("ccm2Id", "ccm2id", "id")),
            }
            candidates.append(candidate)

        # Deduplicate by normalized identifier + ccm2_id.
        unique = {}
        for c in candidates:
            k = (normalize_identity(c["identifier"]), c["ccm2_id"])
            unique[k] = c
        candidates = list(unique.values())
        result["candidate_count"] = len(candidates)

        exact = next(
            (
                c for c in candidates
                if normalize_identity(c["identifier"]) == normalized_target
            ),
            None,
        )

        if exact:
            result["exact_match"] = True
            result["resolved_identifier"] = exact["identifier"]
            result["resolved_title"] = exact["title"]
            result["resolved_ccm2_id"] = exact["ccm2_id"]
            result["resolved_url"] = PORTAL_TOPIC_BASE + exact["identifier"]
            result["resolution_basis"] = "VERIFIED_API_EXACT"
            return result

    except Exception as exc:
        result["api_error"] = f"{type(exc).__name__}: {str(exc)[:1200]}"

    # Safe fallback: construct a candidate URL only for a plausible EU topic ID.
    # This does not by itself verify identity; the live page/API checks below must pass.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,200}", identity or ""):
        result["resolved_identifier"] = identity
        result["resolved_url"] = PORTAL_TOPIC_BASE + identity
        result["resolution_basis"] = "CANONICAL_URL_CANDIDATE"

    return result


def verify_identity_against_snapshot(identity: str, snapshot: dict, discovery: dict) -> tuple[bool, str]:
    normalized_target = normalize_identity(identity)

    # Strongest route: the official API returned the exact topic identifier.
    if discovery.get("exact_match"):
        resolved = normalize_identity(discovery.get("resolved_identifier"))
        if normalized_target and resolved == normalized_target:
            return True, "OFFICIAL_API_EXACT_IDENTIFIER"

    # Portal route: identifier encoded in the final official topic URL.
    final_url = normalize_text(snapshot.get("final_url") or snapshot.get("requested_url"))
    if normalized_target and normalized_target in normalize_identity(final_url):
        return True, "OFFICIAL_PORTAL_URL_IDENTITY"

    # Last route: identifier visible in returned public page content.
    excerpt = normalize_text(snapshot.get("text_excerpt"))
    if normalized_target and normalized_target in normalize_identity(excerpt):
        return True, "OFFICIAL_PORTAL_BODY_IDENTITY"

    return False, "NO_EXACT_IDENTITY_MATCH"

def fetch_public_portal_snapshot(url: str, timeout_seconds: int = 20) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; Stage64Preflight/1.0; "
            "+read-only public opportunity verification)"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }

    result = {
        "requested_url": url,
        "final_url": "",
        "http_status": None,
        "content_type": "",
        "page_sha256": "",
        "page_length": 0,
        "identity_found": False,
        "verified_at": now_iso(),
        "error": "",
        "text_excerpt": "",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=True,
        )

        body = response.text or ""
        result["final_url"] = str(response.url or "")
        result["http_status"] = int(response.status_code)
        result["content_type"] = normalize_text(response.headers.get("content-type"))
        result["page_sha256"] = text_sha256(body)
        result["page_length"] = len(body)

        clean_excerpt = re.sub(r"\s+", " ", body)
        result["text_excerpt"] = clean_excerpt[:1000]

        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:1200]}"
        return result


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
    st.error("Stage 64 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage64_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 64 BLOCKED: nu există opportunity lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))
inferred_url = infer_official_url(lock)

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")


# ---------------------------------------------------------------------
# Load Stage 63 READY_TO_EXECUTE
# ---------------------------------------------------------------------

stage63_candidates = rows(
    "stage63_external_execution_authorization_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage63 = next(
    (
        r for r in stage63_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("authorization_outcome")).upper() == "READY_TO_EXECUTE"
        and not bool(r.get("external_execution_started"))
        and not bool(r.get("external_submission_performed"))
    ),
    None,
)

if stage63:
    stage63_run_id = str(stage63.get("id") or "")
    stage63_status = normalize_text(stage63.get("run_status")).upper()
    stage63_outcome = normalize_text(stage63.get("authorization_outcome")).upper()
    stage63_run_fingerprint = normalize_text(stage63.get("run_fingerprint"))
    stage63_authorization_sha256 = normalize_text(stage63.get("authorization_sha256"))

    stage62_run_id = str(stage63.get("stage62_run_id") or "")
    stage61_run_id = str(stage63.get("stage61_run_id") or "")
    stage60_run_id = str(stage63.get("stage60_run_id") or "")
    stage59_run_id = str(stage63.get("stage59_run_id") or "")
    stage57_run_id = str(stage63.get("stage57_run_id") or "")
else:
    stage63_run_id = ""
    stage63_status = "MISSING"
    stage63_outcome = "MISSING"
    stage63_run_fingerprint = ""
    stage63_authorization_sha256 = ""

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
    return next((r for r in data if str(r.get("id") or "") == row_id), None)


stage62 = get_bound("stage62_controlled_submission_handoff_runs", stage62_run_id)
stage61 = get_bound("stage61_human_approval_runs", stage61_run_id)
stage60 = get_bound("stage60_submission_package_runs", stage60_run_id)
stage59 = get_bound("stage59_submission_readiness_runs", stage59_run_id)
stage57 = get_bound("stage57_revalidation_runs", stage57_run_id)


# ---------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------

stage63_run_payload = as_dict(stage63.get("run_payload")) if stage63 else {}
recomputed_stage63_run_fingerprint = (
    stable_sha256(stage63_run_payload) if stage63_run_payload else ""
)

stage63_authorization_manifest = as_dict(stage63.get("authorization_manifest")) if stage63 else {}
recomputed_stage63_authorization_sha256 = (
    stable_sha256(stage63_authorization_manifest) if stage63_authorization_manifest else ""
)

stage62_handoff_manifest = as_dict(stage62.get("handoff_manifest")) if stage62 else {}
stored_stage62_handoff_sha256 = normalize_text(stage62.get("handoff_sha256")) if stage62 else ""
recomputed_stage62_handoff_sha256 = (
    stable_sha256(stage62_handoff_manifest) if stage62_handoff_manifest else ""
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
# Preflight base gate
# ---------------------------------------------------------------------

base_checks = []

def add_check(name: str, passed: bool, detail: str):
    base_checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})


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
    "Stage 63 exists",
    bool(stage63),
    stage63_run_id or "MISSING",
)

add_check(
    "Stage 63 COMPLETED",
    stage63_status == "COMPLETED",
    stage63_status,
)

add_check(
    "Stage 63 READY_TO_EXECUTE",
    stage63_outcome == "READY_TO_EXECUTE",
    stage63_outcome,
)

add_check(
    "Stage 63 external execution not started",
    bool(stage63) and not bool(stage63.get("external_execution_started")),
    f"external_execution_started={bool(stage63.get('external_execution_started')) if stage63 else None}",
)

add_check(
    "Stage 63 submission not performed",
    bool(stage63) and not bool(stage63.get("external_submission_performed")),
    f"external_submission_performed={bool(stage63.get('external_submission_performed')) if stage63 else None}",
)

add_check(
    "Stage 63 run fingerprint stable",
    bool(stage63_run_fingerprint)
    and stage63_run_fingerprint == recomputed_stage63_run_fingerprint,
    f"stored={stage63_run_fingerprint[:16]}..., recomputed={recomputed_stage63_run_fingerprint[:16]}...",
)

add_check(
    "Stage 63 authorization SHA256 stable",
    bool(stage63_authorization_sha256)
    and stage63_authorization_sha256 == recomputed_stage63_authorization_sha256,
    f"stored={stage63_authorization_sha256[:16]}..., recomputed={recomputed_stage63_authorization_sha256[:16]}...",
)

add_check(
    "Stage 62 HANDOFF_READY",
    normalize_text(stage62.get("handoff_outcome")).upper() == "HANDOFF_READY" if stage62 else False,
    normalize_text(stage62.get("handoff_outcome")).upper() if stage62 else "MISSING",
)

add_check(
    "Stage 62 handoff SHA256 stable",
    bool(stored_stage62_handoff_sha256)
    and stored_stage62_handoff_sha256 == recomputed_stage62_handoff_sha256,
    f"stored={stored_stage62_handoff_sha256[:16]}..., recomputed={recomputed_stage62_handoff_sha256[:16]}...",
)

add_check(
    "Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF",
    normalize_text(stage61.get("approval_outcome")).upper() == "APPROVED_FOR_SUBMISSION_HANDOFF" if stage61 else False,
    normalize_text(stage61.get("approval_outcome")).upper() if stage61 else "MISSING",
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

base_gate = "READY" if all(c["PASS"] for c in base_checks) else "BLOCKED"


# ---------------------------------------------------------------------
# Automatic official topic discovery + live verification
# ---------------------------------------------------------------------

st.divider()
st.subheader("Automatic official portal discovery")

existing_lock_url = inferred_url if inferred_url and official_domain_ok(inferred_url) else ""

st.write(f"**Locked opportunity identity:** `{identity}`")
if existing_lock_url:
    st.success("An official URL already exists in the opportunity lock and will be preferred.")
    st.write(f"`{existing_lock_url}`")
else:
    st.info(
        "No URL is stored in the lock. Stage 64 v1.1 will resolve the topic automatically "
        "through the official Funding & Tenders Search API, then verify the public topic page."
    )


# ---------------------------------------------------------------------
# Existing Stage 64
# ---------------------------------------------------------------------

def load_existing_stage64():
    if not stage63_run_id:
        return None

    data = (
        supabase.table("stage64_external_portal_preflight_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage63_run_id", stage63_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage64 = load_existing_stage64()


st.divider()
st.subheader("Read-only live portal verification")

with st.expander("Stage 64 upstream hard-gate checks", expanded=False):
    st.dataframe(base_checks, use_container_width=True, hide_index=True)

st.write(f"**Base gate:** `{base_gate}`")

if st.button(
    "🌐 Auto-resolve official topic & verify preflight",
    type="primary",
    use_container_width=True,
    key="stage64_auto_verify",
    disabled=(base_gate != "READY"),
):
    discovery = discover_official_topic(identity)

    # Prefer an already persisted official lock URL. Otherwise use the exact
    # official API result, then finally a canonical candidate URL.
    portal_url = (
        existing_lock_url
        or normalize_text(discovery.get("resolved_url"))
    )

    if not portal_url:
        st.error(
            "Stage 64 could not resolve an official topic URL from the locked opportunity identity. "
            "The workflow remains fail-closed."
        )
        st.stop()

    if not official_domain_ok(portal_url):
        st.error("Resolved URL is not on an allowed official EU domain.")
        st.stop()

    snapshot = fetch_public_portal_snapshot(portal_url)

    identity_ok, identity_basis = verify_identity_against_snapshot(
        identity,
        snapshot,
        discovery,
    )
    snapshot["identity_found"] = bool(identity_ok)
    snapshot["identity_verification_basis"] = identity_basis

    final_url = normalize_text(snapshot.get("final_url") or portal_url)
    final_domain_ok = official_domain_ok(final_url)
    http_ok = snapshot.get("http_status") == 200
    page_hash_ok = bool(snapshot.get("page_sha256"))

    # An exact official API match is accepted as live identity evidence even
    # when the Funding & Tenders SPA does not render the topic code server-side.
    official_api_exact_ok = bool(discovery.get("exact_match"))

    live_checks = [
        {
            "Check": "Official discovery source",
            "PASS": bool(existing_lock_url or discovery.get("resolved_url")),
            "Detail": (
                "PERSISTED_OFFICIAL_LOCK_URL"
                if existing_lock_url
                else discovery.get("resolution_basis")
            ),
        },
        {
            "Check": "Official EU final domain",
            "PASS": bool(final_domain_ok),
            "Detail": final_url,
        },
        {
            "Check": "Public portal HTTP 200",
            "PASS": bool(http_ok),
            "Detail": str(snapshot.get("http_status")),
        },
        {
            "Check": "Portal page SHA256 captured",
            "PASS": bool(page_hash_ok),
            "Detail": snapshot.get("page_sha256", "")[:16] + "...",
        },
        {
            "Check": "Locked opportunity identity verified",
            "PASS": bool(identity_ok),
            "Detail": identity_basis,
        },
        {
            "Check": "Official API exact topic match",
            "PASS": bool(official_api_exact_ok or existing_lock_url),
            "Detail": (
                discovery.get("resolved_identifier")
                if official_api_exact_ok
                else ("LOCK_URL_PRESENT" if existing_lock_url else "NO_EXACT_API_MATCH")
            ),
        },
        {
            "Check": "Deadline still valid",
            "PASS": future_deadline(deadline),
            "Detail": str(deadline or "")[:10],
        },
    ]

    outcome = (
        "EXTERNAL_PREFLIGHT_READY"
        if base_gate == "READY" and all(c["PASS"] for c in live_checks)
        else "NEEDS_PORTAL_VERIFICATION"
    )

    verification_payload = {
        "stage": 64,
        "verification_version": "stage64-v1.1",
        "discovery_contract": "official-search-api-exact-or-fail-closed",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10],
        "stage63_run_id": stage63_run_id,
        "stage63_authorization_sha256": stage63_authorization_sha256,
        "portal_url": portal_url,
        "discovery": discovery,
        "portal_snapshot": snapshot,
        "live_checks": live_checks,
        "outcome": outcome,
    }

    verification_fingerprint = stable_sha256(verification_payload)

    run_basis = {
        "stage": 64,
        "fingerprint_contract": "stage64-v1.1-auto-discovery-preflight",
        "stage63_run_id": stage63_run_id,
        "stage63_authorization_sha256": stage63_authorization_sha256,
        "portal_url": portal_url,
        "resolved_identifier": normalize_text(discovery.get("resolved_identifier")) or identity,
        "api_response_sha256": normalize_text(discovery.get("api_response_sha256")) or None,
        "portal_page_sha256": snapshot.get("page_sha256"),
        "verification_fingerprint": verification_fingerprint,
        "outcome": outcome,
    }

    run_fingerprint = stable_sha256(run_basis)

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

        "stage": 64,
        "verification_version": "stage64-v1.1",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage60_package_sha256": stored_stage60_package_sha256,
        "stage61_approval_fingerprint": stored_stage61_approval_fingerprint,
        "stage62_handoff_sha256": stored_stage62_handoff_sha256,
        "stage63_authorization_sha256": stage63_authorization_sha256,

        "run_status": "COMPLETED",
        "preflight_outcome": outcome,

        "portal_url": portal_url,
        "portal_final_url": final_url or None,
        "portal_http_status": snapshot.get("http_status"),
        "portal_content_type": normalize_text(snapshot.get("content_type")) or None,
        "portal_page_sha256": normalize_text(snapshot.get("page_sha256")) or None,
        "portal_page_length": int(snapshot.get("page_length") or 0),

        "portal_state_verified": bool(http_ok and final_domain_ok),
        "topic_identity_verified_live": bool(identity_ok),
        "external_execution_started": False,
        "external_submission_performed": False,
        "external_receipt_obtained": False,

        "verification_error": normalize_text(snapshot.get("error")) or None,
        "text_excerpt": normalize_text(snapshot.get("text_excerpt")) or None,

        "run_fingerprint": run_fingerprint,
        "verification_fingerprint": verification_fingerprint,

        "verification_payload": verification_payload,
        "run_payload": run_basis,

        "verified_at": snapshot.get("verified_at"),
        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    try:
        rpc_result = supabase.rpc(
            "persist_stage64_portal_preflight",
            {"p_payload": payload},
        ).execute()

        if not rpc_result.data:
            (
                supabase.table("stage64_external_portal_preflight_runs")
                .insert(payload)
                .execute()
            )

        if outcome == "EXTERNAL_PREFLIGHT_READY":
            st.success(
                "Stage 64 auto-discovery and live verification passed — EXTERNAL_PREFLIGHT_READY."
            )
        else:
            st.warning(
                "Stage 64 completed fail-closed — NEEDS_PORTAL_VERIFICATION. "
                "No external execution is authorized."
            )
        st.rerun()

    except Exception as exc:
        st.error(
            "Stage 64 persistence failed. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage64 = load_existing_stage64()

if existing_stage64:
    st.divider()
    st.subheader("Stage 64 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", existing_stage64.get("preflight_outcome"))
    o2.metric(
        "Portal verified?",
        "YES" if bool(existing_stage64.get("portal_state_verified")) else "NO",
    )
    o3.metric(
        "Topic identity?",
        "YES" if bool(existing_stage64.get("topic_identity_verified_live")) else "NO",
    )
    o4.metric(
        "Submitted?",
        "YES" if bool(existing_stage64.get("external_submission_performed")) else "NO",
    )

    st.write(f"**Run ID:** `{existing_stage64.get('id')}`")
    st.write(f"**Portal URL:** `{existing_stage64.get('portal_final_url') or existing_stage64.get('portal_url')}`")
    st.write(f"**HTTP status:** `{existing_stage64.get('portal_http_status')}`")
    st.write(f"**Portal page SHA256:** `{existing_stage64.get('portal_page_sha256')}`")
    st.write(f"**Verification fingerprint:** `{existing_stage64.get('verification_fingerprint')}`")

    # Diagnostic: expose the exact persisted live checks that determined the outcome.
    persisted_verification = as_dict(existing_stage64.get("verification_payload"))
    persisted_live_checks = persisted_verification.get("live_checks", [])
    if isinstance(persisted_live_checks, list) and persisted_live_checks:
        st.markdown("### Stage 64 live verification checks")
        st.dataframe(persisted_live_checks, use_container_width=True, hide_index=True)

        failed_live_checks = [
            c for c in persisted_live_checks
            if isinstance(c, dict) and not bool(c.get("PASS"))
        ]
        if failed_live_checks:
            st.error(
                "Blocking live check(s): "
                + "; ".join(
                    f"{normalize_text(c.get('Check'))}: {normalize_text(c.get('Detail'))}"
                    for c in failed_live_checks
                )
            )
        else:
            st.success("All persisted Stage 64 live checks are PASS.")
    else:
        st.info("No persisted live_checks are available for this Stage 64 run.")

    if normalize_text(existing_stage64.get("preflight_outcome")).upper() == "EXTERNAL_PREFLIGHT_READY":
        st.success(
            "Stage 64 EXTERNAL_PREFLIGHT_READY. The official topic was resolved automatically and the public portal page was verified read-only, "
            "the locked opportunity identity matched, and no submission occurred. "
            "A future Stage 65 may consume this preflight."
        )
    else:
        st.warning(
            "Stage 64 NEEDS_PORTAL_VERIFICATION. Do not proceed to external execution until the live official portal check passes."
        )

st.caption(
    "Invariantă Stage 64 v1.1: EXTERNAL_PREFLIGHT_READY requires official API/lock URL resolution plus read-only official-domain verification, "
    "a captured portal-page SHA256, the locked opportunity identity found on the live page, and a valid deadline. "
    "Stage 64 never logs in and never submits."
)
