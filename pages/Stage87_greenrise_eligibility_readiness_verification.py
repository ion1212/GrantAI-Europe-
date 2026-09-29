import hashlib
import json
import os
from datetime import date, datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 87 — Eligibility Readiness", page_icon="✅", layout="wide")
st.title("✅ Etapa 87 — GreenRise Eligibility and Application Readiness Gate")
st.caption(
    "Verifică eligibilitatea reală a GreenRise pentru oportunitatea solo selectată. "
    "Etapa nu creează, nu modifică și nu depune o aplicație."
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
    st.error("Stage 87 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 87 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage87_project")]
project_id = str(project["id"])

stage86_runs = rows(
    "stage86_official_solo_opportunity_searches",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage86 = next(
    (
        run for run in stage86_runs
        if norm(run.get("search_outcome")).upper() == "VERIFIED_SOLO_OPPORTUNITY_SHORTLIST_RECORDED"
        and norm(run.get("future_application_policy")).upper() == "SOLO_ONLY"
        and int(run.get("qualified_candidate_count") or 0) > 0
    ),
    None,
)
if not stage86:
    st.error("Stage 87 BLOCKED: no accepted Stage 86 verified solo shortlist.")
    st.stop()

stage86_run_id = str(stage86["id"])
stage86_fingerprint = norm(stage86.get("run_fingerprint"))
opportunities = stage86.get("qualified_candidates") or []

opportunity_labels = {
    f"{item.get('title') or 'Opportunity'} — {item.get('topic_id') or item.get('slot')}": item
    for item in opportunities
}
selected_label = st.selectbox("Verified Stage 86 opportunity", list(opportunity_labels.keys()), key="stage87_opportunity")
opportunity = opportunity_labels[selected_label]

try:
    deadline = date.fromisoformat(norm(opportunity.get("deadline")))
    days_remaining = (deadline - date.today()).days
except Exception:
    deadline = None
    days_remaining = -1

st.subheader("Stage 86 → Stage 87 binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Policy", "SOLO_ONLY")
c2.metric("Applicant rule", opportunity.get("applicant_rule") or "—")
c3.metric("Deadline", deadline.isoformat() if deadline else "INVALID")
c4.metric("Days remaining", days_remaining)
st.write(f"**Official source:** {opportunity.get('official_url') or '—'}")

st.warning(
    "Nu marca VERIFIED fără dovadă. O Întreprindere Individuală trebuie să poată fi validată în Portal "
    "ca entitate eligibilă și IMM; simpla existență a unui PIC nu dovedește automat eligibilitatea."
)

st.markdown("### 1. Legal entity and SME evidence")
legal_entity_name = st.text_input("Exact legal entity name", value="II Ciobotaru Viorel Razvan Ionut", key="stage87_legal_name")
country = st.selectbox("Country of establishment", ["Romania", "Other EU Member State", "Associated Country", "Other"], key="stage87_country")
entity_type = st.selectbox(
    "Entity type",
    ["SOLE_TRADER_OR_INDIVIDUAL_ENTERPRISE", "LIMITED_COMPANY", "OTHER_LEGAL_ENTITY", "NOT_CONFIRMED"],
    key="stage87_entity_type",
)
sme_status = st.selectbox("SME status", ["PENDING", "VERIFIED", "NOT_ELIGIBLE"], key="stage87_sme")
sme_evidence = st.text_area(
    "SME status evidence",
    placeholder="Portal SME self-assessment, validation record or equivalent official evidence.",
    key="stage87_sme_evidence",
)
pic = st.text_input("Participant Identification Code (PIC)", key="stage87_pic")
pic_status = st.selectbox("PIC status", ["NOT_AVAILABLE", "PENDING", "DECLARED", "VALIDATED"], key="stage87_pic_status")
pic_evidence = st.text_area("PIC/Participant Register evidence", key="stage87_pic_evidence")

st.markdown("### 2. Technology and scope evidence")
trl = st.slider("Current Technology Readiness Level (TRL)", 1, 9, 1, key="stage87_trl")
trl_evidence = st.text_area(
    "Evidence that TRL 5 is completed",
    placeholder="Prototype tests, validation report, dated results and testing environment.",
    key="stage87_trl_evidence",
)
deep_tech = st.checkbox("The proposed innovation is genuinely deep-tech, not only standard equipment purchase.", key="stage87_deeptech")
deep_tech_evidence = st.text_area("Deep-tech novelty evidence", key="stage87_deeptech_evidence")
scope_alignment = st.checkbox("The project is within the official challenge scope.", key="stage87_scope")
scope_evidence = st.text_area(
    "Exact alignment with the call scope",
    placeholder="Explain the climate-smart agriculture problem, breakthrough technology and scalable impact.",
    key="stage87_scope_evidence",
)

st.markdown("### 3. Ownership and application readiness")
ip_control = st.checkbox("GreenRise controls or can legally use the required intellectual property and results.", key="stage87_ip")
ip_evidence = st.text_area("IP/ownership evidence", key="stage87_ip_evidence")
team_capacity = st.checkbox("The applicant has a credible team and operational capacity for scale-up.", key="stage87_team")
team_evidence = st.text_area("Team and operational-capacity evidence", key="stage87_team_evidence")
personal_verification = st.checkbox("I personally verified every statement and the supporting evidence above.", key="stage87_personal")

country_ok = country in ("Romania", "Other EU Member State", "Associated Country")
entity_ok = entity_type != "NOT_CONFIRMED"
sme_ok = sme_status == "VERIFIED" and len(norm(sme_evidence)) >= 20
pic_ok = pic_status in ("DECLARED", "VALIDATED") and len(norm(pic)) >= 9 and len(norm(pic_evidence)) >= 10
trl_ok = trl >= 5 and len(norm(trl_evidence)) >= 30
deep_tech_ok = deep_tech and len(norm(deep_tech_evidence)) >= 30
scope_ok = scope_alignment and len(norm(scope_evidence)) >= 30
ip_ok = ip_control and len(norm(ip_evidence)) >= 20
team_ok = team_capacity and len(norm(team_evidence)) >= 20
solo_ok = norm(opportunity.get("applicant_rule")).upper() in ("SINGLE_APPLICANT_CONFIRMED", "PARTNERS_OPTIONAL")
deadline_ok = days_remaining > 0

checks = [
    ("Stage 86 completed", norm(stage86.get("run_status")).upper() == "COMPLETED"),
    ("Stage 86 fingerprint present", len(stage86_fingerprint) == 64),
    ("Solo applicant permitted", solo_ok),
    ("Call deadline active", deadline_ok),
    ("Eligible establishment country", country_ok),
    ("Legal entity type identified", entity_ok and len(norm(legal_entity_name)) >= 5),
    ("SME status verified", sme_ok),
    ("PIC declared or validated", pic_ok),
    ("TRL 5 completed with evidence", trl_ok),
    ("Deep-tech nature evidenced", deep_tech_ok),
    ("Official challenge scope matched", scope_ok),
    ("IP/control evidenced", ip_ok),
    ("Team capacity evidenced", team_ok),
    ("Personal verification", personal_verification),
]

gaps = [name for name, passed in checks if not passed]
with st.expander("Stage 87 eligibility checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

if gaps:
    st.error("Eligibility is not yet verified. Missing: " + "; ".join(gaps))
    outcome = "CONDITIONAL_ELIGIBILITY_GAPS_RECORDED"
else:
    st.success("All Stage 87 eligibility checks passed. Application preparation may be authorized.")
    outcome = "ELIGIBILITY_VERIFIED_APPLICATION_PREPARATION_AUTHORIZED"

truth_confirmed = st.checkbox("I confirm that no eligibility evidence above was invented or inferred without proof.", key="stage87_truth")
no_external_action = st.checkbox("I understand Stage 87 does not create, edit or submit an application.", key="stage87_no_action")
phrase_target = "CONFIRM STAGE 87 GREENRISE ELIGIBILITY READINESS"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage87_phrase")
ready = truth_confirmed and no_external_action and norm(phrase) == phrase_target

if st.button("✅ Persist Stage 87 eligibility decision", type="primary", use_container_width=True, disabled=not ready, key="stage87_persist"):
    recorded_at = now_iso()
    evidence = {
        "selected_opportunity": opportunity,
        "legal_entity_name": norm(legal_entity_name),
        "country": country,
        "entity_type": entity_type,
        "sme_status": sme_status,
        "sme_evidence": norm(sme_evidence),
        "pic": norm(pic),
        "pic_status": pic_status,
        "pic_evidence": norm(pic_evidence),
        "trl": trl,
        "trl_evidence": norm(trl_evidence),
        "deep_tech_confirmed": deep_tech_ok,
        "deep_tech_evidence": norm(deep_tech_evidence),
        "scope_alignment_confirmed": scope_ok,
        "scope_evidence": norm(scope_evidence),
        "ip_control_confirmed": ip_ok,
        "ip_evidence": norm(ip_evidence),
        "team_capacity_confirmed": team_ok,
        "team_evidence": norm(team_evidence),
        "checks": [{"name": name, "passed": passed} for name, passed in checks],
        "gaps": gaps,
    }
    evidence_sha = sha_json(evidence)
    run_basis = {
        "stage": 87,
        "contract": "stage87-v1.0-greenrise-eligibility-readiness",
        "stage86_run_id": stage86_run_id,
        "outcome": outcome,
        "eligibility_evidence_sha256": evidence_sha,
    }
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage86_run_id": stage86_run_id,
        "stage": 87,
        "verification_version": "stage87-v1.0",
        "run_status": "COMPLETED",
        "eligibility_outcome": outcome,
        "future_application_policy": "SOLO_ONLY",
        "selected_opportunity": opportunity,
        "legal_entity_name": norm(legal_entity_name),
        "establishment_country": country,
        "entity_type": entity_type,
        "sme_status": sme_status,
        "pic": norm(pic) or None,
        "pic_status": pic_status,
        "technology_readiness_level": trl,
        "deep_tech_confirmed": deep_tech_ok,
        "scope_alignment_confirmed": scope_ok,
        "entity_country_eligible": country_ok,
        "solo_applicant_eligible": solo_ok,
        "evidence_payload": evidence,
        "gap_count": len(gaps),
        "eligibility_gaps": gaps,
        "application_preparation_authorized": len(gaps) == 0,
        "external_portal_action_performed": False,
        "application_created": False,
        "submission_performed": False,
        "stage86_run_fingerprint": stage86_fingerprint,
        "eligibility_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "run_payload": run_basis,
        "event_history": [{"outcome": outcome, "recorded_at": recorded_at, "evidence_sha256": evidence_sha}],
        "created_at": recorded_at,
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        existing = rows("stage87_greenrise_eligibility_readiness_verifications", {"user_id": user_id, "stage86_run_id": stage86_run_id}, "created_at", 1)
        if existing:
            payload.pop("created_at", None)
            supabase.table("stage87_greenrise_eligibility_readiness_verifications").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("stage87_greenrise_eligibility_readiness_verifications").insert(payload).execute()
        st.success(f"Stage 87 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 87 persistence failed. Run Stage 87 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage87_greenrise_eligibility_readiness_verifications", {"user_id": user_id, "stage86_run_id": stage86_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 87 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('eligibility_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Eligibility gaps", latest.get("gap_count"))
    b.metric("Preparation authorized", "YES" if latest.get("application_preparation_authorized") else "NO")
    c.metric("Application created", "YES" if latest.get("application_created") else "NO")
    d.metric("Submission", "YES" if latest.get("submission_performed") else "NO")
    if latest.get("eligibility_gaps"):
        st.write("**Recorded gaps:**", latest.get("eligibility_gaps"))
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 87 v1.0: preparation is authorized only when every eligibility condition is evidenced. "
    "No application creation, portal modification or submission is claimed."
)
