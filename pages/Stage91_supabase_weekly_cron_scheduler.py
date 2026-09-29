import hashlib
import json
import os
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 91 — Weekly Cron", page_icon="⏱️", layout="wide")
st.title("⏱️ Etapa 91 — Supabase Weekly Cron Scheduler Gate")
st.caption(
    "Verifică jobul săptămânal din Supabase și testează funcția de dispatch. "
    "Testul manual nu este prezentat drept execuție Cron și nu efectuează încă o căutare externă."
)

JOB_NAME = "greenrise-stage91-weekly-dispatch"
CRON_EXPRESSION = "0 7 * * 2"
SCHEDULE_TEXT = "Every Tuesday at 07:00 UTC"


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


def rpc_data(name, params=None):
    result = supabase.rpc(name, params or {}).execute()
    return result.data


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 91 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 91 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage91_project")]
project_id = str(project["id"])

stage90_runs = rows(
    "stage90_official_search_connections",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage90 = next((r for r in stage90_runs if r.get("official_source_connection_verified") is True), None)
if not stage90:
    st.error("Stage 91 BLOCKED: no completed Stage 90 official-source connection.")
    st.stop()

stage90_run_id = str(stage90["id"])
stage90_fingerprint = norm(stage90.get("run_fingerprint"))
api_url = norm(stage90.get("api_documentation_url"))
topic_url = norm(stage90.get("topic_list_url"))

st.subheader("Stage 90 → Stage 91 scheduler binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Official source", "VERIFIED")
c2.metric("Cron job", JOB_NAME)
c3.metric("Schedule", "Tuesday 07:00")
c4.metric("Timezone", "UTC")

st.info(
    "Acest job săptămânal înregistrează un dispatch intern. Preluarea și analiza efectivă a sursei oficiale "
    "trebuie implementate și probate într-o etapă separată."
)

status_error = None
try:
    status_rows = rpc_data("stage91_scheduler_status") or []
except Exception as exc:
    status_rows = []
    status_error = f"{type(exc).__name__}: {str(exc)[:1000]}"

job = status_rows[0] if isinstance(status_rows, list) and status_rows else {}
job_verified = bool(
    job
    and norm(job.get("jobname")) == JOB_NAME
    and norm(job.get("schedule")) == CRON_EXPRESSION
)
job_active = bool(job_verified and job.get("active") is True)

st.markdown("### Live Supabase Cron status")
if status_error:
    st.error(f"Cron status unavailable. Run the Stage 91 SQL first. {status_error}")
elif job_active:
    st.success("The expected Supabase Cron job exists and is active.")
    st.dataframe([job], use_container_width=True, hide_index=True)
else:
    st.warning("The expected active Cron job was not found. Run the Stage 91 SQL, then reload this page.")

existing_rows = rows(
    "stage91_scheduler_configs",
    {"user_id": user_id, "stage90_run_id": stage90_run_id},
    "created_at",
    1,
)
existing = existing_rows[0] if existing_rows else None

manual_runs = []
if existing:
    manual_runs = rows(
        "stage91_scheduler_dispatch_runs",
        {"config_id": existing["id"], "trigger_source": "MANUAL_VERIFICATION"},
        "triggered_at",
        10,
    )
manual_test_run = manual_runs[0] if manual_runs else None

checks = [
    ("Stage 90 official-source connection completed", True),
    ("Stage 90 fingerprint present", len(stage90_fingerprint) == 64),
    ("Expected Cron job found", job_verified),
    ("Expected Cron job active", job_active),
    ("Schedule equals 0 7 * * 2", norm(job.get("schedule")) == CRON_EXPRESSION),
]

with st.expander("Stage 91 scheduler checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox(
    "I confirm the scheduler status shown above comes from the live Supabase Cron configuration.",
    key="stage91_truth",
)
manual_test_understood = st.checkbox(
    "I understand the immediate dispatcher test is manual and is not evidence that the weekly Cron time has occurred.",
    key="stage91_manual_understood",
)
no_external_claim = st.checkbox(
    "I understand Stage 91 does not yet perform an external search, create an application or submit anything.",
    key="stage91_no_external_claim",
)
phrase_target = "CONFIRM STAGE 91 SUPABASE WEEKLY CRON SCHEDULER"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage91_phrase")

ready = (
    all(passed for _, passed in checks)
    and truth_confirmed
    and manual_test_understood
    and no_external_claim
    and norm(phrase) == phrase_target
)

if st.button(
    "⏱️ Activate configuration and run dispatcher test",
    type="primary",
    use_container_width=True,
    disabled=not ready,
    key="stage91_activate_test",
):
    recorded_at = now_iso()
    pending_evidence = {
        "job": job,
        "schedule_text": SCHEDULE_TEXT,
        "stage90_run_id": stage90_run_id,
        "test_type": "MANUAL_VERIFICATION",
    }
    pending_basis = {
        "stage": 91,
        "contract": "stage91-v1.0-supabase-weekly-cron",
        "stage90_run_id": stage90_run_id,
        "outcome": "WEEKLY_CRON_CONFIGURED_TEST_PENDING",
        "scheduler_evidence_sha256": sha_json(pending_evidence),
    }
    pending_payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage90_run_id": stage90_run_id,
        "stage": 91,
        "scheduler_version": "stage91-v1.0",
        "run_status": "TEST_PENDING",
        "scheduler_outcome": "WEEKLY_CRON_CONFIGURED_TEST_PENDING",
        "cron_job_name": JOB_NAME,
        "cron_expression": CRON_EXPRESSION,
        "schedule_timezone": "UTC",
        "official_api_documentation_url": api_url,
        "official_topic_list_url": topic_url,
        "cron_job_verified": job_verified,
        "cron_job_active": job_active,
        "dispatcher_tested": False,
        "dispatcher_test_run_id": None,
        "first_real_cron_run_verified": False,
        "external_http_performed": False,
        "opportunity_search_performed": False,
        "application_created": False,
        "submission_performed": False,
        "stage90_run_fingerprint": stage90_fingerprint,
        "scheduler_evidence_sha256": sha_json(pending_evidence),
        "run_fingerprint": sha_json(pending_basis),
        "run_payload": pending_basis,
        "event_history": [{"event": "CRON_CONFIGURATION_VERIFIED", "recorded_at": recorded_at}],
        "updated_at": recorded_at,
        "completed_at": None,
    }
    try:
        if existing:
            supabase.table("stage91_scheduler_configs").update(pending_payload).eq("id", existing["id"]).execute()
            config_id = str(existing["id"])
        else:
            pending_payload["created_at"] = recorded_at
            inserted = supabase.table("stage91_scheduler_configs").insert(pending_payload).execute().data or []
            config_id = str(inserted[0]["id"])

        dispatched_count = rpc_data("stage91_dispatch", {"p_trigger_source": "MANUAL_VERIFICATION"})
        test_runs = rows(
            "stage91_scheduler_dispatch_runs",
            {"config_id": config_id, "trigger_source": "MANUAL_VERIFICATION"},
            "triggered_at",
            1,
        )
        if not test_runs:
            raise RuntimeError(f"Dispatcher returned {dispatched_count}, but no manual verification row was visible.")

        test_run = test_runs[0]
        completed_at = now_iso()
        completed_evidence = {
            **pending_evidence,
            "dispatcher_returned_count": dispatched_count,
            "dispatcher_test_run_id": test_run["id"],
            "dispatcher_triggered_at": test_run["triggered_at"],
        }
        completed_basis = {
            "stage": 91,
            "contract": "stage91-v1.0-supabase-weekly-cron",
            "stage90_run_id": stage90_run_id,
            "outcome": "WEEKLY_CRON_CONFIGURED_DISPATCHER_TESTED",
            "scheduler_evidence_sha256": sha_json(completed_evidence),
        }
        completed_payload = {
            "run_status": "COMPLETED",
            "scheduler_outcome": "WEEKLY_CRON_CONFIGURED_DISPATCHER_TESTED",
            "dispatcher_tested": True,
            "dispatcher_test_run_id": test_run["id"],
            "scheduler_evidence_sha256": sha_json(completed_evidence),
            "run_fingerprint": sha_json(completed_basis),
            "run_payload": completed_basis,
            "event_history": [
                {"event": "CRON_CONFIGURATION_VERIFIED", "recorded_at": recorded_at},
                {
                    "event": "MANUAL_DISPATCHER_TEST_RECORDED",
                    "recorded_at": completed_at,
                    "dispatch_run_id": test_run["id"],
                },
            ],
            "updated_at": completed_at,
            "completed_at": completed_at,
        }
        supabase.table("stage91_scheduler_configs").update(completed_payload).eq("id", config_id).execute()
        st.success("Stage 91 completed: weekly Cron configured and dispatcher function tested manually.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 91 persistence/test failed. Run the Stage 91 SQL first. {type(exc).__name__}: {str(exc)[:1800]}")

latest_rows = rows(
    "stage91_scheduler_configs",
    {"user_id": user_id, "stage90_run_id": stage90_run_id},
    "created_at",
    1,
)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 91 outcome")
    if latest.get("run_status") == "COMPLETED":
        st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('scheduler_outcome')}")
    else:
        st.warning(f"Run ID: {latest.get('id')} — Test remains pending.")
    a, b, c, d = st.columns(4)
    a.metric("Cron job", "ACTIVE" if latest.get("cron_job_active") else "NOT ACTIVE")
    b.metric("Dispatcher test", "PASSED" if latest.get("dispatcher_tested") else "PENDING")
    c.metric("Real Cron run", "VERIFIED" if latest.get("first_real_cron_run_verified") else "PENDING")
    d.metric("External search", "NOT PERFORMED")
    st.write(f"**Schedule:** `{latest.get('cron_expression')}` ({latest.get('schedule_timezone')})")
    st.write(f"**Dispatcher test run ID:** `{latest.get('dispatcher_test_run_id')}`")
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 91 v1.0: active Cron configuration plus a manual dispatcher test does not prove a scheduled "
    "Cron run or an external opportunity search. No application is created, modified or submitted."
)
