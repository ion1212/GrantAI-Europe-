import os
import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 78 — Post-Submission Monitoring", page_icon="📡", layout="wide")
st.title("📡 Etapa 78 — AI Post-Submission Monitoring Activation Gate")
st.caption("Activează urmărirea după depunere fără a confunda depunerea cu eligibilitatea sau aprobarea finanțării.")


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


def official_domain_ok(url):
    try:
        parsed = urlparse(norm(url))
        host = (parsed.hostname or "").lower().strip(".")
        return parsed.scheme.lower() == "https" and (host == "europa.eu" or host.endswith(".europa.eu"))
    except Exception:
        return False


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 78 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 78 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage78_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 78 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage77_runs = rows(
    "stage77_official_submission_receipt_verifications",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
allowed_stage77 = {
    "PROVISIONAL_PORTAL_SUBMISSION_EVIDENCE_RECORDED",
    "OFFICIAL_SUBMISSION_RECEIPT_VERIFIED",
}
stage77 = next((run for run in stage77_runs if norm(run.get("verification_outcome")).upper() in allowed_stage77), None)
if not stage77:
    st.error("Stage 78 BLOCKED: no valid completed Stage 77 run.")
    st.stop()

stage77_run_id = str(stage77["id"])
stage77_outcome = norm(stage77.get("verification_outcome")).upper()
application_reference = norm(stage77.get("application_reference"))
final_proposal_id = norm(stage77.get("final_proposal_id"))
portal_url = norm(stage77.get("current_portal_url"))
stage77_fingerprint = norm(stage77.get("run_fingerprint"))
official_receipt = bool(stage77.get("external_receipt_obtained"))

existing_rows = rows("stage78_post_submission_monitoring_runs", {"stage77_run_id": stage77_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 77 → Stage 78 monitoring binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference)
c2.metric("Final ID", final_proposal_id)
c3.metric("Receipt", "OFFICIAL" if official_receipt else "PROVISIONAL")
c4.metric("Monitoring", "ACTIVE" if existing else "PENDING")

if not existing:
    st.info("Pentru această depunere, completează situația exact cum apare în portal. Etapa 78 nu schimbă și nu retrimite propunerea.")

    official_deadline = st.text_input(
        "Official call deadline",
        value="17 September 2026 17:00:00 Brussels Local Time",
        key="stage78_deadline",
    )
    next_review_at = st.text_input(
        "Next portal review date/time",
        value="18 September 2026 17:00:00 Brussels Local Time",
        key="stage78_next_review",
    )
    required_participants = st.number_input("Minimum participants required by the call", min_value=1, max_value=100, value=3, step=1, key="stage78_required_participants")
    current_participants = st.number_input("Participants currently in the proposal", min_value=1, max_value=100, value=1, step=1, key="stage78_current_participants")
    eligibility_warning_visible = st.checkbox("The portal showed an eligibility warning", value=True, key="stage78_warning")

    participant_gap = max(0, int(required_participants) - int(current_participants))
    if participant_gap:
        st.error(
            f"ELIGIBILITY RISK: lipsesc {participant_gap} participant/participanți. "
            "Propunerea este depusă, dar poate fi declarată neeligibilă."
        )
        action_plan = st.selectbox(
            "Human decision before the deadline",
            [
                "ADD_PARTNERS_AND_RESUBMIT",
                "KEEP_SUBMITTED_ACCEPT_ELIGIBILITY_RISK",
                "WITHDRAW_PROPOSAL",
            ],
            key="stage78_action_plan",
        )
    else:
        st.success("Participant minimum currently appears satisfied.")
        action_plan = "MONITOR_PORTAL_AND_RECEIPT"

    monitoring_checks = [
        ("Stage 77 completed", norm(stage77.get("run_status")).upper() == "COMPLETED"),
        ("Accepted Stage 77 outcome", stage77_outcome in allowed_stage77),
        ("Stage 77 fingerprint present", len(stage77_fingerprint) == 64),
        ("Final proposal ID present", len(final_proposal_id) >= 5),
        ("Official portal URL", official_domain_ok(portal_url)),
        ("Official deadline recorded", len(norm(official_deadline)) >= 8),
        ("Next review recorded", len(norm(next_review_at)) >= 8),
    ]
    with st.expander("Monitoring activation checks", expanded=True):
        st.dataframe([{"Check": name, "PASS": passed} for name, passed in monitoring_checks], use_container_width=True, hide_index=True)

    human_control = st.checkbox(
        "I understand the AI may monitor and prepare updates, but adding partners, withdrawing or resubmitting requires my explicit approval.",
        key="stage78_human_control",
    )
    risk_confirmed = st.checkbox(
        "I confirm the participant numbers and eligibility-warning status above match the portal.",
        key="stage78_risk_confirmed",
    )
    phrase_target = "ACTIVATE STAGE 78 POST SUBMISSION MONITORING"
    phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage78_phrase")
    ready = all(passed for _, passed in monitoring_checks) and human_control and risk_confirmed and norm(phrase) == phrase_target

    if st.button("📡 Activate post-submission monitoring", type="primary", use_container_width=True, disabled=not ready, key="stage78_activate"):
        if participant_gap:
            outcome = "MONITORING_ACTIVE_ELIGIBILITY_ACTION_REQUIRED"
        elif official_receipt:
            outcome = "MONITORING_ACTIVE_OFFICIAL_RECEIPT"
        else:
            outcome = "MONITORING_ACTIVE_RECEIPT_PENDING"

        evidence = {
            "monitoring_version": "stage78-v1.0",
            "stage77_run_id": stage77_run_id,
            "stage77_outcome": stage77_outcome,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "portal_url": portal_url,
            "official_deadline_text": norm(official_deadline),
            "next_review_at_text": norm(next_review_at),
            "official_receipt_obtained": official_receipt,
            "eligibility_warning_visible": bool(eligibility_warning_visible),
            "required_participants": int(required_participants),
            "current_participants": int(current_participants),
            "participant_gap": participant_gap,
            "action_plan": action_plan,
            "human_approval_required_for_external_changes": True,
        }
        monitoring_evidence_sha = sha_json(evidence)
        run_basis = {
            "stage": 78,
            "contract": "stage78-v1.0-post-submission-monitoring",
            "stage77_run_id": stage77_run_id,
            "outcome": outcome,
            "monitoring_evidence_sha256": monitoring_evidence_sha,
        }
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage77_run_id": stage77_run_id,
            "stage": 78,
            "monitoring_version": "stage78-v1.0",
            "opportunity_identity": identity,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "current_portal_url": portal_url,
            "run_status": "COMPLETED",
            "monitoring_outcome": outcome,
            "receipt_status": "OFFICIAL" if official_receipt else "PROVISIONAL_PENDING_PDF",
            "eligibility_status": "ACTION_REQUIRED" if participant_gap else "NO_PARTICIPANT_GAP_RECORDED",
            "eligibility_warning_visible": bool(eligibility_warning_visible),
            "required_participants": int(required_participants),
            "current_participants": int(current_participants),
            "participant_gap": participant_gap,
            "action_plan": action_plan,
            "official_deadline_text": norm(official_deadline),
            "next_review_at_text": norm(next_review_at),
            "human_approval_required": True,
            "stage77_run_fingerprint": stage77_fingerprint,
            "monitoring_evidence_sha256": monitoring_evidence_sha,
            "run_fingerprint": sha_json(run_basis),
            "monitoring_payload": evidence,
            "run_payload": run_basis,
            "activated_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            supabase.table("stage78_post_submission_monitoring_runs").insert(payload).execute()
            st.success(f"Stage 78 persisted — {outcome}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Stage 78 persistence failed. Run Stage 78 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

existing_rows = rows("stage78_post_submission_monitoring_runs", {"stage77_run_id": stage77_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
if existing:
    st.divider()
    st.subheader("Stage 78 outcome")
    st.success(f"Run ID: {existing.get('id')} — Outcome: {existing.get('monitoring_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Receipt", existing.get("receipt_status"))
    b.metric("Eligibility", existing.get("eligibility_status"))
    c.metric("Participant gap", existing.get("participant_gap"))
    d.metric("Human approval", "REQUIRED" if existing.get("human_approval_required") else "NO")
    if int(existing.get("participant_gap") or 0) > 0:
        st.error(f"Action before deadline: {existing.get('action_plan')}")
    st.write(f"**Next review:** {existing.get('next_review_at_text')}")
    st.write(f"**Run fingerprint:** `{existing.get('run_fingerprint')}`")

st.caption("Invariant Stage 78 v1.0: monitoring may be automated; partner changes, withdrawal and resubmission remain human-controlled actions.")
