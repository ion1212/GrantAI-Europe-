import hashlib
import json
import os
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 85 — Solo Search Reactivation", page_icon="🔓", layout="wide")
st.title("🔓 Etapa 85 — Controlled Lock Release & Solo Search Reactivation")
st.caption(
    "Eliberează logic oportunitatea arhivată și pregătește o căutare nouă numai pentru apeluri "
    "care permit aplicarea individuală."
)


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


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 85 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 85 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage85_project")]
project_id = str(project["id"])

stage84_runs = rows(
    "stage84_application_closure_records",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage84 = next(
    (
        run
        for run in stage84_runs
        if norm(run.get("closure_outcome")).upper() == "APPLICATION_ARCHIVED_SOLO_SEARCH_AUTHORIZED"
        and norm(run.get("future_application_policy")).upper() == "SOLO_ONLY"
    ),
    None,
)
if not stage84:
    st.error("Stage 85 BLOCKED: no accepted Stage 84 archival and solo-search authorization.")
    st.stop()

stage84_run_id = str(stage84["id"])
stage84_fingerprint = norm(stage84.get("run_fingerprint"))
lock_id = str(stage84.get("opportunity_lock_id"))

lock_rows = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "id": lock_id},
    "created_at",
    1,
)
if not lock_rows:
    st.error("Stage 85 BLOCKED: the archived opportunity lock cannot be found.")
    st.stop()

old_lock = lock_rows[0]
old_lock_status = norm(old_lock.get("lock_status")).upper()
agent_release_status = norm(old_lock.get("agent_release_status")).upper()

existing_rows = rows(
    "stage85_solo_search_reactivation_records",
    {"user_id": user_id, "stage84_run_id": stage84_run_id},
    "created_at",
    1,
)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 84 → Stage 85 reactivation binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Archived application", stage84.get("application_reference") or "—")
c2.metric("Future policy", stage84.get("future_application_policy") or "—")
c3.metric("Original lock", old_lock_status or "—")
c4.metric("Agent release", agent_release_status or "PENDING")

st.info(
    "Blocarea veche este păstrată pentru audit, dar va fi marcată RELEASED_BY_STAGE85. "
    "Etapele noi trebuie să ignore orice blocare cu acest marcaj."
)
st.warning(
    "Etapa 85 nu caută încă pe internet, nu creează o aplicație nouă, nu contactează terți "
    "și nu depune nimic. Ea autorizează numai pornirea controlată a căutării în etapa următoare."
)

search_domains = stage84.get("search_domains") or [
    "Agriculture and rural development",
    "Food and bioeconomy",
    "Renewable energy",
    "Smart buildings",
    "Artificial intelligence and digitalisation",
]
st.write("**Authorized domains:**")
st.write(search_domains)

minimum_days = st.number_input(
    "Minimum days remaining before deadline",
    min_value=7,
    max_value=180,
    value=30,
    step=1,
    key="stage85_minimum_days",
)
funding_regions = st.multiselect(
    "Permitted applicant locations",
    ["Romania", "European Union", "United Kingdom", "International where eligible"],
    default=["Romania", "European Union"],
    key="stage85_regions",
)

confirm_release = st.checkbox(
    "I authorize the old opportunity lock to be logically released for future agent stages.",
    key="stage85_confirm_release",
)
confirm_single_applicant = st.checkbox(
    "Every future result must have official evidence that one applicant is permitted.",
    key="stage85_confirm_single_applicant",
)
confirm_partner_exception = st.checkbox(
    "Partner-required calls must be excluded unless I separately approve an exception.",
    key="stage85_partner_exception",
)
confirm_no_external_action = st.checkbox(
    "I understand this stage performs no portal action, contact, spending or submission.",
    key="stage85_no_external_action",
)
confirm_human_submit = st.checkbox(
    "Final submission and any binding declaration always require my explicit approval.",
    key="stage85_human_submit",
)

checks = [
    ("Stage 84 completed", norm(stage84.get("run_status")).upper() == "COMPLETED"),
    ("Stage 84 fingerprint present", len(stage84_fingerprint) == 64),
    ("Stage 84 archived the old application", norm(stage84.get("closure_outcome")).upper() == "APPLICATION_ARCHIVED_SOLO_SEARCH_AUTHORIZED"),
    ("SOLO_ONLY policy inherited", norm(stage84.get("future_application_policy")).upper() == "SOLO_ONLY"),
    ("Old lock found", bool(lock_id)),
    ("Search domains inherited", len(search_domains) > 0),
    ("Applicant locations selected", len(funding_regions) > 0),
    ("Controlled release approved", bool(confirm_release)),
    ("Official single-applicant evidence required", bool(confirm_single_applicant)),
    ("Partner exception rule preserved", bool(confirm_partner_exception)),
    ("No external action confirmed", bool(confirm_no_external_action)),
    ("Human-controlled submission preserved", bool(confirm_human_submit)),
]

with st.expander("Reactivation checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

phrase_target = "CONFIRM STAGE 85 RELEASE OLD LOCK AND START SOLO SEARCH"
phrase = st.text_input(
    "Confirmation phrase",
    placeholder=f"Type exactly: {phrase_target}",
    key="stage85_phrase",
)
ready = all(passed for _, passed in checks) and norm(phrase) == phrase_target

if st.button(
    "🔓 Persist Stage 85 reactivation",
    type="primary",
    use_container_width=True,
    disabled=not ready,
    key="stage85_persist",
):
    recorded_at = now_iso()
    outcome = "OLD_LOCK_RELEASED_SOLO_SEARCH_READY"
    evidence = {
        "reactivation_version": "stage85-v1.0",
        "stage84_run_id": stage84_run_id,
        "old_opportunity_lock_id": lock_id,
        "old_lock_status_preserved": old_lock_status,
        "agent_release_status": "RELEASED_BY_STAGE85",
        "future_application_policy": "SOLO_ONLY",
        "single_applicant_evidence_required": True,
        "partner_exception_policy": "EXPLICIT_HUMAN_APPROVAL_REQUIRED",
        "minimum_days_before_deadline": int(minimum_days),
        "search_domains": search_domains,
        "funding_regions": funding_regions,
        "external_search_performed": False,
        "external_portal_action_performed": False,
        "application_created": False,
        "submission_performed": False,
    }
    evidence_sha = sha_json(evidence)
    run_basis = {
        "stage": 85,
        "contract": "stage85-v1.0-controlled-lock-release-solo-search-reactivation",
        "stage84_run_id": stage84_run_id,
        "outcome": outcome,
        "reactivation_evidence_sha256": evidence_sha,
    }
    event = {"outcome": outcome, "recorded_at": recorded_at, "evidence_sha256": evidence_sha}
    previous_history = existing.get("event_history") if existing else []
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage84_run_id": stage84_run_id,
        "old_opportunity_lock_id": lock_id,
        "stage": 85,
        "reactivation_version": "stage85-v1.0",
        "run_status": "COMPLETED",
        "reactivation_outcome": outcome,
        "old_lock_status_preserved": old_lock_status,
        "agent_release_status": "RELEASED_BY_STAGE85",
        "future_application_policy": "SOLO_ONLY",
        "single_applicant_evidence_required": True,
        "partner_exception_policy": "EXPLICIT_HUMAN_APPROVAL_REQUIRED",
        "minimum_days_before_deadline": int(minimum_days),
        "search_domains": search_domains,
        "funding_regions": funding_regions,
        "external_search_performed": False,
        "external_portal_action_performed": False,
        "application_created": False,
        "submission_performed": False,
        "stage84_run_fingerprint": stage84_fingerprint,
        "reactivation_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "reactivation_payload": evidence,
        "run_payload": run_basis,
        "event_history": (previous_history or []) + [event],
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        release_result = supabase.table("selected_opportunity_locks").update({
            "agent_release_status": "RELEASED_BY_STAGE85",
            "agent_released_at": recorded_at,
            "agent_release_reason": "APPLICATION_ARCHIVED_RETURN_TO_SOLO_SEARCH",
        }).eq("id", lock_id).eq("user_id", user_id).execute()
        if not (release_result.data or []):
            raise RuntimeError("The old opportunity lock was not updated; verify its RLS update policy.")

        if existing:
            supabase.table("stage85_solo_search_reactivation_records").update(payload).eq("id", existing["id"]).execute()
        else:
            payload["created_at"] = recorded_at
            supabase.table("stage85_solo_search_reactivation_records").insert(payload).execute()
        st.success(f"Stage 85 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 85 persistence failed. Run Stage 85 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows(
    "stage85_solo_search_reactivation_records",
    {"user_id": user_id, "stage84_run_id": stage84_run_id},
    "created_at",
    1,
)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 85 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('reactivation_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Old lock", latest.get("agent_release_status") or "—")
    b.metric("Policy", latest.get("future_application_policy") or "—")
    c.metric("Search executed", "YES" if latest.get("external_search_performed") else "NO")
    d.metric("Ready", "YES" if latest.get("run_status") == "COMPLETED" else "NO")
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 85 v1.0: the old lock is logically released for new agent stages. "
    "No web search, portal action, application creation or submission is claimed as performed."
)
