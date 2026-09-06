import os
import json
import hashlib
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 82 — Partner Contact Approval", page_icon="✉️", layout="wide")
st.title("✉️ Etapa 82 — AI Human Approval for Partner Contact Gate")
st.caption("Pregătește mesajele și înregistrează aprobarea. Nu trimite emailuri și nu contactează automat organizații.")


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
    st.error("Stage 82 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 82 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage82_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 82 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage81_runs = rows(
    "stage81_candidate_partner_verifications",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
allowed_outcomes = {
    "CANDIDATE_DUE_DILIGENCE_PREPARED_PIC_PENDING",
    "CANDIDATES_PRELIMINARILY_VERIFIED_NO_CONTACT",
}
stage81 = next((run for run in stage81_runs if norm(run.get("verification_outcome")).upper() in allowed_outcomes), None)
if not stage81:
    st.error("Stage 82 BLOCKED: no accepted Stage 81 candidate package.")
    st.stop()

stage81_run_id = str(stage81["id"])
application_reference = norm(stage81.get("application_reference"))
final_proposal_id = norm(stage81.get("final_proposal_id"))
participant_gap = int(stage81.get("participant_gap") or 0)
candidates = stage81.get("candidates") or []
all_pic_verified = bool(stage81.get("all_pic_verified"))
stage81_fingerprint = norm(stage81.get("run_fingerprint"))

existing_rows = rows("stage82_partner_contact_approval_packages", {"stage81_run_id": stage81_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 81 → Stage 82 contact-control binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference)
c2.metric("Candidates", len(candidates))
c3.metric("PIC verified", "YES" if all_pic_verified else "PENDING")
c4.metric("Approval", "RECORDED" if existing else "PENDING")

if not existing:
    if not all_pic_verified:
        st.warning("PIC-ul este încă neconfirmat. Poți aproba mesajele pentru verificare manuală, dar nu considera organizațiile eligibile încă.")

    sender_name = st.text_input("Sender name", value="Razvan Ionut Ciobotaru", key="stage82_sender_name")
    sender_organisation = st.text_input("Sender organisation", value=norm(project.get("name")) or "Project coordinator applicant", key="stage82_sender_org")
    reply_deadline = st.text_input("Requested reply deadline", value="10 September 2026 17:00:00 Brussels Local Time", key="stage82_reply_deadline")

    approval_labels = {
        "Aprobă doar mesajele pentru verificarea mea": "APPROVE_DRAFTS_ONLY",
        "Autorizează trimiterea manuală de către mine": "AUTHORIZE_MANUAL_SEND",
    }
    selected_label = st.selectbox("Contact approval level", list(approval_labels.keys()), key="stage82_approval_level")
    approval_level = approval_labels[selected_label]

    messages = []
    for index, candidate in enumerate(candidates):
        slot = index + 1
        candidate_name = norm(candidate.get("candidate_name"))
        subject_default = f"Urgent Horizon Europe partnership enquiry — {identity or application_reference}"
        body_default = (
            f"Dear {candidate_name} team,\n\n"
            f"My name is {norm(sender_name)} and I represent {norm(sender_organisation)}. We are preparing an urgent consortium update for "
            f"Horizon Europe opportunity {identity or application_reference}. Our submitted proposal has Final ID {final_proposal_id}.\n\n"
            f"We identified your organisation as a potential fit for the following role: {norm(candidate.get('target_role'))}. "
            f"Before sharing documents or making any portal change, we would like to confirm whether you are open to an urgent discussion, "
            f"whether your legal entity has a valid Participant Identification Code (PIC), and whether you could assess participation before the call deadline.\n\n"
            f"Please reply by {norm(reply_deadline)}. This message is an initial non-binding enquiry and does not create any commitment.\n\n"
            f"Kind regards,\n{norm(sender_name)}\n{norm(sender_organisation)}"
        )
        st.markdown(f"### Message {slot} — {candidate_name}")
        contact_route = st.text_input(
            "Official contact route (URL or verified email; no message is sent here)",
            value=norm(candidate.get("official_url")),
            key=f"stage82_contact_route_{slot}",
        )
        subject = st.text_input("Subject", value=subject_default, key=f"stage82_subject_{slot}")
        body = st.text_area("Message draft", value=body_default, height=300, key=f"stage82_body_{slot}")
        messages.append({
            "slot": slot,
            "candidate_name": candidate_name,
            "country": norm(candidate.get("country")),
            "contact_route": norm(contact_route),
            "subject": norm(subject),
            "body": norm(body),
            "pic_status": norm(candidate.get("pic_status")),
            "message_sent": False,
            "response_received": False,
        })

    checks = [
        ("Stage 81 completed", norm(stage81.get("run_status")).upper() == "COMPLETED"),
        ("Accepted Stage 81 outcome", norm(stage81.get("verification_outcome")).upper() in allowed_outcomes),
        ("Stage 81 fingerprint present", len(stage81_fingerprint) == 64),
        ("One message per candidate", len(messages) == len(candidates) == participant_gap),
        ("Sender details present", len(norm(sender_name)) >= 3 and len(norm(sender_organisation)) >= 3),
        ("Reply deadline present", len(norm(reply_deadline)) >= 8),
        ("Contact routes present", all(len(message["contact_route"]) >= 5 for message in messages)),
        ("Subjects and bodies complete", all(len(message["subject"]) >= 10 and len(message["body"]) >= 100 for message in messages)),
        ("No message marked sent", all(not message["message_sent"] for message in messages)),
    ]
    with st.expander("Contact-package checks", expanded=True):
        st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

    content_confirmed = st.checkbox("I reviewed the candidate names, contact routes and complete message text.", key="stage82_content")
    manual_only = st.checkbox("I understand this application will not send anything; any sending must be performed manually by me.", key="stage82_manual")
    nonbinding = st.checkbox("I confirm the messages are non-binding enquiries and do not promise funding, budget or consortium membership.", key="stage82_nonbinding")
    phrase_target = "APPROVE STAGE 82 PARTNER CONTACT PACKAGE"
    phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage82_phrase")
    ready = all(passed for _, passed in checks) and content_confirmed and manual_only and nonbinding and norm(phrase) == phrase_target

    if st.button("✉️ Record human approval", type="primary", use_container_width=True, disabled=not ready, key="stage82_persist"):
        contact_authorized = approval_level == "AUTHORIZE_MANUAL_SEND"
        outcome = "MANUAL_PARTNER_CONTACT_AUTHORIZED_NOT_SENT" if contact_authorized else "PARTNER_CONTACT_DRAFTS_APPROVED_NOT_SENT"
        evidence = {
            "approval_version": "stage82-v1.0",
            "stage81_run_id": stage81_run_id,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "sender_name": norm(sender_name),
            "sender_organisation": norm(sender_organisation),
            "reply_deadline_text": norm(reply_deadline),
            "approval_level": approval_level,
            "manual_contact_authorized": contact_authorized,
            "messages": messages,
            "external_contact_performed": False,
            "portal_change_performed": False,
        }
        messages_sha = sha_json(messages)
        evidence_sha = sha_json(evidence)
        run_basis = {
            "stage": 82,
            "contract": "stage82-v1.0-human-partner-contact-approval",
            "stage81_run_id": stage81_run_id,
            "outcome": outcome,
            "messages_sha256": messages_sha,
            "approval_evidence_sha256": evidence_sha,
        }
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage81_run_id": stage81_run_id,
            "stage": 82,
            "approval_version": "stage82-v1.0",
            "opportunity_identity": identity,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "run_status": "COMPLETED",
            "approval_outcome": outcome,
            "approval_level": approval_level,
            "manual_contact_authorized": contact_authorized,
            "sender_name": norm(sender_name),
            "sender_organisation": norm(sender_organisation),
            "reply_deadline_text": norm(reply_deadline),
            "candidate_count": len(candidates),
            "messages": messages,
            "messages_sha256": messages_sha,
            "external_contact_performed": False,
            "portal_change_performed": False,
            "stage81_run_fingerprint": stage81_fingerprint,
            "approval_evidence_sha256": evidence_sha,
            "run_fingerprint": sha_json(run_basis),
            "approval_payload": evidence,
            "run_payload": run_basis,
            "approved_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            supabase.table("stage82_partner_contact_approval_packages").insert(payload).execute()
            st.success(f"Stage 82 persisted — {outcome}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Stage 82 persistence failed. Run Stage 82 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

existing_rows = rows("stage82_partner_contact_approval_packages", {"stage81_run_id": stage81_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
if existing:
    st.divider()
    st.subheader("Stage 82 outcome")
    st.success(f"Run ID: {existing.get('id')} — Outcome: {existing.get('approval_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Messages", existing.get("candidate_count"))
    b.metric("Manual contact", "AUTHORIZED" if existing.get("manual_contact_authorized") else "NOT AUTHORIZED")
    c.metric("Sent", "YES" if existing.get("external_contact_performed") else "NO")
    d.metric("Portal change", "YES" if existing.get("portal_change_performed") else "NO")
    for message in existing.get("messages") or []:
        with st.expander(f"Approved draft — {message.get('candidate_name')}"):
            st.write(f"**Contact route:** {message.get('contact_route')}")
            st.write(f"**Subject:** {message.get('subject')}")
            st.code(message.get("body") or "", language=None)
    st.write(f"**Run fingerprint:** `{existing.get('run_fingerprint')}`")

st.caption("Invariant Stage 82 v1.0: approval and message preparation are recorded, but external contact and portal modification remain false.")
