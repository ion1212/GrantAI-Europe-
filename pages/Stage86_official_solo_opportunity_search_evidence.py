import hashlib
import json
import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 86 — Solo Opportunity Search", page_icon="🔎", layout="wide")
st.title("🔎 Etapa 86 — Official Solo Opportunity Search Evidence Gate")
st.caption(
    "Înregistrează numai apeluri active pentru care o sursă oficială confirmă că aplicarea "
    "cu un singur solicitant este permisă."
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
    st.error("Stage 86 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 86 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage86_project")]
project_id = str(project["id"])

stage85_runs = rows(
    "stage85_solo_search_reactivation_records",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage85 = next(
    (
        run
        for run in stage85_runs
        if norm(run.get("reactivation_outcome")).upper() == "OLD_LOCK_RELEASED_SOLO_SEARCH_READY"
        and norm(run.get("future_application_policy")).upper() == "SOLO_ONLY"
    ),
    None,
)
if not stage85:
    st.error("Stage 86 BLOCKED: no accepted Stage 85 solo-search reactivation.")
    st.stop()

stage85_run_id = str(stage85["id"])
stage85_fingerprint = norm(stage85.get("run_fingerprint"))
minimum_days = int(stage85.get("minimum_days_before_deadline") or 30)
authorized_domains = stage85.get("search_domains") or []
funding_regions = stage85.get("funding_regions") or []

existing_rows = rows(
    "stage86_official_solo_opportunity_searches",
    {"user_id": user_id, "stage85_run_id": stage85_run_id},
    "created_at",
    1,
)
existing = existing_rows[0] if existing_rows else None

st.subheader("Stage 85 → Stage 86 search binding")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Policy", stage85.get("future_application_policy") or "—")
c2.metric("Old lock", stage85.get("agent_release_status") or "—")
c3.metric("Minimum lead time", f"{minimum_days} days")
c4.metric("Submission", "HUMAN CONTROLLED")

st.info(
    "Caută în Portalul Funding & Tenders și în paginile oficiale ale Comisiei Europene. "
    "Pentru fiecare rezultat păstrează URL-ul oficial și pasajul exact privind numărul minim de solicitanți."
)
st.link_button(
    "Open official EU Funding & Tenders opportunities",
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-search",
)

candidate_count = st.number_input(
    "Number of opportunities to verify",
    min_value=1,
    max_value=5,
    value=1,
    step=1,
    key="stage86_candidate_count",
)

candidates = []
candidate_checks = []
for index in range(int(candidate_count)):
    slot = index + 1
    st.markdown(f"### Opportunity {slot}")
    title = st.text_input("Official title", key=f"stage86_title_{slot}")
    topic_id = st.text_input("Topic/call identifier", key=f"stage86_topic_{slot}")
    official_url = st.text_input("Official EU source URL", key=f"stage86_url_{slot}")
    source_document = st.text_input(
        "Official source document or section",
        placeholder="Example: Call document — Admissibility and eligibility conditions",
        key=f"stage86_document_{slot}",
    )
    single_evidence = st.text_area(
        "Official evidence that one applicant is permitted",
        placeholder="Paste a short exact passage from the official call rules.",
        key=f"stage86_evidence_{slot}",
    )
    applicant_rule = st.selectbox(
        "Applicant-number rule",
        ["UNCLEAR", "SINGLE_APPLICANT_CONFIRMED", "PARTNERS_OPTIONAL", "CONSORTIUM_REQUIRED"],
        key=f"stage86_rule_{slot}",
    )
    deadline = st.date_input(
        "Official deadline",
        value=date.today(),
        key=f"stage86_deadline_{slot}",
    )
    domain = st.selectbox(
        "Domain",
        authorized_domains or ["Other authorized domain"],
        key=f"stage86_domain_{slot}",
    )
    applicant_location = st.selectbox(
        "Applicant location supported by the call",
        funding_regions or ["Romania", "European Union"],
        key=f"stage86_region_{slot}",
    )
    entity_evidence = st.text_area(
        "Official evidence that your entity type/location is eligible",
        key=f"stage86_entity_evidence_{slot}",
    )
    fit_note = st.text_area(
        "Short fit note for GreenRise",
        key=f"stage86_fit_{slot}",
    )
    verified_by_user = st.checkbox(
        "I personally checked these details against the official source.",
        key=f"stage86_verified_{slot}",
    )

    days_remaining = (deadline - date.today()).days
    url_ok = official_eu_url(official_url)
    solo_ok = applicant_rule in ("SINGLE_APPLICANT_CONFIRMED", "PARTNERS_OPTIONAL")
    deadline_ok = days_remaining >= minimum_days
    record_ok = all([
        len(norm(title)) >= 5,
        len(norm(topic_id)) >= 3,
        url_ok,
        len(norm(source_document)) >= 5,
        len(norm(single_evidence)) >= 20,
        solo_ok,
        deadline_ok,
        len(norm(entity_evidence)) >= 20,
        len(norm(fit_note)) >= 20,
        bool(verified_by_user),
    ])
    st.write(f"Days remaining: **{days_remaining}** — Qualified: **{'YES' if record_ok else 'NO'}**")

    candidate_checks.append((f"Opportunity {slot} fully verified", record_ok))
    candidates.append({
        "slot": slot,
        "title": norm(title),
        "topic_id": norm(topic_id),
        "official_url": norm(official_url),
        "official_url_verified": url_ok,
        "source_document": norm(source_document),
        "single_applicant_evidence": norm(single_evidence),
        "applicant_rule": applicant_rule,
        "consortium_required": applicant_rule == "CONSORTIUM_REQUIRED",
        "deadline": deadline.isoformat(),
        "days_remaining_at_verification": days_remaining,
        "domain": domain,
        "applicant_location": applicant_location,
        "entity_eligibility_evidence": norm(entity_evidence),
        "fit_note": norm(fit_note),
        "personally_verified": bool(verified_by_user),
        "qualified_solo_candidate": record_ok,
    })

qualified_candidates = [candidate for candidate in candidates if candidate["qualified_solo_candidate"]]

checks = [
    ("Stage 85 completed", norm(stage85.get("run_status")).upper() == "COMPLETED"),
    ("Stage 85 fingerprint present", len(stage85_fingerprint) == 64),
    ("SOLO_ONLY policy active", norm(stage85.get("future_application_policy")).upper() == "SOLO_ONLY"),
    ("Old lock logically released", norm(stage85.get("agent_release_status")).upper() == "RELEASED_BY_STAGE85"),
    ("At least one verified solo opportunity", len(qualified_candidates) > 0),
] + candidate_checks

with st.expander("Official search verification checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox(
    "I confirm that no eligibility, deadline or source evidence above was invented.",
    key="stage86_truth",
)
no_application_action = st.checkbox(
    "I understand Stage 86 does not create, edit or submit an application.",
    key="stage86_no_application",
)
phrase_target = "CONFIRM STAGE 86 VERIFIED SOLO OPPORTUNITY SHORTLIST"
phrase = st.text_input(
    "Confirmation phrase",
    placeholder=f"Type exactly: {phrase_target}",
    key="stage86_phrase",
)
ready = (
    all(passed for _, passed in checks)
    and truth_confirmed
    and no_application_action
    and norm(phrase) == phrase_target
)

if st.button(
    "🔎 Persist Stage 86 verified shortlist",
    type="primary",
    use_container_width=True,
    disabled=not ready,
    key="stage86_persist",
):
    recorded_at = now_iso()
    outcome = "VERIFIED_SOLO_OPPORTUNITY_SHORTLIST_RECORDED"
    evidence = {
        "search_version": "stage86-v1.0",
        "stage85_run_id": stage85_run_id,
        "future_application_policy": "SOLO_ONLY",
        "minimum_days_before_deadline": minimum_days,
        "candidate_count": len(candidates),
        "qualified_candidate_count": len(qualified_candidates),
        "qualified_candidates": qualified_candidates,
        "official_sources_required": True,
        "external_portal_action_performed": False,
        "application_created": False,
        "submission_performed": False,
    }
    evidence_sha = sha_json(evidence)
    run_basis = {
        "stage": 86,
        "contract": "stage86-v1.0-official-solo-opportunity-search-evidence",
        "stage85_run_id": stage85_run_id,
        "outcome": outcome,
        "search_evidence_sha256": evidence_sha,
    }
    event = {"outcome": outcome, "recorded_at": recorded_at, "evidence_sha256": evidence_sha}
    previous_history = existing.get("event_history") if existing else []
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage85_run_id": stage85_run_id,
        "stage": 86,
        "search_version": "stage86-v1.0",
        "run_status": "COMPLETED",
        "search_outcome": outcome,
        "future_application_policy": "SOLO_ONLY",
        "minimum_days_before_deadline": minimum_days,
        "candidate_count": len(candidates),
        "qualified_candidate_count": len(qualified_candidates),
        "candidates": candidates,
        "qualified_candidates": qualified_candidates,
        "official_sources_required": True,
        "external_portal_action_performed": False,
        "application_created": False,
        "submission_performed": False,
        "stage85_run_fingerprint": stage85_fingerprint,
        "search_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "search_payload": evidence,
        "run_payload": run_basis,
        "event_history": (previous_history or []) + [event],
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        if existing:
            supabase.table("stage86_official_solo_opportunity_searches").update(payload).eq("id", existing["id"]).execute()
        else:
            payload["created_at"] = recorded_at
            supabase.table("stage86_official_solo_opportunity_searches").insert(payload).execute()
        st.success(f"Stage 86 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 86 persistence failed. Run Stage 86 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows(
    "stage86_official_solo_opportunity_searches",
    {"user_id": user_id, "stage85_run_id": stage85_run_id},
    "created_at",
    1,
)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 86 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('search_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Candidates checked", latest.get("candidate_count"))
    b.metric("Solo verified", latest.get("qualified_candidate_count"))
    c.metric("Application created", "YES" if latest.get("application_created") else "NO")
    d.metric("Submission", "YES" if latest.get("submission_performed") else "NO")
    st.dataframe(latest.get("qualified_candidates") or [], use_container_width=True, hide_index=True)
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption(
    "Invariant Stage 86 v1.0: only evidenced solo-eligible opportunities may enter the shortlist. "
    "No application creation, portal modification or submission is claimed."
)
