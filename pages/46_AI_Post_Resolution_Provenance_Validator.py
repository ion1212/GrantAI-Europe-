
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
# STAGE 46 — POST-RESOLUTION PROVENANCE VALIDATOR
# =====================================================================

st.set_page_config(
    page_title="Stage 46 — Post-Resolution Provenance Validator",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Etapa 46 — AI Post-Resolution Provenance Validator")
st.caption(
    "Validează independent dovezile RESOLVED produse în Etapa 45. "
    "Etapa 46 nu inventează reguli și nu transformă WAITING în RESOLVED. "
    "PASS este permis numai pentru dovezi oficiale explicite, verificabile și trasabile."
)


# ---------------------------------------------------------------------
# Config / auth / helpers
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


def normalize_body(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def canonical_url(url: Any) -> str:
    return normalize_text(url).split("#", 1)[0].rstrip("/")


# ---------------------------------------------------------------------
# Official source guards
# ---------------------------------------------------------------------

def is_allowed_official_url(url: Any) -> bool:
    host = urlparse(normalize_text(url)).netloc.lower()

    return bool(
        host == "ec.europa.eu"
        or host.endswith(".ec.europa.eu")
        or host == "commission.europa.eu"
        or host.endswith(".commission.europa.eu")
        or host == "funding-tenders.ec.europa.eu"
        or host.endswith(".funding-tenders.ec.europa.eu")
        or host == "europa.eu"
        or host.endswith(".europa.eu")
    )


def is_auth_or_error_url(url: Any) -> bool:
    u = normalize_text(url).lower()
    if not u:
        return True

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

    return any(x in u for x in blocked)


def is_auth_or_error_content(text: Any, title: Any = "") -> bool:
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


def extract_source_text(response) -> tuple[str, str]:
    ctype = normalize_text(response.headers.get("content-type", "")).lower()
    final_url = normalize_text(getattr(response, "url", ""))

    if "pdf" in ctype or final_url.lower().endswith(".pdf"):
        return extract_pdf_text(response.content or b""), "PDF"

    if "json" in ctype:
        try:
            payload = response.json()
            return json.dumps(payload, ensure_ascii=False, default=str)[:1_200_000], "JSON"
        except Exception:
            return normalize_text(response.text)[:1_200_000], "JSON"

    raw_text = ""
    try:
        raw_text = response.text or ""
    except Exception:
        pass

    if "html" in ctype or "<html" in raw_text[:5000].lower():
        return extract_html_text(raw_text), "HTML"

    return raw_text[:1_200_000], "TEXT"


def fetch_source(url: str, timeout=45) -> dict:
    result = {
        "ok": False,
        "requested_url": normalize_text(url),
        "final_url": normalize_text(url),
        "status": None,
        "content_type": "",
        "content_kind": "UNKNOWN",
        "text": "",
        "error": "",
        "redirected": False,
    }

    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "GreenRise/Stage46-ProvenanceValidator",
                "Accept": "application/pdf,application/json,text/html,text/plain,*/*",
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "close",
            },
        )

        result["status"] = r.status_code
        result["final_url"] = normalize_text(getattr(r, "url", "")) or normalize_text(url)
        result["content_type"] = normalize_text(r.headers.get("content-type", ""))
        result["redirected"] = canonical_url(result["final_url"]) != canonical_url(url)

        text, kind = extract_source_text(r)
        result["text"] = text
        result["content_kind"] = kind
        result["ok"] = bool(r.ok and text.strip())

        if not r.ok:
            result["error"] = f"HTTP {r.status_code}"

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)}"

    return result


# ---------------------------------------------------------------------
# Topic identity / excerpt verification
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


def excerpt_present_in_source(excerpt: Any, source_text: Any) -> bool:
    excerpt_n = normalize_body(excerpt).lower()
    source_n = normalize_body(source_text).lower()

    if not excerpt_n or not source_n:
        return False

    if excerpt_n in source_n:
        return True

    words = excerpt_n.split()

    if len(words) < 16:
        return False

    size = 12
    fragments = []

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


def provenance_chain_urls(chain: Any):
    if not chain:
        return []

    urls = []

    def walk(obj):
        if isinstance(obj, dict):
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
        else:
            for u in re.findall(r'https?://[^\s"\'<>\]\[(){}]+', normalize_text(obj)):
                clean = u.rstrip(".,;:")
                if clean and clean not in urls:
                    urls.append(clean)

    walk(chain)

    return urls


def traceable_topic_chain(chain: Any, topic_identity: Any) -> bool:
    urls = provenance_chain_urls(chain)

    if not urls:
        return False

    if not all(is_allowed_official_url(u) for u in urls):
        return False

    try:
        chain_text = json.dumps(chain, ensure_ascii=False, default=str)
    except Exception:
        chain_text = normalize_text(chain)

    return exact_topic_match(chain_text, topic_identity)


# ---------------------------------------------------------------------
# Stage 45 result discovery
# ---------------------------------------------------------------------

def requirement_identity(item: dict) -> str:
    return (
        normalize_text(item.get("execution_task_id"))
        or normalize_text(item.get("requirement_id"))
        or normalize_text(item.get("requirement_key"))
        or normalize_text(item.get("requirement_label"))
    )


def load_stage45_history() -> list[dict]:
    return rows(
        "locked_evidence_worker_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        3000,
    )


def load_best_resolved_stage45_items(history: list[dict]) -> list[dict]:
    """
    Search the whole Stage 45 history, not only the newest row.
    Keep the newest substantive RESOLVED row per execution task/requirement.
    """
    selected = {}

    for item in history:
        route = normalize_text(item.get("route_type")).upper()
        status = normalize_text(item.get("worker_status")).upper()

        if route != "OFFICIAL_VERIFICATION":
            continue

        if status != "RESOLVED" and not bool(item.get("explicit_evidence_verified")):
            continue

        if not normalize_text(item.get("evidence_url")):
            continue

        if not normalize_text(item.get("evidence_excerpt")):
            continue

        key = requirement_identity(item)

        if key and key not in selected:
            selected[key] = item

    return list(selected.values())


def current_official_tasks() -> list[dict]:
    return [
        t for t in execution_tasks
        if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    ]


# ---------------------------------------------------------------------
# Independent provenance verdict
# ---------------------------------------------------------------------

def validate_stage45_item(item: dict) -> dict:
    url = normalize_text(item.get("evidence_url"))
    excerpt = normalize_text(item.get("evidence_excerpt"))
    source_title = normalize_text(item.get("source_title"))
    chain = item.get("provenance_chain")

    verdict = {
        "stage": 46,
        "version": "v1",
        "worker_item_id": item.get("id"),
        "execution_task_id": item.get("execution_task_id"),
        "requirement_id": item.get("requirement_id"),
        "requirement_key": item.get("requirement_key"),
        "requirement_label": item.get("requirement_label"),
        "status": "REJECTED",
        "requested_url": url,
        "final_url": None,
        "http_status": None,
        "content_type": None,
        "content_kind": None,
        "redirected": False,
        "official_final_host": False,
        "auth_or_error_url": False,
        "auth_or_error_content": False,
        "excerpt_present_in_source": False,
        "exact_topic_in_source": False,
        "traceable_topic_chain": False,
        "document_sha256": None,
        "excerpt_sha256": sha256_text(normalize_body(excerpt)),
        "reason": "",
        "checked_at": now_iso(),
    }

    if not url or not excerpt:
        verdict["reason"] = "Missing evidence URL or evidence excerpt."
        return verdict

    fetched = fetch_source(url)

    verdict["final_url"] = fetched.get("final_url")
    verdict["http_status"] = fetched.get("status")
    verdict["content_type"] = fetched.get("content_type")
    verdict["content_kind"] = fetched.get("content_kind")
    verdict["redirected"] = bool(fetched.get("redirected"))

    if not fetched.get("ok"):
        verdict["reason"] = (
            "Evidence source could not be freshly fetched: "
            + normalize_text(fetched.get("error"))
        )
        return verdict

    final_url = normalize_text(fetched.get("final_url"))
    source_text = fetched.get("text") or ""

    verdict["official_final_host"] = is_allowed_official_url(final_url)
    verdict["auth_or_error_url"] = is_auth_or_error_url(final_url)
    verdict["auth_or_error_content"] = is_auth_or_error_content(
        source_text,
        source_title,
    )
    verdict["excerpt_present_in_source"] = excerpt_present_in_source(
        excerpt,
        source_text,
    )
    verdict["exact_topic_in_source"] = exact_topic_match(
        " ".join([
            final_url,
            source_title,
            source_text[:150000],
        ]),
        identity,
    )
    verdict["traceable_topic_chain"] = traceable_topic_chain(
        chain,
        identity,
    )
    verdict["document_sha256"] = sha256_text(normalize_body(source_text))

    if not verdict["official_final_host"]:
        verdict["reason"] = "Final evidence URL is not on an allowed official EC/EU host."
        return verdict

    if verdict["auth_or_error_url"]:
        verdict["reason"] = "Final URL is CAS/login/auth/error infrastructure, not substantive evidence."
        return verdict

    if verdict["auth_or_error_content"]:
        verdict["reason"] = "Fetched source is authentication/error content, not substantive evidence."
        return verdict

    if not verdict["excerpt_present_in_source"]:
        verdict["reason"] = "Evidence excerpt cannot be verified in the freshly fetched source."
        return verdict

    if not (
        verdict["exact_topic_in_source"]
        or verdict["traceable_topic_chain"]
    ):
        verdict["reason"] = "Exact-topic applicability is not proven by source or provenance chain."
        return verdict

    verdict["status"] = "VERIFIED"
    verdict["reason"] = (
        "Fresh-source provenance verified: official final host, substantive source, "
        "cited excerpt present, and exact-topic applicability established."
    )

    return verdict


# ---------------------------------------------------------------------
# Persist Stage 46 result without requiring new SQL tables
# ---------------------------------------------------------------------

def merge_completion_payload(task: dict, stage46_result: dict) -> dict:
    payload = as_dict(task.get("completion_payload"))
    payload["stage46_provenance_validation"] = stage46_result
    return payload


def mark_task_stage46_verified(task: dict, stage46_result: dict):
    supabase.table("locked_evidence_execution_tasks").update({
        "completion_payload": merge_completion_payload(task, stage46_result),
        "completion_status": "VERIFIED",
        "updated_at": now_iso(),
    }).eq("id", task["id"]).eq("user_id", user_id).execute()


def revoke_task_stage45_completion(task: dict, stage46_result: dict):
    supabase.table("locked_evidence_execution_tasks").update({
        "task_status": "WAITING_OFFICIAL",
        "completion_status": None,
        "completion_source": None,
        "completion_reference": None,
        "completed_at": None,
        "completion_payload": {
            "stage": 46,
            "status": "WAITING_OFFICIAL",
            "reason": (
                "Etapa 46 a respins dovada folosită pentru rezolvarea din Etapa 45."
            ),
            "stage46_provenance_validation": stage46_result,
        },
        "updated_at": now_iso(),
    }).eq("id", task["id"]).eq("user_id", user_id).execute()


def update_worker_item_validation(item: dict, stage46_result: dict):
    payload = item.get("official_document_payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    payload["stage46_provenance_validation"] = stage46_result

    verified = stage46_result.get("status") == "VERIFIED"

    update_payload = {
        "official_document_payload": payload,
        "official_verified": verified,
        "authoritative_source_verified": verified,
        "explicit_evidence_verified": verified,
        "official_document_status": "VERIFIED" if verified else "WAITING_OFFICIAL",
        "retrieved_at": now_iso(),
        "updated_at": now_iso(),
    }

    if not verified:
        update_payload["worker_status"] = "WAITING_OFFICIAL"
        update_payload["missing_evidence_reason"] = (
            "Stage 46 provenance validator rejected the previously resolved evidence."
        )
        update_payload["next_action"] = (
            "Return to Stage 45 official evidence resolution with a substantive traceable source."
        )

    supabase.table("locked_evidence_worker_items").update(
        update_payload
    ).eq("id", item["id"]).eq("user_id", user_id).execute()


# ---------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------

supabase = get_supabase()
restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Nu am putut identifica utilizatorul autentificat.")
    st.stop()

projects = rows(
    "projects",
    {"user_id": user_id},
    "updated_at",
    200,
)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_label = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage46_project",
)

project = project_map[selected_label]
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
    st.warning("Nu există execution run disponibil pentru acest lock.")
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

hard_gate = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(execution_tasks)
)

if not hard_gate:
    st.error("Etapa 46 este BLOCKED de hard gate.")
    st.stop()

st.success("Hard gate Etapa 46: PASS.")

st.write(f"**Locked opportunity:** {identity}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")


# ---------------------------------------------------------------------
# Stage 46 workspace
# ---------------------------------------------------------------------

history = load_stage45_history()
resolved_items = load_best_resolved_stage45_items(history)
official_tasks = current_official_tasks()

resolved_by_task = {
    normalize_text(item.get("execution_task_id")): item
    for item in resolved_items
    if item.get("execution_task_id")
}

task_rows = []

for task in official_tasks:
    task_id = str(task.get("id"))
    candidate = resolved_by_task.get(task_id)

    task_rows.append({
        "Requirement": task.get("requirement_label"),
        "Stage 45 task status": task.get("task_status"),
        "Resolved evidence found": bool(candidate),
        "Evidence URL": candidate.get("evidence_url") if candidate else None,
        "Worker item": candidate.get("id") if candidate else None,
    })

st.subheader("Stage 45 → Stage 46 handoff")
st.dataframe(
    task_rows,
    use_container_width=True,
    hide_index=True,
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("OFFICIAL requirements", len(official_tasks))
c2.metric("Resolved evidence found", len(resolved_items))
c3.metric(
    "Still WAITING",
    sum(
        1 for t in official_tasks
        if normalize_text(t.get("task_status")).upper() != "COMPLETED"
    ),
)
c4.metric("History worker items", len(history))


# Pre-flight independent validations for UI.
preflight = []

for item in resolved_items:
    try:
        result = validate_stage45_item(item)
    except Exception as exc:
        result = {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {str(exc)}",
            "final_url": None,
            "official_final_host": False,
            "auth_or_error_url": False,
            "auth_or_error_content": False,
            "excerpt_present_in_source": False,
            "exact_topic_in_source": False,
            "traceable_topic_chain": False,
        }

    preflight.append({
        "item": item,
        "result": result,
    })

st.subheader("Pre-flight provenance validation")

if preflight:
    st.dataframe(
        [
            {
                "Requirement": x["item"].get("requirement_label"),
                "Guard": x["result"].get("status"),
                "HTTP": x["result"].get("http_status"),
                "Final URL": x["result"].get("final_url"),
                "Official host": x["result"].get("official_final_host"),
                "Auth/error URL": x["result"].get("auth_or_error_url"),
                "Auth/error content": x["result"].get("auth_or_error_content"),
                "Excerpt in source": x["result"].get("excerpt_present_in_source"),
                "Exact topic in source": x["result"].get("exact_topic_in_source"),
                "Traceable chain": x["result"].get("traceable_topic_chain"),
                "Reason": x["result"].get("reason"),
            }
            for x in preflight
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Nu există încă dovezi RESOLVED în istoricul Etapei 45.")


if st.button(
    "🛡️ Run Stage 46 provenance validation",
    type="primary",
    use_container_width=True,
    key="stage46_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(official_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "stage46-v1",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {
                "stage": 46,
                "version": "v1",
                "official_requirements": len(official_tasks),
                "stage45_resolved_evidence": len(resolved_items),
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 46 run.")
        st.stop()

    run_id = str(run[0]["id"])

    verified = rejected = waiting = failed = 0
    results = []

    task_map = {
        str(t["id"]): t
        for t in official_tasks
    }

    processed_task_ids = set()

    for item in resolved_items:
        task_id = normalize_text(item.get("execution_task_id"))
        task = task_map.get(task_id)

        if not task:
            continue

        processed_task_ids.add(task_id)

        try:
            verdict = validate_stage45_item(item)

            if verdict.get("status") == "VERIFIED":
                verified += 1
                mark_task_stage46_verified(task, verdict)
                update_worker_item_validation(item, verdict)

            else:
                rejected += 1
                revoke_task_stage45_completion(task, verdict)
                update_worker_item_validation(item, verdict)

            results.append(verdict)

        except Exception as exc:
            failed += 1
            results.append({
                "stage": 46,
                "version": "v1",
                "execution_task_id": task_id,
                "requirement_label": task.get("requirement_label"),
                "status": "ERROR",
                "reason": f"{type(exc).__name__}: {str(exc)}",
                "checked_at": now_iso(),
            })

    # Any OFFICIAL requirement without a resolved Stage 45 evidence row remains WAITING.
    for task in official_tasks:
        task_id = str(task["id"])

        if task_id not in processed_task_ids:
            waiting += 1
            results.append({
                "stage": 46,
                "version": "v1",
                "execution_task_id": task_id,
                "requirement_label": task.get("requirement_label"),
                "status": "WAITING_OFFICIAL",
                "reason": "No Stage 45 RESOLVED evidence exists yet for this requirement.",
                "checked_at": now_iso(),
            })

    # PASS requires every OFFICIAL requirement to have verified provenance.
    pass_gate = (
        len(official_tasks) > 0
        and verified == len(official_tasks)
        and rejected == 0
        and waiting == 0
        and failed == 0
    )

    final = (
        "FAILED"
        if failed and verified == 0 and rejected == 0
        else "PASS"
        if pass_gate
        else "WAITING"
    )

    supabase.table("locked_evidence_worker_runs").update({
        "resolved_tasks": verified,
        "waiting_tasks": waiting + rejected,
        "failed_tasks": failed,
        "worker_status": "COMPLETED" if final == "PASS" else final,
        "diagnostic_status": (
            "FAILED"
            if final == "FAILED"
            else "PARTIAL_FAILURE"
            if failed
            else "CLEAN"
        ),
        "official_tasks_resolved": verified,
        "official_tasks_waiting": waiting + rejected,
        "deep_resolution_version": "stage46-v1",
        "provenance_summary": {
            "stage46_gate": final,
            "verified": verified,
            "rejected": rejected,
            "waiting": waiting,
            "failed": failed,
            "results": results,
        },
        "completed_at": now_iso() if final in {"PASS", "FAILED"} else None,
        "updated_at": now_iso(),
    }).eq("id", run_id).eq("user_id", user_id).execute()

    if final == "PASS":
        st.success(
            "Etapa 46: PASS — toate cerințele OFFICIAL au dovadă RESOLVED "
            "și provenance verificat."
        )
    elif final == "WAITING":
        st.warning(
            f"Etapa 46: WAITING — verified {verified}, rejected {rejected}, "
            f"waiting {waiting}, failed {failed}."
        )
    else:
        st.error(
            f"Etapa 46: FAILED — verified {verified}, rejected {rejected}, "
            f"waiting {waiting}, failed {failed}."
        )

    st.rerun()


# ---------------------------------------------------------------------
# Latest Stage 46 result
# ---------------------------------------------------------------------

stage46_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage46_runs = [
    r for r in stage46_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "stage46-v1"
]

if stage46_runs:
    latest = stage46_runs[0]
    summary = latest.get("provenance_summary") or {}

    st.divider()
    st.subheader("Latest Stage 46 Result")

    z1, z2, z3, z4, z5 = st.columns(5)

    z1.metric("Gate", summary.get("stage46_gate") or latest.get("worker_status") or "—")
    z2.metric("Verified", summary.get("verified", 0))
    z3.metric("Rejected", summary.get("rejected", 0))
    z4.metric("Waiting", summary.get("waiting", 0))
    z5.metric("Failed", summary.get("failed", 0))

    with st.expander("Stage 46 provenance results", expanded=False):
        st.json(summary.get("results", []))

    if summary.get("stage46_gate") == "PASS":
        st.success(
            "Etapa 46 este PASS. Rezultatele OFFICIAL validate pot fi predate Etapei 47."
        )
    else:
        st.warning(
            "Etapa 46 nu este încă PASS. Cerințele WAITING/REJECTED trebuie retrimise "
            "controlat către Etapa 45 înainte de etapa următoare."
        )


st.caption(
    "Invariantă Etapa 46: un rezultat RESOLVED din Etapa 45 nu este trusted automat. "
    "Este acceptat numai dacă sursa finală este oficială și substanțială, pasajul citat "
    "există în documentul recitit, iar aplicabilitatea la topic este demonstrată direct "
    "sau prin provenance chain oficial trasabil."
)
# =====================================================================
# END STAGE 46
# =====================================================================
