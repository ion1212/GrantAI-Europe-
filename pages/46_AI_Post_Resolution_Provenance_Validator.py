import os
import json
import re
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse
from io import BytesIO

import requests
import streamlit as st
from supabase import create_client

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# =====================================================================
# STAGE 46 v2.4 — POST-RESOLUTION PROVENANCE VALIDATOR
# Dedicated Supabase persistence:
#   locked_evidence_provenance_runs
#   locked_evidence_provenance_items
#   locked_evidence_provenance_sources
# =====================================================================

st.set_page_config(
    page_title="Stage 46 v2.6 — Provenance Validator",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Etapa 46 v2.6 — AI Post-Resolution Provenance Validator")
st.caption(
    "Etapa 46 v2 citește rezultatele Stage 45, dar își păstrează propriul audit în tabelele "
    "provenance. PASS este permis numai dacă toate cerințele OFFICIAL au dovadă RESOLVED "
    "și provenance verificat independent."
)


# ---------------------------------------------------------------------
# Generic helpers
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_body(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


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


def rows(table: str, filters=None, order="created_at", limit=1000):
    q = supabase.table(table).select("*")
    for key, value in (filters or {}).items():
        if value not in (None, ""):
            q = q.eq(key, value)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


def restore_auth_session(sb) -> None:
    session = st.session_state.get("auth_session")
    if not session:
        return
    access_token = session.get("access_token") if isinstance(session, dict) else getattr(session, "access_token", None)
    refresh_token = session.get("refresh_token") if isinstance(session, dict) else getattr(session, "refresh_token", None)
    if access_token and refresh_token:
        try:
            sb.auth.set_session(access_token, refresh_token)
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


def project_label(p: dict) -> str:
    return f"{p.get('name') or 'Project'} — {str(p.get('id') or '')[:8]}"


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def sha256_text(value: Any):
    text = normalize_text(value)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest() if text else None


def canonical_url(url: Any) -> str:
    return normalize_text(url).split("#", 1)[0].rstrip("/")


# ---------------------------------------------------------------------
# Source / transport verification
# ---------------------------------------------------------------------

def allowed_official_url(url: Any) -> bool:
    host = urlparse(normalize_text(url)).netloc.lower()
    return bool(
        host == "europa.eu"
        or host.endswith(".europa.eu")
        or host == "ec.europa.eu"
        or host.endswith(".ec.europa.eu")
        or host == "commission.europa.eu"
        or host.endswith(".commission.europa.eu")
        or host == "funding-tenders.ec.europa.eu"
        or host.endswith(".funding-tenders.ec.europa.eu")
    )


def auth_or_error_url(url: Any) -> bool:
    u = normalize_text(url).lower()
    blocked = (
        "/cas/",
        "/login",
        "/signin",
        "/sign-in",
        "/auth",
        "/oauth",
        "/sso",
        "authentication",
        "access-denied",
        "access_denied",
        "/logout",
        "/error",
    )
    return (not u) or any(x in u for x in blocked)


def auth_or_error_content(text: Any, title: Any = "") -> bool:
    corpus = " ".join([normalize_text(title), normalize_text(text)]).lower()
    markers = (
        "central authentication service",
        "authentication required",
        "you must authenticate",
        "single sign-on",
        "single sign on",
        "session expired",
        "access denied",
        "403 forbidden",
        "404 not found",
        "page not found",
        "sign in to continue",
        "log in to continue",
    )
    return any(x in corpus for x in markers)


def extract_pdf_text(content: bytes, max_pages=250, max_chars=1_200_000) -> str:
    if not content or PdfReader is None:
        return ""
    try:
        reader = PdfReader(BytesIO(content))
        parts = []
        total = 0
        for idx, page in enumerate(reader.pages):
            if idx >= max_pages:
                break
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text:
                parts.append(f"\n[PDF PAGE {idx + 1}]\n{page_text}")
                total += len(page_text)
            if total >= max_chars:
                break
        return "\n".join(parts)[:max_chars]
    except Exception:
        return ""


def extract_html_text(html: str, max_chars=1_200_000) -> str:
    if not html:
        return ""
    if BeautifulSoup is None:
        clean = re.sub(r"<script.*?</script>", " ", html, flags=re.I | re.S)
        clean = re.sub(r"<style.*?</style>", " ", clean, flags=re.I | re.S)
        clean = re.sub(r"<[^>]+>", " ", clean)
        return normalize_body(clean)[:max_chars]
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        chunks = []
        for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
            txt = " ".join(node.stripped_strings).strip()
            if txt:
                chunks.append(txt)
        if not chunks:
            chunks = list(soup.stripped_strings)
        return "\n".join(chunks)[:max_chars]
    except Exception:
        return ""


def extract_response_text(response):
    ctype = normalize_text(response.headers.get("content-type", "")).lower()
    final_url = normalize_text(getattr(response, "url", ""))

    if "pdf" in ctype or final_url.lower().endswith(".pdf"):
        return extract_pdf_text(response.content or b""), "PDF"

    if "json" in ctype:
        try:
            return json.dumps(response.json(), ensure_ascii=False, default=str)[:1_200_000], "JSON"
        except Exception:
            try:
                return (response.text or "")[:1_200_000], "JSON"
            except Exception:
                return "", "JSON"

    try:
        raw = response.text or ""
    except Exception:
        raw = ""

    if "html" in ctype or "<html" in raw[:5000].lower():
        return extract_html_text(raw), "HTML"

    return raw[:1_200_000], "TEXT"


def fetch_source(url: str, timeout=45) -> dict:
    result = {
        "ok": False,
        "requested_url": normalize_text(url),
        "final_url": normalize_text(url),
        "status": None,
        "content_type": "",
        "content_kind": "UNKNOWN",
        "response_bytes": 0,
        "text": "",
        "redirected": False,
        "error_type": None,
        "error_message": None,
    }

    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "GreenRise/Stage46-v2",
                "Accept": "application/pdf,application/json,text/html,text/plain,*/*",
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "close",
            },
        )

        result["status"] = r.status_code
        result["final_url"] = normalize_text(getattr(r, "url", "")) or normalize_text(url)
        result["content_type"] = normalize_text(r.headers.get("content-type", ""))
        result["response_bytes"] = len(r.content or b"")
        result["redirected"] = canonical_url(result["final_url"]) != canonical_url(url)

        text, kind = extract_response_text(r)
        result["text"] = text
        result["content_kind"] = kind
        result["ok"] = bool(r.ok and text.strip())

        if not r.ok:
            result["error_type"] = "HTTP_ERROR"
            result["error_message"] = f"HTTP {r.status_code}"

    except requests.exceptions.Timeout as exc:
        result["error_type"] = "TIMEOUT"
        result["error_message"] = str(exc)
    except requests.exceptions.SSLError as exc:
        result["error_type"] = "SSL_ERROR"
        result["error_message"] = str(exc)
    except requests.exceptions.ConnectionError as exc:
        result["error_type"] = "CONNECTION_ERROR"
        result["error_message"] = str(exc)
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)

    return result


# ---------------------------------------------------------------------
# Topic / excerpt / chain verification
# ---------------------------------------------------------------------

def topic_tokens(topic_identity: Any):
    raw = normalize_text(topic_identity).upper()
    if not raw:
        return []
    return [p for p in re.split(r"[^A-Z0-9]+", raw) if len(p) >= 2]


def exact_topic_match(text_value: Any, topic_identity: Any) -> bool:
    hay = normalize_text(text_value).upper()
    topic = normalize_text(topic_identity).upper()

    if not hay or not topic:
        return False

    if topic in hay:
        return True

    tokens = [t for t in topic_tokens(topic) if len(t) >= 3]
    if not tokens:
        return False

    hits = sum(1 for t in tokens if t in hay)
    return hits >= max(3, len(tokens) - 1)


def excerpt_in_source(excerpt: Any, source_text: Any) -> bool:
    excerpt_n = normalize_body(excerpt).lower()
    source_n = normalize_body(source_text).lower()

    if not excerpt_n or not source_n:
        return False

    if excerpt_n in source_n:
        return True

    words = excerpt_n.split()
    if len(words) < 16:
        return False

    fragments = []
    size = 12

    for i in range(0, len(words) - size + 1, size):
        frag = " ".join(words[i:i + size]).strip()
        if len(frag) >= 60:
            fragments.append(frag)

    sampled = fragments[:8]
    if not sampled:
        return False

    hits = sum(1 for frag in sampled if frag in source_n)
    required = max(2, (len(sampled) + 1) // 2)
    return hits >= required


def verify_negative_trl_evidence(excerpt: Any, source_text: Any, topic_identity: Any) -> bool:
    """
    Strict Stage 46 verification for Stage 45's explicit negative TRL finding.

    Stage 45 stores a synthetic audit prefix followed by the verbatim bounded
    exact-topic section after 'SECTION:'. The prefix itself cannot occur in the
    EC PDF, so ordinary literal excerpt matching would always reject it.

    This verifier does NOT trust the synthetic conclusion. It independently:
      1) requires the expected negative-TRL contract marker;
      2) extracts only the cited bounded source section;
      3) proves that cited section is present in the freshly fetched source;
      4) proves exact-topic identity in the cited/fresh source;
      5) re-checks that the cited bounded section itself contains no TRL marker.
    """
    raw = normalize_text(excerpt)
    source = normalize_text(source_text)
    if not raw or not source:
        return False

    upper = raw.upper()
    if "NO_TOPIC_SPECIFIC_TRL_STATED" not in upper or "SECTION:" not in upper:
        return False

    # Preserve original case/content while locating the audit delimiter.
    marker_index = upper.find("SECTION:")
    section = raw[marker_index + len("SECTION:"):].strip()
    if len(normalize_body(section)) < 120:
        return False

    # Fresh-source reproduction: only the source-derived section is matched.
    if not excerpt_in_source(section, source):
        return False

    if not exact_topic_match(" ".join([section, source[:150000]]), topic_identity):
        return False

    section_low = f" {normalize_body(section).lower()} "
    trl_markers = (
        "technology readiness level",
        "starting trl",
        "target trl",
        "reach trl",
        " trl ",
    )
    if any(marker in section_low for marker in trl_markers):
        return False

    return True


def chain_urls(chain: Any):
    urls = []

    def walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        else:
            for u in re.findall(r'https?://[^\s"\'<>\]\[(){}]+', normalize_text(obj)):
                clean = u.rstrip(".,;:")
                if clean and clean not in urls:
                    urls.append(clean)

    walk(chain)
    return urls


def chain_verified(chain: Any, topic_identity: Any) -> bool:
    urls = chain_urls(chain)

    if not urls:
        return False

    if not all(allowed_official_url(u) for u in urls):
        return False

    try:
        chain_text = json.dumps(chain, ensure_ascii=False, default=str)
    except Exception:
        chain_text = normalize_text(chain)

    return exact_topic_match(chain_text, topic_identity)


# ---------------------------------------------------------------------
# Stage 45 handoff discovery
# ---------------------------------------------------------------------

def _first_value(row: dict, names: list[str]):
    for name in names:
        value = row.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def _canonical_requirement(value: Any) -> str:
    s = normalize_text(value).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = " ".join(s.split())

    aliases = {
        "applicant eligibility": "applicant eligibility",
        "eligibility": "applicant eligibility",
        "applicant": "applicant eligibility",
        "consortium requirements": "consortium requirements",
        "consortium requirement": "consortium requirements",
        "consortium": "consortium requirements",
        "trl requirements": "trl requirements",
        "trl requirement": "trl requirements",
        "trl": "trl requirements",
        "technology readiness level": "trl requirements",
        "technology readiness level requirements": "trl requirements",
    }
    return aliases.get(s, s)


def requirement_match_key(row: dict) -> list[str]:
    values = [
        normalize_text(row.get("execution_task_id")),
        normalize_text(row.get("requirement_id")),
        normalize_text(row.get("requirement_key")).lower(),
        _canonical_requirement(row.get("requirement_label")),
        _canonical_requirement(row.get("requirement_category")),
        _canonical_requirement(row.get("requirement")),
        _canonical_requirement(row.get("task_label")),
    ]
    return [v for v in values if v]


def _stage45_status(item: dict) -> str:
    return normalize_text(_first_value(item, [
        "worker_status", "resolution_status", "status", "task_status",
        "validation_status", "completion_status", "official_document_status",
    ])).upper()


def _stage45_resolution_method(item: dict) -> str:
    return normalize_text(_first_value(item, [
        "resolution_method", "evidence_method", "verification_method",
        "completion_source", "source_method",
    ])).upper()


def _stage45_url(item: dict) -> str:
    candidates = [
        "final_url", "evidence_url", "official_url", "source_url", "resolved_url",
        "document_url", "requested_url", "evidence_reference", "completion_reference",
        "canonical_url", "final_pdf_url", "official_document_url",
    ]
    for field in candidates:
        value = normalize_text(item.get(field))
        if value.startswith(("http://", "https://")):
            return value

    # Historical/current workers can persist the URL inside JSON payloads.
    for container_name in (
        "completion_payload", "resolution_payload", "validation_payload",
        "evidence_payload", "source_snapshot", "worker_payload", "result_payload",
    ):
        payload = as_dict(item.get(container_name))
        for field in candidates:
            value = normalize_text(payload.get(field))
            if value.startswith(("http://", "https://")):
                return value
    return ""


def _stage45_excerpt(item: dict) -> str:
    fields = [
        "evidence_excerpt", "explicit_evidence_excerpt", "resolved_excerpt",
        "source_excerpt", "document_excerpt", "evidence_text", "passage", "excerpt",
        "official_excerpt", "final_excerpt", "completion_excerpt",
    ]
    value = _first_value(item, fields)
    if value not in (None, ""):
        return normalize_text(value)

    for container_name in (
        "completion_payload", "resolution_payload", "validation_payload",
        "evidence_payload", "source_snapshot", "worker_payload", "result_payload",
    ):
        payload = as_dict(item.get(container_name))
        value = _first_value(payload, fields)
        if value not in (None, ""):
            return normalize_text(value)
    return ""


def _stage45_bool(item: dict, names: list[str]) -> bool:
    for name in names:
        if item.get(name) is True:
            return True
    for container_name in (
        "completion_payload", "resolution_payload", "validation_payload",
        "evidence_payload", "source_snapshot", "worker_payload", "result_payload",
    ):
        payload = as_dict(item.get(container_name))
        for name in names:
            if payload.get(name) is True:
                return True
    return False


def normalize_stage45_item(item: dict) -> dict:
    """Normalize Stage 45 historical/current persistence into the Stage 46 handoff contract."""
    out = dict(item)
    out["worker_status"] = _stage45_status(item)
    out["resolution_method"] = _stage45_resolution_method(item)
    out["evidence_url"] = _stage45_url(item)
    out["evidence_excerpt"] = _stage45_excerpt(item)

    out["exact_topic_verified"] = _stage45_bool(item, [
        "exact_topic_verified", "topic_verified", "exact_topic_match",
    ])
    out["authoritative_source_verified"] = _stage45_bool(item, [
        "authoritative_source_verified", "official_source_verified", "official_host_verified",
    ])
    out["explicit_evidence_verified"] = _stage45_bool(item, [
        "explicit_evidence_verified", "excerpt_verified", "evidence_verified",
    ])

    if not out.get("worker_run_id"):
        out["worker_run_id"] = _first_value(item, [
            "run_id", "execution_run_id", "resolution_run_id"
        ])

    if not out.get("evidence_source"):
        out["evidence_source"] = _first_value(item, [
            "source_type", "official_source", "document_source", "resolution_method"
        ])

    if not out.get("provenance_chain"):
        out["provenance_chain"] = _first_value(item, [
            "reference_chain", "official_reference_chain", "source_chain"
        ]) or []

    return out


def is_stage45_resolved_evidence(item: dict) -> bool:
    item = normalize_stage45_item(item)
    route = normalize_text(item.get("route_type")).upper()
    method = _stage45_resolution_method(item)

    allowed_methods = {
        "OFFICIAL_DOCUMENTATION", "STORED_EVIDENCE", "USER_EVIDENCE", "MANUAL_VERIFICATION"
    }
    if method and method not in allowed_methods:
        return False

    if route and route not in {
        "OFFICIAL_VERIFICATION", "OFFICIAL", "OFFICIAL_EVIDENCE", "EVIDENCE_RESOLUTION",
    } and not (
        item.get("authoritative_source_verified")
        or item.get("explicit_evidence_verified")
        or item.get("exact_topic_verified")
        or method in allowed_methods
    ):
        return False

    resolved = (
        _stage45_status(item) in {"RESOLVED", "COMPLETED", "VERIFIED", "PASS", "PASSED"}
        or bool(item.get("explicit_evidence_verified"))
        or normalize_text(item.get("official_document_status")).upper() in {"RESOLVED", "VERIFIED", "PASS", "PASSED"}
    )
    return bool(resolved and _stage45_url(item) and _stage45_excerpt(item))

def load_stage45_history():
    """
    v2.1 historical recovery:
    1. Read the exact current lock first.
    2. Also read project history, because older Stage 45 versions could persist
       a valid worker item without the current opportunity_lock_id/execution_run_id.
    3. Deduplicate by row id.
    """
    exact = rows(
        "locked_evidence_worker_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        5000,
    )

    project_history = rows(
        "locked_evidence_worker_items",
        {
            "user_id": user_id,
            "project_id": project_id,
        },
        "created_at",
        5000,
    )

    merged = []
    seen = set()
    for raw in exact + project_history:
        rid = normalize_text(raw.get("id")) or json.dumps(raw, sort_keys=True, default=str)
        if rid in seen:
            continue
        seen.add(rid)
        merged.append(normalize_stage45_item(raw))

    return merged


def _candidate_score(task: dict, item: dict) -> int:
    """
    Prefer exact identifiers, then requirement identity, then the current lock.
    A historical row can still win without lock_id if it has a strong
    requirement match and substantive RESOLVED evidence.
    """
    score = 0

    task_id = normalize_text(task.get("id"))
    item_task_id = normalize_text(item.get("execution_task_id"))
    if task_id and item_task_id and task_id == item_task_id:
        score += 100

    task_req_id = normalize_text(task.get("requirement_id"))
    item_req_id = normalize_text(item.get("requirement_id"))
    if task_req_id and item_req_id and task_req_id == item_req_id:
        score += 80

    task_req_key = normalize_text(task.get("requirement_key")).lower()
    item_req_key = normalize_text(item.get("requirement_key")).lower()
    if task_req_key and item_req_key and task_req_key == item_req_key:
        score += 70

    task_label = _canonical_requirement(task.get("requirement_label"))
    item_labels = {
        _canonical_requirement(item.get("requirement_label")),
        _canonical_requirement(item.get("requirement_category")),
        _canonical_requirement(item.get("requirement")),
        _canonical_requirement(item.get("task_label")),
    }
    item_labels.discard("")
    if task_label and task_label in item_labels:
        score += 60

    if normalize_text(item.get("opportunity_lock_id")) == normalize_text(lock_id):
        score += 25

    if normalize_text(item.get("execution_run_id")) == normalize_text(execution_run_id):
        score += 15

    item_identity = normalize_text(_first_value(item, [
        "opportunity_identity", "topic_identity", "topic_id", "opportunity_id"
    ]))
    if item_identity and normalize_text(identity) and item_identity.lower() == normalize_text(identity).lower():
        score += 20

    if bool(item.get("exact_topic_verified")):
        score += 5
    if bool(item.get("authoritative_source_verified")):
        score += 5
    if bool(item.get("explicit_evidence_verified")):
        score += 10

    return score


def find_best_stage45_item(task: dict, history: list[dict]):
    """
    Stage 46 v2.6 — strict safe-canonical Stage 45 selector.

    Selection rules:
    - same requirement only;
    - Stage 45 must already qualify as resolved evidence;
    - candidate URL must be on an allowed official EC/EU host;
    - CAS/login/auth/error infrastructure is NEVER selectable;
    - prefer strict Stage 45 flags and OFFICIAL_DOCUMENTATION;
    - prefer the current lock, then newest row.

    Stage 46 still independently GETs and validates the selected source.
    """
    strict = []
    compatible = []

    task_req_id = normalize_text(task.get("requirement_id"))
    task_req_key = normalize_text(task.get("requirement_key")).lower()
    task_label = _canonical_requirement(task.get("requirement_label"))

    for raw in history:
        item = normalize_stage45_item(raw)

        item_req_id = normalize_text(item.get("requirement_id"))
        item_req_key = normalize_text(item.get("requirement_key")).lower()
        item_labels = {
            _canonical_requirement(item.get("requirement_label")),
            _canonical_requirement(item.get("requirement_category")),
            _canonical_requirement(item.get("requirement")),
            _canonical_requirement(item.get("task_label")),
        }
        item_labels.discard("")

        requirement_match = bool(
            (task_req_id and item_req_id and task_req_id == item_req_id)
            or (task_req_key and item_req_key and task_req_key == item_req_key)
            or (task_label and task_label in item_labels)
        )
        if not requirement_match:
            continue

        if not is_stage45_resolved_evidence(item):
            continue

        candidate_url = _stage45_url(item)

        # Critical v2.5 invariant: auth/CAS/error infrastructure can never
        # become the selected Stage 45 handoff, regardless of run/task match.
        if not candidate_url:
            continue
        if not allowed_official_url(candidate_url):
            continue
        if auth_or_error_url(candidate_url):
            continue

        method = _stage45_resolution_method(item)
        canonical_flags = bool(
            item.get("exact_topic_verified") is True
            and item.get("authoritative_source_verified") is True
            and item.get("explicit_evidence_verified") is True
        )

        # Current Stage 45 canonical contract for official evidence.
        canonical_method = method == "OFFICIAL_DOCUMENTATION"

        # Strengthen obvious official PDF handoffs, but do not require .pdf
        # because Stage 46 can also independently validate substantive HTML.
        parsed = urlparse(candidate_url)
        is_pdf = normalize_text(parsed.path).lower().endswith(".pdf")

        item["_s46_v25_candidate_url"] = candidate_url
        item["_s46_v25_is_pdf"] = is_pdf
        item["_s46_v25_canonical_flags"] = canonical_flags
        item["_s46_v25_canonical_method"] = canonical_method

        if canonical_flags and canonical_method:
            strict.append(item)
        elif canonical_flags or canonical_method:
            compatible.append(item)

    def rank(item: dict):
        # Evidence quality outranks run affinity.
        strict_flags = int(bool(item.get("_s46_v25_canonical_flags")))
        official_method = int(bool(item.get("_s46_v25_canonical_method")))
        is_pdf = int(bool(item.get("_s46_v25_is_pdf")))
        current_lock = int(
            normalize_text(item.get("opportunity_lock_id")) == normalize_text(lock_id)
        )
        current_run = int(
            normalize_text(item.get("execution_run_id")) == normalize_text(execution_run_id)
        )
        created_at = normalize_text(item.get("created_at"))
        updated_at = normalize_text(item.get("updated_at"))

        return (
            strict_flags,
            official_method,
            is_pdf,
            current_lock,
            current_run,
            updated_at,
            created_at,
        )

    if strict:
        return sorted(strict, key=rank, reverse=True)[0]

    if compatible:
        return sorted(compatible, key=rank, reverse=True)[0]

    return None


# ---------------------------------------------------------------------
# Dedicated Stage 46 persistence
# ---------------------------------------------------------------------

def create_provenance_run(total_requirements: int, source_items_found: int):
    data = (
        supabase.table("locked_evidence_provenance_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "stage": 46,
            "validator_version": "stage46-v2.6",
            "run_status": "RUNNING",
            "total_requirements": total_requirements,
            "source_worker_items_found": source_items_found,
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "summary": {
                "stage": 46,
                "version": "v2.6",
            },
            "diagnostics": [],
            "started_at": now_iso(),
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not create locked_evidence_provenance_runs row.")

    return data[0]


def create_provenance_item(run_id: str, task: dict, stage45_item: dict | None):
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
        "provenance_run_id": run_id,
        "execution_task_id": task.get("id"),
        "requirement_id": task.get("requirement_id"),
        "requirement_key": task.get("requirement_key"),
        "requirement_category": task.get("requirement_category"),
        "requirement_label": task.get("requirement_label"),
        "opportunity_identity": identity,
        "source_stage": 45,
        "validation_stage": 46,
        "validation_status": "VALIDATING" if stage45_item else "WAITING",
        "updated_at": now_iso(),
    }

    if stage45_item:
        payload.update({
            "stage45_worker_run_id": stage45_item.get("worker_run_id"),
            "stage45_worker_item_id": stage45_item.get("id"),
            "stage45_worker_status": stage45_item.get("worker_status"),
            "stage45_evidence_source": stage45_item.get("evidence_source"),
            "requested_url": stage45_item.get("evidence_url"),
            "evidence_reference": stage45_item.get("evidence_reference"),
            "evidence_excerpt": stage45_item.get("evidence_excerpt"),
            "provenance_chain": stage45_item.get("provenance_chain") or [],
            "source_snapshot": {
                "stage45_worker_item_id": stage45_item.get("id"),
                "worker_status": stage45_item.get("worker_status"),
                "evidence_source": stage45_item.get("evidence_source"),
                "evidence_url": stage45_item.get("evidence_url"),
                "evidence_reference": stage45_item.get("evidence_reference"),
                "exact_topic_verified": stage45_item.get("exact_topic_verified"),
                "authoritative_source_verified": stage45_item.get("authoritative_source_verified"),
                "explicit_evidence_verified": stage45_item.get("explicit_evidence_verified"),
            },
        })
    else:
        payload.update({
            "validation_reason": "No Stage 45 RESOLVED evidence found for this requirement.",
            "next_action": "Return requirement to Stage 45 official evidence resolution.",
            "checked_at": now_iso(),
        })

    data = (
        supabase.table("locked_evidence_provenance_items")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not create locked_evidence_provenance_items row.")

    return data[0]


def save_provenance_source(
    run_id: str,
    provenance_item_id: str,
    task: dict,
    stage45_item: dict,
    fetched: dict,
    checks: dict,
):
    fetch_status = (
        "TRANSPORT_FAILED"
        if not fetched.get("ok")
        else "REJECTED"
        if checks.get("validation_status") == "REJECTED"
        else "VERIFIED"
        if checks.get("validation_status") == "VERIFIED"
        else "FETCHED"
    )

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "provenance_run_id": run_id,
        "provenance_item_id": provenance_item_id,
        "execution_task_id": task.get("id"),
        "stage45_worker_item_id": stage45_item.get("id"),
        "opportunity_identity": identity,
        "requested_url": stage45_item.get("evidence_url"),
        "final_url": fetched.get("final_url"),
        "source_title": stage45_item.get("source_title"),
        "http_status": fetched.get("status"),
        "content_type": fetched.get("content_type"),
        "content_kind": fetched.get("content_kind"),
        "redirected": bool(fetched.get("redirected")),
        "official_host": bool(checks.get("official_final_host_verified")),
        "auth_or_error_url": bool(checks.get("auth_or_error_url_detected")),
        "auth_or_error_content": bool(checks.get("auth_or_error_content_detected")),
        "fetch_status": fetch_status,
        "response_bytes": int(fetched.get("response_bytes") or 0),
        "document_sha256": checks.get("document_sha256"),
        "excerpt_sha256": checks.get("excerpt_sha256"),
        "evidence_excerpt": stage45_item.get("evidence_excerpt"),
        "excerpt_verified": bool(checks.get("excerpt_present_in_source")),
        "topic_verified": bool(checks.get("exact_topic_in_source")),
        "provenance_chain": stage45_item.get("provenance_chain") or [],
        "fetch_payload": {
            "version": "stage46-v2.6",
            "checks": checks,
        },
        "error_type": fetched.get("error_type"),
        "error_message": fetched.get("error_message"),
        "retrieved_at": now_iso(),
        "updated_at": now_iso(),
    }

    supabase.table("locked_evidence_provenance_sources").insert(payload).execute()


def update_provenance_item(item_id: str, verdict: dict):
    status = verdict.get("validation_status")

    payload = {
        "validation_status": status,
        "final_url": verdict.get("final_url"),
        "http_status": verdict.get("http_status"),
        "content_type": verdict.get("content_type"),
        "content_kind": verdict.get("content_kind"),
        "redirected": bool(verdict.get("redirected")),
        "official_final_host_verified": bool(verdict.get("official_final_host_verified")),
        "auth_or_error_url_detected": bool(verdict.get("auth_or_error_url_detected")),
        "auth_or_error_content_detected": bool(verdict.get("auth_or_error_content_detected")),
        "excerpt_present_in_source": bool(verdict.get("excerpt_present_in_source")),
        "exact_topic_in_source": bool(verdict.get("exact_topic_in_source")),
        "provenance_chain_verified": bool(verdict.get("provenance_chain_verified")),
        "substantive_source_verified": bool(verdict.get("substantive_source_verified")),
        "explicit_evidence_verified": bool(verdict.get("explicit_evidence_verified")),
        "document_sha256": verdict.get("document_sha256"),
        "excerpt_sha256": verdict.get("excerpt_sha256"),
        "validation_payload": verdict,
        "rejection_reason": verdict.get("rejection_reason"),
        "validation_reason": verdict.get("validation_reason"),
        "next_action": verdict.get("next_action"),
        "checked_at": now_iso(),
        "updated_at": now_iso(),
    }

    supabase.table("locked_evidence_provenance_items").update(
        payload
    ).eq("id", item_id).eq("user_id", user_id).execute()


# ---------------------------------------------------------------------
# Independent Stage 46 verdict
# ---------------------------------------------------------------------

def validate_stage45_evidence(stage45_item: dict):
    requested_url = normalize_text(stage45_item.get("evidence_url"))
    excerpt = normalize_text(stage45_item.get("evidence_excerpt"))
    chain = stage45_item.get("provenance_chain") or []
    source_title = normalize_text(stage45_item.get("source_title"))

    fetched = fetch_source(requested_url)

    verdict = {
        "validation_status": "REJECTED",
        "requested_url": requested_url,
        "final_url": fetched.get("final_url"),
        "http_status": fetched.get("status"),
        "content_type": fetched.get("content_type"),
        "content_kind": fetched.get("content_kind"),
        "redirected": bool(fetched.get("redirected")),
        "official_final_host_verified": False,
        "auth_or_error_url_detected": False,
        "auth_or_error_content_detected": False,
        "excerpt_present_in_source": False,
        "exact_topic_in_source": False,
        "provenance_chain_verified": False,
        "substantive_source_verified": False,
        "explicit_evidence_verified": False,
        "document_sha256": None,
        "excerpt_sha256": sha256_text(normalize_body(excerpt)),
        "rejection_reason": None,
        "validation_reason": None,
        "next_action": None,
    }

    if not requested_url or not excerpt:
        verdict["rejection_reason"] = "Missing evidence URL or evidence excerpt."
        verdict["next_action"] = "Return to Stage 45."
        return verdict, fetched

    if not fetched.get("ok"):
        verdict["validation_status"] = "WAITING"
        verdict["validation_reason"] = (
            "Fresh-source retrieval failed; evidence cannot be accepted or rejected factually."
        )
        verdict["next_action"] = "Retry provenance validation or resolve transport."
        return verdict, fetched

    final_url = normalize_text(fetched.get("final_url"))
    source_text = fetched.get("text") or ""

    verdict["official_final_host_verified"] = allowed_official_url(final_url)
    verdict["auth_or_error_url_detected"] = auth_or_error_url(final_url)
    verdict["auth_or_error_content_detected"] = auth_or_error_content(
        source_text,
        source_title,
    )
    evidence_reference = normalize_text(stage45_item.get("evidence_reference")).upper()

    if evidence_reference == "TRL_NOT_SPECIFIED_IN_EXACT_TOPIC":
        verdict["excerpt_present_in_source"] = verify_negative_trl_evidence(
            excerpt,
            source_text,
            identity,
        )
    else:
        verdict["excerpt_present_in_source"] = excerpt_in_source(
            excerpt,
            source_text,
        )
    verdict["exact_topic_in_source"] = exact_topic_match(
        " ".join([final_url, source_title, source_text[:150000]]),
        identity,
    )
    verdict["provenance_chain_verified"] = chain_verified(
        chain,
        identity,
    )
    verdict["document_sha256"] = sha256_text(normalize_body(source_text))

    verdict["substantive_source_verified"] = bool(
        verdict["official_final_host_verified"]
        and not verdict["auth_or_error_url_detected"]
        and not verdict["auth_or_error_content_detected"]
    )

    verdict["explicit_evidence_verified"] = bool(
        verdict["substantive_source_verified"]
        and verdict["excerpt_present_in_source"]
        and (
            verdict["exact_topic_in_source"]
            or verdict["provenance_chain_verified"]
        )
    )

    if not verdict["official_final_host_verified"]:
        verdict["rejection_reason"] = "Final URL is not an allowed official EC/EU host."
    elif verdict["auth_or_error_url_detected"]:
        verdict["rejection_reason"] = "Final URL is authentication/error infrastructure."
    elif verdict["auth_or_error_content_detected"]:
        verdict["rejection_reason"] = "Fetched content is authentication/error content."
    elif not verdict["excerpt_present_in_source"]:
        verdict["rejection_reason"] = "Cited excerpt was not found in the freshly fetched source."
    elif not (
        verdict["exact_topic_in_source"]
        or verdict["provenance_chain_verified"]
    ):
        verdict["rejection_reason"] = "Exact-topic applicability is not proven."
    else:
        verdict["validation_status"] = "VERIFIED"
        verdict["validation_reason"] = (
            "Official substantive source verified; cited excerpt exists in the fresh source; "
            "exact-topic applicability is established directly or through the official provenance chain."
        )
        verdict["next_action"] = "ALLOW_DOWNSTREAM"
        return verdict, fetched

    verdict["next_action"] = "RETURN_TO_STAGE_45"
    return verdict, fetched


# ---------------------------------------------------------------------
# Update only execution task after Stage 46 verdict.
# Stage 45 audit is intentionally preserved.
# ---------------------------------------------------------------------

def apply_verified_to_execution_task(task: dict, provenance_item_id: str, verdict: dict):
    completion_payload = as_dict(task.get("completion_payload"))
    completion_payload["stage46_provenance_validation"] = {
        "provenance_item_id": provenance_item_id,
        **verdict,
    }

    supabase.table("locked_evidence_execution_tasks").update({
        "completion_status": "VERIFIED",
        "completion_payload": completion_payload,
        "updated_at": now_iso(),
    }).eq("id", task["id"]).eq("user_id", user_id).execute()


def apply_rejected_to_execution_task(task: dict, provenance_item_id: str, verdict: dict):
    supabase.table("locked_evidence_execution_tasks").update({
        "task_status": "WAITING_OFFICIAL",
        "completion_status": None,
        "completion_source": None,
        "completion_reference": None,
        "completed_at": None,
        "completion_payload": {
            "stage": 46,
            "status": "WAITING_OFFICIAL",
            "stage46_provenance_item_id": provenance_item_id,
            "stage46_provenance_validation": verdict,
        },
        "updated_at": now_iso(),
    }).eq("id", task["id"]).eq("user_id", user_id).execute()


# ---------------------------------------------------------------------
# Init / gate
# ---------------------------------------------------------------------

supabase = get_supabase()
restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Nu am putut identifica utilizatorul autentificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage46_v2_project",
)

project = project_map[selected_project]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {
        "user_id": user_id,
        "project_id": project_id,
        "lock_status": "ACTIVE",
    },
    "created_at",
    10,
)

if not locks:
    st.warning("Nu există lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

execution_runs = rows(
    "locked_evidence_execution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

execution_run = next(
    (
        r for r in execution_runs
        if normalize_text(r.get("execution_status")).upper()
        in {"WAITING", "DISPATCHED", "COMPLETED"}
    ),
    None,
)

if not execution_run:
    st.warning("Nu există execution run disponibil.")
    st.stop()

execution_run_id = str(execution_run["id"])

execution_tasks = rows(
    "locked_evidence_execution_tasks",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
    },
    "created_at",
    500,
)

official_tasks = [
    t for t in execution_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
]

hard_gate = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(official_tasks)
)

if not hard_gate:
    st.error("Etapa 46 v2 este BLOCKED de hard gate.")
    st.stop()

st.success("Hard gate Etapa 46 v2.6: PASS.")
st.write(f"**Locked opportunity:** {identity}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")


# ---------------------------------------------------------------------
# Handoff preview
# ---------------------------------------------------------------------

stage45_history = load_stage45_history()

handoff = []
for task in official_tasks:
    item = find_best_stage45_item(task, stage45_history)
    handoff.append({
        "task": task,
        "stage45_item": item,
    })

st.subheader("Stage 45 → Stage 46 v2.6 handoff")

st.dataframe(
    [
        {
            "Requirement": x["task"].get("requirement_label"),
            "Task status": x["task"].get("task_status"),
            "Historical RESOLVED evidence": bool(x["stage45_item"]),
            "Strict canonical": bool(
                x["stage45_item"]
                and _stage45_status(x["stage45_item"]) == "RESOLVED"
                and x["stage45_item"].get("exact_topic_verified") is True
                and x["stage45_item"].get("authoritative_source_verified") is True
                and x["stage45_item"].get("explicit_evidence_verified") is True
                or (
                    x["stage45_item"]
                    and _stage45_resolution_method(x["stage45_item"]) in {
                        "OFFICIAL_DOCUMENTATION", "STORED_EVIDENCE", "USER_EVIDENCE", "MANUAL_VERIFICATION"
                    }
                    and is_stage45_resolved_evidence(x["stage45_item"])
                )
            ),
            "Worker item": x["stage45_item"].get("id") if x["stage45_item"] else None,
            "Safe URL": bool(
                x["stage45_item"]
                and _stage45_url(x["stage45_item"])
                and allowed_official_url(_stage45_url(x["stage45_item"]))
                and not auth_or_error_url(_stage45_url(x["stage45_item"]))
            ),
            "Evidence URL": _stage45_url(x["stage45_item"]) if x["stage45_item"] else None,
        }
        for x in handoff
    ],
    use_container_width=True,
    hide_index=True,
)

h1, h2, h3, h4 = st.columns(4)
h1.metric("OFFICIAL requirements", len(official_tasks))
h2.metric("Historical RESOLVED found", sum(1 for x in handoff if x["stage45_item"]))
h3.metric("Missing RESOLVED evidence", sum(1 for x in handoff if not x["stage45_item"]))
h4.metric("Stage 45 history rows", len(stage45_history))


# ---------------------------------------------------------------------
# Execute Stage 46 v2.5
# ---------------------------------------------------------------------

if st.button(
    "🛡️ Run Stage 46 v2.6 provenance validation",
    type="primary",
    use_container_width=True,
    key="stage46_v2_6_run",
):
    source_items_found = sum(1 for x in handoff if x["stage45_item"])

    run = create_provenance_run(
        total_requirements=len(official_tasks),
        source_items_found=source_items_found,
    )
    run_id = str(run["id"])

    verified = rejected = waiting = failed = documents_revalidated = 0
    diagnostics = []

    progress = st.progress(0)

    for idx, entry in enumerate(handoff, 1):
        task = entry["task"]
        stage45_item = entry["stage45_item"]

        try:
            provenance_item = create_provenance_item(
                run_id,
                task,
                stage45_item,
            )
            provenance_item_id = str(provenance_item["id"])

            if not stage45_item:
                waiting += 1
                progress.progress(idx / len(handoff))
                continue

            verdict, fetched = validate_stage45_evidence(stage45_item)
            documents_revalidated += int(bool(fetched.get("requested_url")))

            save_provenance_source(
                run_id,
                provenance_item_id,
                task,
                stage45_item,
                fetched,
                verdict,
            )

            update_provenance_item(
                provenance_item_id,
                verdict,
            )

            if verdict.get("validation_status") == "VERIFIED":
                verified += 1
                apply_verified_to_execution_task(
                    task,
                    provenance_item_id,
                    verdict,
                )

            elif verdict.get("validation_status") == "WAITING":
                waiting += 1

            else:
                rejected += 1
                apply_rejected_to_execution_task(
                    task,
                    provenance_item_id,
                    verdict,
                )

        except Exception as exc:
            failed += 1
            diagnostics.append({
                "requirement": task.get("requirement_label"),
                "type": type(exc).__name__,
                "message": str(exc),
                "time": now_iso(),
            })

        progress.progress(idx / len(handoff))

    gate_pass = (
        len(official_tasks) > 0
        and verified == len(official_tasks)
        and rejected == 0
        and waiting == 0
        and failed == 0
    )

    run_status = (
        "PASS"
        if gate_pass
        else "FAILED"
        if failed and verified == 0 and rejected == 0 and waiting == 0
        else "PARTIAL_FAILURE"
        if failed
        else "WAITING"
    )

    supabase.table("locked_evidence_provenance_runs").update({
        "run_status": run_status,
        "verified_requirements": verified,
        "rejected_requirements": rejected,
        "waiting_requirements": waiting,
        "failed_requirements": failed,
        "source_worker_items_found": source_items_found,
        "documents_revalidated": documents_revalidated,
        "diagnostic_status": (
            "FAILED"
            if run_status == "FAILED"
            else "PARTIAL_FAILURE"
            if run_status == "PARTIAL_FAILURE"
            else "CLEAN"
        ),
        "error_count": failed,
        "summary": {
            "stage": 46,
            "version": "v2.6",
            "gate": run_status,
            "verified": verified,
            "rejected": rejected,
            "waiting": waiting,
            "failed": failed,
            "source_worker_items_found": source_items_found,
            "documents_revalidated": documents_revalidated,
        },
        "diagnostics": diagnostics,
        "completed_at": now_iso() if run_status in {"PASS", "FAILED"} else None,
        "updated_at": now_iso(),
    }).eq("id", run_id).eq("user_id", user_id).execute()

    if run_status == "PASS":
        st.success("Etapa 46 v2.6: PASS.")
    elif run_status == "WAITING":
        st.warning(
            f"Etapa 46 v2.6: WAITING — Verified {verified}, Rejected {rejected}, "
            f"Waiting {waiting}, Failed {failed}."
        )
    else:
        st.error(
            f"Etapa 46 v2.6: {run_status} — Verified {verified}, Rejected {rejected}, "
            f"Waiting {waiting}, Failed {failed}."
        )

    st.rerun()


# ---------------------------------------------------------------------
# Latest Stage 46 dedicated result
# ---------------------------------------------------------------------

provenance_runs = rows(
    "locked_evidence_provenance_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

if provenance_runs:
    latest = provenance_runs[0]
    latest_run_id = str(latest["id"])

    st.divider()
    st.subheader("Latest Stage 46 v2.6 Result")

    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Gate", latest.get("run_status") or "—")
    p2.metric("Verified", latest.get("verified_requirements") or 0)
    p3.metric("Rejected", latest.get("rejected_requirements") or 0)
    p4.metric("Waiting", latest.get("waiting_requirements") or 0)
    p5.metric("Failed", latest.get("failed_requirements") or 0)

    items = rows(
        "locked_evidence_provenance_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "provenance_run_id": latest_run_id,
        },
        "created_at",
        500,
    )

    if items:
        st.subheader("Requirement provenance verdicts")

        st.dataframe(
            [
                {
                    "Requirement": i.get("requirement_label"),
                    "Stage 45 status": i.get("stage45_worker_status"),
                    "Stage 46 verdict": i.get("validation_status"),
                    "Official host": i.get("official_final_host_verified"),
                    "Auth/error URL": i.get("auth_or_error_url_detected"),
                    "Auth/error content": i.get("auth_or_error_content_detected"),
                    "Excerpt verified": i.get("excerpt_present_in_source"),
                    "Exact topic": i.get("exact_topic_in_source"),
                    "Chain verified": i.get("provenance_chain_verified"),
                    "Explicit evidence": i.get("explicit_evidence_verified"),
                    "Final URL": i.get("final_url"),
                    "Reason": i.get("validation_reason") or i.get("rejection_reason"),
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

    sources = rows(
        "locked_evidence_provenance_sources",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "provenance_run_id": latest_run_id,
        },
        "created_at",
        500,
    )

    with st.expander("Stage 46 source audit", expanded=False):
        if sources:
            st.dataframe(
                [
                    {
                        "Fetch status": s.get("fetch_status"),
                        "HTTP": s.get("http_status"),
                        "Requested URL": s.get("requested_url"),
                        "Final URL": s.get("final_url"),
                        "Content type": s.get("content_type"),
                        "Official host": s.get("official_host"),
                        "Auth/error URL": s.get("auth_or_error_url"),
                        "Excerpt verified": s.get("excerpt_verified"),
                        "Topic verified": s.get("topic_verified"),
                        "Document SHA256": s.get("document_sha256"),
                        "Excerpt SHA256": s.get("excerpt_sha256"),
                        "Error": s.get("error_message"),
                    }
                    for s in sources
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Nu există source audit pentru ultimul run.")

    if normalize_text(latest.get("run_status")).upper() == "PASS":
        st.success("Etapa 46 este PASS și poate preda controlul Etapei 47.")
    else:
        st.warning(
            "Etapa 46 nu este încă PASS. Cerințele WAITING/REJECTED trebuie rezolvate "
            "în Stage 45 înainte de etapa următoare."
        )


st.caption(
    "Invariantă Etapa 46 v2.6: Stage 45 furnizează doar candidate evidence, dar verdictul provenance "
    "este păstrat separat. PASS necesită VERIFIED pentru fiecare requirement OFFICIAL."
)
# =====================================================================
# END STAGE 46 v2
# =====================================================================
