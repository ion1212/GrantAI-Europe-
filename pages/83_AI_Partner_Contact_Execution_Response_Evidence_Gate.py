import os
import json
import hashlib
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 83 — Partner Contact Evidence", page_icon="📨", layout="wide")
st.title("📨 Etapa 83 — AI Partner Contact Execution & Response Evidence Gate")
st.caption("Autorizează contactarea manuală și înregistrează numai trimiteri sau răspunsuri care s-au produs în realitate.")


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


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


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
    st.error("Stage 83 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 83 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage83_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 83 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage82_runs = rows(
    "stage82_partner_contact_approval_packages",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
allowed_stage82 = {"PARTNER_CONTACT_DRAFTS_APPROVED_NOT_SENT", "MANUAL_PARTNER_CONTACT_AUTHORIZED_NOT_SENT"}
stage82 = next((run for run in stage82_runs if norm(run.get("approval_outcome")).upper() in allowed_stage82), None)
if not stage82:
    st.error("Stage 83 BLOCKED: no accepted Stage 82 approval package.")
    st.stop()

stage82_run_id = str(stage82["id"])
application_reference = norm(stage82.get("application_reference"))
final_proposal_id = norm(stage82.get("final_proposal_id"))
stage82_fingerprint = norm(stage82.get("run_fingerprint"))
approved_messages = stage82.get("messages") or []
candidate_count = int(stage82.get("candidate_count") or len(approved_messages))

existing_rows = rows("stage83_partner_contact_execution_evidence", {"stage82_run_id": stage82_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
current_outcome = norm(existing.get("execution_outcome") if existing else "")

st.subheader("Stage 82 → Stage 83 execution binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference)
c2.metric("Candidates", candidate_count)
c3.metric("Manual contact", "AUTHORIZED" if (existing or stage82.get("manual_contact_authorized")) else "NOT AUTHORIZED")
c4.metric("Sent", "YES" if existing and existing.get("external_contact_performed") else "NO")

if not existing:
    action_options = ["Authorize manual sending (not sent yet)", "Record messages already sent manually"]
elif not existing.get("external_contact_performed"):
    action_options = ["Record messages sent manually"]
else:
    action_options = ["Record or update real partner responses"]

action = st.radio("Current action", action_options, key="stage83_action")
authorize_only = action.startswith("Authorize")
record_sent = "messages" in action.lower() and "sent" in action.lower()
record_responses = "responses" in action.lower()

contact_records = []
base_records = existing.get("contact_records") if existing else None
base_records = base_records or approved_messages

if authorize_only:
    st.info("Această acțiune îți permite să trimiți manual mesajele aprobate. Nu marchează mesajele ca trimise.")
    contact_records = [
        {
            "slot": message.get("slot"),
            "candidate_name": message.get("candidate_name"),
            "contact_route": message.get("contact_route"),
            "subject": message.get("subject"),
            "message_sent": False,
            "sent_at_text": None,
            "send_evidence_file_name": None,
            "send_evidence_sha256": None,
            "response_status": "NOT_SENT",
            "response_received_at_text": None,
            "response_note": None,
            "response_evidence_file_name": None,
            "response_evidence_sha256": None,
        }
        for message in approved_messages
    ]

if record_sent:
    st.warning("Selectează această cale numai după ce ai trimis personal fiecare mesaj printr-o adresă sau pagină oficială.")
    for index, message in enumerate(approved_messages):
        slot = index + 1
        candidate_name = norm(message.get("candidate_name"))
        previous = base_records[index] if index < len(base_records) else message
        st.markdown(f"### Manual send {slot} — {candidate_name}")
        contact_route = st.text_input("Actual recipient/contact route", value=norm(previous.get("contact_route")), key=f"stage83_route_{slot}")
        sent_at = st.text_input("Actual sent date/time", placeholder="Example: 7 September 2026 10:30 UK time", key=f"stage83_sent_at_{slot}")
        evidence_file = st.file_uploader("Optional unedited send evidence (PNG/JPG/PDF)", type=["png", "jpg", "jpeg", "pdf"], key=f"stage83_send_file_{slot}")
        sent_confirmed = st.checkbox("I personally sent this exact approved message.", key=f"stage83_sent_confirmed_{slot}")
        raw = evidence_file.getvalue() if evidence_file else b""
        contact_records.append({
            "slot": message.get("slot") or slot,
            "candidate_name": candidate_name,
            "contact_route": norm(contact_route),
            "subject": norm(message.get("subject")),
            "message_sent": bool(sent_confirmed),
            "sent_at_text": norm(sent_at) or None,
            "send_evidence_file_name": evidence_file.name if evidence_file else None,
            "send_evidence_sha256": sha_bytes(raw) if raw else None,
            "response_status": "AWAITING_RESPONSE" if sent_confirmed else "NOT_SENT",
            "response_received_at_text": None,
            "response_note": None,
            "response_evidence_file_name": None,
            "response_evidence_sha256": None,
        })

if record_responses:
    st.info("Nu selecta INTERESTED sau DECLINED fără un răspuns real primit de la organizație.")
    for index, previous in enumerate(base_records):
        slot = index + 1
        candidate_name = norm(previous.get("candidate_name"))
        st.markdown(f"### Response {slot} — {candidate_name}")
        status = st.selectbox(
            "Current response status",
            ["AWAITING_RESPONSE", "INTERESTED_NON_BINDING", "NEEDS_MORE_INFORMATION", "DECLINED"],
            index=0,
            key=f"stage83_response_status_{slot}",
        )
        received_at = st.text_input("Response received date/time", key=f"stage83_response_at_{slot}")
        response_note = st.text_area("Accurate response summary", key=f"stage83_response_note_{slot}")
        response_file = st.file_uploader("Optional unedited response evidence (PNG/JPG/PDF)", type=["png", "jpg", "jpeg", "pdf"], key=f"stage83_response_file_{slot}")
        raw = response_file.getvalue() if response_file else b""
        response_received = status != "AWAITING_RESPONSE"
        contact_records.append({
            **previous,
            "response_status": status,
            "response_received_at_text": norm(received_at) if response_received else None,
            "response_note": norm(response_note) if response_received else None,
            "response_evidence_file_name": response_file.name if response_file else None,
            "response_evidence_sha256": sha_bytes(raw) if raw else None,
        })

all_messages_sent = bool(contact_records) and all(bool(record.get("message_sent")) for record in contact_records)
response_count = sum(1 for record in contact_records if record.get("response_status") not in (None, "NOT_SENT", "AWAITING_RESPONSE"))
positive_response_count = sum(1 for record in contact_records if record.get("response_status") in ("INTERESTED_NON_BINDING", "NEEDS_MORE_INFORMATION"))

if authorize_only:
    outcome = "MANUAL_CONTACT_AUTHORIZED_NOT_SENT"
elif record_sent:
    outcome = "MANUAL_CONTACT_RECORDED_AWAITING_RESPONSES"
else:
    outcome = "PARTNER_RESPONSES_RECORDED"

checks = [
    ("Stage 82 completed", norm(stage82.get("run_status")).upper() == "COMPLETED"),
    ("Stage 82 fingerprint present", len(stage82_fingerprint) == 64),
    ("Approved message count preserved", len(contact_records) == candidate_count),
]
if record_sent:
    checks.extend([
        ("All messages personally confirmed sent", all_messages_sent),
        ("All actual contact routes recorded", all(len(norm(record.get("contact_route"))) >= 5 for record in contact_records)),
        ("All sent timestamps recorded", all(len(norm(record.get("sent_at_text"))) >= 8 for record in contact_records)),
    ])
if record_responses:
    checks.extend([
        ("Previous sending evidence present", bool(existing and existing.get("external_contact_performed"))),
        ("At least one real response recorded", response_count > 0),
        ("Response timestamp and summary present", all(
            record.get("response_status") == "AWAITING_RESPONSE"
            or (len(norm(record.get("response_received_at_text"))) >= 8 and len(norm(record.get("response_note"))) >= 10)
            for record in contact_records
        )),
    ])

with st.expander("Execution/response checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox("I confirm every status, timestamp and evidence above describes an action or response that actually occurred.", key="stage83_truth")
manual_control = st.checkbox("I understand the AI did not send messages, read an inbox or contact any organisation.", key="stage83_manual_control")
no_commitment = st.checkbox("I understand interest is non-binding until formal eligibility and written consortium agreement are verified.", key="stage83_no_commitment")
phrase_target = "CONFIRM STAGE 83 PARTNER CONTACT EVIDENCE"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage83_phrase")
ready = all(passed for _, passed in checks) and truth_confirmed and manual_control and no_commitment and norm(phrase) == phrase_target

if st.button("📨 Persist Stage 83 evidence", type="primary", use_container_width=True, disabled=not ready, key="stage83_persist"):
    external_contact_performed = all_messages_sent
    evidence = {
        "execution_version": "stage83-v1.0",
        "stage82_run_id": stage82_run_id,
        "application_reference": application_reference,
        "final_proposal_id": final_proposal_id,
        "contact_records": contact_records,
        "manual_contact_authorized": True,
        "external_contact_performed": external_contact_performed,
        "all_messages_sent": all_messages_sent,
        "response_count": response_count,
        "positive_response_count": positive_response_count,
        "portal_change_performed": False,
    }
    evidence_sha = sha_json(evidence)
    records_sha = sha_json(contact_records)
    run_basis = {
        "stage": 83,
        "contract": "stage83-v1.0-partner-contact-execution-evidence",
        "stage82_run_id": stage82_run_id,
        "outcome": outcome,
        "contact_records_sha256": records_sha,
        "execution_evidence_sha256": evidence_sha,
    }
    event = {"outcome": outcome, "recorded_at": now_iso(), "evidence_sha256": evidence_sha}
    previous_history = existing.get("event_history") if existing else []
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage82_run_id": stage82_run_id,
        "stage": 83,
        "execution_version": "stage83-v1.0",
        "opportunity_identity": identity,
        "application_reference": application_reference,
        "final_proposal_id": final_proposal_id,
        "run_status": "COMPLETED",
        "execution_outcome": outcome,
        "manual_contact_authorized": True,
        "external_contact_performed": external_contact_performed,
        "all_messages_sent": all_messages_sent,
        "candidate_count": candidate_count,
        "response_count": response_count,
        "positive_response_count": positive_response_count,
        "contact_records": contact_records,
        "contact_records_sha256": records_sha,
        "portal_change_performed": False,
        "stage82_run_fingerprint": stage82_fingerprint,
        "execution_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "execution_payload": evidence,
        "run_payload": run_basis,
        "event_history": (previous_history or []) + [event],
        "authorized_at": existing.get("authorized_at") if existing else now_iso(),
        "sent_at": now_iso() if external_contact_performed else None,
        "updated_at": now_iso(),
        "completed_at": now_iso(),
    }
    try:
        if existing:
            supabase.table("stage83_partner_contact_execution_evidence").update(payload).eq("id", existing["id"]).execute()
        else:
            payload["created_at"] = now_iso()
            supabase.table("stage83_partner_contact_execution_evidence").insert(payload).execute()
        st.success(f"Stage 83 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 83 persistence failed. Run Stage 83 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage83_partner_contact_execution_evidence", {"stage82_run_id": stage82_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 83 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('execution_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Authorized", "YES" if latest.get("manual_contact_authorized") else "NO")
    b.metric("All sent", "YES" if latest.get("all_messages_sent") else "NO")
    c.metric("Responses", latest.get("response_count"))
    d.metric("Positive/possible", latest.get("positive_response_count"))
    st.dataframe(latest.get("contact_records") or [], use_container_width=True, hide_index=True)
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption("Invariant Stage 83 v1.0: authorization, sending and responses are distinct states. No external action is inferred or invented.")
