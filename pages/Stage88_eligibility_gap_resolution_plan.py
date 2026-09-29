import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 88 — Gap Resolution Plan", page_icon="🧭", layout="wide")
st.title("🧭 Etapa 88 — Eligibility Gap Resolution Plan")
st.caption(
    "Transformă fiecare lipsă reală din etapa 87 într-o acțiune verificabilă. "
    "Planificarea nu dovedește eligibilitatea și nu autorizează depunerea."
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


ACTION_DEFAULTS = {
    "SME status verified": (
        "Complete the official SME self-assessment and retain the resulting Portal evidence.",
        "Official SME self-assessment or validation record showing SME status.",
    ),
    "PIC declared or validated": (
        "Register or locate the legal entity in the Participant Register and record its genuine PIC and status.",
        "Participant Register page or official validation record matching the exact legal entity.",
    ),
    "TRL 5 completed with evidence": (
        "Build and validate the integrated prototype in a relevant agricultural environment; document dated test results.",
        "Prototype specification, test protocol, dated results, photos and independent validation where available.",
    ),
    "Deep-tech nature evidenced": (
        "Document the scientific or engineering breakthrough and compare it with existing commercial solutions.",
        "Technical novelty note, architecture, benchmark and prior-art or competitor comparison.",
    ),
    "Official challenge scope matched": (
        "Map every project objective and result to the official climate-adaptation challenge wording.",
        "Call-scope compliance matrix with exact official passages and project evidence.",
    ),
    "IP/control evidenced": (
        "Identify all software, designs, datasets and inventions and document ownership or lawful licences.",
        "Signed ownership declarations, assignments, licences or relevant registration documents.",
    ),
    "Team capacity evidenced": (
        "Prepare the core-team structure, CVs, responsibilities and evidence of technical and commercial delivery capacity.",
        "Named team table, CVs, role commitments and relevant delivery evidence.",
    ),
}


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 88 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 88 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage88_project")]
project_id = str(project["id"])

stage87_runs = rows(
    "stage87_greenrise_eligibility_readiness_verifications",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage87 = next(
    (
        run for run in stage87_runs
        if norm(run.get("eligibility_outcome")).upper() == "CONDITIONAL_ELIGIBILITY_GAPS_RECORDED"
        and int(run.get("gap_count") or 0) > 0
    ),
    None,
)
if not stage87:
    st.error("Stage 88 BLOCKED: no Stage 87 conditional eligibility gaps record.")
    st.stop()

stage87_run_id = str(stage87["id"])
stage87_fingerprint = norm(stage87.get("run_fingerprint"))
gaps = [norm(item) for item in (stage87.get("eligibility_gaps") or []) if norm(item)]
opportunity = stage87.get("selected_opportunity") or {}
try:
    call_deadline = date.fromisoformat(norm(opportunity.get("deadline")))
except Exception:
    call_deadline = None

st.subheader("Stage 87 → Stage 88 binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Eligibility outcome", stage87.get("eligibility_outcome") or "—")
c2.metric("Source gaps", len(gaps))
c3.metric("Call deadline", call_deadline.isoformat() if call_deadline else "UNKNOWN")
c4.metric("Preparation authorized", "NO")

st.info(
    "Pentru fiecare lipsă, selectează FEASIBLE numai dacă acțiunea și dovada pot fi finalizate realist. "
    "În special, TRL 5 nu poate fi declarat fără un prototip integrat testat într-un mediu relevant."
)

actions = []
action_validations = []
for index, gap in enumerate(gaps, start=1):
    default_action, default_evidence = ACTION_DEFAULTS.get(
        gap,
        ("Define and complete a verifiable corrective action for this gap.", "Official or independently verifiable evidence."),
    )
    st.markdown(f"### Gap {index}: {gap}")
    status = st.selectbox(
        "Resolution feasibility",
        ["NOT_ASSESSED", "FEASIBLE_BEFORE_DEADLINE", "BLOCKED_FOR_CURRENT_CALL"],
        key=f"stage88_status_{index}",
    )
    owner = st.text_input("Responsible person/organisation", value="Razvan Ionut Ciobotaru / GreenRise", key=f"stage88_owner_{index}")
    action = st.text_area("Required action", value=default_action, key=f"stage88_action_{index}")
    evidence = st.text_area("Evidence required to close the gap", value=default_evidence, key=f"stage88_evidence_{index}")
    default_target = min(date.today() + timedelta(days=14), call_deadline) if call_deadline else date.today() + timedelta(days=14)
    target_date = st.date_input("Target completion date", value=default_target, key=f"stage88_target_{index}")
    dependency = st.text_area("Dependencies or blocker note", key=f"stage88_dependency_{index}")

    before_deadline = call_deadline is None or target_date <= call_deadline
    valid = all([
        status != "NOT_ASSESSED",
        len(norm(owner)) >= 5,
        len(norm(action)) >= 25,
        len(norm(evidence)) >= 20,
        before_deadline or status == "BLOCKED_FOR_CURRENT_CALL",
    ])
    action_validations.append((f"Plan complete for: {gap}", valid))
    actions.append({
        "gap": gap,
        "resolution_status": status,
        "owner": norm(owner),
        "required_action": norm(action),
        "required_evidence": norm(evidence),
        "target_date": target_date.isoformat(),
        "target_on_or_before_call_deadline": before_deadline,
        "dependency_or_blocker": norm(dependency),
        "gap_closed": False,
    })

feasible_count = sum(item["resolution_status"] == "FEASIBLE_BEFORE_DEADLINE" for item in actions)
blocked_count = sum(item["resolution_status"] == "BLOCKED_FOR_CURRENT_CALL" for item in actions)
all_planned = bool(actions) and all(passed for _, passed in action_validations)
plan_feasible = all_planned and feasible_count == len(actions)
outcome = (
    "ELIGIBILITY_GAP_RESOLUTION_PLAN_RECORDED"
    if plan_feasible
    else "CURRENT_CALL_NOT_FEASIBLE_HOLD_RECORDED"
)

checks = [
    ("Stage 87 conditional outcome present", norm(stage87.get("eligibility_outcome")).upper() == "CONDITIONAL_ELIGIBILITY_GAPS_RECORDED"),
    ("Stage 87 fingerprint present", len(stage87_fingerprint) == 64),
    ("All Stage 87 gaps preserved", len(actions) == int(stage87.get("gap_count") or 0)),
    ("Every gap has a complete plan", all_planned),
] + action_validations

with st.expander("Stage 88 planning checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

if all_planned:
    if plan_feasible:
        st.success("All gap-resolution actions are recorded as feasible before the current deadline.")
    else:
        st.warning("At least one gap is blocked. The current call must remain on hold unless real evidence closes every gap.")
else:
    st.error("Complete the plan and feasibility decision for every gap.")

truth_confirmed = st.checkbox("I confirm this is a plan only and none of these gaps is claimed as already closed.", key="stage88_truth")
no_authorization = st.checkbox("I understand Stage 88 does not authorize application preparation or submission.", key="stage88_no_auth")
phrase_target = "CONFIRM STAGE 88 ELIGIBILITY GAP RESOLUTION PLAN"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage88_phrase")
ready = all(passed for _, passed in checks) and truth_confirmed and no_authorization and norm(phrase) == phrase_target

if st.button("🧭 Persist Stage 88 resolution plan", type="primary", use_container_width=True, disabled=not ready, key="stage88_persist"):
    recorded_at = now_iso()
    plan_evidence = {
        "source_stage87_run_id": stage87_run_id,
        "source_gaps": gaps,
        "resolution_actions": actions,
        "call_deadline": call_deadline.isoformat() if call_deadline else None,
        "plan_feasible_before_deadline": plan_feasible,
        "eligibility_verified": False,
        "application_preparation_authorized": False,
    }
    evidence_sha = sha_json(plan_evidence)
    run_basis = {
        "stage": 88,
        "contract": "stage88-v1.0-eligibility-gap-resolution-plan",
        "stage87_run_id": stage87_run_id,
        "outcome": outcome,
        "plan_evidence_sha256": evidence_sha,
    }
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage87_run_id": stage87_run_id,
        "stage": 88,
        "plan_version": "stage88-v1.0",
        "run_status": "COMPLETED",
        "plan_outcome": outcome,
        "future_application_policy": "SOLO_ONLY",
        "source_gap_count": len(gaps),
        "planned_gap_count": len(actions),
        "feasible_gap_count": feasible_count,
        "blocked_gap_count": blocked_count,
        "resolution_actions": actions,
        "target_call_deadline": call_deadline.isoformat() if call_deadline else None,
        "plan_feasible_before_deadline": plan_feasible,
        "application_preparation_authorized": False,
        "eligibility_verified": False,
        "external_portal_action_performed": False,
        "submission_performed": False,
        "stage87_run_fingerprint": stage87_fingerprint,
        "plan_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "plan_payload": plan_evidence,
        "run_payload": run_basis,
        "event_history": [{"outcome": outcome, "recorded_at": recorded_at, "evidence_sha256": evidence_sha}],
        "created_at": recorded_at,
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        existing = rows("stage88_eligibility_gap_resolution_plans", {"user_id": user_id, "stage87_run_id": stage87_run_id}, "created_at", 1)
        if existing:
            payload.pop("created_at", None)
            supabase.table("stage88_eligibility_gap_resolution_plans").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("stage88_eligibility_gap_resolution_plans").insert(payload).execute()
        st.success(f"Stage 88 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 88 persistence failed. Run Stage 88 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage88_eligibility_gap_resolution_plans", {"user_id": user_id, "stage87_run_id": stage87_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 88 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('plan_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Gaps planned", latest.get("planned_gap_count"))
    b.metric("Feasible", latest.get("feasible_gap_count"))
    c.metric("Blocked", latest.get("blocked_gap_count"))
    d.metric("Eligibility verified", "YES" if latest.get("eligibility_verified") else "NO")
    st.dataframe(latest.get("resolution_actions") or [], use_container_width=True, hide_index=True)
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 88 v1.0: a resolution plan is not eligibility evidence. "
    "No gap is closed, no application is authorized and no submission is performed by this stage."
)
