import os
import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 81 — Candidate Partner Verification", page_icon="🔎", layout="wide")
st.title("🔎 Etapa 81 — AI Candidate Partner Identification & Preliminary Verification Gate")
st.caption("Identifică organizații reale și construiește dosarul de verificare. Nu contactează candidații și nu confirmă eligibilitatea fără dovezi oficiale.")


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


def valid_https_url(value):
    try:
        parsed = urlparse(norm(value))
        return parsed.scheme.lower() == "https" and bool(parsed.hostname)
    except Exception:
        return False


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
    st.error("Stage 81 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 81 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage81_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 81 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage80_runs = rows(
    "stage80_partner_option_profiles",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage80 = next(
    (run for run in stage80_runs if norm(run.get("preparation_outcome")).upper() == "PARTNER_OPTION_PROFILES_PREPARED_NO_CONTACT"),
    None,
)
if not stage80:
    st.error("Stage 81 BLOCKED: no completed Stage 80 partner profiles.")
    st.stop()

stage80_run_id = str(stage80["id"])
application_reference = norm(stage80.get("application_reference"))
final_proposal_id = norm(stage80.get("final_proposal_id"))
participant_gap = int(stage80.get("participant_gap") or 0)
applicant_country = norm(stage80.get("applicant_country"))
stage80_fingerprint = norm(stage80.get("run_fingerprint"))
profiles = stage80.get("partner_profiles") or []

existing_rows = rows("stage81_candidate_partner_verifications", {"stage80_run_id": stage80_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 80 → Stage 81 candidate binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Application", application_reference)
c2.metric("Final ID", final_proposal_id)
c3.metric("Candidates required", participant_gap)
c4.metric("Stage 81", "COMPLETE" if existing else "PENDING")

if not existing:
    st.warning("PRELIMINARY ONLY: potrivirea tehnică nu înseamnă eligibilitate. PIC-ul, statutul juridic și acceptul organizației nu sunt încă confirmate.")

    defaults = [
        {
            "name": "Fraunhofer Institute UMSICHT",
            "legal_name": "Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V.",
            "country": "Germany",
            "url": "https://www.umsicht.fraunhofer.de/en.html",
            "evidence_url": "https://www.umsicht.fraunhofer.de/en.html",
            "fit": "Official institute site describes circular economy research, life-cycle assessment, bio-based materials and pilot-scale process development.",
        },
        {
            "name": "ChainCraft",
            "legal_name": "ChainCraft B.V. — exact registered name must be verified",
            "country": "Netherlands",
            "url": "https://chaincraft.com/our-company/",
            "evidence_url": "https://chaincraft.com/our-company/",
            "fit": "Official company site describes conversion of organic waste streams into circular chemicals and scale-up from laboratory to pilot and demonstration factory.",
        },
    ]
    candidates = []
    for index in range(participant_gap):
        slot = index + 1
        profile = profiles[index] if index < len(profiles) else {}
        default = defaults[index] if index < len(defaults) else {"name": "", "legal_name": "", "country": norm(profile.get("target_country")), "url": "", "evidence_url": "", "fit": ""}
        st.markdown(f"### Candidate {slot} — {norm(profile.get('expected_role')) or 'Partner role'}")
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Candidate organisation", value=default["name"], key=f"stage81_name_{slot}")
            legal_name = st.text_input("Legal entity name", value=default["legal_name"], key=f"stage81_legal_{slot}")
            country = st.text_input("Country of establishment", value=default["country"], key=f"stage81_country_{slot}")
            official_url = st.text_input("Official organisation URL", value=default["url"], key=f"stage81_url_{slot}")
        with col2:
            fit_evidence = st.text_area("Technical-fit evidence", value=default["fit"], key=f"stage81_fit_{slot}")
            evidence_url = st.text_input("Evidence source URL", value=default["evidence_url"], key=f"stage81_evidence_url_{slot}")
            pic_status = st.selectbox(
                "Participant Register / PIC status",
                ["NOT_CHECKED", "PIC_FOUND_IN_PORTAL", "PIC_OR_REGISTRATION_REQUIRED"],
                key=f"stage81_pic_status_{slot}",
            )
            pic = st.text_input("PIC (leave empty until verified in official portal)", value="", key=f"stage81_pic_{slot}")

        candidates.append({
            "slot": slot,
            "candidate_name": norm(name),
            "legal_entity_name": norm(legal_name),
            "country": norm(country),
            "official_url": norm(official_url),
            "evidence_url": norm(evidence_url),
            "technical_fit_evidence": norm(fit_evidence),
            "target_role": norm(profile.get("expected_role")),
            "pic_status": norm(pic_status),
            "pic": norm(pic) or None,
            "official_portal_eligibility_verified": bool(norm(pic)) and pic_status == "PIC_FOUND_IN_PORTAL",
            "contact_performed": False,
            "interest_confirmed": False,
        })

    names = [candidate["candidate_name"].casefold() for candidate in candidates if candidate["candidate_name"]]
    countries = [candidate["country"].casefold() for candidate in candidates if candidate["country"]]
    basic_checks = [
        ("Stage 80 completed", norm(stage80.get("preparation_outcome")).upper() == "PARTNER_OPTION_PROFILES_PREPARED_NO_CONTACT"),
        ("Stage 80 fingerprint present", len(stage80_fingerprint) == 64),
        ("One candidate per missing participant", len(candidates) == participant_gap),
        ("Candidate names completed and distinct", len(names) == participant_gap and len(set(names)) == participant_gap),
        ("Countries completed and distinct", len(countries) == participant_gap and len(set(countries)) == participant_gap),
        ("Countries differ from applicant country", all(country != applicant_country.casefold() for country in countries)),
        ("Official HTTPS URLs present", all(valid_https_url(candidate["official_url"]) for candidate in candidates)),
        ("Evidence HTTPS URLs present", all(valid_https_url(candidate["evidence_url"]) for candidate in candidates)),
        ("Legal names and fit evidence completed", all(len(candidate["legal_entity_name"]) >= 5 and len(candidate["technical_fit_evidence"]) >= 20 for candidate in candidates)),
    ]
    all_pic_verified = all(candidate["official_portal_eligibility_verified"] for candidate in candidates)

    with st.expander("Candidate verification checks", expanded=True):
        st.dataframe([{"Check": name, "PASS": passed} for name, passed in basic_checks], use_container_width=True, hide_index=True)
        st.write(f"**PIC/official portal verification complete:** {'YES' if all_pic_verified else 'NO — deferred'}")

    sources_confirmed = st.checkbox("I confirm the organisation and technical-fit information comes from the official URLs shown above.", key="stage81_sources")
    preliminary_only = st.checkbox("I understand no candidate is eligible or available until legal status, PIC and written interest are verified.", key="stage81_preliminary")
    no_contact = st.checkbox("I confirm no candidate has been contacted and no portal change has been performed.", key="stage81_no_contact")
    phrase_target = "RECORD STAGE 81 CANDIDATE DUE DILIGENCE"
    phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage81_phrase")
    ready = all(passed for _, passed in basic_checks) and sources_confirmed and preliminary_only and no_contact and norm(phrase) == phrase_target

    if st.button("🔎 Record candidate due-diligence package", type="primary", use_container_width=True, disabled=not ready, key="stage81_persist"):
        outcome = "CANDIDATES_PRELIMINARILY_VERIFIED_NO_CONTACT" if all_pic_verified else "CANDIDATE_DUE_DILIGENCE_PREPARED_PIC_PENDING"
        evidence = {
            "verification_version": "stage81-v1.0",
            "stage80_run_id": stage80_run_id,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "applicant_country": applicant_country,
            "participant_gap": participant_gap,
            "candidates": candidates,
            "all_pic_verified": all_pic_verified,
            "written_interest_confirmed": False,
            "external_contact_performed": False,
            "portal_change_performed": False,
        }
        candidates_sha = sha_json(candidates)
        evidence_sha = sha_json(evidence)
        run_basis = {
            "stage": 81,
            "contract": "stage81-v1.0-candidate-due-diligence",
            "stage80_run_id": stage80_run_id,
            "outcome": outcome,
            "candidates_sha256": candidates_sha,
            "verification_evidence_sha256": evidence_sha,
        }
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage80_run_id": stage80_run_id,
            "stage": 81,
            "verification_version": "stage81-v1.0",
            "opportunity_identity": identity,
            "application_reference": application_reference,
            "final_proposal_id": final_proposal_id,
            "run_status": "COMPLETED",
            "verification_outcome": outcome,
            "applicant_country": applicant_country,
            "participant_gap": participant_gap,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "candidates_sha256": candidates_sha,
            "all_pic_verified": all_pic_verified,
            "written_interest_confirmed": False,
            "external_contact_performed": False,
            "portal_change_performed": False,
            "stage80_run_fingerprint": stage80_fingerprint,
            "verification_evidence_sha256": evidence_sha,
            "run_fingerprint": sha_json(run_basis),
            "verification_payload": evidence,
            "run_payload": run_basis,
            "verified_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            supabase.table("stage81_candidate_partner_verifications").insert(payload).execute()
            st.success(f"Stage 81 persisted — {outcome}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Stage 81 persistence failed. Run Stage 81 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

existing_rows = rows("stage81_candidate_partner_verifications", {"stage80_run_id": stage80_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
if existing:
    st.divider()
    st.subheader("Stage 81 outcome")
    st.success(f"Run ID: {existing.get('id')} — Outcome: {existing.get('verification_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Candidates", existing.get("candidate_count"))
    b.metric("PIC verified", "YES" if existing.get("all_pic_verified") else "PENDING")
    c.metric("Contact", "PERFORMED" if existing.get("external_contact_performed") else "NOT PERFORMED")
    d.metric("Written interest", "CONFIRMED" if existing.get("written_interest_confirmed") else "NOT CONFIRMED")
    st.dataframe(existing.get("candidates") or [], use_container_width=True, hide_index=True)
    st.write(f"**Run fingerprint:** `{existing.get('run_fingerprint')}`")

st.caption("Invariant Stage 81 v1.0: public-source technical fit is preliminary. Formal eligibility, PIC and partner interest require separate evidence.")
