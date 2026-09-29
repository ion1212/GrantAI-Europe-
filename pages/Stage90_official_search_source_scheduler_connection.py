import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 90 — Search Connection", page_icon="🔌", layout="wide")
st.title("🔌 Etapa 90 — Official Search Source and Scheduler Connection Gate")
st.caption(
    "Testează sursele oficiale în timp real și înregistrează separat dovada unui scheduler real. "
    "O configurație fără dovadă nu este declarată ca automatizare activă."
)

API_DOC_URL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis"
TOPIC_LIST_URL = "https://ec.europa.eu/info/funding-tenders/opportunities/data/topic-list.html"


def secret(name, default=""):
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def get_supabase():
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"))


def norm(value):
    return str(value or "").strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha_json(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def rows(table, filters=None, order="created_at", limit=100):
    query = supabase.table(table).select("*")
    for key, value in (filters or {}).items():
        if value not in (None, ""):
            query = query.eq(key, value)
    if order:
        query = query.order(order, desc=True)
    return query.limit(limit).execute().data or []


def restore_auth_session(sb):
    session = st.session_state.get("auth_session")
    if not session:
        return
    access = session.get("access_token") if isinstance(session, dict) else getattr(session, "access_token", None)
    refresh = session.get("refresh_token") if isinstance(session, dict) else getattr(session, "refresh_token", None)
    if access and refresh:
        try:
            sb.auth.set_session(access, refresh)
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
        if st.session_state.get(key):
            return str(st.session_state[key])
    try:
        user = sb.auth.get_user().user
        return str(user.id) if user and getattr(user, "id", None) else None
    except Exception:
        return None


def project_label(project):
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


def official_eu_url(value):
    try:
        host = (urlparse(norm(value)).hostname or "").lower()
        return norm(value).startswith("https://") and (host == "europa.eu" or host.endswith(".europa.eu"))
    except Exception:
        return False


def live_get(url):
    tested_at = now_iso()
    try:
        response = requests.get(
            url,
            timeout=20,
            allow_redirects=True,
            headers={"User-Agent": "GreenRise-Stage90/1.0 eligibility-monitor"},
        )
        text = response.text or ""
        return {
            "ok": response.status_code == 200 and official_eu_url(response.url),
            "status": int(response.status_code),
            "final_url": response.url,
            "content_type": response.headers.get("content-type", ""),
            "content_length": len(response.content or b""),
            "topic_identifier_count": len(set(re.findall(r"[A-Z][A-Z0-9-]{5,}", text))),
            "tested_at": tested_at,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "final_url": url,
            "content_type": "",
            "content_length": 0,
            "topic_identifier_count": 0,
            "tested_at": tested_at,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 90 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 90 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage90_project")]
project_id = str(project["id"])

stage89_runs = rows(
    "stage89_future_solo_opportunity_recovery_profiles",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage89 = next(
    (run for run in stage89_runs if norm(run.get("recovery_outcome")).upper() == "FUTURE_SOLO_SEARCH_PROFILE_ACTIVATED"),
    None,
)
if not stage89:
    st.error("Stage 90 BLOCKED: no active Stage 89 future solo search profile.")
    st.stop()

stage89_run_id = str(stage89["id"])
stage89_fingerprint = norm(stage89.get("run_fingerprint"))

st.subheader("Stage 89 → Stage 90 connection binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Profile", stage89.get("recovery_outcome") or "—")
c2.metric("Current TRL", stage89.get("current_project_trl"))
c3.metric("Review frequency", stage89.get("review_frequency"))
c4.metric("Existing scheduler", "CONNECTED" if stage89.get("scheduler_connected") else "NOT CONNECTED")

st.markdown("### Official European Commission sources")
st.write(f"**API documentation:** {API_DOC_URL}")
st.write(f"**Official topic list:** {TOPIC_LIST_URL}")
st.link_button("Open official API documentation", API_DOC_URL)

if "stage90_api_test" not in st.session_state:
    st.session_state.stage90_api_test = None
if "stage90_topic_test" not in st.session_state:
    st.session_state.stage90_topic_test = None

if st.button("🌐 Run live official-source connection test", type="primary", key="stage90_test"):
    with st.spinner("Testing official European Commission sources..."):
        st.session_state.stage90_api_test = live_get(API_DOC_URL)
        st.session_state.stage90_topic_test = live_get(TOPIC_LIST_URL)

api_test = st.session_state.stage90_api_test
topic_test = st.session_state.stage90_topic_test
if api_test and topic_test:
    st.dataframe([
        {"Source": "API documentation", **api_test},
        {"Source": "Official topic list", **topic_test},
    ], use_container_width=True, hide_index=True)

source_connected = bool(api_test and topic_test and api_test.get("ok") and topic_test.get("ok") and topic_test.get("content_length", 0) > 1000)
if source_connected:
    st.success("Official sources responded successfully during this live session.")
else:
    st.warning("Run the live test. Stage 90 cannot verify the official connection without successful responses.")

st.markdown("### Scheduler evidence")
scheduler_type = st.selectbox(
    "Scheduler type",
    ["NOT_CONNECTED", "SUPABASE_CRON", "GITHUB_ACTIONS", "EXTERNAL_CRON"],
    key="stage90_scheduler_type",
)
scheduler_reference = st.text_input(
    "Scheduler job/workflow reference",
    placeholder="Real job name, workflow URL or cron identifier",
    key="stage90_scheduler_reference",
)
scheduler_evidence = st.text_area(
    "Scheduler execution evidence",
    placeholder="Paste a real execution timestamp and result/log reference. Do not paste passwords, tokens or secret keys.",
    key="stage90_scheduler_evidence",
)
scheduler_verified = st.checkbox(
    "I personally verified that this scheduler is enabled and has successfully executed the official-source check.",
    key="stage90_scheduler_verified",
)

scheduler_connected = all([
    scheduler_type != "NOT_CONNECTED",
    len(norm(scheduler_reference)) >= 5,
    len(norm(scheduler_evidence)) >= 20,
    scheduler_verified,
])

checks = [
    ("Stage 89 active profile present", norm(stage89.get("recovery_outcome")).upper() == "FUTURE_SOLO_SEARCH_PROFILE_ACTIVATED"),
    ("Stage 89 fingerprint present", len(stage89_fingerprint) == 64),
    ("Official API documentation URL", official_eu_url(API_DOC_URL)),
    ("Official topic-list URL", official_eu_url(TOPIC_LIST_URL)),
    ("Live official-source connection verified", source_connected),
]

with st.expander("Stage 90 connection checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

outcome = (
    "OFFICIAL_SOURCE_AND_SCHEDULER_CONNECTION_VERIFIED"
    if scheduler_connected
    else "OFFICIAL_SOURCE_CONNECTION_VERIFIED_SCHEDULER_PENDING"
)
if source_connected and not scheduler_connected:
    st.info("The official source is connected, but the external scheduler remains pending.")
elif source_connected and scheduler_connected:
    st.success("Both the official source and the evidenced scheduler are ready to be recorded.")

truth_confirmed = st.checkbox("I confirm the connection and scheduler evidence above describes real tests that occurred.", key="stage90_truth")
no_application_action = st.checkbox("I understand Stage 90 does not create or submit any funding application.", key="stage90_no_action")
phrase_target = "CONFIRM STAGE 90 OFFICIAL SEARCH CONNECTION"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage90_phrase")
ready = all(passed for _, passed in checks) and truth_confirmed and no_application_action and norm(phrase) == phrase_target

if st.button("🔌 Persist Stage 90 connection evidence", type="primary", use_container_width=True, disabled=not ready, key="stage90_persist"):
    recorded_at = now_iso()
    connection = {
        "api_documentation_url": API_DOC_URL,
        "topic_list_url": TOPIC_LIST_URL,
        "api_test": api_test,
        "topic_test": topic_test,
        "official_source_connection_verified": source_connected,
        "scheduler_type": scheduler_type,
        "scheduler_reference": norm(scheduler_reference),
        "scheduler_evidence": norm(scheduler_evidence),
        "scheduler_connected": scheduler_connected,
        "automatic_search_running": scheduler_connected,
    }
    evidence_sha = sha_json(connection)
    run_basis = {
        "stage": 90,
        "contract": "stage90-v1.0-official-source-scheduler-connection",
        "stage89_run_id": stage89_run_id,
        "outcome": outcome,
        "connection_evidence_sha256": evidence_sha,
    }
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage89_run_id": stage89_run_id,
        "stage": 90,
        "connection_version": "stage90-v1.0",
        "run_status": "COMPLETED",
        "connection_outcome": outcome,
        "api_documentation_url": API_DOC_URL,
        "topic_list_url": TOPIC_LIST_URL,
        "api_documentation_http_status": int(api_test.get("status") or 0),
        "topic_list_http_status": int(topic_test.get("status") or 0),
        "official_source_connection_verified": source_connected,
        "live_tested_at": topic_test.get("tested_at") or recorded_at,
        "scheduler_type": scheduler_type,
        "scheduler_reference": norm(scheduler_reference) or None,
        "scheduler_evidence": norm(scheduler_evidence) or None,
        "scheduler_connected": scheduler_connected,
        "automatic_search_running": scheduler_connected,
        "search_execution_performed": False,
        "application_created": False,
        "submission_performed": False,
        "connection_payload": connection,
        "stage89_run_fingerprint": stage89_fingerprint,
        "connection_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "run_payload": run_basis,
        "event_history": [{"outcome": outcome, "recorded_at": recorded_at, "evidence_sha256": evidence_sha}],
        "created_at": recorded_at,
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        existing = rows("stage90_official_search_connections", {"user_id": user_id, "stage89_run_id": stage89_run_id}, "created_at", 1)
        if existing:
            payload.pop("created_at", None)
            supabase.table("stage90_official_search_connections").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("stage90_official_search_connections").insert(payload).execute()
        st.success(f"Stage 90 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 90 persistence failed. Run Stage 90 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage90_official_search_connections", {"user_id": user_id, "stage89_run_id": stage89_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 90 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('connection_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Official source", "VERIFIED" if latest.get("official_source_connection_verified") else "FAILED")
    b.metric("API HTTP", latest.get("api_documentation_http_status"))
    c.metric("Topic list HTTP", latest.get("topic_list_http_status"))
    d.metric("Scheduler", "CONNECTED" if latest.get("scheduler_connected") else "PENDING")
    st.write(f"**Live tested at:** {latest.get('live_tested_at')}")
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 90 v1.0: an HTTP source test is not proof of recurring execution. "
    "Scheduler status becomes connected only with a real job reference and execution evidence. "
    "No funding application is created or submitted."
)
