import hashlib
import json
import os
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 84 — Application Closure", page_icon="🗃️", layout="wide")
st.title("🗃️ Etapa 84 — AI Application Closure & Solo Search Return Gate")
st.caption(
    "Închide intern aplicația fără răspunsuri și autorizează revenirea la căutarea "
    "apelurilor eligibile pentru aplicare individuală."
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
    st.error("Stage 84 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 84 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage84_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)
if not locks:
    st.error("Stage 84 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
opportunity_identity = norm(lock.get("opportunity_identity"))

stage83_runs = rows(
    "stage83_partner_contact_execution_evidence",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "run_status": "COMPLETED",
    },
    "created_at",
    100,
)
stage83 = next(
    (run for run in stage83_runs if norm(run.get("execution_outcome")).upper() == "NO_RESPONSE_DEADLINE_PASSED"),
    None,
)
if not stage83:
    st.error("Stage 84 BLOCKED: Stage 83 must end with NO_RESPONSE_DEADLINE_PASSED.")
    st.stop()

stage83_run_id = str(stage83["id"])
stage83_fingerprint = norm(stage83.get("run_fingerprint"))
application_reference = norm(stage83.get("application_reference"))
final_proposal_id = norm(stage83.get("final_proposal_id"))
deadline_text = norm(stage83.get("no_response_deadline_text"))
response_count = int(stage83.get("response_count") or 0)

existing_rows = rows(
    "stage84_application_closure_records",
    {"user_id": user_id, "stage83_run_id": stage83_run_id},
    "created_at",
    1,
)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 83 → Stage 84 closure binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference or "—")
c2.metric("Final ID", final_proposal_id or "—")
c3.metric("Responses", response_count)
c4.metric("Stage 83", "NO RESPONSE")

st.warning(
    "Această etapă arhivează aplicația numai în agent. Nu retrage propunerea din portalul UE, "
    "nu schimbă participanții și nu execută nicio acțiune externă."
)

closure_reason = st.text_area(
    "Closure reason",
    value=(
        "The application deadline passed without partner responses. The minimum participant "
        "requirement was not recovered before the deadline."
    ),
    key="stage84_closure_reason",
)

search_domains = st.multiselect(
    "Domains allowed for the next solo-opportunity search",
    [
        "Agriculture and rural development",
        "Food and bioeconomy",
        "Renewable energy",
        "Smart buildings",
        "Artificial intelligence and digitalisation",
        "Circular economy",
    ],
    default=[
        "Agriculture and rural development",
        "Food and bioeconomy",
        "Renewable energy",
        "Smart buildings",
        "Artificial intelligence and digitalisation",
    ],
    key="stage84_search_domains",
)

solo_policy = st.checkbox(
    "Use SOLO_ONLY as the default policy for future opportunities.",
    value=True,
    key="stage84_solo_policy",
)
exceptional_partners = st.checkbox(
    "Allow partner-based opportunities only after my separate explicit approval.",
    value=True,
    key="stage84_exceptional_partners",
)
archive_confirmed = st.checkbox(
    "I approve internal archival of this application as eligibility not recovered before the deadline.",
    key="stage84_archive_confirmed",
)
portal_unchanged = st.checkbox(
    "I confirm Stage 84 must not withdraw or modify the proposal in the official portal.",
    key="stage84_portal_unchanged",
)
search_authorized = st.checkbox(
    "I authorize the next stage to search for new opportunities that permit a single applicant.",
    key="stage84_search_authorized",
)

checks = [
    ("Stage 83 completed", norm(stage83.get("run_status")).upper() == "COMPLETED"),
    ("Stage 83 no-response outcome", norm(stage83.get("execution_outcome")).upper() == "NO_RESPONSE_DEADLINE_PASSED"),
    ("Stage 83 fingerprint present", len(stage83_fingerprint) == 64),
    ("No partner response recorded", response_count == 0),
    ("Passed deadline recorded", len(deadline_text) >= 8),
    ("Closure reason present", len(norm(closure_reason)) >= 20),
    ("At least one solo-search domain selected", len(search_domains) > 0),
    ("SOLO_ONLY policy confirmed", bool(solo_policy)),
    ("Exceptional partner approval rule confirmed", bool(exceptional_partners)),
    ("Internal archival approved", bool(archive_confirmed)),
    ("Official portal remains unchanged", bool(portal_unchanged)),
    ("Next solo search authorized", bool(search_authorized)),
]

with st.expander("Closure checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

phrase_target = "CONFIRM STAGE 84 ARCHIVE AND RETURN TO SOLO SEARCH"
phrase = st.text_input(
    "Confirmation phrase",
    placeholder=f"Type exactly: {phrase_target}",
    key="stage84_phrase",
)
ready = all(passed for _, passed in checks) and norm(phrase) == phrase_target

if st.button(
    "🗃️ Persist Stage 84 closure",
    type="primary",
    use_container_width=True,
    disabled=not ready,
    key="stage84_persist",
):
    closure_outcome = "APPLICATION_ARCHIVED_SOLO_SEARCH_AUTHORIZED"
    evidence = {
        "closure_version": "stage84-v1.0",
        "stage83_run_id": stage83_run_id,
        "stage83_outcome": norm(stage83.get("execution_outcome")),
        "application_reference": application_reference,
        "final_proposal_id": final_proposal_id,
        "deadline_text": deadline_text,
        "response_count": response_count,
        "closure_reason": norm(closure_reason),
        "eligibility_disposition": "INELIGIBLE_PARTICIPANT_REQUIREMENT_NOT_RECOVERED",
        "future_application_policy": "SOLO_ONLY",
        "partners_exception_policy": "EXPLICIT_HUMAN_APPROVAL_REQUIRED",
        "next_action": "RETURN_TO_SOLO_OPPORTUNITY_SEARCH",
        "search_domains": search_domains,
        "external_portal_action_performed": False,
        "proposal_withdrawn": False,
        "opportunity_lock_release_authorized": True,
        "opportunity_lock_release_performed": False,
    }
    evidence_sha = sha_json(evidence)
    run_basis = {
        "stage": 84,
        "contract": "stage84-v1.0-application-closure-solo-search-return",
        "stage83_run_id": stage83_run_id,
        "outcome": closure_outcome,
        "closure_evidence_sha256": evidence_sha,
    }
    event = {"outcome": closure_outcome, "recorded_at": now_iso(), "evidence_sha256": evidence_sha}
    previous_history = existing.get("event_history") if existing else []
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage83_run_id": stage83_run_id,
        "stage": 84,
        "closure_version": "stage84-v1.0",
        "opportunity_identity": opportunity_identity,
        "application_reference": application_reference,
        "final_proposal_id": final_proposal_id,
        "stage83_outcome": norm(stage83.get("execution_outcome")),
        "stage83_run_fingerprint": stage83_fingerprint,
        "run_status": "COMPLETED",
        "closure_outcome": closure_outcome,
        "eligibility_disposition": "INELIGIBLE_PARTICIPANT_REQUIREMENT_NOT_RECOVERED",
        "future_application_policy": "SOLO_ONLY",
        "partners_exception_policy": "EXPLICIT_HUMAN_APPROVAL_REQUIRED",
        "next_action": "RETURN_TO_SOLO_OPPORTUNITY_SEARCH",
        "closure_reason": norm(closure_reason),
        "search_domains": search_domains,
        "external_portal_action_performed": False,
        "proposal_withdrawn": False,
        "opportunity_lock_release_authorized": True,
        "opportunity_lock_release_performed": False,
        "closure_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "closure_payload": evidence,
        "run_payload": run_basis,
        "event_history": (previous_history or []) + [event],
        "updated_at": now_iso(),
        "completed_at": now_iso(),
    }
    try:
        if existing:
            supabase.table("stage84_application_closure_records").update(payload).eq("id", existing["id"]).execute()
        else:
            payload["created_at"] = now_iso()
            supabase.table("stage84_application_closure_records").insert(payload).execute()
        st.success(f"Stage 84 persisted — {closure_outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 84 persistence failed. Run Stage 84 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage84_application_closure_records", {"user_id": user_id, "stage83_run_id": stage83_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 84 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('closure_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Archived", "YES" if latest.get("run_status") == "COMPLETED" else "NO")
    b.metric("Future policy", latest.get("future_application_policy") or "—")
    c.metric("Portal action", "YES" if latest.get("external_portal_action_performed") else "NO")
    d.metric("Next action", "SOLO SEARCH")
    st.write(f"**Eligibility disposition:** `{latest.get('eligibility_disposition')}`")
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 84 v1.0: archival is internal. No withdrawal, portal modification, "
    "new search, or opportunity-lock release is claimed as already performed."
)
