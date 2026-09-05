import os
import json
import hashlib
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 80 — Partner Options Preparation", page_icon="🧩", layout="wide")
st.title("🧩 Etapa 80 — AI Partner Options Preparation Gate")
st.caption("Pregătește profilurile partenerilor lipsă. Nu identifică, nu contactează și nu adaugă automat organizații.")


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
    st.error("Stage 80 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 80 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage80_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 80 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage79_runs = rows(
    "stage79_eligibility_recovery_decisions",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage79 = next(
    (run for run in stage79_runs if norm(run.get("decision_outcome")).upper() == "PARTNER_OPTIONS_PREPARATION_AUTHORIZED"),
    None,
)
if not stage79:
    st.error("Stage 80 BLOCKED: Stage 79 did not authorize preparation of partner options.")
    st.stop()

stage79_run_id = str(stage79["id"])
application_reference = norm(stage79.get("application_reference"))
final_proposal_id = norm(stage79.get("final_proposal_id"))
participant_gap = int(stage79.get("participant_gap") or 0)
required_participants = int(stage79.get("required_participants") or 0)
current_participants = int(stage79.get("current_participants") or 0)
official_deadline = norm(stage79.get("official_deadline_text"))
decision_deadline = norm(stage79.get("decision_deadline_text"))
stage79_fingerprint = norm(stage79.get("run_fingerprint"))

if participant_gap < 1:
    st.error("Stage 80 BLOCKED: no participant gap.")
    st.stop()

existing_rows = rows("stage80_partner_option_profiles", {"stage79_run_id": stage79_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 79 → Stage 80 preparation binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference)
c2.metric("Final ID", final_proposal_id)
c3.metric("Profiles needed", participant_gap)
c4.metric("Preparation", "COMPLETE" if existing else "PENDING")

if not existing:
    st.info(
        "Completează profiluri ideale, nu nume de firme. Etapa 81 va verifica separat orice organizație reală, "
        "inclusiv țara eligibilă, PIC-ul și independența juridică."
    )
    applicant_country = st.text_input("Current applicant country", value="Romania", key="stage80_applicant_country")
    search_deadline = st.text_input("Partner-options preparation deadline", value=decision_deadline, key="stage80_search_deadline")

    profiles = []
    entity_options = ["University / research organisation", "SME / company", "Public authority", "NGO / association", "Other legal entity"]
    default_roles = [
        "Research, validation and scientific evidence",
        "Pilot implementation, demonstration and market replication",
    ]
    default_expertise = [
        "Circular bioeconomy, life-cycle assessment, data collection and impact validation",
        "Industrial or agricultural pilot site, process implementation, exploitation and dissemination",
    ]

    for index in range(participant_gap):
        slot = index + 1
        st.markdown(f"### Partner profile {slot}")
        col1, col2 = st.columns(2)
        with col1:
            target_country = st.text_input(
                "Target country",
                value="Germany" if slot == 1 else ("Netherlands" if slot == 2 else ""),
                key=f"stage80_country_{slot}",
            )
            entity_type = st.selectbox("Preferred entity type", entity_options, index=0 if slot == 1 else 1, key=f"stage80_entity_{slot}")
            role = st.text_area(
                "Expected project role",
                value=default_roles[index] if index < len(default_roles) else "Complementary eligible project role",
                key=f"stage80_role_{slot}",
            )
        with col2:
            expertise = st.text_area(
                "Required expertise and evidence",
                value=default_expertise[index] if index < len(default_expertise) else "Relevant technical capacity and verifiable experience",
                key=f"stage80_expertise_{slot}",
            )
            minimum_evidence = st.text_area(
                "Minimum evidence to verify in Stage 81",
                value="Legal name, official website, eligible-country establishment, PIC/status, relevant projects, contact role and written interest",
                key=f"stage80_evidence_{slot}",
            )

        profiles.append({
            "slot": slot,
            "target_country": norm(target_country),
            "entity_type": norm(entity_type),
            "expected_role": norm(role),
            "required_expertise": norm(expertise),
            "minimum_verification_evidence": norm(minimum_evidence),
            "candidate_organisation": None,
            "contact_performed": False,
            "eligibility_verified": False,
        })

    target_countries = [profile["target_country"].casefold() for profile in profiles if profile["target_country"]]
    applicant_country_norm = norm(applicant_country).casefold()
    countries_complete = len(target_countries) == participant_gap
    countries_distinct = len(set(target_countries)) == participant_gap
    countries_different_from_applicant = all(country != applicant_country_norm for country in target_countries)
    profiles_complete = all(
        len(profile["expected_role"]) >= 10
        and len(profile["required_expertise"]) >= 10
        and len(profile["minimum_verification_evidence"]) >= 10
        for profile in profiles
    )

    checks = [
        ("Stage 79 preparation authorization", norm(stage79.get("decision_outcome")).upper() == "PARTNER_OPTIONS_PREPARATION_AUTHORIZED"),
        ("Stage 79 external action not performed", not bool(stage79.get("external_action_performed"))),
        ("Stage 79 fingerprint present", len(stage79_fingerprint) == 64),
        ("One profile per missing participant", len(profiles) == participant_gap),
        ("Target countries completed", countries_complete),
        ("Target countries are distinct", countries_distinct),
        ("Target countries differ from applicant country", countries_different_from_applicant),
        ("Profile descriptions completed", profiles_complete),
        ("Preparation deadline recorded", len(norm(search_deadline)) >= 8),
    ]
    with st.expander("Partner-profile preparation checks", expanded=True):
        st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

    st.warning("Țările introduse sunt doar ținte de căutare. Etapa 80 nu confirmă că o țară sau organizație este eligibilă.")
    no_real_candidates = st.checkbox("I confirm these are target profiles, not verified partner organisations.", key="stage80_profiles_only")
    no_contact = st.checkbox("I confirm no organisation has been contacted and no portal change has been made.", key="stage80_no_contact")
    solo_policy = st.checkbox("I confirm SOLO_ONLY_BY_DEFAULT remains the policy for all future opportunities.", key="stage80_solo_policy")
    phrase_target = "CONFIRM STAGE 80 PARTNER OPTION PROFILES"
    phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage80_phrase")
    ready = all(passed for _, passed in checks) and no_real_candidates and no_contact and solo_policy and norm(phrase) == phrase_target

    if st.button("🧩 Persist partner option profiles", type="primary", use_container_width=True, disabled=not ready, key="stage80_persist"):
        evidence = {
            "preparation_version": "stage80-v1.0",
            "stage79_run_id": stage79_run_id,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "applicant_country": norm(applicant_country),
            "required_participants": required_participants,
            "current_participants": current_participants,
            "participant_gap": participant_gap,
            "official_deadline_text": official_deadline,
            "preparation_deadline_text": norm(search_deadline),
            "partner_profiles": profiles,
            "country_eligibility_verified": False,
            "candidate_organisations_identified": False,
            "external_contact_performed": False,
            "portal_change_performed": False,
            "future_application_policy": "SOLO_ONLY_BY_DEFAULT",
        }
        profiles_sha = sha_json(profiles)
        evidence_sha = sha_json(evidence)
        outcome = "PARTNER_OPTION_PROFILES_PREPARED_NO_CONTACT"
        run_basis = {
            "stage": 80,
            "contract": "stage80-v1.0-partner-options-preparation",
            "stage79_run_id": stage79_run_id,
            "outcome": outcome,
            "profiles_sha256": profiles_sha,
            "preparation_evidence_sha256": evidence_sha,
        }
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage79_run_id": stage79_run_id,
            "stage": 80,
            "preparation_version": "stage80-v1.0",
            "opportunity_identity": identity,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "run_status": "COMPLETED",
            "preparation_outcome": outcome,
            "applicant_country": norm(applicant_country),
            "required_participants": required_participants,
            "current_participants": current_participants,
            "participant_gap": participant_gap,
            "profile_count": len(profiles),
            "partner_profiles": profiles,
            "profiles_sha256": profiles_sha,
            "country_eligibility_verified": False,
            "candidate_organisations_identified": False,
            "external_contact_performed": False,
            "portal_change_performed": False,
            "official_deadline_text": official_deadline,
            "preparation_deadline_text": norm(search_deadline),
            "future_application_policy": "SOLO_ONLY_BY_DEFAULT",
            "stage79_run_fingerprint": stage79_fingerprint,
            "preparation_evidence_sha256": evidence_sha,
            "run_fingerprint": sha_json(run_basis),
            "preparation_payload": evidence,
            "run_payload": run_basis,
            "prepared_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            supabase.table("stage80_partner_option_profiles").insert(payload).execute()
            st.success(f"Stage 80 persisted — {outcome}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Stage 80 persistence failed. Run Stage 80 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

existing_rows = rows("stage80_partner_option_profiles", {"stage79_run_id": stage79_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
if existing:
    st.divider()
    st.subheader("Stage 80 outcome")
    st.success(f"Run ID: {existing.get('id')} — Outcome: {existing.get('preparation_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Profiles", existing.get("profile_count"))
    b.metric("Candidates", "IDENTIFIED" if existing.get("candidate_organisations_identified") else "NOT IDENTIFIED")
    c.metric("Contact", "PERFORMED" if existing.get("external_contact_performed") else "NOT PERFORMED")
    d.metric("Portal change", "PERFORMED" if existing.get("portal_change_performed") else "NOT PERFORMED")
    st.dataframe(existing.get("partner_profiles") or [], use_container_width=True, hide_index=True)
    st.write(f"**Run fingerprint:** `{existing.get('run_fingerprint')}`")

st.caption("Invariant Stage 80 v1.0: profiles are planning targets only. No candidate, country eligibility, contact or portal change is claimed.")
