import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 89 — Future Solo Recovery", page_icon="🌱", layout="wide")
st.title("🌱 Etapa 89 — Future Solo Opportunity Recovery Profile")
st.caption(
    "Configurează căutarea viitoare pentru apeluri potrivite nivelului actual GreenRise: "
    "aplicant unic, proiect timpuriu și fără cerință de intrare TRL 5."
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


def official_eu_url(value):
    try:
        host = (urlparse(norm(value)).hostname or "").lower()
        return norm(value).startswith("https://") and (host == "europa.eu" or host.endswith(".europa.eu"))
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
    st.error("Stage 89 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 89 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage89_project")]
project_id = str(project["id"])

stage88_runs = rows(
    "stage88_eligibility_gap_resolution_plans",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage88 = next(
    (
        run for run in stage88_runs
        if norm(run.get("plan_outcome")).upper() == "CURRENT_CALL_NOT_FEASIBLE_HOLD_RECORDED"
        and norm(run.get("future_application_policy")).upper() == "SOLO_ONLY"
    ),
    None,
)
if not stage88:
    st.error("Stage 89 BLOCKED: no Stage 88 current-call hold record.")
    st.stop()

stage88_run_id = str(stage88["id"])
stage88_fingerprint = norm(stage88.get("run_fingerprint"))

st.subheader("Stage 88 → Stage 89 recovery binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Previous outcome", stage88.get("plan_outcome") or "—")
c2.metric("Policy", "SOLO_ONLY")
c3.metric("Blocked gaps", stage88.get("blocked_gap_count") or 0)
c4.metric("Current call", "ON HOLD")

st.info(
    "Această etapă salvează profilul de căutare. Nu pornește singură un serviciu extern permanent. "
    "Pentru căutare automată reală va fi necesar un scheduler conectat la API sau o automatizare separată."
)

st.markdown("### Project maturity and applicant profile")
current_trl = st.slider("Current demonstrated TRL", 1, 4, 1, key="stage89_current_trl")
max_entry_trl = st.slider("Maximum acceptable entry-TRL requirement", 1, 4, max(2, current_trl), key="stage89_max_trl")
country = st.selectbox("Applicant country", ["Romania"], key="stage89_country")
entity_type = st.selectbox(
    "Applicant entity type",
    ["SOLE_TRADER_OR_INDIVIDUAL_ENTERPRISE", "SME_IF_OFFICIALLY_VERIFIED"],
    key="stage89_entity",
)
minimum_days = st.number_input("Minimum days before deadline", min_value=30, max_value=365, value=60, step=5, key="stage89_days")

st.markdown("### Target opportunities")
target_domains = st.multiselect(
    "Target domains",
    [
        "Agriculture and rural development",
        "Climate adaptation",
        "Smart farming and irrigation",
        "Protected cultivation and greenhouses",
        "Renewable energy for agriculture",
        "Circular bioeconomy",
        "Early-stage digital innovation",
    ],
    default=[
        "Agriculture and rural development",
        "Climate adaptation",
        "Smart farming and irrigation",
        "Protected cultivation and greenhouses",
    ],
    key="stage89_domains",
)
target_instruments = st.multiselect(
    "Target instrument types",
    [
        "Feasibility and concept validation grants",
        "Prototype development grants",
        "Open innovation and cascade funding",
        "Accelerators and incubators with grants",
        "National or regional SME grants",
        "Horizon Europe calls allowing one applicant",
    ],
    default=[
        "Feasibility and concept validation grants",
        "Prototype development grants",
        "Open innovation and cascade funding",
        "Accelerators and incubators with grants",
        "National or regional SME grants",
    ],
    key="stage89_instruments",
)
excluded_requirements = st.multiselect(
    "Mandatory exclusions",
    [
        "Consortium mandatory",
        "Entry TRL 5 or higher",
        "Validated deep-tech breakthrough already required",
        "Large experienced delivery team required at submission",
        "Deadline below minimum lead time",
        "Applicant country or entity type not eligible",
    ],
    default=[
        "Consortium mandatory",
        "Entry TRL 5 or higher",
        "Validated deep-tech breakthrough already required",
        "Large experienced delivery team required at submission",
        "Deadline below minimum lead time",
        "Applicant country or entity type not eligible",
    ],
    key="stage89_exclusions",
)

keywords_text = st.text_area(
    "Search keywords — one per line",
    value="climate-smart agriculture\nsmart greenhouse\nefficient irrigation\nprotected cultivation\nagricultural renewable energy\nearly-stage agritech\nrural innovation",
    key="stage89_keywords",
)
keywords = [norm(item) for item in keywords_text.splitlines() if norm(item)]

st.markdown("### Official sources and review schedule")
portal_url = st.text_input(
    "EU Funding & Tenders official search URL",
    value="https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-search",
    key="stage89_portal_url",
)
eic_url = st.text_input(
    "European Innovation Council official opportunities URL",
    value="https://eic.ec.europa.eu/eic-funding-opportunities_en",
    key="stage89_eic_url",
)
review_frequency = st.selectbox("Review frequency", ["WEEKLY", "DAILY", "BIWEEKLY"], key="stage89_frequency")
frequency_days = {"DAILY": 1, "WEEKLY": 7, "BIWEEKLY": 14}[review_frequency]
next_review = st.date_input("Next review date", value=date.today() + timedelta(days=frequency_days), key="stage89_next_review")

filters_verified = st.checkbox("I verified that these filters reflect GreenRise's current real maturity and solo-only policy.", key="stage89_filters_verified")
source_verified = st.checkbox("I verified that the source URLs above are official EU sources.", key="stage89_sources_verified")

checks = [
    ("Stage 88 hold outcome present", norm(stage88.get("plan_outcome")).upper() == "CURRENT_CALL_NOT_FEASIBLE_HOLD_RECORDED"),
    ("Stage 88 fingerprint present", len(stage88_fingerprint) == 64),
    ("Current TRL restricted below 5", 1 <= current_trl <= 4),
    ("Entry TRL filter restricted below 5", current_trl <= max_entry_trl <= 4),
    ("Minimum lead time at least 30 days", int(minimum_days) >= 30),
    ("At least one target domain", len(target_domains) > 0),
    ("At least one target instrument", len(target_instruments) > 0),
    ("Consortium and TRL 5 exclusions active", "Consortium mandatory" in excluded_requirements and "Entry TRL 5 or higher" in excluded_requirements),
    ("Search keywords present", len(keywords) >= 3),
    ("Official Funding & Tenders URL", official_eu_url(portal_url)),
    ("Official EIC URL", official_eu_url(eic_url)),
    ("Next review is in the future", next_review > date.today()),
    ("Search filters personally verified", filters_verified),
    ("Official sources personally verified", source_verified),
]

with st.expander("Stage 89 recovery-profile checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox("I understand this records a search profile, not a completed search or eligibility decision.", key="stage89_truth")
no_external_action = st.checkbox("I understand no external scheduler, application creation or submission is activated here.", key="stage89_no_action")
phrase_target = "CONFIRM STAGE 89 FUTURE SOLO SEARCH PROFILE"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage89_phrase")
ready = all(passed for _, passed in checks) and truth_confirmed and no_external_action and norm(phrase) == phrase_target

if st.button("🌱 Activate Stage 89 recovery profile", type="primary", use_container_width=True, disabled=not ready, key="stage89_persist"):
    recorded_at = now_iso()
    outcome = "FUTURE_SOLO_SEARCH_PROFILE_ACTIVATED"
    profile = {
        "current_project_trl": current_trl,
        "maximum_required_entry_trl": max_entry_trl,
        "minimum_days_before_deadline": int(minimum_days),
        "applicant_country": country,
        "entity_type": entity_type,
        "target_domains": target_domains,
        "target_instruments": target_instruments,
        "excluded_requirements": excluded_requirements,
        "official_source_urls": [norm(portal_url), norm(eic_url)],
        "search_keywords": keywords,
        "review_frequency": review_frequency,
        "next_review_date": next_review.isoformat(),
        "scheduler_connected": False,
        "automatic_external_search_running": False,
    }
    profile_sha = sha_json(profile)
    run_basis = {
        "stage": 89,
        "contract": "stage89-v1.0-future-solo-opportunity-recovery",
        "stage88_run_id": stage88_run_id,
        "outcome": outcome,
        "profile_evidence_sha256": profile_sha,
    }
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage88_run_id": stage88_run_id,
        "stage": 89,
        "profile_version": "stage89-v1.0",
        "run_status": "COMPLETED",
        "recovery_outcome": outcome,
        "future_application_policy": "SOLO_ONLY",
        "current_project_trl": current_trl,
        "maximum_required_entry_trl": max_entry_trl,
        "minimum_days_before_deadline": int(minimum_days),
        "applicant_country": country,
        "entity_type": entity_type,
        "target_domains": target_domains,
        "target_instruments": target_instruments,
        "excluded_requirements": excluded_requirements,
        "official_source_urls": [norm(portal_url), norm(eic_url)],
        "search_keywords": keywords,
        "review_frequency": review_frequency,
        "next_review_date": next_review.isoformat(),
        "scheduler_connected": False,
        "automatic_external_search_running": False,
        "application_preparation_authorized": False,
        "submission_authorized": False,
        "external_portal_action_performed": False,
        "stage88_run_fingerprint": stage88_fingerprint,
        "profile_evidence_sha256": profile_sha,
        "run_fingerprint": sha_json(run_basis),
        "profile_payload": profile,
        "run_payload": run_basis,
        "event_history": [{"outcome": outcome, "recorded_at": recorded_at, "profile_sha256": profile_sha}],
        "created_at": recorded_at,
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        existing = rows("stage89_future_solo_opportunity_recovery_profiles", {"user_id": user_id, "stage88_run_id": stage88_run_id}, "created_at", 1)
        if existing:
            payload.pop("created_at", None)
            supabase.table("stage89_future_solo_opportunity_recovery_profiles").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("stage89_future_solo_opportunity_recovery_profiles").insert(payload).execute()
        st.success(f"Stage 89 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 89 persistence failed. Run Stage 89 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage89_future_solo_opportunity_recovery_profiles", {"user_id": user_id, "stage88_run_id": stage88_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 89 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('recovery_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Current TRL", latest.get("current_project_trl"))
    b.metric("Maximum entry TRL", latest.get("maximum_required_entry_trl"))
    c.metric("Next review", latest.get("next_review_date"))
    d.metric("External scheduler", "CONNECTED" if latest.get("scheduler_connected") else "NOT CONNECTED")
    st.write("**Target domains:**", latest.get("target_domains") or [])
    st.write("**Excluded requirements:**", latest.get("excluded_requirements") or [])
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 89 v1.0: the profile targets early-stage solo opportunities and excludes mandatory consortium or TRL 5 entry. "
    "No external monitoring, application creation or submission is claimed."
)
