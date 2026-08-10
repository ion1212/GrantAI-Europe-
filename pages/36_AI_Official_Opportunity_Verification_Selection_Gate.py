import os
import json
import re
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="Official Opportunity Verification & Selection Gate",
    page_icon="✅",
    layout="wide",
)

st.title("✅ Etapa 36 — AI Official Opportunity Verification & Selection Gate")
st.caption(
    "Verifică oficial cele mai bune rezultate din Etapa 35 înainte de selectare. "
    "Nu presupune eligibilitate, TRL, buget, consorțiu sau alte cerințe nesusținute."
)


# ---------------------------------------------------------------------
# Config / clients / auth
# ---------------------------------------------------------------------
def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def get_supabase():
    return create_client(
        secret("SUPABASE_URL"),
        secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"),
    )


@st.cache_resource
def get_openai():
    return OpenAI(api_key=secret("OPENAI_API_KEY"))


def model_name() -> str:
    return secret("OPENAI_MODEL", "gpt-4.1-mini")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows(table: str, filters=None, order="created_at", limit=1000):
    try:
        q = supabase.table(table).select("*")
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                q = q.eq(key, value)
        if order:
            q = q.order(order, desc=True)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def restore_auth_session(sb) -> None:
    session = st.session_state.get("auth_session")
    if not session:
        return

    access_token = (
        session.get("access_token")
        if isinstance(session, dict)
        else getattr(session, "access_token", None)
    )
    refresh_token = (
        session.get("refresh_token")
        if isinstance(session, dict)
        else getattr(session, "refresh_token", None)
    )

    if access_token and refresh_token:
        try:
            sb.auth.set_session(access_token, refresh_token)
        except Exception:
            pass


def current_user_id(sb) -> str | None:
    for key in ("auth_user", "user"):
        user = st.session_state.get(key)
        if isinstance(user, dict) and user.get("id"):
            return str(user["id"])
        if getattr(user, "id", None):
            return str(user.id)

    for key in ("user_id", "auth_user_id"):
        value = st.session_state.get(key)
        if value:
            return str(value)

    try:
        user = sb.auth.get_user().user
        if user and getattr(user, "id", None):
            return str(user.id)
    except Exception:
        pass

    return None


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def clean_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = (
            text.replace("```json", "", 1)
            .replace("```JSON", "", 1)
            .replace("```", "")
            .strip()
        )
    return text


def unwrap_value(value):
    if value is None:
        return None

    if isinstance(value, list):
        for item in value:
            item = unwrap_value(item)
            if item not in (None, "", [], {}):
                return item
        return None

    if isinstance(value, dict):
        for key in (
            "value", "date", "label", "name", "title", "text",
            "identifier", "reference", "url",
        ):
            if key in value:
                item = unwrap_value(value.get(key))
                if item not in (None, "", [], {}):
                    return item

        for item in value.values():
            item = unwrap_value(item)
            if item not in (None, "", [], {}):
                return item

        return None

    return value


def norm(value: Any) -> str:
    value = unwrap_value(value)
    return str(value or "").strip()


def parse_date(value):
    value = unwrap_value(value)
    if value in (None, ""):
        return None

    text = str(value).strip()

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass

    return None


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase nu este configurat corect: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Intră în cont din pagina principală și revino.")
    st.stop()


# ---------------------------------------------------------------------
# Project / latest Etapa 35
# ---------------------------------------------------------------------
projects = rows("projects", {"user_id": user_id}, "updated_at", 100)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_options = {
    f"{p.get('name') or 'Project'} — {str(p.get('id'))[:8]}": p
    for p in projects
}

project_label = st.selectbox("Project", list(project_options.keys()))
project = project_options[project_label]
project_id = str(project["id"])
project_data = as_dict(project.get("data"))

st.write(f"**Project:** {project.get('name') or '—'}")

scoring_runs = rows(
    "opportunity_scoring_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    100,
)

latest_scoring_run = next(
    (r for r in scoring_runs if str(r.get("run_status") or "") == "Completed"),
    None,
)

if not latest_scoring_run:
    st.warning("Nu există un scoring Etapa 35 finalizat pentru acest proiect.")
    st.stop()

scoring_run_id = str(latest_scoring_run["id"])

scoring_results = rows(
    "opportunity_scoring_results",
    {
        "user_id": user_id,
        "project_id": project_id,
        "scoring_run_id": scoring_run_id,
    },
    "overall_score",
    1000,
)

if not scoring_results:
    st.warning("Ultimul run Etapa 35 nu conține rezultate.")
    st.stop()


# ---------------------------------------------------------------------
# Candidate selection scope
# ---------------------------------------------------------------------
top_n = st.slider(
    "Număr de candidați din Top Etapa 35 pentru verificare oficială",
    min_value=1,
    max_value=min(20, len(scoring_results)),
    value=min(10, len(scoring_results)),
    step=1,
)

candidates = scoring_results[:top_n]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Scoring run", scoring_run_id[:8])
m2.metric("Available results", len(scoring_results))
m3.metric("Candidates", len(candidates))
m4.metric("Top score", f"{float(scoring_results[0].get('overall_score') or 0):.1f}")

st.info(
    "Etapa 36 nu aprobă automat oportunitatea #1. "
    "Fiecare candidat trebuie să aibă identitatea și statusul/deadline-ul susținute "
    "de date oficiale înainte să devină SELECTABLE."
)


# ---------------------------------------------------------------------
# EU Funding & Tenders SEARCH API helpers
# ---------------------------------------------------------------------
def _multipart_json_files(parts: dict):
    boundary = "----GrantAIEuropeBoundary36"
    body = bytearray()

    for name, value in parts.items():
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="blob"\r\n'
                "Content-Type: application/json\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(payload)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def fetch_official_records(identity: str, title: str, limit: int = 10):
    """
    Query the EU Funding & Tenders SEARCH API by exact topic code first
    and by title second.

    Important:
    - the API can expose internal numeric EC IDs;
    - numeric IDs are never accepted as the opportunity identity;
    - all returned official responses are merged and checked later for the
      exact expected HORIZON/ERASMUS/etc. topic code.
    """
    endpoint = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

    query_data = {
        "bool": {
            "must": [
                {"terms": {"type": ["1", "2", "8"]}}
            ]
        }
    }

    sort_data = {"order": "ASC", "field": "sortStatus"}

    display_fields = [
        "type",
        "identifier",
        "reference",
        "callccm2Id",
        "title",
        "status",
        "caName",
        "startDate",
        "deadlineDate",
        "deadlineModel",
        "frameworkProgramme",
        "typesOfAction",
        "description",
        "programmePeriod",
        "callIdentifier",
        "topicConditions",
        "topicConditionsData",
        "destinationId",
    ]

    search_terms = []
    for value in (identity.strip(), title.strip()):
        if value and value not in search_terms:
            search_terms.append(value)

    if not search_terms:
        raise ValueError(
            "Oportunitatea nu are identity sau title pentru verificarea oficială."
        )

    official_responses = []
    source_urls = []

    for search_text in search_terms:
        params = {
            "apiKey": "SEDIA",
            "text": search_text,
            "pageSize": str(max(int(limit), 20)),
            "pageNumber": "1",
        }
        url = endpoint + "?" + urllib.parse.urlencode(params)

        body, content_type = _multipart_json_files({
            "sort": sort_data,
            "query": query_data,
            "languages": ["en"],
            "displayFields": display_fields,
        })

        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Content-Type": content_type,
                "Origin": "https://ec.europa.eu",
                "Referer": "https://ec.europa.eu/",
                "Accept-Language": "en-GB,en;q=0.9",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"EU Funding & Tenders API HTTP {exc.code}: "
                f"{details[:1200] or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Nu s-a putut conecta la EU Funding & Tenders API: {exc.reason}"
            ) from exc

        try:
            official_responses.append(json.loads(response_body))
            source_urls.append(url)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "EU Funding & Tenders API nu a returnat JSON valid."
            ) from exc

    return {
        "official_search_responses": official_responses
    }, " | ".join(source_urls)


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def pick(d: dict, names):
    lowered = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if name.lower() in lowered:
            value = unwrap_value(lowered[name.lower()])
            if value not in (None, "", [], {}):
                return value
    return None



def normalize_topic_code(value: Any) -> str:
    """Normalize EU topic identities for deterministic comparison."""
    text = norm(value).upper().strip()
    if not text:
        return ""
    text = urllib.parse.unquote(text)
    text = re.sub(r"\s+", "", text)
    return text


def topic_code_occurs(value: Any, expected_identity: str) -> bool:
    """
    Return True only when the expected topic code occurs as a complete token.
    This allows codes embedded in official URLs/strings but rejects numeric IDs.
    """
    candidate = normalize_topic_code(value)
    expected = normalize_topic_code(expected_identity)

    if not candidate or not expected:
        return False

    if candidate == expected:
        return True

    pattern = (
        r"(?<![A-Z0-9_-])"
        + re.escape(expected)
        + r"(?![A-Z0-9_-])"
    )
    return re.search(pattern, candidate) is not None


def find_expected_identity_in_obj(obj: Any, expected_identity: str) -> str:
    """
    Confirm identity only when the exact expected topic code is actually present
    in the official response object.
    """
    expected = normalize_topic_code(expected_identity)
    if not expected:
        return ""

    for node in walk(obj):
        if not isinstance(node, dict):
            continue

        for value in node.values():
            if isinstance(value, (str, int, float)):
                if topic_code_occurs(value, expected_identity):
                    return expected_identity.strip()

            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, (str, int, float)):
                        if topic_code_occurs(item, expected_identity):
                            return expected_identity.strip()

    return ""


def extract_topic_identity(d: dict, expected_identity: str = "") -> str:
    """
    Prefer real topic/call codes. Never use generic numeric `id` as official identity.
    """
    preferred_fields = [
        "identifier", "topicIdentifier", "topicId", "topic_id",
        "reference", "callIdentifier", "callCode", "topicCode",
    ]
    expected = normalize_topic_code(expected_identity)

    # Exact expected code wins, regardless of which official field contains it.
    if expected:
        found = find_expected_identity_in_obj(d, expected_identity)
        if found:
            return found

    # Otherwise accept only values that look like EU opportunity/topic codes.
    for field in preferred_fields:
        value = norm(pick(d, [field]))
        if not value:
            continue
        upper = value.upper()
        if (
            upper.startswith(("HORIZON-", "ERASMUS-", "EMFAF-", "SMP-", "LIFE-",
                              "DIGITAL-", "CEF-", "EU4H-", "CREA-", "AMIF-",
                              "ISF-", "BMVI-", "EDF-", "EIC-", "ERC-"))
            or ("-" in upper and not upper.isdigit())
        ):
            return value
    return ""


def extract_deadline_from_obj(obj: Any):
    """Find a usable explicit deadline recursively without treating arbitrary dates as deadlines."""
    deadline_keys = {
        "deadlinedate", "deadline", "submissiondeadline", "closingdate",
        "deadline_date", "submissionenddate", "endofsubmission",
    }
    candidates = []
    for node in walk(obj):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if str(key).lower() in deadline_keys:
                raw = unwrap_value(value)
                parsed = parse_date(raw)
                if parsed:
                    candidates.append((parsed, norm(raw)))
    if not candidates:
        return ""
    # Prefer the latest explicit deadline when the API exposes several cut-offs.
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def looks_like_record(d: dict):
    keys = {str(k).lower() for k in d.keys()}
    useful = {
        "title", "identifier", "reference", "deadlineDate".lower(),
        "status", "callIdentifier".lower(), "frameworkProgramme".lower(),
    }
    return len(keys & useful) >= 2


def extract_official_records(raw, expected_identity: str = ""):
    records = []
    seen = set()

    for d in walk(raw):
        if not isinstance(d, dict) or not looks_like_record(d):
            continue

        identity = extract_topic_identity(d, expected_identity)
        title = norm(pick(d, ["title", "name", "topicTitle", "callTitle"]))
        deadline = norm(
            pick(d, [
                "deadlineDate", "deadline", "submissionDeadline",
                "closingDate",
            ])
        ) or extract_deadline_from_obj(d)
        status = norm(pick(d, ["status", "callStatus", "topicStatus"]))
        programme = norm(
            pick(d, [
                "frameworkProgramme", "programme", "program",
                "programmeName", "programmePeriod",
            ])
        )
        description = norm(pick(d, ["description", "summary", "abstract"]))
        action_type = norm(pick(d, ["typesOfAction", "typeOfAction"]))
        topic_conditions = pick(
            d,
            ["topicConditions", "topicConditionsData", "conditions"],
        )

        key = (identity, title, deadline)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "identity": identity,
            "title": title,
            "deadline": deadline,
            "status": status,
            "programme": programme,
            "description": description,
            "action_type": action_type,
            "topic_conditions": topic_conditions,
            "raw": d,
        })

    return records


def choose_best_official_record(records: list[dict], expected_identity: str):
    expected = normalize_topic_code(expected_identity)

    exact = [
        r for r in records
        if normalize_topic_code(r.get("identity")) == expected
    ]

    if exact:
        exact[0]["identity"] = expected_identity.strip()
        return exact[0], "MATCH"

    for r in records:
        found = find_expected_identity_in_obj(
            r.get("raw") or {},
            expected_identity,
        )
        if found:
            r["identity"] = expected_identity.strip()
            return r, "MATCH"

    # A search hit that does not contain the expected exact topic code is NOT
    # evidence of mismatch. Keep it UNVERIFIED instead of rejecting the candidate.
    if records:
        return records[0], "UNVERIFIED"

    return None, "UNVERIFIED"


# ---------------------------------------------------------------------
# AI official interpretation
# ---------------------------------------------------------------------
SYSTEM = """You are a strict official grant-opportunity verification gate.

You receive:
- project information;
- an Etapa 35 scoring result;
- one official record returned by the EU Funding & Tenders SEARCH API, when found;
- current date.

STRICT RULES:
- Treat the official record as the only official source supplied here.
- Never invent applicant eligibility, consortium rules, TRL requirements,
  funding rate, budget thresholds, geographic eligibility, or any requirement
  not explicitly present in the supplied official record.
- Identity must not be declared MATCH unless the exact expected identity is
  present in the official record data.
- Deadline is verified only if an explicit deadline value is present.
- Status is verified only if an explicit official status is present OR an explicit
  future deadline supports that applications are not yet past deadline; do not
  turn that into a stronger claim than the source supports.
- Applicant/consortium/TRL/funding/geographic requirements are verified only if
  the supplied source explicitly provides them.
- SELECTABLE requires:
  1) identity MATCH,
  2) deadline verified and not expired,
  3) no supplied evidence of CLOSED/EXPIRED,
  4) no positively established critical project incompatibility.
  Missing applicant/consortium/TRL/funding details should normally lead to
  NEEDS_VERIFICATION rather than SELECTABLE when they are material to selection.
- REJECTED is for identity mismatch, expired/closed opportunity, or a positively
  established incompatibility.
- BLOCKED is for a critical verified incompatibility that prevents proceeding.
- Return JSON only.

Schema:
{
  "official_identity": "",
  "official_title": "",
  "identity_status": "MATCH|MISMATCH|UNVERIFIED|UNCLEAR",
  "official_status": "",
  "status_verified": false,
  "official_deadline": null,
  "deadline_verified": false,
  "programme": "",
  "programme_verified": false,

  "applicant_requirements": {},
  "applicant_requirements_verified": false,
  "consortium_requirements": {},
  "consortium_requirements_verified": false,
  "trl_requirements": {},
  "trl_requirements_verified": false,
  "funding_requirements": {},
  "funding_requirements_verified": false,
  "geographic_requirements": {},
  "geographic_requirements_verified": false,

  "selection_status": "SELECTABLE|REJECTED|NEEDS_VERIFICATION|BLOCKED",
  "rejection_reason": "",
  "verification_reason": "",
  "confidence": "Low|Medium|High",
  "official_source_reference": "",
  "official_source_excerpt": ""
}
"""


def verify_candidate(scoring_result: dict):
    identity = str(scoring_result.get("opportunity_identity") or "")
    title = str(scoring_result.get("opportunity_title") or "")

    raw_response, api_url = fetch_official_records(identity, title, 10)
    records = extract_official_records(raw_response, identity)
    record, deterministic_identity_status = choose_best_official_record(
        records,
        identity,
    )

    if not record:
        return {
            "official_identity": "",
            "official_title": "",
            "identity_status": "UNVERIFIED",
            "official_status": "",
            "status_verified": False,
            "official_deadline": None,
            "deadline_verified": False,
            "programme": "",
            "programme_verified": False,
            "applicant_requirements": {},
            "applicant_requirements_verified": False,
            "consortium_requirements": {},
            "consortium_requirements_verified": False,
            "trl_requirements": {},
            "trl_requirements_verified": False,
            "funding_requirements": {},
            "funding_requirements_verified": False,
            "geographic_requirements": {},
            "geographic_requirements_verified": False,
            "selection_status": "NEEDS_VERIFICATION",
            "rejection_reason": "",
            "verification_reason": (
                "Identitatea oportunității nu a putut fi confirmată din "
                "răspunsul oficial Funding & Tenders."
            ),
            "confidence": "Low",
            "official_source_title": "EU Funding & Tenders Portal SEARCH API",
            "official_source_url": api_url,
            "official_source_reference": identity,
            "official_source_excerpt": "",
            "ai_result": {},
        }

    client = get_openai()

    payload = {
        "current_date": date.today().isoformat(),
        "project": {
            "id": project_id,
            "name": project.get("name"),
            "data": project_data,
        },
        "scoring_result": {
            "id": scoring_result.get("id"),
            "opportunity_identity": identity,
            "opportunity_title": title,
            "overall_score": scoring_result.get("overall_score"),
            "verdict": scoring_result.get("verdict"),
            "critical_gaps": scoring_result.get("critical_gaps"),
            "missing_evidence": scoring_result.get("missing_evidence"),
        },
        "deterministic_identity_status": deterministic_identity_status,
        "official_record": record,
    }

    response = client.chat.completions.create(
        model=model_name(),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
    )

    result = json.loads(clean_json(response.choices[0].message.content))
    if not isinstance(result, dict):
        raise ValueError("Răspuns AI invalid.")

    # Identity is controlled deterministically; AI cannot promote a mismatch.
    result["identity_status"] = deterministic_identity_status

    official_deadline = parse_date(
        result.get("official_deadline") or record.get("deadline")
    )
    result["official_deadline"] = (
        official_deadline.isoformat() if official_deadline else None
    )
    result["deadline_verified"] = bool(official_deadline)

    # Deterministic expiry override.
    if official_deadline and official_deadline < date.today():
        result["selection_status"] = "REJECTED"
        result["rejection_reason"] = (
            f"Deadline-ul oficial {official_deadline.isoformat()} este expirat."
        )

    if deterministic_identity_status == "MISMATCH":
        result["selection_status"] = "REJECTED"
        result["rejection_reason"] = (
            "Identitatea returnată de sursa oficială nu corespunde "
            "oportunității scorate în Etapa 35."
        )

    if deterministic_identity_status in ("UNVERIFIED", "UNCLEAR"):
        if result.get("selection_status") == "SELECTABLE":
            result["selection_status"] = "NEEDS_VERIFICATION"

    # Missing eligibility/TRL/funding detail is not an identity rejection.
    # Keep it as NEEDS_VERIFICATION unless the official source positively proves incompatibility.
    if (
        deterministic_identity_status == "MATCH"
        and official_deadline
        and official_deadline >= date.today()
        and result.get("selection_status") == "REJECTED"
        and not result.get("rejection_reason")
    ):
        result["selection_status"] = "NEEDS_VERIFICATION"

    allowed_selection = {
        "SELECTABLE", "REJECTED", "NEEDS_VERIFICATION", "BLOCKED"
    }
    if result.get("selection_status") not in allowed_selection:
        result["selection_status"] = "NEEDS_VERIFICATION"

    confidence = str(result.get("confidence") or "Low")
    if confidence not in ("Low", "Medium", "High"):
        confidence = "Low"
    result["confidence"] = confidence

    # The API URL is the source reference for this verification run.
    result["official_source_title"] = "EU Funding & Tenders Portal SEARCH API"
    result["official_source_url"] = api_url

    if not result.get("official_source_reference"):
        result["official_source_reference"] = identity

    # Preserve official values even if AI omitted them.
    result["official_identity"] = (
        identity.strip()
        if deterministic_identity_status == "MATCH"
        else str(record.get("identity") or "")
    )
    result["official_title"] = str(
        result.get("official_title") or record.get("title") or ""
    )
    result["official_status"] = str(
        result.get("official_status") or record.get("status") or ""
    )
    result["programme"] = str(
        result.get("programme") or record.get("programme") or ""
    )

    result["status_verified"] = bool(
        result.get("status_verified") and result["official_status"]
    )
    result["programme_verified"] = bool(
        result.get("programme_verified") and result["programme"]
    )

    for name in (
        "applicant_requirements",
        "consortium_requirements",
        "trl_requirements",
        "funding_requirements",
        "geographic_requirements",
    ):
        if not isinstance(result.get(name), dict):
            result[name] = {}

    result["ai_result"] = result.copy()
    return result


# ---------------------------------------------------------------------
# Run verification
# ---------------------------------------------------------------------
if st.button(
    "✅ Verifică oficial Top oportunități",
    type="primary",
    use_container_width=True,
):
    selection_run_id = None

    with st.spinner(
        f"Verific oficial {len(candidates)} oportunități din Etapa 35..."
    ):
        try:
            run_insert = (
                supabase.table("opportunity_selection_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "scoring_run_id": scoring_run_id,
                    "candidates_considered": len(candidates),
                    "run_status": "Running",
                    "started_at": now_iso(),
                    "updated_at": now_iso(),
                })
                .execute()
            ).data or []

            if not run_insert:
                raise RuntimeError("Nu am putut crea selection run.")

            selection_run_id = str(run_insert[0]["id"])

            counts = {
                "verified_candidates": 0,
                "selectable_candidates": 0,
                "rejected_candidates": 0,
                "needs_verification": 0,
            }

            progress = st.progress(0)
            message = st.empty()

            for i, scoring_result in enumerate(candidates, start=1):
                identity = scoring_result.get("opportunity_identity")
                message.write(
                    f"Verificare {i}/{len(candidates)} — "
                    f"{scoring_result.get('opportunity_title') or identity}"
                )

                result = verify_candidate(scoring_result)
                status = result["selection_status"]

                if result.get("identity_status") == "MATCH":
                    counts["verified_candidates"] += 1

                if status == "SELECTABLE":
                    counts["selectable_candidates"] += 1
                elif status in ("REJECTED", "BLOCKED"):
                    counts["rejected_candidates"] += 1
                else:
                    counts["needs_verification"] += 1

                supabase.table("opportunity_selection_results").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "selection_run_id": selection_run_id,
                    "scoring_result_id": scoring_result["id"],
                    "opportunity_identity": scoring_result.get("opportunity_identity"),
                    "opportunity_title": scoring_result.get("opportunity_title"),
                    "scoring_score": scoring_result.get("overall_score"),
                    "scoring_verdict": scoring_result.get("verdict"),

                    "official_identity": result.get("official_identity"),
                    "official_title": result.get("official_title"),
                    "identity_status": result.get("identity_status"),

                    "official_status": result.get("official_status"),
                    "status_verified": bool(result.get("status_verified")),
                    "official_deadline": result.get("official_deadline"),
                    "deadline_verified": bool(result.get("deadline_verified")),

                    "programme": result.get("programme"),
                    "programme_verified": bool(result.get("programme_verified")),

                    "applicant_requirements": result.get("applicant_requirements") or {},
                    "applicant_requirements_verified": bool(
                        result.get("applicant_requirements_verified")
                    ),

                    "consortium_requirements": result.get("consortium_requirements") or {},
                    "consortium_requirements_verified": bool(
                        result.get("consortium_requirements_verified")
                    ),

                    "trl_requirements": result.get("trl_requirements") or {},
                    "trl_requirements_verified": bool(
                        result.get("trl_requirements_verified")
                    ),

                    "funding_requirements": result.get("funding_requirements") or {},
                    "funding_requirements_verified": bool(
                        result.get("funding_requirements_verified")
                    ),

                    "geographic_requirements": result.get("geographic_requirements") or {},
                    "geographic_requirements_verified": bool(
                        result.get("geographic_requirements_verified")
                    ),

                    "official_source_title": result.get("official_source_title"),
                    "official_source_url": result.get("official_source_url"),
                    "official_source_reference": result.get("official_source_reference"),
                    "official_source_excerpt": result.get("official_source_excerpt"),

                    "selection_status": status,
                    "rejection_reason": result.get("rejection_reason"),
                    "verification_reason": result.get("verification_reason"),
                    "confidence": result.get("confidence"),
                    "user_selected": False,
                    "ai_result": result.get("ai_result") or result,
                    "updated_at": now_iso(),
                }).execute()

                progress.progress(i / len(candidates))

            run_status = (
                "Completed"
                if counts["selectable_candidates"] > 0
                else "Needs attention"
            )

            supabase.table("opportunity_selection_runs").update({
                **counts,
                "run_status": run_status,
                "summary": {
                    "stage": 36,
                    "source_scoring_run_id": scoring_run_id,
                    "candidate_limit": len(candidates),
                    "source": "EU Funding & Tenders Portal SEARCH API",
                },
                "completed_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", selection_run_id).eq("user_id", user_id).execute()

            st.success(
                f"Etapa 36 finalizată: "
                f"{counts['selectable_candidates']} SELECTABLE, "
                f"{counts['needs_verification']} NEEDS_VERIFICATION."
            )
            st.rerun()

        except Exception as exc:
            error_text = str(exc)

            if selection_run_id:
                try:
                    supabase.table("opportunity_selection_runs").update({
                        "run_status": "Failed",
                        "summary": {
                            "stage": 36,
                            "error": error_text[:4000],
                        },
                        "completed_at": now_iso(),
                        "updated_at": now_iso(),
                    }).eq("id", selection_run_id).eq("user_id", user_id).execute()
                except Exception:
                    pass

            st.error(f"Etapa 36 nu a putut finaliza verificarea: {error_text}")


# ---------------------------------------------------------------------
# Latest verification results + explicit user selection
# ---------------------------------------------------------------------
selection_runs = rows(
    "opportunity_selection_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    100,
)

latest_selection_run = next(
    (
        r for r in selection_runs
        if str(r.get("run_status") or "") in ("Completed", "Needs attention")
    ),
    None,
)

st.divider()
st.subheader("Official Verification Results")

if not latest_selection_run:
    st.caption("Nu există încă o verificare Etapa 36 finalizată.")
else:
    selection_run_id = str(latest_selection_run["id"])

    results = rows(
        "opportunity_selection_results",
        {
            "user_id": user_id,
            "project_id": project_id,
            "selection_run_id": selection_run_id,
        },
        "scoring_score",
        100,
    )

    a, b, c, d = st.columns(4)
    a.metric("Candidates", int(latest_selection_run.get("candidates_considered") or 0))
    b.metric("Verified identity", int(latest_selection_run.get("verified_candidates") or 0))
    c.metric("Selectable", int(latest_selection_run.get("selectable_candidates") or 0))
    d.metric("Needs verification", int(latest_selection_run.get("needs_verification") or 0))

    if results:
        st.dataframe(
            [
                {
                    "Opportunity": r.get("opportunity_title"),
                    "Identity": r.get("opportunity_identity"),
                    "Score": float(r.get("scoring_score") or 0),
                    "Identity status": r.get("identity_status"),
                    "Official deadline": r.get("official_deadline"),
                    "Selection": r.get("selection_status"),
                    "Confidence": r.get("confidence"),
                }
                for r in results
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Candidate details")

        for rank, r in enumerate(results, start=1):
            label = (
                f"#{rank} — {r.get('opportunity_title') or r.get('opportunity_identity')} "
                f"— {float(r.get('scoring_score') or 0):.1f}/100 "
                f"[{r.get('selection_status')}]"
            )

            with st.expander(label, expanded=(rank <= 3)):
                c1, c2, c3 = st.columns(3)
                c1.metric("Identity", r.get("identity_status") or "—")
                c2.metric("Deadline verified", "YES" if r.get("deadline_verified") else "NO")
                c3.metric("Status verified", "YES" if r.get("status_verified") else "NO")

                st.write(f"**Official identity:** {r.get('official_identity') or '—'}")
                st.write(f"**Official title:** {r.get('official_title') or '—'}")
                st.write(f"**Official deadline:** {r.get('official_deadline') or '—'}")
                st.write(f"**Programme:** {r.get('programme') or '—'}")

                st.write("**Verification reason**")
                st.write(r.get("verification_reason") or "—")

                if r.get("rejection_reason"):
                    st.error(r.get("rejection_reason"))

                checks = [
                    ("Applicant", r.get("applicant_requirements_verified")),
                    ("Consortium", r.get("consortium_requirements_verified")),
                    ("TRL", r.get("trl_requirements_verified")),
                    ("Funding", r.get("funding_requirements_verified")),
                    ("Geographic", r.get("geographic_requirements_verified")),
                ]
                st.write(
                    "**Requirement verification:** "
                    + " • ".join(
                        f"{name}: {'YES' if ok else 'NO'}"
                        for name, ok in checks
                    )
                )

                if r.get("official_source_url"):
                    st.caption(
                        f"Official source: {r.get('official_source_title') or ''} "
                        f"— {r.get('official_source_reference') or ''}"
                    )

        selectable = [
            r for r in results
            if str(r.get("selection_status") or "") == "SELECTABLE"
        ]

        st.divider()
        st.subheader("Select opportunity")

        if selectable:
            selectable_options = {
                (
                    f"{r.get('opportunity_title') or r.get('opportunity_identity')} "
                    f"— {float(r.get('scoring_score') or 0):.1f}/100"
                ): r
                for r in selectable
            }

            selected_label = st.selectbox(
                "Oportunitate verificată pentru continuare",
                list(selectable_options.keys()),
            )
            selected = selectable_options[selected_label]

            confirm = st.checkbox(
                "Confirm că vreau să selectez această oportunitate pentru continuarea fluxului."
            )

            if st.button(
                "✅ Selectează oportunitatea verificată",
                disabled=not confirm,
                use_container_width=True,
            ):
                # Clear prior selection in this run.
                supabase.table("opportunity_selection_results").update({
                    "user_selected": False,
                    "updated_at": now_iso(),
                }).eq("selection_run_id", selection_run_id).eq(
                    "user_id", user_id
                ).execute()

                supabase.table("opportunity_selection_results").update({
                    "user_selected": True,
                    "updated_at": now_iso(),
                }).eq("id", selected["id"]).eq("user_id", user_id).execute()

                supabase.table("opportunity_selection_runs").update({
                    "selected_result_id": selected["id"],
                    "selected_opportunity_identity": selected["opportunity_identity"],
                    "updated_at": now_iso(),
                }).eq("id", selection_run_id).eq("user_id", user_id).execute()

                st.success(
                    f"Oportunitatea {selected['opportunity_identity']} a fost selectată."
                )
                st.rerun()

        else:
            st.warning(
                "Niciun candidat nu este încă SELECTABLE. "
                "Completează verificările oficiale lipsă înainte de selectare."
            )
            st.info(
                "Dacă Identity rămâne UNVERIFIED, SEARCH API nu a returnat "
                "codul exact HORIZON/ERASMUS/etc. în datele oficiale. "
                "Etapa 36 nu acceptă un ID numeric intern EC ca identitate."
            )


with st.expander("Istoric Etapa 36"):
    if selection_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "scoring_run": str(r.get("scoring_run_id") or "")[:8],
                    "candidates": r.get("candidates_considered"),
                    "verified": r.get("verified_candidates"),
                    "selectable": r.get("selectable_candidates"),
                    "rejected": r.get("rejected_candidates"),
                    "needs_verification": r.get("needs_verification"),
                    "selected": r.get("selected_opportunity_identity"),
                    "status": r.get("run_status"),
                }
                for r in selection_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 36.")

st.caption(
    "Etapa 36 nu înlocuiește analiza completă a documentației apelului. "
    "Ea confirmă identitatea/statusul/deadline-ul din sursa oficială disponibilă și "
    "blochează selectarea automată când cerințele critice nu sunt încă verificate."
)
