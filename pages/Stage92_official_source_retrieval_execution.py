import hashlib
import json
import os
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 92 — Official Retrieval", page_icon="🌐", layout="wide")
st.title("🌐 Etapa 92 — Official Source Retrieval Execution Gate")
st.caption(
    "Execută o descărcare reală a sursei oficiale și păstrează dovezi tehnice. "
    "Descărcarea nu dovedește încă eligibilitatea niciunei oportunități."
)

JOB_NAME = "greenrise-stage91-weekly-dispatch"
CRON_EXPRESSION = "0 7 * * 2"
EXPECTED_COMMAND_FRAGMENT = "stage92_execute_official_retrieval"


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
    return supabase.rpc(name, params or {}).execute().data


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 92 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 92 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage92_project")]
project_id = str(project["id"])

stage91_runs = rows(
    "stage91_scheduler_configs",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage91 = next(
    (r for r in stage91_runs if norm(r.get("scheduler_outcome")) == "WEEKLY_CRON_CONFIGURED_DISPATCHER_TESTED"),
    None,
)
if not stage91:
    st.error("Stage 92 BLOCKED: no completed Stage 91 weekly scheduler configuration.")
    st.stop()

stage91_run_id = str(stage91["id"])
stage91_fingerprint = norm(stage91.get("run_fingerprint"))
source_url = norm(stage91.get("official_topic_list_url"))

st.subheader("Stage 91 → Stage 92 retrieval binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Stage 91", "COMPLETED")
c2.metric("Cron job", "ACTIVE" if stage91.get("cron_job_active") else "NOT ACTIVE")
c3.metric("Schedule", stage91.get("cron_expression") or "—")
c4.metric("Source", "OFFICIAL EU")
st.write(f"**Official source URL:** {source_url}")

status_error = None
try:
    status_rows = rpc_data("stage92_cron_status") or []
except Exception as exc:
    status_rows = []
    status_error = f"{type(exc).__name__}: {str(exc)[:1200]}"

job = status_rows[0] if isinstance(status_rows, list) and status_rows else {}
job_verified = bool(
    norm(job.get("jobname")) == JOB_NAME
    and norm(job.get("schedule")) == CRON_EXPRESSION
    and EXPECTED_COMMAND_FRAGMENT in norm(job.get("command"))
)
job_active = bool(job_verified and job.get("active") is True)

st.markdown("### Live retrieval scheduler status")
if status_error:
    st.error(f"Stage 92 Cron status unavailable. Run the Stage 92 SQL first. {status_error}")
elif job_active:
    st.success("Weekly Cron is active and now points to the Stage 92 retrieval function.")
    st.dataframe([job], use_container_width=True, hide_index=True)
else:
    st.warning("The Stage 92 retrieval command is not active. Run the Stage 92 SQL and reload.")

existing_rows = rows(
    "stage92_retrieval_configs",
    {"user_id": user_id, "stage91_run_id": stage91_run_id},
    "created_at",
    1,
)
existing = existing_rows[0] if existing_rows else None

executions = []
if existing:
    executions = rows("stage92_retrieval_executions", {"config_id": existing["id"]}, "completed_at", 50)

successful_manual = next(
    (r for r in executions if r.get("trigger_source") == "MANUAL_VERIFICATION" and r.get("execution_status") == "SUCCEEDED"),
    None,
)
successful_cron = next(
    (r for r in executions if r.get("trigger_source") == "CRON" and r.get("execution_status") == "SUCCEEDED"),
    None,
)

checks = [
    ("Stage 91 scheduler completed", True),
    ("Stage 91 fingerprint present", len(stage91_fingerprint) == 64),
    ("Official source URL preserved", source_url.startswith("https://ec.europa.eu/")),
    ("Cron job active", job_active),
    ("Cron command points to Stage 92 retrieval", EXPECTED_COMMAND_FRAGMENT in norm(job.get("command"))),
]

with st.expander("Stage 92 pre-execution checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox(
    "I confirm the URL and scheduler configuration above are the live values returned by the system.",
    key="stage92_truth",
)
retrieval_limit_understood = st.checkbox(
    "I understand successful retrieval proves access to the source, not that any opportunity is eligible for GreenRise.",
    key="stage92_limit",
)
no_application_action = st.checkbox(
    "I understand Stage 92 does not create, edit or submit an application.",
    key="stage92_no_application",
)
phrase_target = "CONFIRM STAGE 92 OFFICIAL SOURCE RETRIEVAL EXECUTION"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage92_phrase")

ready = (
    all(passed for _, passed in checks)
    and truth_confirmed
    and retrieval_limit_understood
    and no_application_action
    and norm(phrase) == phrase_target
)

if st.button(
    "🌐 Activate retrieval and run real source test",
    type="primary",
    use_container_width=True,
    disabled=not ready,
    key="stage92_execute",
):
    recorded_at = now_iso()
    pending_evidence = {
        "stage91_run_id": stage91_run_id,
        "source_url": source_url,
        "cron_job": job,
        "execution_type": "MANUAL_VERIFICATION",
    }
    pending_basis = {
        "stage": 92,
        "contract": "stage92-v1.0-official-source-retrieval",
        "stage91_run_id": stage91_run_id,
        "outcome": "OFFICIAL_RETRIEVAL_CONFIGURED_TEST_PENDING",
        "evidence_sha256": sha_json(pending_evidence),
    }
    pending_payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage91_run_id": stage91_run_id,
        "stage": 92,
        "retrieval_version": "stage92-v1.0",
        "run_status": "TEST_PENDING",
        "retrieval_outcome": "OFFICIAL_RETRIEVAL_CONFIGURED_TEST_PENDING",
        "official_source_url": source_url,
        "cron_job_name": JOB_NAME,
        "cron_expression": CRON_EXPRESSION,
        "cron_job_active": job_active,
        "retrieval_function_tested": False,
        "successful_test_execution_id": None,
        "first_real_cron_retrieval_verified": False,
        "opportunity_eligibility_evaluated": False,
        "application_created": False,
        "submission_performed": False,
        "stage91_run_fingerprint": stage91_fingerprint,
        "retrieval_evidence_sha256": sha_json(pending_evidence),
        "run_fingerprint": sha_json(pending_basis),
        "run_payload": pending_basis,
        "event_history": [{"event": "RETRIEVAL_CONFIGURATION_VERIFIED", "recorded_at": recorded_at}],
        "updated_at": recorded_at,
        "completed_at": None,
    }
    try:
        if existing:
            supabase.table("stage92_retrieval_configs").update(pending_payload).eq("id", existing["id"]).execute()
            config_id = str(existing["id"])
        else:
            pending_payload["created_at"] = recorded_at
            inserted = supabase.table("stage92_retrieval_configs").insert(pending_payload).execute().data or []
            config_id = str(inserted[0]["id"])

        success_count = rpc_data(
            "stage92_execute_official_retrieval",
            {"p_trigger_source": "MANUAL_VERIFICATION"},
        )
        test_rows = rows(
            "stage92_retrieval_executions",
            {"config_id": config_id, "trigger_source": "MANUAL_VERIFICATION"},
            "completed_at",
            1,
        )
        if not test_rows:
            raise RuntimeError(f"Retrieval returned {success_count}, but no execution evidence was visible.")
        test_run = test_rows[0]
        if test_run.get("execution_status") != "SUCCEEDED":
            raise RuntimeError(
                f"Official retrieval failed: HTTP {test_run.get('http_status')} — {test_run.get('error_text') or 'unknown error'}"
            )

        completed_at = now_iso()
        completed_evidence = {
            **pending_evidence,
            "execution_id": test_run["id"],
            "http_status": test_run["http_status"],
            "content_type": test_run.get("content_type"),
            "content_bytes": test_run["content_bytes"],
            "content_sha256": test_run["content_sha256"],
            "horizon_marker_count": test_run.get("horizon_marker_count"),
        }
        completed_basis = {
            "stage": 92,
            "contract": "stage92-v1.0-official-source-retrieval",
            "stage91_run_id": stage91_run_id,
            "outcome": "OFFICIAL_SOURCE_RETRIEVED_MANUALLY_CRON_ARMED",
            "evidence_sha256": sha_json(completed_evidence),
        }
        completed_payload = {
            "run_status": "COMPLETED",
            "retrieval_outcome": "OFFICIAL_SOURCE_RETRIEVED_MANUALLY_CRON_ARMED",
            "retrieval_function_tested": True,
            "successful_test_execution_id": test_run["id"],
            "retrieval_evidence_sha256": sha_json(completed_evidence),
            "run_fingerprint": sha_json(completed_basis),
            "run_payload": completed_basis,
            "event_history": [
                {"event": "RETRIEVAL_CONFIGURATION_VERIFIED", "recorded_at": recorded_at},
                {
                    "event": "MANUAL_OFFICIAL_SOURCE_RETRIEVAL_SUCCEEDED",
                    "recorded_at": completed_at,
                    "execution_id": test_run["id"],
                    "http_status": test_run["http_status"],
                    "content_sha256": test_run["content_sha256"],
                },
            ],
            "updated_at": completed_at,
            "completed_at": completed_at,
        }
        supabase.table("stage92_retrieval_configs").update(completed_payload).eq("id", config_id).execute()
        st.success("Stage 92 completed: official source retrieved successfully and weekly Cron armed.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 92 execution failed. Run the Stage 92 SQL first. {type(exc).__name__}: {str(exc)[:1800]}")

latest_rows = rows(
    "stage92_retrieval_configs",
    {"user_id": user_id, "stage91_run_id": stage91_run_id},
    "created_at",
    1,
)
latest = latest_rows[0] if latest_rows else None
if latest:
    latest_execs = rows("stage92_retrieval_executions", {"config_id": latest["id"]}, "completed_at", 50)
    latest_success = next((r for r in latest_execs if r.get("execution_status") == "SUCCEEDED"), None)
    real_cron_success = next(
        (r for r in latest_execs if r.get("execution_status") == "SUCCEEDED" and r.get("trigger_source") == "CRON"),
        None,
    )

    st.divider()
    st.subheader("Stage 92 outcome")
    if latest.get("run_status") == "COMPLETED":
        st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('retrieval_outcome')}")
    else:
        st.warning(f"Run ID: {latest.get('id')} — Retrieval test remains pending.")
    a, b, c, d = st.columns(4)
    a.metric("Cron retrieval", "ARMED" if latest.get("cron_job_active") else "NOT ACTIVE")
    b.metric("Manual retrieval", "SUCCEEDED" if latest_success else "PENDING")
    c.metric("Real Cron retrieval", "VERIFIED" if real_cron_success else "PENDING")
    d.metric("Eligibility", "NOT EVALUATED")
    if latest_success:
        st.write(f"**Trigger:** `{latest_success.get('trigger_source')}`")
        st.write(f"**HTTP status:** `{latest_success.get('http_status')}`")
        st.write(f"**Content type:** `{latest_success.get('content_type')}`")
        st.write(f"**Content size:** `{latest_success.get('content_bytes')}` bytes")
        st.write(f"**HORIZON markers:** `{latest_success.get('horizon_marker_count')}`")
        st.write(f"**Content SHA-256:** `{latest_success.get('content_sha256')}`")
        st.write(f"**Execution ID:** `{latest_success.get('id')}`")
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 92 v1.0: HTTP 2xx plus non-empty content and SHA-256 proves retrieval only. "
    "It does not prove that a call is active, solo-eligible, suitable for GreenRise or ready for submission."
)
