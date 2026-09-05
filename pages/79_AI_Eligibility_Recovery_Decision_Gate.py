import os
import json
import hashlib
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 79 — Eligibility Recovery Decision", page_icon="🛡️", layout="wide")
st.title("🛡️ Etapa 79 — AI Eligibility Recovery Decision Gate")
st.caption("Înregistrează decizia umană pentru riscul de eligibilitate. Nu modifică și nu retrimite automat propunerea.")


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
    st.error("Stage 79 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 79 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage79_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 79 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage78_runs = rows(
    "stage78_post_submission_monitoring_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage78 = next(
    (
        run for run in stage78_runs
        if norm(run.get("monitoring_outcome")).upper() == "MONITORING_ACTIVE_ELIGIBILITY_ACTION_REQUIRED"
        and int(run.get("participant_gap") or 0) > 0
    ),
    None,
)
if not stage78:
    st.error("Stage 79 BLOCKED: no Stage 78 eligibility action is pending.")
    st.stop()

stage78_run_id = str(stage78["id"])
application_reference = norm(stage78.get("application_reference"))
final_proposal_id = norm(stage78.get("final_proposal_id"))
participant_gap = int(stage78.get("participant_gap") or 0)
required_participants = int(stage78.get("required_participants") or 0)
current_participants = int(stage78.get("current_participants") or 0)
official_deadline = norm(stage78.get("official_deadline_text"))
stage78_fingerprint = norm(stage78.get("run_fingerprint"))

existing_rows = rows("stage79_eligibility_recovery_decisions", {"stage78_run_id": stage78_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 78 → Stage 79 decision binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference)
c2.metric("Final ID", final_proposal_id)
c3.metric("Participant gap", participant_gap)
c4.metric("Decision", "RECORDED" if existing else "PENDING")

if not existing:
    st.error(
        f"Apelul cere minimum {required_participants} participanți, iar propunerea are {current_participants}. "
        f"Lipsesc {participant_gap}. Termen: {official_deadline}."
    )
    st.info(
        "Politica permanentă a agentului: SOLO_ONLY_BY_DEFAULT. Partenerii sunt permiși numai ca excepție, "
        "pentru un proiect identificat și cu aprobarea ta explicită."
    )

    choice_labels = {
        "Pregătește opțiuni de parteneri, fără contact sau modificări": "PREPARE_PARTNER_OPTIONS_ONLY",
        "Autorizează excepțional recuperarea prin parteneri": "AUTHORIZE_EXCEPTIONAL_PARTNER_RECOVERY",
        "Păstrează propunerea depusă și acceptă riscul": "KEEP_SUBMITTED_ACCEPT_RISK",
        "Autorizează retragerea propunerii": "AUTHORIZE_WITHDRAWAL",
    }
    selected_label = st.selectbox("Eligibility recovery decision", list(choice_labels.keys()), key="stage79_decision")
    decision = choice_labels[selected_label]

    decision_deadline = st.text_input(
        "Action/decision deadline",
        value="12 September 2026 17:00:00 Brussels Local Time",
        key="stage79_decision_deadline",
    )
    decision_note = st.text_area(
        "Decision note",
        placeholder="Optional non-sensitive explanation.",
        key="stage79_note",
    )

    if decision == "PREPARE_PARTNER_OPTIONS_ONLY":
        st.info("Agentul poate pregăti o listă și mesaje-draft. Nu poate contacta parteneri și nu poate modifica portalul.")
        outcome = "PARTNER_OPTIONS_PREPARATION_AUTHORIZED"
    elif decision == "AUTHORIZE_EXCEPTIONAL_PARTNER_RECOVERY":
        st.warning("Aceasta este o excepție numai pentru proiectul curent. Executarea și retrimiterea vor necesita o etapă separată de aprobare.")
        outcome = "EXCEPTIONAL_PARTNER_RECOVERY_AUTHORIZED_PENDING_EXECUTION"
    elif decision == "KEEP_SUBMITTED_ACCEPT_RISK":
        st.warning("Propunerea rămâne depusă, dar poate fi respinsă ca neeligibilă fără evaluarea conținutului.")
        outcome = "ELIGIBILITY_RISK_ACCEPTED_NO_CHANGE"
    else:
        st.warning("Retragerea nu este executată aici. Va necesita confirmare separată în portal.")
        outcome = "WITHDRAWAL_AUTHORIZED_PENDING_EXECUTION"

    checks = [
        ("Stage 78 completed", norm(stage78.get("run_status")).upper() == "COMPLETED"),
        ("Eligibility action required", norm(stage78.get("monitoring_outcome")).upper() == "MONITORING_ACTIVE_ELIGIBILITY_ACTION_REQUIRED"),
        ("Participant gap present", participant_gap > 0),
        ("Stage 78 fingerprint present", len(stage78_fingerprint) == 64),
        ("Final proposal ID present", len(final_proposal_id) >= 5),
        ("Decision deadline recorded", len(norm(decision_deadline)) >= 8),
    ]
    with st.expander("Decision checks", expanded=True):
        st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

    facts_confirmed = st.checkbox("I confirm the participant gap and deadline shown above are correct.", key="stage79_facts")
    solo_policy_confirmed = st.checkbox(
        "I confirm future opportunities remain SOLO_ONLY_BY_DEFAULT; partnerships always require a project-specific exception.",
        key="stage79_solo_policy",
    )
    no_execution_confirmed = st.checkbox(
        "I understand this stage records a decision only and does not contact partners, change, withdraw or resubmit the proposal.",
        key="stage79_no_execution",
    )
    phrase_target = "CONFIRM STAGE 79 ELIGIBILITY RECOVERY DECISION"
    phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage79_phrase")
    ready = all(passed for _, passed in checks) and facts_confirmed and solo_policy_confirmed and no_execution_confirmed and norm(phrase) == phrase_target

    if st.button("🛡️ Record eligibility recovery decision", type="primary", use_container_width=True, disabled=not ready, key="stage79_record"):
        evidence = {
            "decision_version": "stage79-v1.0",
            "stage78_run_id": stage78_run_id,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "required_participants": required_participants,
            "current_participants": current_participants,
            "participant_gap": participant_gap,
            "official_deadline_text": official_deadline,
            "decision_deadline_text": norm(decision_deadline),
            "recovery_decision": decision,
            "decision_note": norm(decision_note),
            "future_application_policy": "SOLO_ONLY_BY_DEFAULT",
            "partner_exception_requires_project_specific_human_approval": True,
            "external_action_performed": False,
        }
        decision_evidence_sha = sha_json(evidence)
        run_basis = {
            "stage": 79,
            "contract": "stage79-v1.0-eligibility-recovery-decision",
            "stage78_run_id": stage78_run_id,
            "outcome": outcome,
            "decision_evidence_sha256": decision_evidence_sha,
        }
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage78_run_id": stage78_run_id,
            "stage": 79,
            "decision_version": "stage79-v1.0",
            "opportunity_identity": identity,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "run_status": "COMPLETED",
            "decision_outcome": outcome,
            "recovery_decision": decision,
            "required_participants": required_participants,
            "current_participants": current_participants,
            "participant_gap": participant_gap,
            "official_deadline_text": official_deadline,
            "decision_deadline_text": norm(decision_deadline),
            "decision_note": norm(decision_note) or None,
            "future_application_policy": "SOLO_ONLY_BY_DEFAULT",
            "partner_exception_requires_human_approval": True,
            "external_action_performed": False,
            "stage78_run_fingerprint": stage78_fingerprint,
            "decision_evidence_sha256": decision_evidence_sha,
            "run_fingerprint": sha_json(run_basis),
            "decision_payload": evidence,
            "run_payload": run_basis,
            "decided_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            supabase.table("stage79_eligibility_recovery_decisions").insert(payload).execute()
            st.success(f"Stage 79 persisted — {outcome}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Stage 79 persistence failed. Run Stage 79 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

existing_rows = rows("stage79_eligibility_recovery_decisions", {"stage78_run_id": stage78_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
if existing:
    st.divider()
    st.subheader("Stage 79 outcome")
    st.success(f"Run ID: {existing.get('id')} — Outcome: {existing.get('decision_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Decision", existing.get("recovery_decision"))
    b.metric("Participant gap", existing.get("participant_gap"))
    c.metric("Future policy", existing.get("future_application_policy"))
    d.metric("External action", "PERFORMED" if existing.get("external_action_performed") else "NOT PERFORMED")
    st.write(f"**Decision deadline:** {existing.get('decision_deadline_text')}")
    st.write(f"**Run fingerprint:** `{existing.get('run_fingerprint')}`")

st.caption("Invariant Stage 79 v1.0: the decision is recorded, but no external portal action is executed. Solo-only remains the default policy.")
