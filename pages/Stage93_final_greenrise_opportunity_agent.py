import hashlib
import html
import json
import os
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Stage 93 FINAL — GreenRise Agent", page_icon="🏁", layout="wide")
st.title("🏁 Etapa 93 FINALĂ — GreenRise Opportunity Monitoring Agent")
st.caption(
    "Extrage apeluri din sursa oficială, elimină duplicatele, calculează relevanța și creează o coadă de verificare. "
    "Nu inventează eligibilitatea și nu depune aplicații fără aprobarea umană finală."
)

KEYWORD_WEIGHTS = {
    "agriculture": 8,
    "agricultural": 8,
    "farming": 8,
    "farm": 6,
    "rural": 7,
    "food": 6,
    "bioeconomy": 8,
    "circular": 6,
    "climate": 7,
    "adaptation": 6,
    "energy": 5,
    "renewable": 6,
    "solar": 6,
    "greenhouse": 8,
    "irrigation": 8,
    "water": 5,
    "soil": 7,
    "digital": 4,
    "artificial intelligence": 5,
    "robotics": 5,
    "biotechnology": 6,
    "waste": 5,
}
PROGRAM_PREFIXES = ("HORIZON-", "LIFE-", "DIGITAL-", "SMP-", "CERV-", "CREA-", "EIC-")
TOPIC_PATTERN = re.compile(
    r"\b(?:HORIZON|LIFE|DIGITAL|SMP|CERV|CREA|EIC)-[A-Z0-9][A-Z0-9_-]*(?:-[A-Z0-9][A-Z0-9_-]*){1,}\b",
    re.IGNORECASE,
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


def strip_html(value):
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def official_eu_url(value):
    try:
        host = (urlparse(value).hostname or "").lower()
        return value.startswith("https://") and (host == "europa.eu" or host.endswith(".europa.eu"))
    except Exception:
        return False


def extract_deadline(text):
    patterns = [
        (r"\b(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])\b", "%Y-%m-%d"),
        (r"\b([0-2]?\d|3[01])/(0?\d|1[0-2])/(20\d{2})\b", "%d/%m/%Y"),
    ]
    for pattern, fmt in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return datetime.strptime(match.group(0), fmt).date()
            except ValueError:
                pass
    month_match = re.search(
        r"\b([0-2]?\d|3[01])\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
        text,
        flags=re.I,
    )
    if month_match:
        try:
            return datetime.strptime(month_match.group(0), "%d %B %Y").date()
        except ValueError:
            return None
    return None


def candidate_title(raw_html, topic_id, context_text):
    escaped = re.escape(topic_id)
    patterns = [rf"<a\b[^>]*href=[\"'][^\"']*{escaped}[^\"']*[\"'][^>]*>(.*?)</a>"]
    for pattern in patterns:
        match = re.search(pattern, raw_html, flags=re.I | re.S)
        if match:
            title = strip_html(match.group(1))
            if 4 <= len(title) <= 500 and title.upper() != topic_id.upper():
                return title
    return topic_id


def candidate_url(raw_context, source_url, topic_id):
    hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", raw_context, flags=re.I)
    for href in hrefs:
        if topic_id.lower() in html.unescape(href).lower():
            url = urljoin(source_url, html.unescape(href))
            if official_eu_url(url):
                return url
    return (
        "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/"
        + topic_id
    )


def score_candidate(text):
    lowered = text.lower()
    matched = []
    score = 0
    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword in lowered:
            matched.append(keyword)
            score += weight
    return score, matched


def identifier_year(topic_id):
    match = re.search(r"-(20\d{2})(?:-|$)", topic_id)
    return int(match.group(1)) if match else None


def programme_fit_bonus(topic_id):
    upper = topic_id.upper()
    if "-CL6-" in upper:
        return 12, ["HORIZON Cluster 6"]
    if "-CL5-" in upper:
        return 7, ["HORIZON Cluster 5"]
    if upper.startswith("LIFE-"):
        return 6, ["LIFE programme"]
    if upper.startswith("EIC-"):
        return 5, ["EIC programme"]
    return 0, []


def retrieve_and_extract(source_url, minimum_score, maximum_results, minimum_lead_days):
    response = requests.get(
        source_url,
        timeout=45,
        allow_redirects=True,
        headers={"User-Agent": "GreenRise-Stage93-Final/1.0 opportunity-monitor"},
    )
    content = response.content or b""
    raw_html = response.text or ""
    if response.status_code < 200 or response.status_code > 299:
        raise RuntimeError(f"Official source returned HTTP {response.status_code}")
    if not content:
        raise RuntimeError("Official source returned empty content")
    if not official_eu_url(response.url):
        raise RuntimeError(f"Official source redirected outside europa.eu: {response.url}")

    found = {}
    for match in TOPIC_PATTERN.finditer(raw_html):
        topic_id = match.group(0).upper().rstrip("-_")
        if not topic_id.startswith(PROGRAM_PREFIXES):
            continue
        if topic_id in found:
            continue
        topic_year = identifier_year(topic_id)
        if topic_year is not None and topic_year < date.today().year:
            continue
        start = max(0, match.start() - 350)
        end = min(len(raw_html), match.end() + 650)
        raw_context = raw_html[start:end]
        context_text = strip_html(raw_context)
        score, keywords = score_candidate(context_text)
        bonus, programme_keywords = programme_fit_bonus(topic_id)
        score += bonus
        keywords.extend(programme_keywords)
        deadline = extract_deadline(context_text)
        days_remaining = (deadline - date.today()).days if deadline else None
        if days_remaining is not None and days_remaining < minimum_lead_days:
            review_status = "REJECTED_INSUFFICIENT_LEAD_TIME"
        elif score < minimum_score:
            review_status = "REJECTED_LOW_RELEVANCE"
        else:
            review_status = "REQUIRES_OFFICIAL_ELIGIBILITY_REVIEW"
        extracted_title = candidate_title(raw_html, topic_id, context_text)
        found[topic_id] = {
            "topic_identifier": topic_id,
            "extracted_title": extracted_title,
            "official_topic_url": candidate_url(raw_context, source_url, topic_id),
            "official_source_url": source_url,
            "extracted_deadline": deadline.isoformat() if deadline else None,
            "days_remaining": days_remaining,
            "relevance_score": score,
            "matched_keywords": keywords,
            "applicant_rule": "UNCLEAR",
            "applicant_rule_evidence": None,
            "location_eligibility": "UNVERIFIED",
            "entity_type_eligibility": "UNVERIFIED",
            "review_status": review_status,
            "raw_source_context": context_text[:6000],
        }

    all_candidates = list(found.values())
    queue = [c for c in all_candidates if c["review_status"] == "REQUIRES_OFFICIAL_ELIGIBILITY_REVIEW"]
    queue.sort(key=lambda c: (-c["relevance_score"], -(c["days_remaining"] or -999999), c["topic_identifier"]))
    queue = queue[:maximum_results]
    for index, candidate in enumerate(queue, 1):
        candidate["rank"] = index

    return {
        "http_status": int(response.status_code),
        "content_bytes": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "retrieved_at": now_iso(),
        "final_url": response.url,
        "discovered_count": len(all_candidates),
        "relevant_count": sum(1 for c in all_candidates if c["relevance_score"] >= minimum_score),
        "queue": queue,
    }


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 93 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("Stage 93 BLOCKED: no projects.")
    st.stop()

project_map = {project_label(project): project for project in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage93_project")]
project_id = str(project["id"])

stage92_runs = rows(
    "stage92_retrieval_configs",
    {"user_id": user_id, "project_id": project_id, "run_status": "COMPLETED"},
    "created_at",
    100,
)
stage92 = next(
    (r for r in stage92_runs if norm(r.get("retrieval_outcome")) == "OFFICIAL_SOURCE_RETRIEVED_MANUALLY_CRON_ARMED"),
    None,
)
if not stage92:
    st.error("Stage 93 BLOCKED: no completed Stage 92 official-source retrieval.")
    st.stop()

stage92_run_id = str(stage92["id"])
stage92_fingerprint = norm(stage92.get("run_fingerprint"))
source_url = norm(stage92.get("official_source_url"))
weekly_monitoring_armed = bool(stage92.get("cron_job_active"))

st.subheader("Final operating policy")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Policy", "SOLO ONLY")
c2.metric("Minimum lead", "30 days")
c3.metric("Weekly monitoring", "ARMED" if weekly_monitoring_armed else "NOT ACTIVE")
c4.metric("Final submission", "HUMAN CONTROLLED")

st.warning(
    "FINAL safety rule: a topic stays in the review queue until an official call document confirms applicant number, "
    "Romania, legal-entity type, deadline and all other eligibility conditions."
)

minimum_score = st.slider("Minimum GreenRise relevance score", 5, 40, 12, 1, key="stage93_min_score")
maximum_results = st.slider("Maximum opportunities in final review queue", 1, 50, 20, 1, key="stage93_max_results")
minimum_lead_days = 30

if "stage93_extraction" not in st.session_state:
    st.session_state.stage93_extraction = None

if st.button("🔎 Run final official opportunity extraction", type="primary", key="stage93_extract"):
    try:
        with st.spinner("Retrieving and analysing the official EU topic list..."):
            st.session_state.stage93_extraction = retrieve_and_extract(
                source_url,
                minimum_score,
                maximum_results,
                minimum_lead_days,
            )
        st.success("Official extraction completed. Review the queue below before final activation.")
    except Exception as exc:
        st.session_state.stage93_extraction = None
        st.error(f"Final extraction failed: {type(exc).__name__}: {str(exc)[:1800]}")

extraction = st.session_state.stage93_extraction
if extraction:
    st.markdown("### Extraction evidence")
    a, b, c, d = st.columns(4)
    a.metric("HTTP", extraction["http_status"])
    b.metric("Source bytes", extraction["content_bytes"])
    c.metric("Topics discovered", extraction["discovered_count"])
    d.metric("Review queue", len(extraction["queue"]))
    st.write(f"**Source SHA-256:** `{extraction['content_sha256']}`")

    if extraction["queue"]:
        st.markdown("### Final human-review queue")
        preview = []
        for candidate in extraction["queue"]:
            preview.append({
                "Rank": candidate["rank"],
                "Topic": candidate["topic_identifier"],
                "Extracted title": candidate["extracted_title"],
                "Score": candidate["relevance_score"],
                "Deadline": candidate["extracted_deadline"],
                "Days": candidate["days_remaining"],
                "Applicant rule": candidate["applicant_rule"],
                "Status": candidate["review_status"],
            })
        st.dataframe(preview, use_container_width=True, hide_index=True)
    else:
        st.info("No candidate passed the selected relevance and lead-time filters. This is a valid final result.")

checks = [
    ("Stage 92 completed", True),
    ("Stage 92 fingerprint present", len(stage92_fingerprint) == 64),
    ("Weekly monitoring armed", weekly_monitoring_armed),
    ("Official source extraction completed", bool(extraction)),
    ("Official HTTP response verified", bool(extraction and extraction["http_status"] == 200)),
    ("Official content SHA-256 present", bool(extraction and len(extraction["content_sha256"]) == 64)),
    ("All extracted applicant rules remain unverified", bool(extraction and all(c["applicant_rule"] == "UNCLEAR" for c in extraction["queue"]))),
]

with st.expander("Final Stage 93 controls", expanded=True):
    st.dataframe([{"Control": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox(
    "I confirm that no applicant-number, entity, location or deadline eligibility was invented.",
    key="stage93_truth",
)
human_review_confirmed = st.checkbox(
    "I understand every shortlisted topic requires official eligibility verification before any application is prepared.",
    key="stage93_review",
)
submission_control_confirmed = st.checkbox(
    "I understand the AI may monitor and prepare information, but final portal submission remains under my control.",
    key="stage93_submission",
)
phrase_target = "CONFIRM STAGE 93 FINAL GREENRISE OPPORTUNITY AGENT"
phrase = st.text_input("Final confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage93_phrase")

ready = (
    all(passed for _, passed in checks)
    and truth_confirmed
    and human_review_confirmed
    and submission_control_confirmed
    and norm(phrase) == phrase_target
)

if st.button(
    "🏁 Activate final GreenRise monitoring agent",
    type="primary",
    use_container_width=True,
    disabled=not ready,
    key="stage93_activate",
):
    recorded_at = now_iso()
    queue = extraction["queue"]
    evidence = {
        "stage92_run_id": stage92_run_id,
        "source_url": source_url,
        "source_http_status": extraction["http_status"],
        "source_content_bytes": extraction["content_bytes"],
        "source_content_sha256": extraction["content_sha256"],
        "discovered_count": extraction["discovered_count"],
        "relevant_count": extraction["relevant_count"],
        "review_queue": [
            {
                "rank": c["rank"],
                "topic_identifier": c["topic_identifier"],
                "relevance_score": c["relevance_score"],
                "review_status": c["review_status"],
            }
            for c in queue
        ],
    }
    evidence_sha = sha_json(evidence)
    run_basis = {
        "stage": 93,
        "contract": "stage93-final-v1.1-greenrise-opportunity-agent",
        "stage92_run_id": stage92_run_id,
        "outcome": "FINAL_AGENT_MONITORING_ACTIVE_REVIEW_QUEUE_READY",
        "extraction_evidence_sha256": evidence_sha,
    }
    run_payload = {
        "user_id": user_id,
        "project_id": project_id,
        "stage92_run_id": stage92_run_id,
        "stage": 93,
        "agent_version": "stage93-final-v1.1",
        "run_status": "COMPLETED",
        "final_outcome": "FINAL_AGENT_MONITORING_ACTIVE_REVIEW_QUEUE_READY",
        "operating_policy": "SOLO_ONLY",
        "minimum_lead_days": minimum_lead_days,
        "source_url": source_url,
        "source_http_status": extraction["http_status"],
        "source_content_bytes": extraction["content_bytes"],
        "source_content_sha256": extraction["content_sha256"],
        "source_retrieved_at": extraction["retrieved_at"],
        "discovered_count": extraction["discovered_count"],
        "relevant_count": extraction["relevant_count"],
        "review_queue_count": len(queue),
        "verified_solo_count": 0,
        "weekly_monitoring_armed": weekly_monitoring_armed,
        "automatic_application_creation": False,
        "automatic_submission": False,
        "final_submission_control": "HUMAN_CONTROLLED",
        "eligibility_claimed": False,
        "stage92_run_fingerprint": stage92_fingerprint,
        "extraction_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "run_payload": run_basis,
        "event_history": [{"event": "FINAL_AGENT_ACTIVATED", "recorded_at": recorded_at, "evidence_sha256": evidence_sha}],
        "created_at": recorded_at,
        "updated_at": recorded_at,
        "completed_at": recorded_at,
    }
    try:
        existing_runs = rows(
            "stage93_final_agent_runs",
            {"user_id": user_id, "stage92_run_id": stage92_run_id},
            "created_at",
            1,
        )
        if existing_runs:
            run_id = str(existing_runs[0]["id"])
            run_payload.pop("created_at", None)
            supabase.table("stage93_opportunity_review_queue").delete().eq("stage93_run_id", run_id).execute()
            supabase.table("stage93_final_agent_runs").update(run_payload).eq("id", run_id).execute()
        else:
            inserted = supabase.table("stage93_final_agent_runs").insert(run_payload).execute().data or []
            run_id = str(inserted[0]["id"])

        if queue:
            queue_payload = []
            for candidate in queue:
                candidate_basis = {
                    "stage93_run_id": run_id,
                    "topic_identifier": candidate["topic_identifier"],
                    "source_content_sha256": extraction["content_sha256"],
                    "relevance_score": candidate["relevance_score"],
                }
                queue_payload.append({
                    **candidate,
                    "stage93_run_id": run_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "application_created": False,
                    "submission_performed": False,
                    "candidate_fingerprint": sha_json(candidate_basis),
                })
            supabase.table("stage93_opportunity_review_queue").insert(queue_payload).execute()

        st.success("FINAL Stage 93 completed. GreenRise monitoring and human-review queue are active.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 93 persistence failed. Run the Stage 93 SQL first. {type(exc).__name__}: {str(exc)[:1800]}")

latest_rows = rows(
    "stage93_final_agent_runs",
    {"user_id": user_id, "stage92_run_id": stage92_run_id},
    "created_at",
    1,
)
latest = latest_rows[0] if latest_rows else None
if latest:
    final_queue = rows("stage93_opportunity_review_queue", {"stage93_run_id": latest["id"]}, "rank", 100)
    final_queue.sort(key=lambda item: int(item.get("rank") or 999999))
    st.divider()
    st.subheader("FINAL Stage 93 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('final_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Monitoring", "ACTIVE" if latest.get("weekly_monitoring_armed") else "NOT ACTIVE")
    b.metric("Discovered", latest.get("discovered_count"))
    c.metric("Review queue", latest.get("review_queue_count"))
    d.metric("Verified solo", latest.get("verified_solo_count"))
    st.write(f"**Operating policy:** `{latest.get('operating_policy')}`")
    st.write(f"**Final submission:** `{latest.get('final_submission_control')}`")
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")
    if final_queue:
        st.dataframe(
            [
                {
                    "Rank": item.get("rank"),
                    "Topic": item.get("topic_identifier"),
                    "Title": item.get("extracted_title"),
                    "Score": item.get("relevance_score"),
                    "Deadline": item.get("extracted_deadline"),
                    "Applicant rule": item.get("applicant_rule"),
                    "Review status": item.get("review_status"),
                }
                for item in final_queue
            ],
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "FINAL invariant Stage 93 v1.1: monitoring and relevance ranking may be automated. Official eligibility, "
    "application creation, portal modification and final submission remain separately evidenced and human controlled."
)
