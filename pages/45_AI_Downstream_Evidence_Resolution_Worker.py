import requests
import os
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from openai import OpenAI
from supabase import create_client
from io import BytesIO
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# ===== Stage 45 v3 official-source transport =====
def s45v3_urls(raw):
    if not raw:
        return []
    vals = raw if isinstance(raw, (list, tuple, set)) else re.split(r"\s*\|\s*|\s*\n\s*", str(raw))
    out = []
    for val in vals:
        for u in re.findall(r'https?://[^\s|<>"\']+', str(val)):
            u = u.rstrip(".,;)")
            if u not in out:
                out.append(u)
    return out

def s45v3_fetch(raw, timeout=20):
    attempts = []
    for url in s45v3_urls(raw):
        try:
            r = requests.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": "GreenRise/Stage45-v3",
                    "Accept": "application/json,text/html,application/xhtml+xml,*/*",
                },
            )
            attempts.append({
                "url": url,
                "http_status": r.status_code,
                "content_type": r.headers.get("content-type", ""),
                "bytes": len(r.content or b""),
            })
            if r.ok and (r.text or "").strip():
                return {
                    "ok": True, "url": url, "response": r,
                    "text": r.text, "attempts": attempts
                }
        except Exception as e:
            attempts.append({"url": url, "error": repr(e)})
    return {"ok": False, "url": None, "response": None, "text": "", "attempts": attempts}
# ===== end v3 =====

class _S45V3CompatResponse:
    def __init__(self, result):
        self._result = result
        r = result.get("response")
        self.status_code = getattr(r, "status_code", 599)
        self.text = result.get("text", "")
        self.content = getattr(r, "content", b"")
        self.headers = getattr(r, "headers", {})
        self.ok = bool(result.get("ok"))
    def json(self):
        r = self._result.get("response")
        if r is None:
            raise ValueError("No readable official response")
        return r.json()
    def raise_for_status(self):
        r = self._result.get("response")
        if r is None:
            raise requests.HTTPError("No readable official response")
        return r.raise_for_status()

def s45v3_get(raw, *args, **kwargs):
    timeout = kwargs.get("timeout", 20)
    return _S45V3CompatResponse(s45v3_fetch(raw, timeout=timeout))



st.set_page_config(
    page_title="Downstream Evidence Resolution Worker",
    page_icon="🛠️",
    layout="wide",
)

st.title("🛠️ Etapa 45 — AI Downstream Evidence Resolution Worker")
st.caption(
    "Execută efectiv task-urile WAITING din Etapa 43: verificare oficială, "
    "dovadă de la utilizator și evidence resolver. "
    "Nu marchează COMPLETED fără dovadă explicită și trasabilă."
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


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def project_label(p: dict) -> str:
    return f"{p.get('name') or 'Project'} — {str(p.get('id') or '')[:8]}"


def walk(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, value
            yield from walk(value, child)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            child = f"{path}[{i}]"
            yield child, value
            yield from walk(value, child)


def compact(value: Any, limit=1400) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = normalize_text(value)
    return text[:limit]


# ---------------------------------------------------------------------
# Evidence retrieval
# ---------------------------------------------------------------------
def requirement_tokens(task: dict) -> set[str]:
    category = normalize_text(task.get("requirement_category")).lower()
    key = normalize_text(task.get("requirement_key")).lower()
    label = normalize_text(task.get("requirement_label")).lower()

    aliases = {
        "eligibility": ["eligibility", "eligible", "applicant", "beneficiary", "participant"],
        "consortium": ["consortium", "partners", "partner", "participants"],
        "trl": ["trl", "technology readiness level", "readiness level"],
        "funding": ["funding", "budget", "grant", "funding rate", "co-financing", "cofunding"],
        "geographic": ["geographic", "country", "countries", "region", "eligible countries"],
    }

    tokens = set()
    for text in (category, key, label):
        for token in re.split(r"[\s_\-]+", text):
            if len(token) >= 4:
                tokens.add(token)

    for alias, vals in aliases.items():
        if alias in category or alias in key or alias in label:
            tokens.update(vals)

    return tokens


def collect_snapshot_evidence(task: dict, sources: dict) -> list[dict]:
    tokens = requirement_tokens(task)
    matches = []

    for source_name, source_obj in sources.items():
        for path, value in walk(source_obj):
            if value in (None, "", [], {}):
                continue
            haystack = f"{path} {compact(value)}".lower()
            if any(t.lower() in haystack for t in tokens):
                matches.append({
                    "source": source_name,
                    "path": path,
                    "value": compact(value),
                })
            if len(matches) >= 30:
                break
        if len(matches) >= 30:
            break

    return matches[:30]


def fetch_official_url(url: str) -> tuple[str, str]:
    if not url or not (
        "ec.europa.eu/" in url.lower()
        or "commission.europa.eu/" in url.lower()
        or "api.tech.ec.europa.eu/" in url.lower()
    ):
        return "", ""

    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/html,text/plain,*/*",
            "Accept-Language": "en-GB,en;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
            return raw[:120000], ctype
    except Exception:
        return "", ""


# ---------------------------------------------------------------------
# AI official/stored evidence evaluator
# ---------------------------------------------------------------------
SYSTEM = """You are an evidence worker for a grant application workflow.

You receive ONE requirement and evidence candidates from:
1) stored project/opportunity/verification snapshots;
2) optionally an official European Commission source response.

STRICT RULES:
- Never invent eligibility rules, consortium requirements, TRL, funding rules,
  geographic eligibility, applicant facts, or call conditions.
- Mark RESOLVED only if the supplied evidence explicitly answers the requirement.
- For an OFFICIAL_VERIFICATION task, RESOLVED requires explicit official-source evidence.
- For an EVIDENCE_RESOLVER task, stored evidence can resolve it only if explicit.
- If evidence is insufficient, keep the correct WAITING_* status.
- Do not treat a deadline or identity match as proof of eligibility.
- Return JSON only.

Schema:
{
  "status": "RESOLVED|WAITING_OFFICIAL|WAITING_USER|WAITING_RESOLVER|BLOCKED",
  "resolved_value": {},
  "evidence_source": "",
  "evidence_reference": "",
  "evidence_url": "",
  "evidence_excerpt": "",
  "confidence": "Low|Medium|High",
  "reason": "",
  "next_action": ""
}
"""


def ai_evaluate(task: dict, snapshot_evidence: list[dict], official_text: str, official_url: str) -> dict:
    client = get_openai()

    payload = {
        "requirement": {
            "key": task.get("requirement_key"),
            "category": task.get("requirement_category"),
            "label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "required_action": task.get("required_action"),
        },
        "snapshot_evidence": snapshot_evidence,
        "official_source": {
            "url": official_url,
            "content_excerpt": official_text[:45000] if official_text else "",
        },
    }

    resp = client.chat.completions.create(
        model=model_name(),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
    )

    result = json.loads(resp.choices[0].message.content or "{}")
    if not isinstance(result, dict):
        result = {}

    allowed = {
        "RESOLVED", "WAITING_OFFICIAL", "WAITING_USER", "WAITING_RESOLVER", "BLOCKED"
    }

    status = normalize_text(result.get("status")).upper()
    if status not in allowed:
        route = normalize_text(task.get("route_type")).upper()
        status = {
            "OFFICIAL_VERIFICATION": "WAITING_OFFICIAL",
            "USER_EVIDENCE": "WAITING_USER",
            "EVIDENCE_RESOLVER": "WAITING_RESOLVER",
        }.get(route, "WAITING_RESOLVER")

    resolved_value = result.get("resolved_value")
    if not isinstance(resolved_value, dict):
        resolved_value = {}

    confidence = normalize_text(result.get("confidence")).title()
    if confidence not in {"Low", "Medium", "High"}:
        confidence = "Low"

    # Hard safety: official route cannot resolve without actual fetched official content.
    if (
        normalize_text(task.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
        and status == "RESOLVED"
        and not official_text
    ):
        status = "WAITING_OFFICIAL"
        resolved_value = {}

    # Hard safety: RESOLVED needs explicit value + excerpt/source.
    if status == "RESOLVED":
        if not resolved_value or not (
            normalize_text(result.get("evidence_excerpt"))
            or normalize_text(result.get("evidence_reference"))
        ):
            status = (
                "WAITING_OFFICIAL"
                if normalize_text(task.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
                else "WAITING_RESOLVER"
            )
            resolved_value = {}

    return {
        "status": status,
        "resolved_value": resolved_value,
        "evidence_source": normalize_text(result.get("evidence_source")),
        "evidence_reference": normalize_text(result.get("evidence_reference")),
        "evidence_url": normalize_text(result.get("evidence_url")) or official_url,
        "evidence_excerpt": normalize_text(result.get("evidence_excerpt"))[:5000],
        "confidence": confidence,
        "reason": normalize_text(result.get("reason"))[:5000],
        "next_action": normalize_text(result.get("next_action"))[:5000],
        "raw": result,
    }


def update_execution_task_completed(task: dict, worker_result: dict, completion_status: str):
    completion_payload = {
        "stage": 45,
        "status": completion_status,
        "result_status": completion_status,
        "requirement_key": task.get("requirement_key"),
        "requirement_label": task.get("requirement_label"),
        "resolved_value": worker_result.get("resolved_value") or {},
        "evidence_source": worker_result.get("evidence_source"),
        "evidence_reference": worker_result.get("evidence_reference"),
        "evidence_url": worker_result.get("evidence_url"),
        "evidence_excerpt": worker_result.get("evidence_excerpt"),
        "confidence": worker_result.get("confidence"),
        "reason": worker_result.get("reason"),
        "completed_at": now_iso(),
    }

    supabase.table("locked_evidence_execution_tasks").update({
        "task_status": "COMPLETED",
        "completion_payload": completion_payload,
        "completion_status": completion_status,
        "completion_source": worker_result.get("evidence_source") or "Stage 45 worker",
        "completion_reference": worker_result.get("evidence_reference") or task.get("requirement_key"),
        "completed_at": now_iso(),
        "updated_at": now_iso(),
    }).eq("id", task["id"]).eq("user_id", user_id).execute()


# ---------------------------------------------------------------------
# Init
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
selected_label = st.selectbox("Project", list(project_map.keys()))
project = project_map[selected_label]
project_id = str(project["id"])
project_name = normalize_text(project.get("name"))
project_data = as_dict(project.get("data"))

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
official_url = normalize_text(lock.get("official_source_url"))

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
    st.warning("Nu există execution run Etapa 43 disponibil.")
    st.stop()

execution_run_id = str(execution_run["id"])

tasks = rows(
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

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 43", normalize_text(execution_run.get("execution_status")) or "—")
c4.metric("Tasks", len(tasks))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Official source:** {official_url or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(tasks)
)

if not hard_gate_ok:
    st.error("Etapa 45 este BLOCKED de hard gate.")
    st.stop()

st.success("Hard gate Etapa 45: PASS.")

waiting_tasks = [
    t for t in tasks
    if normalize_text(t.get("task_status")).upper()
    in {"WAITING_OFFICIAL", "WAITING_USER", "WAITING_RESOLVER", "IN_PROGRESS", "DISPATCHED"}
]

completed_tasks = [
    t for t in tasks
    if normalize_text(t.get("task_status")).upper() == "COMPLETED"
]

# ===== Stage 45 v4 — Official Documentation Resolver =====
# This layer does NOT fabricate completion. It discovers and inspects official EC
# documentation references returned by the official Search API response.

from urllib.parse import urljoin

def s45v4_collect_official_documents(payload, base_url="https://ec.europa.eu/"):
    """Recursively collect plausible official EC document/page URLs from JSON."""
    found = []
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            for u in re.findall(r'https?://[^\s|<>"\']+', obj):
                if any(host in u.lower() for host in (
                    "ec.europa.eu", "europa.eu", "funding-tenders.ec.europa.eu"
                )):
                    u = u.rstrip(".,;)")
                    if u not in found:
                        found.append(u)
    walk(payload)
    return found

def s45v4_keyword_evidence(text, requirement):
    """Conservative evidence detector: returns excerpts, never an inferred verdict."""
    if not text:
        return []
    groups = {
        "applicant": ["eligible applicants", "eligible entities", "eligibility", "legal entities"],
        "consortium": ["consortium", "beneficiaries", "independent legal entities", "minimum number"],
        "trl": ["trl", "technology readiness level", "readiness level"],
        "funding": ["funding rate", "funding rates", "reimbursement rate", "eligible costs"],
        "geographic": ["eligible countries", "member states", "associated countries", "country eligibility"],
    }
    key = str(requirement or "").lower()
    family = next((k for k in groups if k in key), key)
    needles = groups.get(family, [])
    clean = re.sub(r"\s+", " ", text)
    low = clean.lower()
    excerpts = []
    for needle in needles:
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx < 0:
                break
            a, b = max(0, idx-350), min(len(clean), idx+700)
            excerpt = clean[a:b].strip()
            if excerpt not in excerpts:
                excerpts.append(excerpt)
            start = idx + len(needle)
            if len(excerpts) >= 5:
                return excerpts
    return excerpts

def s45v4_resolve_official_source(raw_source, requirement):
    """
    1. Query each official source separately.
    2. Inspect returned JSON/text.
    3. Follow only official EC URLs discovered in the response.
    4. Return traceable excerpts. Caller must decide COMPLETED only if the excerpt
       explicitly proves the requirement.
    """
    trace = []
    evidence = []
    first = s45v3_fetch(raw_source)
    trace.extend(first.get("attempts", []))
    if not first.get("ok"):
        return {"status": "WAITING_OFFICIAL", "trace": trace, "evidence": []}

    response = first.get("response")
    body = first.get("text", "")
    evidence.extend([{"url": first.get("url"), "excerpt": x}
                     for x in s45v4_keyword_evidence(body, requirement)])

    docs = []
    try:
        payload = response.json()
        docs = s45v4_collect_official_documents(payload)
    except Exception:
        payload = None

    # Limit traversal deliberately: deterministic and safe.
    for url in docs[:12]:
        fetched = s45v3_fetch(url)
        trace.extend(fetched.get("attempts", []))
        if not fetched.get("ok"):
            continue
        for excerpt in s45v4_keyword_evidence(fetched.get("text", ""), requirement):
            evidence.append({"url": fetched.get("url"), "excerpt": excerpt})
        if len(evidence) >= 5:
            break

    return {
        "status": "EVIDENCE_FOUND" if evidence else "WAITING_OFFICIAL",
        "trace": trace,
        "evidence": evidence[:5],
        "discovered_official_urls": docs[:12],
    }

st.info(
    "Stage 45 v5 — Official Documentation Resolver EXECUTION activ. "
    "Search API este folosit pentru descoperirea documentației EC; "
    "COMPLETED rămâne interzis fără dovadă explicită și trasabilă."
)
# ===== End Stage 45 v4 =====


st.subheader("Task workspace")

st.info("Stage 45 v5: Official Documentation Resolver este conectat la execuția OFFICIAL_VERIFICATION; verdictul rămâne WAITING fără dovadă explicită.")


if completed_tasks:
    st.success(f"{len(completed_tasks)} task-uri sunt deja COMPLETED.")

sources = {
    "project.data": project_data,
    "verification_snapshot": as_dict(lock.get("verification_snapshot")),
    "opportunity_snapshot": as_dict(lock.get("opportunity_snapshot")),
    "scoring_snapshot": as_dict(lock.get("scoring_snapshot")),
}

# Cache official fetch once per page run.
official_text = ""
official_content_type = ""

auto_tasks = [
    t for t in waiting_tasks
    if normalize_text(t.get("route_type")).upper()
    in {"OFFICIAL_VERIFICATION", "EVIDENCE_RESOLVER"}
]

if auto_tasks and official_url:
    with st.spinner("Pregătesc sursa oficială disponibilă..."):
        official_text, official_content_type = fetch_official_url(official_url)

if official_url and not official_text:
    st.info(
        "URL-ul oficial nu a putut fi citit automat prin GET. "
        "Task-urile OFFICIAL rămân WAITING dacă snapshot-urile nu conțin cerința explicită."
    )

for task in tasks:
    status = normalize_text(task.get("task_status")).upper()
    route = normalize_text(task.get("route_type")).upper()

    with st.expander(f"{task.get('requirement_label')} — {status}", expanded=False):
        st.write(f"**Route:** {route}")
        st.write(f"**Destination:** {task.get('destination_module')}")
        st.write(f"**Instruction:** {task.get('task_instruction') or '—'}")
        st.write(f"**Required action:** {task.get('required_action') or '—'}")

        if status == "COMPLETED":
            st.success("Task-ul este deja COMPLETED.")
            payload = as_dict(task.get("completion_payload"))
            st.json(payload)
            continue

        if route in {"OFFICIAL_VERIFICATION", "EVIDENCE_RESOLVER"}:
            snapshot_evidence = collect_snapshot_evidence(task, sources)

            st.write(f"**Stored candidate evidence:** {len(snapshot_evidence)}")
            if snapshot_evidence:
                st.dataframe(snapshot_evidence[:10], use_container_width=True, hide_index=True)

            button_key = f"resolve_{task['id']}"
            if st.button(
                "🔎 Run controlled evidence resolution",
                key=button_key,
                use_container_width=True,
            ):
                with st.spinner("Verific dovezile disponibile..."):
                    # v5: OFFICIAL_VERIFICATION first uses the v4 documentation resolver.
                    # Its traceable excerpts are fed into the existing conservative evaluator;
                    # COMPLETED is still impossible unless ai_evaluate finds explicit evidence.
                    v4_resolution = None
                    v4_official_text = official_text
                    v4_official_url = official_url

                    if route == "OFFICIAL_VERIFICATION" and official_url:
                        v4_resolution = s45v4_resolve_official_source(
                            official_url,
                            task.get("requirement_label") or task.get("requirement_key") or "",
                        )
                        if v4_resolution.get("evidence"):
                            v4_official_text = "\n\n".join(
                                e.get("excerpt", "")
                                for e in v4_resolution["evidence"]
                                if e.get("excerpt")
                            )
                            v4_official_url = next(
                                (
                                    e.get("url")
                                    for e in v4_resolution["evidence"]
                                    if e.get("url")
                                ),
                                official_url,
                            )

                    result = ai_evaluate(
                        task,
                        snapshot_evidence,
                        v4_official_text if route == "OFFICIAL_VERIFICATION" else "",
                        v4_official_url if route == "OFFICIAL_VERIFICATION" else "",
                    )

                    if v4_resolution is not None:
                        result["v4_official_resolution"] = {
                            "status": v4_resolution.get("status"),
                            "trace": v4_resolution.get("trace", []),
                            "discovered_official_urls": v4_resolution.get("discovered_official_urls", []),
                            "evidence_count": len(v4_resolution.get("evidence", [])),
                        }

                if result["status"] == "RESOLVED":
                    update_execution_task_completed(
                        task,
                        result,
                        "VERIFIED" if route == "OFFICIAL_VERIFICATION" else "RESOLVED",
                    )
                    st.success("Task rezolvat cu dovadă explicită.")
                    st.rerun()
                elif result["status"] == "BLOCKED":
                    supabase.table("locked_evidence_execution_tasks").update({
                        "task_status": "BLOCKED",
                        "completion_payload": {
                            "stage": 45,
                            "status": "BLOCKED",
                            "reason": result["reason"],
                            "evidence_excerpt": result["evidence_excerpt"],
                        },
                        "completion_status": "BLOCKED",
                        "completion_source": result["evidence_source"],
                        "completion_reference": result["evidence_reference"],
                        "updated_at": now_iso(),
                    }).eq("id", task["id"]).eq("user_id", user_id).execute()
                    st.error("Task-ul a devenit BLOCKED pe baza dovezii.")
                    st.rerun()
                else:
                    st.warning(f"Rămâne {result['status']}.")
                    st.write(result["reason"])
                    st.write(f"**Next action:** {result['next_action'] or '—'}")

        elif route == "USER_EVIDENCE":
            st.info(
                "Acest task necesită informație factuală furnizată de utilizator. "
                "Nu este completat automat."
            )
            user_value = st.text_area(
                "Răspuns / informație factuală",
                key=f"user_value_{task['id']}",
            )
            user_reference = st.text_input(
                "Referință document / dovadă",
                key=f"user_ref_{task['id']}",
            )
            user_confirm = st.checkbox(
                "Confirm că informația introdusă este reală și poate fi folosită în proiect.",
                key=f"user_confirm_{task['id']}",
            )

            if st.button(
                "✅ Save confirmed user evidence",
                key=f"save_user_{task['id']}",
                use_container_width=True,
                disabled=not (user_confirm and normalize_text(user_value)),
            ):
                worker_result = {
                    "resolved_value": {"value": normalize_text(user_value)},
                    "evidence_source": "USER_EVIDENCE",
                    "evidence_reference": normalize_text(user_reference) or "User confirmed input",
                    "evidence_url": "",
                    "evidence_excerpt": normalize_text(user_value)[:5000],
                    "confidence": "High",
                    "reason": "Applicant supplied and explicitly confirmed factual evidence.",
                }
                update_execution_task_completed(task, worker_result, "VERIFIED")
                st.success("Dovada utilizatorului a fost salvată și task-ul este COMPLETED.")
                st.rerun()

        else:
            st.warning("Ruta acestui task nu este executabilă automat în Etapa 45.")


# ---------------------------------------------------------------------
# Recompute execution run status
# ---------------------------------------------------------------------
current_tasks = rows(
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

counts = {
    "completed": sum(1 for t in current_tasks if normalize_text(t.get("task_status")).upper() == "COMPLETED"),
    "waiting_official": sum(1 for t in current_tasks if normalize_text(t.get("task_status")).upper() == "WAITING_OFFICIAL"),
    "waiting_user": sum(1 for t in current_tasks if normalize_text(t.get("task_status")).upper() == "WAITING_USER"),
    "waiting_resolver": sum(1 for t in current_tasks if normalize_text(t.get("task_status")).upper() == "WAITING_RESOLVER"),
    "blocked": sum(1 for t in current_tasks if normalize_text(t.get("task_status")).upper() == "BLOCKED"),
    "failed": sum(1 for t in current_tasks if normalize_text(t.get("task_status")).upper() == "FAILED"),
}

if counts["blocked"] > 0:
    execution_status = "BLOCKED"
elif counts["failed"] > 0:
    execution_status = "FAILED"
elif counts["completed"] == len(current_tasks):
    execution_status = "COMPLETED"
else:
    execution_status = "WAITING"

supabase.table("locked_evidence_execution_runs").update({
    "completed_tasks": counts["completed"],
    "waiting_official_tasks": counts["waiting_official"],
    "waiting_user_tasks": counts["waiting_user"],
    "waiting_resolver_tasks": counts["waiting_resolver"],
    "blocked_tasks": counts["blocked"],
    "failed_tasks": counts["failed"],
    "execution_status": execution_status,
    "completed_at": now_iso() if execution_status in {"COMPLETED", "BLOCKED", "FAILED"} else None,
    "updated_at": now_iso(),
}).eq("id", execution_run_id).eq("user_id", user_id).execute()

st.divider()
st.subheader("Stage 45 progress")

a, b, c, d, e = st.columns(5)
a.metric("Execution", execution_status)
b.metric("Completed", counts["completed"])
c.metric("Official", counts["waiting_official"])
d.metric("User", counts["waiting_user"])
e.metric("Resolver", counts["waiting_resolver"])

if execution_status == "COMPLETED":
    st.success(
        "Toate task-urile au fost rezolvate explicit. "
        "Revino în Etapa 44 și rulează din nou Completion Gate."
    )
elif execution_status == "WAITING":
    st.warning(
        "Etapa 45 este WAITING. Rezolvă task-urile rămase; "
        "apoi Etapa 44 poate fi rerulată pentru PASS."
    )
elif execution_status == "BLOCKED":
    st.error("Etapa 45 BLOCKED: există o incompatibilitate susținută de dovadă.")
elif execution_status == "FAILED":
    st.error("Etapa 45 FAILED.")

st.caption(
    "Invariantă Etapa 45: un task devine COMPLETED numai după dovadă explicită "
    "oficială/stocată sau după confirmare factuală a utilizatorului."
)


# =====================================================================
# STAGE 45 v6 — DATABASE-BACKED OFFICIAL DEEP RESOLUTION
# =====================================================================

def s45v6_save_document(task, worker_run_id, worker_item_id, url, excerpt="", status="FETCHED", payload=None):
    exact = bool(identity and (
        identity.lower() in (url or "").lower()
        or identity.lower() in (excerpt or "").lower()
    ))

    row = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
        "worker_run_id": worker_run_id,
        "worker_item_id": worker_item_id,
        "execution_task_id": task.get("id"),
        "requirement_id": task.get("requirement_id"),
        "opportunity_identity": identity,
        "requirement_key": task.get("requirement_key"),
        "requirement_category": task.get("requirement_category"),
        "requirement_label": task.get("requirement_label"),
        "source_url": url,
        "source_title": "Official European Commission source",
        "document_type": "OFFICIAL_EC_DOCUMENT",
        "source_authority": "EUROPEAN_COMMISSION",
        "topic_identity": identity,
        "exact_topic_verified": exact,
        "applicability_verified": exact,
        "applicability_reason": (
            "Exact locked topic identity found in source URL/evidence."
            if exact else
            "Official source found, but exact applicability to locked topic is not explicit."
        ),
        "evidence_found": bool(excerpt),
        "evidence_excerpt": excerpt[:10000] if excerpt else None,
        "evidence_reference": task.get("requirement_label"),
        "evidence_payload": payload or {},
        "provenance_chain": [official_url, url] if url != official_url else [url],
        "retrieval_status": "VERIFIED" if (exact and excerpt) else status,
        "retrieved_at": now_iso(),
        "updated_at": now_iso(),
    }

    existing = (
        supabase.table("locked_evidence_official_documents")
        .select("id")
        .eq("user_id", user_id)
        .eq("worker_item_id", worker_item_id)
        .eq("source_url", url)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        supabase.table("locked_evidence_official_documents").update(row).eq(
            "id", existing[0]["id"]
        ).eq("user_id", user_id).execute()
    else:
        supabase.table("locked_evidence_official_documents").insert(row).execute()

    return exact


def s45v6_run_task(task, worker_run_id):
    item_insert = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task["requirement_id"],
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "OFFICIAL_DOCUMENTATION_DEEP_RESOLUTION",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v6"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item_insert:
        raise RuntimeError("Could not create v6 worker item.")

    worker_item_id = str(item_insert[0]["id"])

    result = s45v4_resolve_official_source(
        official_url,
        task.get("requirement_label") or task.get("requirement_key") or "",
    )

    evidence = result.get("evidence", []) or []
    trace = result.get("trace", []) or []
    docs = result.get("discovered_official_urls", []) or []

    verified = []
    for ev in evidence:
        url = normalize_text(ev.get("url"))
        excerpt = normalize_text(ev.get("excerpt"))
        if not url or not excerpt:
            continue
        exact = s45v6_save_document(
            task, worker_run_id, worker_item_id, url, excerpt,
            payload={"resolver_status": result.get("status")}
        )
        if exact:
            verified.append(ev)

    # Save discovered official URLs even when no evidence was extracted.
    for url in docs:
        url = normalize_text(url)
        if url:
            s45v6_save_document(
                task, worker_run_id, worker_item_id, url,
                status="FETCHED",
                payload={"discovered_only": True}
            )

    if verified:
        combined = "\n\n".join(normalize_text(x.get("excerpt")) for x in verified)
        evaluation = ai_evaluate(
            task,
            collect_snapshot_evidence(task, sources),
            combined,
            normalize_text(verified[0].get("url")),
        )

        if evaluation.get("status") == "RESOLVED":
            worker_result = {
                "resolved_value": evaluation.get("resolved_value") or {},
                "evidence_source": "OFFICIAL_DOCUMENTATION",
                "evidence_reference": evaluation.get("evidence_reference") or task.get("requirement_label"),
                "evidence_url": evaluation.get("evidence_url") or verified[0].get("url"),
                "evidence_excerpt": evaluation.get("evidence_excerpt") or combined[:5000],
                "confidence": evaluation.get("confidence") or "High",
                "reason": evaluation.get("reason") or "Explicit official evidence verified.",
            }
            update_execution_task_completed(task, worker_result, "VERIFIED")

            supabase.table("locked_evidence_worker_items").update({
                "worker_status": "RESOLVED",
                "resolved_value": worker_result["resolved_value"],
                "evidence_source": worker_result["evidence_source"],
                "evidence_reference": worker_result["evidence_reference"],
                "evidence_url": worker_result["evidence_url"],
                "evidence_excerpt": worker_result["evidence_excerpt"],
                "confidence": worker_result["confidence"],
                "official_verified": True,
                "reason": worker_result["reason"],
                "next_action": "RETURN_TO_STAGE_44",
                "source_title": "Official European Commission source",
                "document_type": "OFFICIAL_EC_DOCUMENT",
                "source_authority": "EUROPEAN_COMMISSION",
                "topic_identity": identity,
                "provenance_chain": [official_url, worker_result["evidence_url"]],
                "documents_checked": docs,
                "searches_attempted": [official_url],
                "transport_attempts": trace,
                "resolution_method": "OFFICIAL_DOCUMENTATION",
                "retrieved_at": now_iso(),
                "exact_topic_verified": True,
                "authoritative_source_verified": True,
                "explicit_evidence_verified": True,
                "official_document_status": "VERIFIED",
                "official_document_payload": {"resolver": result, "evaluation": evaluation},
                "resolved_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", worker_item_id).eq("user_id", user_id).execute()
            return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": docs,
        "searches_attempted": [official_url],
        "transport_attempts": trace,
        "missing_evidence_reason": "No explicit authoritative evidence applicable to the exact locked topic was established.",
        "next_action": "Continue official document discovery; do not infer the missing rule.",
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(evidence),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "resolver_status": result.get("status"),
            "discovered_official_urls": docs,
            "evidence_count": len(evidence),
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()

    return "WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v6 — Official Documentation Deep Resolver")
st.info(
    "v6 folosește schema SQL nouă și salvează documentele/provenance. "
    "Procesează numai OFFICIAL_VERIFICATION nerezolvate și rămâne fail-closed."
)

v6_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

st.metric("Unresolved OFFICIAL tasks", len(v6_tasks))

if v6_tasks and st.button(
    "🔬 Run official deep resolution",
    type="primary",
    use_container_width=True,
    key="stage45_v6_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v6_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v6",
            "started_at": now_iso(),
            "summary": {"stage": 45, "version": "v6"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v6 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v6_tasks, 1):
            try:
                result_status = s45v6_run_task(task, run_id)
                if result_status == "RESOLVED":
                    resolved += 1
                else:
                    waiting += 1
            except Exception as exc:
                failed += 1
                st.warning(f"{task.get('requirement_label')}: {str(exc)[:500]}")
            bar.progress(idx / len(v6_tasks))

        final = (
            "FAILED" if failed
            else "COMPLETED" if resolved == len(v6_tasks)
            else "WAITING"
        )

        docs_saved = rows(
            "locked_evidence_official_documents",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "worker_run_id": run_id,
            },
            "created_at",
            5000,
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": final,
            "diagnostic_status": (
                "FAILED" if failed and resolved == 0 and waiting == 0
                else "PARTIAL_FAILURE" if failed
                else "CLEAN"
            ),
            "official_documents_checked": len(docs_saved),
            "official_sources_found": len(docs_saved),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v6",
            "provenance_summary": {
                "documents_saved": len(docs_saved),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v6: {final} — Resolved {resolved}, Waiting {waiting}, "
            f"Failed {failed}, Documents {len(docs_saved)}."
        )
        st.rerun()

v6_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)
v6_runs = [r for r in v6_runs if normalize_text(r.get("deep_resolution_version")).lower() == "v6"]

if v6_runs:
    latest_v6 = v6_runs[0]
    st.subheader("Latest Stage 45 v6 Result")
    x1, x2, x3, x4 = st.columns(4)
    x1.metric("Status", latest_v6.get("worker_status") or "—")
    x2.metric("Official resolved", latest_v6.get("official_tasks_resolved") or 0)
    x3.metric("Official waiting", latest_v6.get("official_tasks_waiting") or 0)
    x4.metric("Documents", latest_v6.get("official_documents_checked") or 0)

st.caption(
    "v6 invariant: finding a URL/document is not completion. "
    "COMPLETED requires explicit authoritative evidence traceable to the exact locked opportunity."
)
# =====================================================================
# END STAGE 45 v6
# =====================================================================

# =====================================================================
# STAGE 45 v7 — OFFICIAL TOPIC-AWARE DEEP RESOLVER
# =====================================================================

from urllib.parse import quote_plus, urlparse, parse_qs
import traceback

def s45v7_norm(value):
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value).lower())

def s45v7_identity_variants(value):
    raw = normalize_text(value)
    if not raw:
        return set()
    variants = {raw.lower(), s45v7_norm(raw)}
    # Horizon topic IDs are frequently represented with punctuation/spacing variants.
    variants.add(raw.lower().replace("_", "-"))
    variants.add(raw.lower().replace("-", "_"))
    return {v for v in variants if v}

def s45v7_exact_topic(text_or_url):
    hay = normalize_text(text_or_url).lower()
    hay_norm = s45v7_norm(hay)
    for v in s45v7_identity_variants(identity):
        if v in hay or s45v7_norm(v) in hay_norm:
            return True
    return False

def s45v7_requirement_family(task):
    raw = " ".join([
        normalize_text(task.get("requirement_key")),
        normalize_text(task.get("requirement_category")),
        normalize_text(task.get("requirement_label")),
    ]).lower()
    if any(x in raw for x in ("applicant", "eligib", "beneficiar", "participant")):
        return "applicant"
    if any(x in raw for x in ("consortium", "partner", "participants")):
        return "consortium"
    if any(x in raw for x in ("trl", "technology readiness", "readiness level")):
        return "trl"
    if any(x in raw for x in ("funding", "budget", "grant", "cofin", "co-fin")):
        return "funding"
    if any(x in raw for x in ("geographic", "country", "countries", "region")):
        return "geographic"
    return normalize_text(task.get("requirement_key")).lower() or "requirement"

def s45v7_needles(family):
    return {
        "applicant": [
            "eligible applicants", "eligible entities", "eligible participants",
            "legal entities eligible", "beneficiaries", "eligibility conditions",
            "conditions for participation"
        ],
        "consortium": [
            "consortium", "minimum number of", "independent legal entities",
            "at least three independent legal entities", "beneficiaries"
        ],
        "trl": [
            "technology readiness level", "trl", "starting trl", "target trl"
        ],
        "funding": [
            "funding rate", "reimbursement rate", "eligible costs",
            "maximum grant", "budget", "funding conditions"
        ],
        "geographic": [
            "eligible countries", "member states", "associated countries",
            "country eligibility", "eligible for funding"
        ],
    }.get(family, [])

def s45v7_flatten_strings(obj, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            out.extend(s45v7_flatten_strings(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(s45v7_flatten_strings(v, f"{path}[{i}]"))
    elif isinstance(obj, (str, int, float, bool)):
        val = normalize_text(obj)
        if val:
            out.append((path, val))
    return out

def s45v7_extract_explicit(payload_or_text, task, source_url):
    family = s45v7_requirement_family(task)
    needles = s45v7_needles(family)
    candidates = []

    if isinstance(payload_or_text, (dict, list)):
        strings = s45v7_flatten_strings(payload_or_text)
        # Join neighbouring searchable values into a traceable corpus while
        # retaining the JSON path as evidence reference.
        for path, value in strings:
            low = value.lower()
            if any(n in low for n in needles):
                candidates.append({
                    "url": source_url,
                    "reference": path,
                    "excerpt": value[:7000],
                    "exact_topic": s45v7_exact_topic(value) or s45v7_exact_topic(source_url),
                })
    else:
        clean = re.sub(r"\s+", " ", normalize_text(payload_or_text))
        low = clean.lower()
        for needle in needles:
            start = 0
            while True:
                idx = low.find(needle, start)
                if idx < 0:
                    break
                a, b = max(0, idx - 700), min(len(clean), idx + 1800)
                excerpt = clean[a:b].strip()
                candidates.append({
                    "url": source_url,
                    "reference": needle,
                    "excerpt": excerpt,
                    "exact_topic": s45v7_exact_topic(excerpt) or s45v7_exact_topic(source_url),
                })
                start = idx + len(needle)
                if len(candidates) >= 12:
                    break
            if len(candidates) >= 12:
                break

    # Prefer evidence that explicitly carries the locked topic identity.
    exact = [x for x in candidates if x["exact_topic"]]
    return (exact or candidates)[:8]

def s45v7_collect_urls(obj):
    urls = []
    for _, value in s45v7_flatten_strings(obj):
        for u in re.findall(r'https?://[^\s|<>"\']+', value):
            u = u.rstrip(".,;)")
            host = urlparse(u).netloc.lower()
            if (
                host.endswith("europa.eu")
                or host.endswith("ec.europa.eu")
                or host.endswith("funding-tenders.ec.europa.eu")
            ) and u not in urls:
                urls.append(u)
    return urls

def s45v7_search_urls():
    topic = normalize_text(identity)
    if not topic:
        return s45v3_urls(official_url)

    urls = list(s45v3_urls(official_url))
    q = quote_plus(topic)
    candidates = [
        f"https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text={q}&pageSize=50&pageNumber=1",
        f"https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text=%22{q}%22&pageSize=50&pageNumber=1",
    ]
    for u in candidates:
        if u not in urls:
            urls.append(u)
    return urls

def s45v7_fetch_any(url, timeout=35):
    result = s45v3_fetch(url, timeout=timeout)
    if not result.get("ok"):
        return {
            "ok": False,
            "url": url,
            "text": "",
            "json": None,
            "attempts": result.get("attempts", []),
        }

    response = result.get("response")
    payload = None
    try:
        payload = response.json()
    except Exception:
        pass

    return {
        "ok": True,
        "url": result.get("url") or url,
        "text": result.get("text", ""),
        "json": payload,
        "attempts": result.get("attempts", []),
    }

def s45v7_resolve_task(task, worker_run_id):
    item = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task.get("requirement_id"),
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "OFFICIAL_TOPIC_AWARE_DEEP_RESOLUTION",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v7"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item:
        raise RuntimeError("Could not create v7 worker item.")
    worker_item_id = str(item[0]["id"])

    trace = []
    discovered = []
    evidence = []

    # 1) Query every official source independently, including an exact-topic Search API query.
    queue = s45v7_search_urls()
    visited = set()

    while queue and len(visited) < 30:
        url = queue.pop(0)
        if not url or url in visited:
            continue
        visited.add(url)

        fetched = s45v7_fetch_any(url)
        trace.extend(fetched.get("attempts", []))
        if not fetched.get("ok"):
            continue

        payload = fetched.get("json")
        body = fetched.get("text", "")
        source_obj = payload if payload is not None else body

        local_evidence = s45v7_extract_explicit(source_obj, task, fetched["url"])
        evidence.extend(local_evidence)

        if payload is not None:
            for found_url in s45v7_collect_urls(payload):
                if found_url not in discovered:
                    discovered.append(found_url)
                if found_url not in visited and found_url not in queue:
                    queue.append(found_url)

        # Save fetched official page/document even without extracted evidence.
        best_excerpt = local_evidence[0]["excerpt"] if local_evidence else ""
        s45v6_save_document(
            task,
            worker_run_id,
            worker_item_id,
            fetched["url"],
            best_excerpt,
            status="FETCHED",
            payload={
                "version": "v7",
                "exact_topic": s45v7_exact_topic(fetched["url"]) or s45v7_exact_topic(best_excerpt),
                "evidence_candidates": len(local_evidence),
            },
        )

        # Enough explicit candidates; evaluator will still decide whether they prove the rule.
        if len(evidence) >= 10:
            break

    # 2) Prefer candidates traceable to the exact locked topic.
    exact_evidence = [
        e for e in evidence
        if e.get("exact_topic") or s45v7_exact_topic(e.get("url")) or s45v7_exact_topic(e.get("excerpt"))
    ]

    # Do not use generic programme rules as topic-specific proof unless the evaluator
    # can see an exact-topic candidate. This is deliberately fail-closed.
    usable = exact_evidence

    if usable:
        official_blob = "\n\n".join(
            f"[SOURCE {e.get('url')} | REF {e.get('reference')}]\n{e.get('excerpt')}"
            for e in usable[:10]
        )
        evaluation = ai_evaluate(
            task,
            collect_snapshot_evidence(task, sources),
            official_blob,
            usable[0].get("url") or official_url,
        )

        if evaluation.get("status") == "RESOLVED":
            worker_result = {
                "resolved_value": evaluation.get("resolved_value") or {},
                "evidence_source": "OFFICIAL_DOCUMENTATION_V7",
                "evidence_reference": evaluation.get("evidence_reference") or usable[0].get("reference") or task.get("requirement_label"),
                "evidence_url": evaluation.get("evidence_url") or usable[0].get("url"),
                "evidence_excerpt": evaluation.get("evidence_excerpt") or usable[0].get("excerpt", "")[:5000],
                "confidence": evaluation.get("confidence") or "High",
                "reason": evaluation.get("reason") or "Explicit topic-specific official evidence verified.",
            }
            update_execution_task_completed(task, worker_result, "VERIFIED")

            supabase.table("locked_evidence_worker_items").update({
                "worker_status": "RESOLVED",
                "resolved_value": worker_result["resolved_value"],
                "evidence_source": worker_result["evidence_source"],
                "evidence_reference": worker_result["evidence_reference"],
                "evidence_url": worker_result["evidence_url"],
                "evidence_excerpt": worker_result["evidence_excerpt"],
                "confidence": worker_result["confidence"],
                "official_verified": True,
                "reason": worker_result["reason"],
                "next_action": "RETURN_TO_STAGE_44",
                "documents_checked": list(visited),
                "searches_attempted": s45v7_search_urls(),
                "transport_attempts": trace,
                "resolution_method": "OFFICIAL_DOCUMENTATION",
                "retrieved_at": now_iso(),
                "exact_topic_verified": True,
                "authoritative_source_verified": True,
                "explicit_evidence_verified": True,
                "official_document_status": "VERIFIED",
                "official_document_payload": {
                    "version": "v7",
                    "evidence_candidates": usable[:10],
                    "evaluation": evaluation,
                },
                "resolved_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", worker_item_id).eq("user_id", user_id).execute()
            return "RESOLVED"

    reason = (
        "Official documents were inspected, but no explicit requirement evidence "
        "traceable to the exact locked topic was established."
    )
    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": list(visited),
        "searches_attempted": s45v7_search_urls(),
        "transport_attempts": trace,
        "missing_evidence_reason": reason,
        "next_action": "Keep WAITING_OFFICIAL; do not infer a missing call condition.",
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(evidence),
        "exact_topic_verified": bool(exact_evidence),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "version": "v7",
            "visited_count": len(visited),
            "discovered_count": len(discovered),
            "candidate_count": len(evidence),
            "exact_candidate_count": len(exact_evidence),
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()
    return "WAITING_OFFICIAL"



# ===== Stage 45 v7.1 — Persistent Error Diagnostics =====


def s45v72_log_result_failure(
    *,
    task,
    worker_run_id,
    result_status,
    reason="",
    result_payload=None,
    error_stage="V7_RESULT_STATUS",
    error_url=None,
):
    """Persist a resolver failure even when no Python exception was raised."""
    result_payload = result_payload or {}
    message = normalize_text(reason) or (
        f"Resolver returned terminal/non-success status: {result_status}"
    )

    row = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
        "worker_run_id": worker_run_id,
        "worker_item_id": None,
        "execution_task_id": task.get("id"),
        "requirement_id": task.get("requirement_id"),
        "opportunity_identity": identity,
        "requirement_key": task.get("requirement_key"),
        "requirement_category": task.get("requirement_category"),
        "requirement_label": task.get("requirement_label"),
        "route_type": task.get("route_type"),
        "destination_module": task.get("destination_module"),
        "deep_resolution_version": "v7.3",
        "error_stage": error_stage,
        "error_type": "RESULT_FAILED",
        "error_message": message[:12000],
        "error_url": error_url,
        "error_document": None,
        "error_traceback": None,
        "request_payload": {
            "identity": identity,
            "requirement": task.get("requirement_label"),
            "route_type": task.get("route_type"),
        },
        "response_payload": s45v71_safe_json(result_payload),
        "diagnostic_payload": {
            "stage": 45,
            "version": "v7.3",
            "result_status": result_status,
            "function": "s45v7_resolve_task",
        },
        "retryable": True,
        "resolved": False,
        "updated_at": now_iso(),
    }

    supabase.table("locked_evidence_worker_errors").insert(row).execute()

    diagnostic = {
        "error_type": "RESULT_FAILED",
        "error_message": message,
        "error_stage": error_stage,
    }
    s45v71_update_run_diagnostics(worker_run_id, task, diagnostic)
    return diagnostic


def s45v71_safe_json(value):
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return {"repr": repr(value)}

def s45v71_log_error(
    *,
    task,
    worker_run_id,
    worker_item_id=None,
    error_stage,
    exc,
    error_url=None,
    error_document=None,
    request_payload=None,
    response_payload=None,
    diagnostic_payload=None,
):
    error_type = type(exc).__name__
    error_message = str(exc)[:12000]
    tb = traceback.format_exc()[-30000:]

    row = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
        "worker_run_id": worker_run_id,
        "worker_item_id": worker_item_id,
        "execution_task_id": task.get("id"),
        "requirement_id": task.get("requirement_id"),
        "opportunity_identity": identity,
        "requirement_key": task.get("requirement_key"),
        "requirement_category": task.get("requirement_category"),
        "requirement_label": task.get("requirement_label"),
        "route_type": task.get("route_type"),
        "destination_module": task.get("destination_module"),
        "deep_resolution_version": "v7.3",
        "error_stage": error_stage,
        "error_type": error_type,
        "error_message": error_message,
        "error_url": error_url,
        "error_document": error_document,
        "error_traceback": tb,
        "request_payload": s45v71_safe_json(request_payload or {}),
        "response_payload": s45v71_safe_json(response_payload or {}),
        "diagnostic_payload": s45v71_safe_json(diagnostic_payload or {}),
        "retryable": True,
        "resolved": False,
        "updated_at": now_iso(),
    }

    try:
        supabase.table("locked_evidence_worker_errors").insert(row).execute()
    except Exception as log_exc:
        st.warning(
            f"Diagnostic logging failed for {task.get('requirement_label')}: "
            f"{type(log_exc).__name__}: {str(log_exc)[:600]}"
        )

    if worker_item_id:
        try:
            supabase.table("locked_evidence_worker_items").update({
                "worker_status": "FAILED",
                "error_type": error_type,
                "error_message": error_message,
                "error_stage": error_stage,
                "error_url": error_url,
                "error_document": error_document,
                "error_traceback": tb,
                "diagnostic_payload": s45v71_safe_json(diagnostic_payload or {}),
                "failed_at": now_iso(),
                "official_document_status": "FAILED",
                "updated_at": now_iso(),
            }).eq("id", worker_item_id).eq("user_id", user_id).execute()
        except Exception:
            pass

    return {
        "error_type": error_type,
        "error_message": error_message,
        "error_stage": error_stage,
        "traceback": tb,
    }

def s45v71_update_run_diagnostics(worker_run_id, task, diagnostic):
    try:
        current = (
            supabase.table("locked_evidence_worker_runs")
            .select("diagnostics,error_count")
            .eq("id", worker_run_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        ).data or []

        existing = current[0] if current else {}
        diagnostics = existing.get("diagnostics") or []
        if not isinstance(diagnostics, list):
            diagnostics = []

        diagnostics.append({
            "requirement": task.get("requirement_label"),
            "task_id": task.get("id"),
            "error_type": diagnostic.get("error_type"),
            "error_message": diagnostic.get("error_message"),
            "error_stage": diagnostic.get("error_stage"),
            "recorded_at": now_iso(),
        })

        supabase.table("locked_evidence_worker_runs").update({
            "diagnostic_status": "FAILED",
            "last_error_type": diagnostic.get("error_type"),
            "last_error_message": diagnostic.get("error_message"),
            "last_error_stage": diagnostic.get("error_stage"),
            "last_error_task": task.get("requirement_label"),
            "error_count": int(existing.get("error_count") or 0) + 1,
            "diagnostics": diagnostics[-50:],
            "updated_at": now_iso(),
        }).eq("id", worker_run_id).eq("user_id", user_id).execute()
    except Exception:
        pass

def s45v71_latest_errors(limit=20):
    try:
        return (
            supabase.table("locked_evidence_worker_errors")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_lock_id", lock_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
    except Exception:
        return []

# ===== End Stage 45 v7.1 diagnostics =====

st.divider()
st.subheader("Stage 45 v7 — Official Topic-Aware Deep Resolver")
st.info(
    "v7 caută explicit topicul blocat în sursele oficiale EC, inspectează JSON-ul Search API "
    "și documentele oficiale descoperite și rămâne fail-closed: fără dovadă explicită => WAITING_OFFICIAL."
)

v7_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

st.metric("Unresolved OFFICIAL tasks (v7)", len(v7_tasks))

if v7_tasks and st.button(
    "🧭 Run Stage 45 v7 topic-aware resolution",
    type="primary",
    use_container_width=True,
    key="stage45_v7_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v7_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.1",
            "started_at": now_iso(),
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "summary": {"stage": 45, "version": "v7.3"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v7_tasks, 1):
            try:
                state = s45v7_resolve_task(task, run_id)
                state_norm = normalize_text(state).upper()

                if state_norm == "RESOLVED":
                    resolved += 1

                elif state_norm in {"WAITING_OFFICIAL", "WAITING", "PENDING"}:
                    waiting += 1

                else:
                    failed += 1
                    diagnostic = s45v72_log_result_failure(
                        task=task,
                        worker_run_id=run_id,
                        result_status=state_norm or "EMPTY",
                        reason=f"s45v7_resolve_task returned {state_norm or 'EMPTY'}",
                        result_payload={
                            "returned_state": state,
                            "task_status_before": task.get("task_status"),
                            "route_type": task.get("route_type"),
                            "requirement_key": task.get("requirement_key"),
                        },
                        error_stage="V7_RESULT_CLASSIFICATION",
                        error_url=official_url,
                    )
                    st.error(
                        f"{task.get('requirement_label')} — "
                        f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                    )

            except Exception as exc:
                failed += 1
                diagnostic = s45v71_log_error(
                    task=task,
                    worker_run_id=run_id,
                    worker_item_id=None,
                    error_stage="V7_TASK_EXECUTION",
                    exc=exc,
                    error_url=official_url,
                    request_payload={
                        "identity": identity,
                        "requirement": task.get("requirement_label"),
                        "route_type": task.get("route_type"),
                    },
                    diagnostic_payload={
                        "stage": 45,
                        "version": "v7.3",
                        "function": "s45v7_resolve_task",
                    },
                )
                s45v71_update_run_diagnostics(run_id, task, diagnostic)
                st.error(
                    f"{task.get('requirement_label')} — "
                    f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                )

            bar.progress(idx / len(v7_tasks))

        final = (
            "FAILED" if failed
            else "COMPLETED" if resolved == len(v7_tasks)
            else "WAITING"
        )

        docs_saved = rows(
            "locked_evidence_official_documents",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "worker_run_id": run_id,
            },
            "created_at",
            5000,
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": final,
            "official_documents_checked": len(docs_saved),
            "official_sources_found": len(docs_saved),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7",
            "provenance_summary": {
                "documents_saved": len(docs_saved),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7: {final} — Resolved {resolved}, Waiting {waiting}, "
            f"Failed {failed}, Documents {len(docs_saved)}."
        )
        st.rerun()

v7_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)
v7_runs = [
    r for r in v7_runs
    if normalize_text(r.get("deep_resolution_version")).lower() in {"v7", "v7.1", "v7.2"}
]

if v7_runs:
    latest_v7 = v7_runs[0]
    st.subheader("Latest Stage 45 v7.3 Result")
    y1, y2, y3, y4 = st.columns(4)
    y1.metric("Status", latest_v7.get("worker_status") or "—")
    y2.metric("Official resolved", latest_v7.get("official_tasks_resolved") or 0)
    y3.metric("Official waiting", latest_v7.get("official_tasks_waiting") or 0)
    y4.metric("Documents", latest_v7.get("official_documents_checked") or 0)

    d1, d2, d3 = st.columns(3)
    d1.metric("Diagnostic status", latest_v7.get("diagnostic_status") or "—")
    d2.metric("Error count", latest_v7.get("error_count") or 0)
    d3.metric("Last error type", latest_v7.get("last_error_type") or "—")

    if latest_v7.get("last_error_message"):
        st.error(
            f"Last error: {latest_v7.get('last_error_task') or '—'} — "
            f"{latest_v7.get('last_error_type') or 'Error'}: "
            f"{latest_v7.get('last_error_message')}"
        )

st.caption(
    "v7 invariant: generic programme text is not enough. COMPLETED requires explicit "
    "authoritative evidence traceable to the exact locked opportunity."
)
# =====================================================================
# END STAGE 45 v7
# =====================================================================


st.divider()
st.subheader("Stage 45 v7.3 — Persistent Error Diagnostics")

diagnostic_errors = s45v71_latest_errors(30)

if diagnostic_errors:
    st.error(f"Persisted errors: {len(diagnostic_errors)}")
    st.dataframe(
        [
            {
                "Time": e.get("created_at"),
                "Requirement": e.get("requirement_label"),
                "Stage": e.get("error_stage"),
                "Type": e.get("error_type"),
                "Message": e.get("error_message"),
                "URL": e.get("error_url"),
                "Retryable": e.get("retryable"),
                "Resolved": e.get("resolved"),
            }
            for e in diagnostic_errors
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Diagnostic details")
    for e in diagnostic_errors[:10]:
        with st.expander(
            f"{e.get('requirement_label') or 'Unknown requirement'} — "
            f"{e.get('error_type') or 'Error'}"
        ):
            st.write(f"**Stage:** {e.get('error_stage') or '—'}")
            st.write(f"**Message:** {e.get('error_message') or '—'}")
            st.write(f"**URL:** {e.get('error_url') or '—'}")
            st.write(f"**Document:** {e.get('error_document') or '—'}")
            if e.get("error_traceback"):
                st.code(e.get("error_traceback"), language="text")
            if e.get("diagnostic_payload"):
                st.json(e.get("diagnostic_payload"))
else:
    st.success("Nu există încă erori persistente pentru lock-ul curent.")

st.caption(
    "v7.3 păstrează diagnosticul persistent și folosește resolution_method canonic OFFICIAL_DOCUMENTATION. "
    "Doar persistă excepțiile pentru diagnostic și reparare controlată."
)



# =====================================================================
# STAGE 45 v7.4 — OFFICIAL DISCOVERY TRACE + FALLBACK
# =====================================================================

def s45v74_extract_search_hits(payload):
    """Extract likely EC search hits with titles/snippets/URLs from arbitrary Search API JSON."""
    hits = []
    if not isinstance(payload, (dict, list)):
        return hits

    def walk(node):
        if isinstance(node, dict):
            # Generic extraction across varying EC Search API shapes.
            url = None
            title = None
            snippet = None
            identity_hit = False

            for k, v in node.items():
                kl = str(k).lower()
                if isinstance(v, str):
                    if kl in {"url", "link", "href", "uri"} and v.startswith("http"):
                        url = v
                    elif "title" in kl or kl in {"name", "label"}:
                        title = title or v
                    elif any(x in kl for x in ("summary", "snippet", "description", "content", "text")):
                        snippet = snippet or v

                    if identity and identity.lower() in v.lower():
                        identity_hit = True

            if url or title or snippet:
                hits.append({
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "identity_hit": identity_hit,
                    "raw": node,
                })

            for v in node.values():
                walk(v)

        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)

    # Deduplicate by URL/title/snippet tuple.
    dedup = []
    seen = set()
    for h in hits:
        key = (
            normalize_text(h.get("url")),
            normalize_text(h.get("title")),
            normalize_text(h.get("snippet"))[:500],
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(h)

    return dedup[:100]

def s45v74_candidate_urls_from_hit(hit):
    urls = []
    for field in ("url", "title", "snippet"):
        val = normalize_text(hit.get(field))
        for u in re.findall(r'https?://[^\s|<>"\']+', val):
            u = u.rstrip(".,;)")
            host = urlparse(u).netloc.lower()
            if host.endswith("europa.eu") or host.endswith("ec.europa.eu"):
                if u not in urls:
                    urls.append(u)
    return urls

def s45v74_discovery_probe():
    """Probe search/discovery only. No task verdict changes."""
    probes = []
    search_urls = s45v7_search_urls()

    for url in search_urls:
        fetched = s45v7_fetch_any(url)
        rec = {
            "url": url,
            "ok": fetched.get("ok"),
            "attempts": fetched.get("attempts", []),
            "json_type": type(fetched.get("json")).__name__ if fetched.get("json") is not None else None,
            "text_len": len(fetched.get("text") or ""),
            "hits": [],
        }

        payload = fetched.get("json")
        if payload is not None:
            rec["hits"] = s45v74_extract_search_hits(payload)

        probes.append(rec)

    return probes

def s45v74_discovery_summary(probes):
    out = []
    for p in probes:
        out.append({
            "Search URL": p.get("url"),
            "OK": p.get("ok"),
            "JSON": p.get("json_type"),
            "Text bytes": p.get("text_len"),
            "Hits": len(p.get("hits") or []),
            "Identity hits": sum(1 for h in (p.get("hits") or []) if h.get("identity_hit")),
        })
    return out

def s45v74_expand_queue_from_probes(probes):
    queue = []
    for p in probes:
        for h in p.get("hits") or []:
            if h.get("identity_hit") or s45v7_exact_topic(h.get("title")) or s45v7_exact_topic(h.get("snippet")):
                for u in s45v74_candidate_urls_from_hit(h):
                    if u not in queue:
                        queue.append(u)

            raw = h.get("raw")
            for u in s45v7_collect_urls(raw):
                if s45v7_exact_topic(u) or h.get("identity_hit"):
                    if u not in queue:
                        queue.append(u)

    return queue[:30]

def s45v74_run_task(task, worker_run_id, probes):
    item = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task.get("requirement_id"),
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "OFFICIAL_DISCOVERY_TRACE_FALLBACK",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v7.4"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item:
        raise RuntimeError("Could not create v7.4 worker item.")
    worker_item_id = str(item[0]["id"])

    evidence = []
    visited = set()
    trace = []
    queue = []

    # Start with exact-topic URLs discovered from Search API payloads.
    queue.extend(s45v74_expand_queue_from_probes(probes))

    # Also include original official source parts.
    for u in s45v3_urls(official_url):
        if u not in queue:
            queue.append(u)

    # Finally include exact-topic Search API URLs themselves so their JSON can be used as evidence.
    for u in s45v7_search_urls():
        if u not in queue:
            queue.append(u)

    while queue and len(visited) < 40:
        url = queue.pop(0)
        if not url or url in visited:
            continue
        visited.add(url)

        fetched = s45v7_fetch_any(url)
        trace.extend(fetched.get("attempts", []))
        if not fetched.get("ok"):
            continue

        payload = fetched.get("json")
        body = fetched.get("text") or ""
        source_obj = payload if payload is not None else body

        local = s45v7_extract_explicit(source_obj, task, fetched.get("url") or url)
        evidence.extend(local)

        # Persist every successfully fetched official resource.
        best_excerpt = local[0]["excerpt"] if local else ""
        s45v6_save_document(
            task,
            worker_run_id,
            worker_item_id,
            fetched.get("url") or url,
            best_excerpt,
            status="FETCHED",
            payload={
                "version": "v7.4",
                "source_kind": "SEARCH_API_JSON" if payload is not None else "OFFICIAL_RESOURCE",
                "evidence_candidates": len(local),
            },
        )

        # Follow any official URLs discovered in JSON/text.
        if payload is not None:
            next_urls = s45v7_collect_urls(payload)
        else:
            next_urls = []
            for u in re.findall(r'https?://[^\s|<>"\']+', body):
                host = urlparse(u).netloc.lower()
                if host.endswith("europa.eu") or host.endswith("ec.europa.eu"):
                    next_urls.append(u.rstrip(".,;)"))

        for u in next_urls:
            if u not in visited and u not in queue:
                queue.append(u)

        if len(evidence) >= 12:
            break

    exact_evidence = [
        e for e in evidence
        if e.get("exact_topic")
        or s45v7_exact_topic(e.get("url"))
        or s45v7_exact_topic(e.get("excerpt"))
    ]

    if exact_evidence:
        official_blob = "\n\n".join(
            f"[SOURCE {e.get('url')} | REF {e.get('reference')}]\n{e.get('excerpt')}"
            for e in exact_evidence[:10]
        )

        evaluation = ai_evaluate(
            task,
            collect_snapshot_evidence(task, sources),
            official_blob,
            exact_evidence[0].get("url") or official_url,
        )

        if evaluation.get("status") == "RESOLVED":
            worker_result = {
                "resolved_value": evaluation.get("resolved_value") or {},
                "evidence_source": "OFFICIAL_DOCUMENTATION",
                "evidence_reference": evaluation.get("evidence_reference") or exact_evidence[0].get("reference") or task.get("requirement_label"),
                "evidence_url": evaluation.get("evidence_url") or exact_evidence[0].get("url"),
                "evidence_excerpt": evaluation.get("evidence_excerpt") or exact_evidence[0].get("excerpt", "")[:5000],
                "confidence": evaluation.get("confidence") or "High",
                "reason": evaluation.get("reason") or "Explicit official evidence verified by v7.4.",
            }

            update_execution_task_completed(task, worker_result, "VERIFIED")

            supabase.table("locked_evidence_worker_items").update({
                "worker_status": "RESOLVED",
                "resolved_value": worker_result["resolved_value"],
                "evidence_source": worker_result["evidence_source"],
                "evidence_reference": worker_result["evidence_reference"],
                "evidence_url": worker_result["evidence_url"],
                "evidence_excerpt": worker_result["evidence_excerpt"],
                "confidence": worker_result["confidence"],
                "official_verified": True,
                "reason": worker_result["reason"],
                "next_action": "RETURN_TO_STAGE_44",
                "documents_checked": list(visited),
                "searches_attempted": s45v7_search_urls(),
                "transport_attempts": trace,
                "resolution_method": "OFFICIAL_DOCUMENTATION",
                "retrieved_at": now_iso(),
                "exact_topic_verified": True,
                "authoritative_source_verified": True,
                "explicit_evidence_verified": True,
                "official_document_status": "VERIFIED",
                "official_document_payload": {
                    "version": "v7.4",
                    "evidence_candidates": exact_evidence[:10],
                    "evaluation": evaluation,
                },
                "resolved_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", worker_item_id).eq("user_id", user_id).execute()

            return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": list(visited),
        "searches_attempted": s45v7_search_urls(),
        "transport_attempts": trace,
        "missing_evidence_reason": (
            "v7.4 inspected Search API payloads and discovered official resources, "
            "but no explicit exact-topic rule was sufficient to resolve this requirement."
        ),
        "next_action": "Remain WAITING_OFFICIAL; review discovery trace and official resources.",
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(evidence),
        "exact_topic_verified": bool(exact_evidence),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "version": "v7.4",
            "visited_count": len(visited),
            "candidate_count": len(evidence),
            "exact_candidate_count": len(exact_evidence),
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()

    return "WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v7.4 — Official Discovery Trace + Fallback")
st.info(
    "v7.4 diagnostichează mai întâi Search API-ul, afișează hit-urile și apoi urmărește "
    "resursele oficiale găsite. Nu schimbă verdictul fără dovadă explicită."
)

v74_probes = s45v74_discovery_probe()

if v74_probes:
    st.subheader("Search API discovery trace")
    st.dataframe(
        s45v74_discovery_summary(v74_probes),
        use_container_width=True,
        hide_index=True,
    )

    total_hits = sum(len(p.get("hits") or []) for p in v74_probes)
    total_identity_hits = sum(
        sum(1 for h in (p.get("hits") or []) if h.get("identity_hit"))
        for p in v74_probes
    )
    c74a, c74b, c74c = st.columns(3)
    c74a.metric("Search probes", len(v74_probes))
    c74b.metric("Search hits", total_hits)
    c74c.metric("Exact identity hits", total_identity_hits)

    with st.expander("Discovery hit details", expanded=False):
        details = []
        for p in v74_probes:
            for h in p.get("hits") or []:
                details.append({
                    "Search URL": p.get("url"),
                    "Identity hit": h.get("identity_hit"),
                    "Title": h.get("title"),
                    "URL": h.get("url"),
                    "Snippet": normalize_text(h.get("snippet"))[:1200],
                })
        if details:
            st.dataframe(details, use_container_width=True, hide_index=True)
        else:
            st.info("Search API responded, but no structured hits were extracted.")

v74_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

if v74_tasks and st.button(
    "🧪 Run Stage 45 v7.4 discovery + resolution",
    type="primary",
    use_container_width=True,
    key="stage45_v74_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v74_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.4",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {"stage": 45, "version": "v7.4"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.4 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v74_tasks, 1):
            try:
                state = s45v74_run_task(task, run_id, v74_probes)
                if normalize_text(state).upper() == "RESOLVED":
                    resolved += 1
                else:
                    waiting += 1
            except Exception as exc:
                failed += 1
                diagnostic = s45v71_log_error(
                    task=task,
                    worker_run_id=run_id,
                    worker_item_id=None,
                    error_stage="V74_TASK_EXECUTION",
                    exc=exc,
                    error_url=official_url,
                    request_payload={
                        "identity": identity,
                        "requirement": task.get("requirement_label"),
                    },
                    diagnostic_payload={
                        "stage": 45,
                        "version": "v7.4",
                        "function": "s45v74_run_task",
                    },
                )
                s45v71_update_run_diagnostics(run_id, task, diagnostic)
                st.error(
                    f"{task.get('requirement_label')} — "
                    f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                )

            bar.progress(idx / len(v74_tasks))

        final = (
            "FAILED" if failed and resolved == 0 and waiting == 0
            else "PARTIAL_FAILURE" if failed
            else "COMPLETED" if resolved == len(v74_tasks)
            else "WAITING"
        )

        docs_saved = rows(
            "locked_evidence_official_documents",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "worker_run_id": run_id,
            },
            "created_at",
            5000,
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": "FAILED" if final == "FAILED" else ("COMPLETED" if final == "COMPLETED" else "WAITING"),
            "diagnostic_status": (
                "FAILED" if final == "FAILED"
                else "PARTIAL_FAILURE" if final == "PARTIAL_FAILURE"
                else "CLEAN"
            ),
            "official_documents_checked": len(docs_saved),
            "official_sources_found": len(docs_saved),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7.4",
            "provenance_summary": {
                "documents_saved": len(docs_saved),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
                "search_probes": len(v74_probes),
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.4: {final} — Resolved {resolved}, Waiting {waiting}, "
            f"Failed {failed}, Documents {len(docs_saved)}."
        )
        st.rerun()

v74_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)
v74_runs = [
    r for r in v74_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.4"
]

if v74_runs:
    latest_v74 = v74_runs[0]
    st.subheader("Latest Stage 45 v7.4 Result")

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Status", latest_v74.get("worker_status") or "—")
    r2.metric("Official resolved", latest_v74.get("official_tasks_resolved") or 0)
    r3.metric("Official waiting", latest_v74.get("official_tasks_waiting") or 0)
    r4.metric("Documents", latest_v74.get("official_documents_checked") or 0)

    rr1, rr2, rr3 = st.columns(3)
    rr1.metric("Diagnostic status", latest_v74.get("diagnostic_status") or "—")
    rr2.metric("Error count", latest_v74.get("error_count") or 0)
    rr3.metric("Search probes", (latest_v74.get("provenance_summary") or {}).get("search_probes", 0))

st.caption(
    "v7.4 invariant: discovery trace is diagnostic evidence only. "
    "A task becomes COMPLETED only with explicit authoritative evidence."
)
# =====================================================================
# END STAGE 45 v7.4
# =====================================================================



# =====================================================================
# STAGE 45 v7.5 — SEARCH API RAW RESPONSE DIAGNOSTICS + ADAPTIVE PARSER
# =====================================================================

def s45v75_response_preview(value, limit=5000):
    if value is None:
        return ""
    try:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)[:limit]
        return normalize_text(value)[:limit]
    except Exception:
        return repr(value)[:limit]

def s45v75_probe_url(url, timeout=35):
    record = {
        "url": url,
        "ok": False,
        "http_status": None,
        "content_type": "",
        "response_bytes": 0,
        "final_url": url,
        "json_type": None,
        "json_keys": [],
        "preview": "",
        "attempts": [],
        "error": "",
    }

    try:
        result = s45v3_fetch(url, timeout=timeout)
        record["attempts"] = result.get("attempts", [])

        response = result.get("response")
        record["ok"] = bool(result.get("ok"))

        if response is not None:
            record["http_status"] = getattr(response, "status_code", None)
            record["content_type"] = normalize_text(
                getattr(response, "headers", {}).get("content-type", "")
            )
            record["final_url"] = normalize_text(getattr(response, "url", "")) or url
            content = getattr(response, "content", b"") or b""
            record["response_bytes"] = len(content)

            try:
                payload = response.json()
                record["json_type"] = type(payload).__name__
                if isinstance(payload, dict):
                    record["json_keys"] = list(payload.keys())[:100]
                record["preview"] = s45v75_response_preview(payload)
            except Exception:
                record["preview"] = s45v75_response_preview(result.get("text", ""))

        else:
            record["preview"] = s45v75_response_preview(result.get("text", ""))

        return record

    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)}"
        return record

def s45v75_all_probes():
    out = []
    seen = set()
    for u in s45v7_search_urls():
        if u and u not in seen:
            seen.add(u)
            out.append(s45v75_probe_url(u))
    return out

def s45v75_adaptive_hits_from_json(payload, source_url):
    """
    Adaptive extractor for unknown Search API shapes.
    It treats any dict containing topic identity/title/url/description-like fields
    as a candidate hit and recursively descends.
    """
    hits = []

    def walk(node, path=""):
        if isinstance(node, dict):
            lower_map = {str(k).lower(): v for k, v in node.items()}

            text_parts = []
            candidate_url = None
            candidate_title = None

            for k, v in node.items():
                kl = str(k).lower()
                if isinstance(v, (str, int, float, bool)):
                    sv = normalize_text(v)
                    text_parts.append(f"{k}: {sv}")

                    if not candidate_url and (
                        kl in {"url", "link", "href", "uri", "weburl", "web_url"}
                        or "url" in kl
                        or "link" in kl
                    ) and sv.startswith("http"):
                        candidate_url = sv

                    if not candidate_title and any(
                        x in kl for x in ("title", "name", "label", "subject", "topic")
                    ):
                        candidate_title = sv

            joined = " | ".join(text_parts)
            identity_hit = s45v7_exact_topic(joined)

            if identity_hit or candidate_url or candidate_title:
                hits.append({
                    "path": path or "$",
                    "url": candidate_url,
                    "title": candidate_title,
                    "text": joined[:10000],
                    "identity_hit": identity_hit,
                    "raw": node,
                })

            for k, v in node.items():
                child = f"{path}.{k}" if path else str(k)
                walk(v, child)

        elif isinstance(node, list):
            for i, v in enumerate(node):
                child = f"{path}[{i}]"
                walk(v, child)

    walk(payload)

    dedup = []
    seen = set()
    for h in hits:
        key = (
            normalize_text(h.get("url")),
            normalize_text(h.get("title")),
            normalize_text(h.get("text"))[:1000],
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(h)

    return dedup[:200]

def s45v75_probe_with_payload(url, timeout=35):
    result = s45v3_fetch(url, timeout=timeout)
    response = result.get("response")
    payload = None
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            pass
    return result, payload

def s45v75_extract_candidate_urls_from_payload(payload):
    urls = []
    if payload is None:
        return urls

    for _, value in s45v7_flatten_strings(payload):
        for u in re.findall(r'https?://[^\s|<>"\']+', value):
            u = u.rstrip(".,;)")
            host = urlparse(u).netloc.lower()
            if (
                host.endswith("europa.eu")
                or host.endswith("ec.europa.eu")
                or host.endswith("funding-tenders.ec.europa.eu")
            ) and u not in urls:
                urls.append(u)

    return urls[:100]

def s45v75_discovery_diagnostics():
    diagnostics = []
    for url in s45v7_search_urls():
        result, payload = s45v75_probe_with_payload(url)

        response = result.get("response")
        status = getattr(response, "status_code", None) if response is not None else None
        ctype = ""
        final_url = url
        byte_count = 0
        if response is not None:
            try:
                ctype = normalize_text(response.headers.get("content-type"))
            except Exception:
                pass
            try:
                final_url = normalize_text(response.url) or url
            except Exception:
                pass
            try:
                byte_count = len(response.content or b"")
            except Exception:
                pass

        adaptive_hits = s45v75_adaptive_hits_from_json(payload, final_url) if payload is not None else []
        candidate_urls = s45v75_extract_candidate_urls_from_payload(payload)

        diagnostics.append({
            "url": url,
            "final_url": final_url,
            "ok": bool(result.get("ok")),
            "http_status": status,
            "content_type": ctype,
            "response_bytes": byte_count,
            "json_type": type(payload).__name__ if payload is not None else None,
            "json_keys": list(payload.keys())[:100] if isinstance(payload, dict) else [],
            "preview": s45v75_response_preview(payload if payload is not None else result.get("text", "")),
            "adaptive_hits": adaptive_hits,
            "candidate_urls": candidate_urls,
            "attempts": result.get("attempts", []),
        })

    return diagnostics

def s45v75_summarize_probe_rows(diags):
    rows_out = []
    for d in diags:
        rows_out.append({
            "HTTP": d.get("http_status"),
            "OK": d.get("ok"),
            "Content-Type": d.get("content_type"),
            "Bytes": d.get("response_bytes"),
            "JSON type": d.get("json_type"),
            "Top-level keys": ", ".join(d.get("json_keys") or [])[:1000],
            "Adaptive hits": len(d.get("adaptive_hits") or []),
            "Candidate URLs": len(d.get("candidate_urls") or []),
            "Final URL": d.get("final_url"),
        })
    return rows_out

def s45v75_run_task(task, worker_run_id, discovery_diags):
    item_insert = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task.get("requirement_id"),
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "SEARCH_API_RAW_DIAGNOSTICS_ADAPTIVE_RESOLUTION",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v7.5"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item_insert:
        raise RuntimeError("Could not create v7.5 worker item.")

    worker_item_id = str(item_insert[0]["id"])

    queue = []
    visited = set()
    trace = []
    evidence = []

    # 1. Seed from every Search API endpoint.
    for d in discovery_diags:
        u = normalize_text(d.get("final_url") or d.get("url"))
        if u and u not in queue:
            queue.append(u)

        for h in d.get("adaptive_hits") or []:
            hu = normalize_text(h.get("url"))
            if hu and hu not in queue:
                queue.append(hu)

        for cu in d.get("candidate_urls") or []:
            if cu and cu not in queue:
                queue.append(cu)

    # 2. Preserve upstream official URL(s).
    for u in s45v3_urls(official_url):
        if u not in queue:
            queue.append(u)

    while queue and len(visited) < 50:
        url = queue.pop(0)
        if not url or url in visited:
            continue

        visited.add(url)
        result = s45v3_fetch(url, timeout=35)
        trace.extend(result.get("attempts", []))

        if not result.get("ok"):
            continue

        response = result.get("response")
        payload = None
        if response is not None:
            try:
                payload = response.json()
            except Exception:
                pass

        source_obj = payload if payload is not None else result.get("text", "")
        fetched_url = normalize_text(result.get("url")) or url

        local_evidence = s45v7_extract_explicit(source_obj, task, fetched_url)
        evidence.extend(local_evidence)

        # Persist each successfully fetched official resource.
        best_excerpt = local_evidence[0]["excerpt"] if local_evidence else ""
        s45v6_save_document(
            task,
            worker_run_id,
            worker_item_id,
            fetched_url,
            best_excerpt,
            status="FETCHED",
            payload={
                "version": "v7.5",
                "payload_type": type(payload).__name__ if payload is not None else "text",
                "evidence_candidates": len(local_evidence),
                "response_preview": s45v75_response_preview(
                    payload if payload is not None else result.get("text", ""),
                    4000,
                ),
            },
        )

        # Follow recursively discovered official URLs.
        next_urls = []
        if payload is not None:
            next_urls.extend(s45v75_extract_candidate_urls_from_payload(payload))
            for hit in s45v75_adaptive_hits_from_json(payload, fetched_url):
                hu = normalize_text(hit.get("url"))
                if hu:
                    next_urls.append(hu)
        else:
            for u in re.findall(r'https?://[^\s|<>"\']+', result.get("text", "")):
                host = urlparse(u).netloc.lower()
                if host.endswith("europa.eu") or host.endswith("ec.europa.eu"):
                    next_urls.append(u.rstrip(".,;)"))

        for u in next_urls:
            if u and u not in visited and u not in queue:
                queue.append(u)

        if len(evidence) >= 20:
            break

    exact_evidence = [
        e for e in evidence
        if e.get("exact_topic")
        or s45v7_exact_topic(e.get("url"))
        or s45v7_exact_topic(e.get("excerpt"))
    ]

    if exact_evidence:
        official_blob = "\n\n".join(
            f"[SOURCE {e.get('url')} | REF {e.get('reference')}]\n{e.get('excerpt')}"
            for e in exact_evidence[:12]
        )

        evaluation = ai_evaluate(
            task,
            collect_snapshot_evidence(task, sources),
            official_blob,
            exact_evidence[0].get("url") or official_url,
        )

        if evaluation.get("status") == "RESOLVED":
            worker_result = {
                "resolved_value": evaluation.get("resolved_value") or {},
                "evidence_source": "OFFICIAL_DOCUMENTATION",
                "evidence_reference": evaluation.get("evidence_reference") or exact_evidence[0].get("reference") or task.get("requirement_label"),
                "evidence_url": evaluation.get("evidence_url") or exact_evidence[0].get("url"),
                "evidence_excerpt": evaluation.get("evidence_excerpt") or exact_evidence[0].get("excerpt", "")[:5000],
                "confidence": evaluation.get("confidence") or "High",
                "reason": evaluation.get("reason") or "Explicit official evidence verified by v7.5.",
            }

            update_execution_task_completed(task, worker_result, "VERIFIED")

            supabase.table("locked_evidence_worker_items").update({
                "worker_status": "RESOLVED",
                "resolved_value": worker_result["resolved_value"],
                "evidence_source": worker_result["evidence_source"],
                "evidence_reference": worker_result["evidence_reference"],
                "evidence_url": worker_result["evidence_url"],
                "evidence_excerpt": worker_result["evidence_excerpt"],
                "confidence": worker_result["confidence"],
                "official_verified": True,
                "reason": worker_result["reason"],
                "next_action": "RETURN_TO_STAGE_44",
                "documents_checked": list(visited),
                "searches_attempted": s45v7_search_urls(),
                "transport_attempts": trace,
                "resolution_method": "OFFICIAL_DOCUMENTATION",
                "retrieved_at": now_iso(),
                "exact_topic_verified": True,
                "authoritative_source_verified": True,
                "explicit_evidence_verified": True,
                "official_document_status": "VERIFIED",
                "official_document_payload": {
                    "version": "v7.5",
                    "evidence_candidates": exact_evidence[:12],
                    "evaluation": evaluation,
                },
                "resolved_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", worker_item_id).eq("user_id", user_id).execute()

            return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": list(visited),
        "searches_attempted": s45v7_search_urls(),
        "transport_attempts": trace,
        "missing_evidence_reason": (
            "v7.5 successfully diagnosed Search API responses and followed official resources, "
            "but no explicit exact-topic evidence was sufficient to resolve this requirement."
        ),
        "next_action": "Remain WAITING_OFFICIAL and review raw Search API response diagnostics.",
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(evidence),
        "exact_topic_verified": bool(exact_evidence),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "version": "v7.5",
            "visited_count": len(visited),
            "candidate_count": len(evidence),
            "exact_candidate_count": len(exact_evidence),
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()

    return "WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v7.5 — Search API Raw Diagnostics + Adaptive Parser")
st.info(
    "v7.5 afișează exact ce răspunde Search API: HTTP status, content-type, bytes, "
    "tip JSON, chei top-level și preview. Parserul se adaptează recursiv la structura reală."
)

v75_diags = s45v75_discovery_diagnostics()

st.subheader("Raw Search API diagnostics")
if v75_diags:
    st.dataframe(
        s45v75_summarize_probe_rows(v75_diags),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Raw response previews", expanded=False):
        for idx, d in enumerate(v75_diags, 1):
            st.markdown(f"### Probe {idx}")
            st.write(f"**Requested URL:** {d.get('url')}")
            st.write(f"**Final URL:** {d.get('final_url')}")
            st.write(f"**HTTP:** {d.get('http_status')}")
            st.write(f"**Content-Type:** {d.get('content_type') or '—'}")
            st.write(f"**Bytes:** {d.get('response_bytes')}")
            st.write(f"**JSON type:** {d.get('json_type') or '—'}")
            st.write(f"**Top-level keys:** {d.get('json_keys') or []}")
            st.write(f"**Adaptive hits:** {len(d.get('adaptive_hits') or [])}")
            st.write(f"**Candidate URLs:** {len(d.get('candidate_urls') or [])}")
            st.code(d.get("preview") or "—", language="json")

    with st.expander("Adaptive hit details", expanded=False):
        adaptive_rows = []
        for idx, d in enumerate(v75_diags, 1):
            for h in d.get("adaptive_hits") or []:
                adaptive_rows.append({
                    "Probe": idx,
                    "Path": h.get("path"),
                    "Identity hit": h.get("identity_hit"),
                    "Title": h.get("title"),
                    "URL": h.get("url"),
                    "Text": normalize_text(h.get("text"))[:1500],
                })

        if adaptive_rows:
            st.dataframe(adaptive_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No adaptive hits extracted from current Search API responses.")
else:
    st.warning("No Search API diagnostics were produced.")

v75_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

if v75_tasks and st.button(
    "🧬 Run Stage 45 v7.5 adaptive discovery + resolution",
    type="primary",
    use_container_width=True,
    key="stage45_v75_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v75_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.5",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {
                "stage": 45,
                "version": "v7.5",
                "search_probe_count": len(v75_diags),
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.5 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v75_tasks, 1):
            try:
                state = s45v75_run_task(task, run_id, v75_diags)
                if normalize_text(state).upper() == "RESOLVED":
                    resolved += 1
                else:
                    waiting += 1

            except Exception as exc:
                failed += 1
                diagnostic = s45v71_log_error(
                    task=task,
                    worker_run_id=run_id,
                    worker_item_id=None,
                    error_stage="V75_TASK_EXECUTION",
                    exc=exc,
                    error_url=official_url,
                    request_payload={
                        "identity": identity,
                        "requirement": task.get("requirement_label"),
                    },
                    diagnostic_payload={
                        "stage": 45,
                        "version": "v7.5",
                        "function": "s45v75_run_task",
                    },
                )
                s45v71_update_run_diagnostics(run_id, task, diagnostic)
                st.error(
                    f"{task.get('requirement_label')} — "
                    f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                )

            bar.progress(idx / len(v75_tasks))

        final = (
            "FAILED" if failed and resolved == 0 and waiting == 0
            else "PARTIAL_FAILURE" if failed
            else "COMPLETED" if resolved == len(v75_tasks)
            else "WAITING"
        )

        docs_saved = rows(
            "locked_evidence_official_documents",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "worker_run_id": run_id,
            },
            "created_at",
            5000,
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": "FAILED" if final == "FAILED" else ("COMPLETED" if final == "COMPLETED" else "WAITING"),
            "diagnostic_status": (
                "FAILED" if final == "FAILED"
                else "PARTIAL_FAILURE" if final == "PARTIAL_FAILURE"
                else "CLEAN"
            ),
            "official_documents_checked": len(docs_saved),
            "official_sources_found": len(docs_saved),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7.5",
            "provenance_summary": {
                "documents_saved": len(docs_saved),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
                "search_probes": len(v75_diags),
                "http_statuses": [d.get("http_status") for d in v75_diags],
                "response_bytes": [d.get("response_bytes") for d in v75_diags],
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.5: {final} — Resolved {resolved}, Waiting {waiting}, "
            f"Failed {failed}, Documents {len(docs_saved)}."
        )
        st.rerun()

v75_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)

v75_runs = [
    r for r in v75_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.5"
]

if v75_runs:
    latest_v75 = v75_runs[0]

    st.subheader("Latest Stage 45 v7.5 Result")
    z1, z2, z3, z4 = st.columns(4)
    z1.metric("Status", latest_v75.get("worker_status") or "—")
    z2.metric("Official resolved", latest_v75.get("official_tasks_resolved") or 0)
    z3.metric("Official waiting", latest_v75.get("official_tasks_waiting") or 0)
    z4.metric("Documents", latest_v75.get("official_documents_checked") or 0)

    zz1, zz2, zz3 = st.columns(3)
    zz1.metric("Diagnostic status", latest_v75.get("diagnostic_status") or "—")
    zz2.metric("Error count", latest_v75.get("error_count") or 0)
    zz3.metric("Search probes", (latest_v75.get("provenance_summary") or {}).get("search_probes", 0))

st.caption(
    "v7.5 invariant: raw Search API diagnostics never count as substantive evidence. "
    "COMPLETED still requires explicit authoritative evidence traceable to the exact locked topic."
)
# =====================================================================
# END STAGE 45 v7.5
# =====================================================================



# =====================================================================
# STAGE 45 v7.6 — TRANSPORT RESOLVER + EXPLICIT NETWORK FAILURE DIAGNOSTICS
# =====================================================================

def s45v76_transport_probe(url, timeout=20):
    """
    Low-level transport probe.
    Distinguishes:
      - DNS / connection / SSL / timeout exceptions
      - HTTP non-2xx responses
      - empty-body responses
      - successful responses
    Never treats transport failure as 'zero search results'.
    """
    record = {
        "url": url,
        "ok": False,
        "http_status": None,
        "content_type": "",
        "response_bytes": 0,
        "final_url": url,
        "transport_error_type": "",
        "transport_error_message": "",
        "body_preview": "",
    }

    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "GreenRise/Stage45-v7.6",
                "Accept": "application/json,text/html,text/plain,*/*",
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "close",
            },
        )

        record["http_status"] = r.status_code
        record["content_type"] = normalize_text(r.headers.get("content-type", ""))
        record["response_bytes"] = len(r.content or b"")
        record["final_url"] = normalize_text(getattr(r, "url", "")) or url
        record["body_preview"] = normalize_text(r.text)[:4000]
        record["ok"] = bool(r.ok and (r.text or "").strip())

        if not r.ok:
            record["transport_error_type"] = "HTTP_ERROR"
            record["transport_error_message"] = f"HTTP {r.status_code}"

        elif not (r.text or "").strip():
            record["transport_error_type"] = "EMPTY_RESPONSE"
            record["transport_error_message"] = "HTTP response body is empty."

        return record

    except requests.exceptions.Timeout as exc:
        record["transport_error_type"] = "TIMEOUT"
        record["transport_error_message"] = str(exc)
    except requests.exceptions.SSLError as exc:
        record["transport_error_type"] = "SSL_ERROR"
        record["transport_error_message"] = str(exc)
    except requests.exceptions.ConnectionError as exc:
        msg = str(exc)
        record["transport_error_type"] = (
            "DNS_OR_CONNECTION_ERROR"
            if any(x in msg.lower() for x in ("name resolution", "nodename", "dns", "failed to resolve"))
            else "CONNECTION_ERROR"
        )
        record["transport_error_message"] = msg
    except Exception as exc:
        record["transport_error_type"] = type(exc).__name__
        record["transport_error_message"] = str(exc)

    return record

def s45v76_candidate_transport_urls():
    urls = []
    for u in s45v7_search_urls():
        if u and u not in urls:
            urls.append(u)

    # Alternative official EC entry points / exact-topic search variants.
    topic = normalize_text(identity)
    if topic:
        q = quote_plus(topic)
        extras = [
            f"https://funding-tenders.ec.europa.eu/portal/screen/opportunities/topic-details/{topic.lower()}",
            f"https://commission.europa.eu/search_en?query={q}",
            f"https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-search?keywords={q}",
        ]
        for u in extras:
            if u not in urls:
                urls.append(u)

    for u in s45v3_urls(official_url):
        if u and u not in urls:
            urls.append(u)

    return urls[:12]

def s45v76_run_transport_matrix():
    return [s45v76_transport_probe(u) for u in s45v76_candidate_transport_urls()]

def s45v76_transport_summary(records):
    return [
        {
            "URL": r.get("url"),
            "OK": r.get("ok"),
            "HTTP": r.get("http_status"),
            "Bytes": r.get("response_bytes"),
            "Content-Type": r.get("content_type"),
            "Error type": r.get("transport_error_type"),
            "Error message": r.get("transport_error_message"),
            "Final URL": r.get("final_url"),
        }
        for r in records
    ]

def s45v76_usable_transport_urls(records):
    return [
        r.get("final_url") or r.get("url")
        for r in records
        if r.get("ok") and (r.get("final_url") or r.get("url"))
    ]

def s45v76_run_task(task, worker_run_id, transport_records):
    item_insert = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task.get("requirement_id"),
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "TRANSPORT_RESOLVER_OFFICIAL_DOCUMENTATION",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v7.6"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item_insert:
        raise RuntimeError("Could not create v7.6 worker item.")

    worker_item_id = str(item_insert[0]["id"])

    usable_urls = s45v76_usable_transport_urls(transport_records)

    if not usable_urls:
        supabase.table("locked_evidence_worker_items").update({
            "worker_status": "WAITING_OFFICIAL",
            "documents_checked": [],
            "searches_attempted": s45v76_candidate_transport_urls(),
            "transport_attempts": transport_records,
            "missing_evidence_reason": (
                "No official endpoint returned a usable HTTP response. "
                "This is a transport failure, not evidence that the requirement is absent."
            ),
            "next_action": "Resolve network/endpoint access before official-document evaluation.",
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "retrieved_at": now_iso(),
            "authoritative_source_verified": False,
            "exact_topic_verified": False,
            "explicit_evidence_verified": False,
            "official_document_status": "WAITING_OFFICIAL",
            "official_document_payload": {
                "version": "v7.6",
                "transport_only": True,
                "transport_records": transport_records,
            },
            "updated_at": now_iso(),
        }).eq("id", worker_item_id).eq("user_id", user_id).execute()

        return "WAITING_OFFICIAL"

    evidence = []
    visited = set()
    trace = []

    queue = list(usable_urls)

    while queue and len(visited) < 40:
        url = queue.pop(0)
        if not url or url in visited:
            continue
        visited.add(url)

        fetched = s45v3_fetch(url, timeout=35)
        trace.extend(fetched.get("attempts", []))
        if not fetched.get("ok"):
            continue

        response = fetched.get("response")
        payload = None
        if response is not None:
            try:
                payload = response.json()
            except Exception:
                pass

        source_obj = payload if payload is not None else fetched.get("text", "")
        fetched_url = normalize_text(fetched.get("url")) or url

        local_evidence = s45v7_extract_explicit(source_obj, task, fetched_url)
        evidence.extend(local_evidence)

        best_excerpt = local_evidence[0]["excerpt"] if local_evidence else ""
        s45v6_save_document(
            task,
            worker_run_id,
            worker_item_id,
            fetched_url,
            best_excerpt,
            status="FETCHED",
            payload={
                "version": "v7.6",
                "transport_resolved": True,
                "payload_type": type(payload).__name__ if payload is not None else "text",
                "evidence_candidates": len(local_evidence),
            },
        )

        next_urls = []
        if payload is not None:
            next_urls.extend(s45v75_extract_candidate_urls_from_payload(payload))
        else:
            body = fetched.get("text", "")
            for u in re.findall(r'https?://[^\s|<>"\']+', body):
                host = urlparse(u).netloc.lower()
                if host.endswith("europa.eu") or host.endswith("ec.europa.eu"):
                    next_urls.append(u.rstrip(".,;)"))

        for u in next_urls:
            if u and u not in visited and u not in queue:
                queue.append(u)

        if len(evidence) >= 20:
            break

    exact_evidence = [
        e for e in evidence
        if e.get("exact_topic")
        or s45v7_exact_topic(e.get("url"))
        or s45v7_exact_topic(e.get("excerpt"))
    ]

    if exact_evidence:
        official_blob = "\n\n".join(
            f"[SOURCE {e.get('url')} | REF {e.get('reference')}]\n{e.get('excerpt')}"
            for e in exact_evidence[:12]
        )

        evaluation = ai_evaluate(
            task,
            collect_snapshot_evidence(task, sources),
            official_blob,
            exact_evidence[0].get("url") or official_url,
        )

        if evaluation.get("status") == "RESOLVED":
            worker_result = {
                "resolved_value": evaluation.get("resolved_value") or {},
                "evidence_source": "OFFICIAL_DOCUMENTATION",
                "evidence_reference": evaluation.get("evidence_reference") or exact_evidence[0].get("reference") or task.get("requirement_label"),
                "evidence_url": evaluation.get("evidence_url") or exact_evidence[0].get("url"),
                "evidence_excerpt": evaluation.get("evidence_excerpt") or exact_evidence[0].get("excerpt", "")[:5000],
                "confidence": evaluation.get("confidence") or "High",
                "reason": evaluation.get("reason") or "Explicit official evidence verified by v7.6.",
            }

            update_execution_task_completed(task, worker_result, "VERIFIED")

            supabase.table("locked_evidence_worker_items").update({
                "worker_status": "RESOLVED",
                "resolved_value": worker_result["resolved_value"],
                "evidence_source": worker_result["evidence_source"],
                "evidence_reference": worker_result["evidence_reference"],
                "evidence_url": worker_result["evidence_url"],
                "evidence_excerpt": worker_result["evidence_excerpt"],
                "confidence": worker_result["confidence"],
                "official_verified": True,
                "reason": worker_result["reason"],
                "next_action": "RETURN_TO_STAGE_44",
                "documents_checked": list(visited),
                "searches_attempted": s45v76_candidate_transport_urls(),
                "transport_attempts": transport_records + trace,
                "resolution_method": "OFFICIAL_DOCUMENTATION",
                "retrieved_at": now_iso(),
                "exact_topic_verified": True,
                "authoritative_source_verified": True,
                "explicit_evidence_verified": True,
                "official_document_status": "VERIFIED",
                "official_document_payload": {
                    "version": "v7.6",
                    "transport_records": transport_records,
                    "evidence_candidates": exact_evidence[:12],
                    "evaluation": evaluation,
                },
                "resolved_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", worker_item_id).eq("user_id", user_id).execute()

            return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": list(visited),
        "searches_attempted": s45v76_candidate_transport_urls(),
        "transport_attempts": transport_records + trace,
        "missing_evidence_reason": (
            "Transport succeeded for at least one official endpoint, but no explicit "
            "exact-topic evidence was sufficient to resolve this requirement."
        ),
        "next_action": "Remain WAITING_OFFICIAL and review fetched official resources.",
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(evidence),
        "exact_topic_verified": bool(exact_evidence),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "version": "v7.6",
            "transport_records": transport_records,
            "visited_count": len(visited),
            "candidate_count": len(evidence),
            "exact_candidate_count": len(exact_evidence),
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()

    return "WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v7.6 — Transport Resolver")
st.info(
    "v7.6 separă explicit transportul de verificarea factuală. "
    "HTTP None / 0 bytes nu mai este tratat ca zero rezultate."
)

v76_transport_records = s45v76_run_transport_matrix()

st.subheader("Transport diagnostics")
st.dataframe(
    s45v76_transport_summary(v76_transport_records),
    use_container_width=True,
    hide_index=True,
)

with st.expander("Transport response previews", expanded=False):
    for idx, r in enumerate(v76_transport_records, 1):
        st.markdown(f"### Transport probe {idx}")
        st.write(f"**URL:** {r.get('url')}")
        st.write(f"**Final URL:** {r.get('final_url')}")
        st.write(f"**HTTP:** {r.get('http_status')}")
        st.write(f"**OK:** {r.get('ok')}")
        st.write(f"**Bytes:** {r.get('response_bytes')}")
        st.write(f"**Content-Type:** {r.get('content_type') or '—'}")
        st.write(f"**Transport error type:** {r.get('transport_error_type') or '—'}")
        st.write(f"**Transport error message:** {r.get('transport_error_message') or '—'}")
        st.code(r.get("body_preview") or "—", language="text")

transport_failures = [r for r in v76_transport_records if not r.get("ok")]
usable_transports = [r for r in v76_transport_records if r.get("ok")]

m1, m2, m3 = st.columns(3)
m1.metric("Transport probes", len(v76_transport_records))
m2.metric("Usable responses", len(usable_transports))
m3.metric("Transport failures", len(transport_failures))

if transport_failures:
    st.warning(
        "Există probe de transport nereușite. Acestea sunt probleme de acces/endpoint, "
        "nu dovadă că apelul sau cerința nu există."
    )

v76_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

if v76_tasks and st.button(
    "🛰️ Run Stage 45 v7.6 transport + resolution",
    type="primary",
    use_container_width=True,
    key="stage45_v76_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v76_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.6",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {
                "stage": 45,
                "version": "v7.6",
                "transport_probe_count": len(v76_transport_records),
                "usable_transport_count": len(usable_transports),
                "transport_failure_count": len(transport_failures),
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.6 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v76_tasks, 1):
            try:
                state = s45v76_run_task(task, run_id, v76_transport_records)
                if normalize_text(state).upper() == "RESOLVED":
                    resolved += 1
                else:
                    waiting += 1

            except Exception as exc:
                failed += 1
                diagnostic = s45v71_log_error(
                    task=task,
                    worker_run_id=run_id,
                    worker_item_id=None,
                    error_stage="V76_TASK_EXECUTION",
                    exc=exc,
                    error_url=official_url,
                    request_payload={
                        "identity": identity,
                        "requirement": task.get("requirement_label"),
                    },
                    diagnostic_payload={
                        "stage": 45,
                        "version": "v7.6",
                        "function": "s45v76_run_task",
                    },
                )
                s45v71_update_run_diagnostics(run_id, task, diagnostic)
                st.error(
                    f"{task.get('requirement_label')} — "
                    f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                )

            bar.progress(idx / len(v76_tasks))

        final = (
            "FAILED" if failed and resolved == 0 and waiting == 0
            else "PARTIAL_FAILURE" if failed
            else "COMPLETED" if resolved == len(v76_tasks)
            else "WAITING"
        )

        docs_saved = rows(
            "locked_evidence_official_documents",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "worker_run_id": run_id,
            },
            "created_at",
            5000,
        )

        diag_status = (
            "FAILED" if final == "FAILED"
            else "PARTIAL_FAILURE" if final == "PARTIAL_FAILURE"
            else "WARNING" if transport_failures and not usable_transports
            else "CLEAN"
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": "FAILED" if final == "FAILED" else ("COMPLETED" if final == "COMPLETED" else "WAITING"),
            "diagnostic_status": diag_status,
            "official_documents_checked": len(docs_saved),
            "official_sources_found": len(docs_saved),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7.6",
            "provenance_summary": {
                "documents_saved": len(docs_saved),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
                "transport_probes": len(v76_transport_records),
                "usable_transports": len(usable_transports),
                "transport_failures": len(transport_failures),
                "transport_error_types": [
                    r.get("transport_error_type")
                    for r in transport_failures
                    if r.get("transport_error_type")
                ],
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.6: {final} — Resolved {resolved}, Waiting {waiting}, "
            f"Failed {failed}, Documents {len(docs_saved)}."
        )
        st.rerun()

v76_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)

v76_runs = [
    r for r in v76_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.6"
]

if v76_runs:
    latest_v76 = v76_runs[0]

    st.subheader("Latest Stage 45 v7.6 Result")
    n1, n2, n3, n4 = st.columns(4)
    n1.metric("Status", latest_v76.get("worker_status") or "—")
    n2.metric("Official resolved", latest_v76.get("official_tasks_resolved") or 0)
    n3.metric("Official waiting", latest_v76.get("official_tasks_waiting") or 0)
    n4.metric("Documents", latest_v76.get("official_documents_checked") or 0)

    nn1, nn2, nn3 = st.columns(3)
    nn1.metric("Diagnostic status", latest_v76.get("diagnostic_status") or "—")
    nn2.metric("Transport usable", (latest_v76.get("provenance_summary") or {}).get("usable_transports", 0))
    nn3.metric("Transport failures", (latest_v76.get("provenance_summary") or {}).get("transport_failures", 0))

st.caption(
    "v7.6 invariant: transport failure is never interpreted as factual absence. "
    "COMPLETED still requires explicit authoritative evidence."
)
# =====================================================================
# END STAGE 45 v7.6
# =====================================================================



# =====================================================================
# STAGE 45 v7.7 — AUTHORITATIVE EVIDENCE EXTRACTOR
# =====================================================================

def s45v77_normalize_url(url):
    return normalize_text(url).split("#", 1)[0].rstrip("/")

def s45v77_score_document(doc):
    """
    Rank already-discovered official documents for the exact locked topic.
    This is ranking only; it never creates factual evidence.
    """
    score = 0
    url = normalize_text(doc.get("source_url"))
    title = normalize_text(doc.get("source_title"))
    excerpt = normalize_text(doc.get("evidence_excerpt"))
    doc_type = normalize_text(doc.get("document_type"))
    authority = normalize_text(doc.get("source_authority")).upper()

    hay = " ".join([url, title, excerpt, doc_type])

    if s45v7_exact_topic(hay):
        score += 100
    if authority == "EUROPEAN_COMMISSION":
        score += 25
    if "funding-tenders" in url.lower():
        score += 25
    if "work programme" in hay.lower():
        score += 15
    if "general annex" in hay.lower() or "general annexes" in hay.lower():
        score += 12
    if doc.get("evidence_found"):
        score += 10
    if doc.get("applicability_verified"):
        score += 15
    if doc.get("exact_topic_verified"):
        score += 20
    if normalize_text(doc.get("retrieval_status")).upper() == "VERIFIED":
        score += 20

    return score

def s45v77_load_documents():
    docs = rows(
        "locked_evidence_official_documents",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        5000,
    )

    # Deduplicate by source URL while retaining strongest row.
    best = {}
    for d in docs:
        url = s45v77_normalize_url(d.get("source_url"))
        if not url:
            continue
        score = s45v77_score_document(d)
        current = best.get(url)
        if current is None or score > current["_score"]:
            clone = dict(d)
            clone["_score"] = score
            best[url] = clone

    out = list(best.values())
    out.sort(key=lambda x: (x["_score"], x.get("created_at") or ""), reverse=True)
    return out

def s45v77_requirement_needles(task):
    family = s45v7_requirement_family(task)
    return s45v7_needles(family)

def s45v77_fetch_document_for_extraction(doc):
    url = normalize_text(doc.get("source_url"))
    if not url:
        return {"ok": False, "url": "", "text": "", "json": None, "reason": "missing_url"}

    fetched = s45v7_fetch_any(url, timeout=35)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "url": url,
            "text": "",
            "json": None,
            "reason": "transport_failed",
            "attempts": fetched.get("attempts", []),
        }

    return {
        "ok": True,
        "url": fetched.get("url") or url,
        "text": fetched.get("text") or "",
        "json": fetched.get("json"),
        "attempts": fetched.get("attempts", []),
    }

def s45v77_extract_from_document(doc, task):
    fetched = s45v77_fetch_document_for_extraction(doc)

    source_url = fetched.get("url") or normalize_text(doc.get("source_url"))
    candidates = []

    # Stored excerpt first.
    stored_excerpt = normalize_text(doc.get("evidence_excerpt"))
    if stored_excerpt:
        stored = s45v7_extract_explicit(stored_excerpt, task, source_url)
        for c in stored:
            c["origin"] = "stored_excerpt"
        candidates.extend(stored)

    # Fresh source content.
    if fetched.get("ok"):
        source_obj = fetched.get("json") if fetched.get("json") is not None else fetched.get("text", "")
        fresh = s45v7_extract_explicit(source_obj, task, source_url)
        for c in fresh:
            c["origin"] = "fresh_fetch"
        candidates.extend(fresh)

    # Remove duplicates.
    dedup = []
    seen = set()
    for c in candidates:
        key = (
            s45v77_normalize_url(c.get("url")),
            normalize_text(c.get("reference")),
            normalize_text(c.get("excerpt"))[:1500],
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)

    return {
        "document": doc,
        "fetch": fetched,
        "candidates": dedup[:20],
    }

def s45v77_candidate_is_authoritative(candidate, doc):
    url = normalize_text(candidate.get("url") or doc.get("source_url"))
    host = urlparse(url).netloc.lower()

    official_host = (
        host.endswith("europa.eu")
        or host.endswith("ec.europa.eu")
        or host.endswith("funding-tenders.ec.europa.eu")
    )

    exact_topic = (
        candidate.get("exact_topic")
        or s45v7_exact_topic(candidate.get("excerpt"))
        or s45v7_exact_topic(url)
        or bool(doc.get("exact_topic_verified"))
    )

    authority = normalize_text(doc.get("source_authority")).upper()
    authoritative = official_host and authority in {"EUROPEAN_COMMISSION", ""}

    return authoritative, exact_topic

def s45v77_build_evidence_packet(task, ranked_docs):
    packet = []
    inspected = []

    for doc in ranked_docs[:40]:
        result = s45v77_extract_from_document(doc, task)
        inspected.append({
            "source_url": doc.get("source_url"),
            "score": doc.get("_score"),
            "candidate_count": len(result.get("candidates") or []),
            "fetch_ok": bool(result.get("fetch", {}).get("ok")),
        })

        for candidate in result.get("candidates") or []:
            authoritative, exact_topic = s45v77_candidate_is_authoritative(candidate, doc)

            if not authoritative:
                continue

            packet.append({
                "source_url": candidate.get("url") or doc.get("source_url"),
                "source_title": doc.get("source_title"),
                "document_type": doc.get("document_type"),
                "reference": candidate.get("reference"),
                "excerpt": candidate.get("excerpt"),
                "exact_topic": bool(exact_topic),
                "authoritative": True,
                "origin": candidate.get("origin"),
                "document_score": doc.get("_score"),
                "document_id": doc.get("id"),
            })

        if len(packet) >= 25:
            break

    # Prefer exact-topic candidates, but keep authoritative generic candidates
    # for evaluator context; generic text alone still cannot complete.
    packet.sort(
        key=lambda x: (
            1 if x.get("exact_topic") else 0,
            x.get("document_score") or 0,
        ),
        reverse=True,
    )

    return packet[:25], inspected

def s45v77_evaluate_packet(task, packet):
    exact_packet = [p for p in packet if p.get("exact_topic")]

    if not exact_packet:
        return {
            "status": "WAITING_OFFICIAL",
            "reason": "No exact-topic authoritative evidence candidate was found.",
            "exact_candidates": 0,
        }

    official_blob = "\n\n".join(
        (
            f"[SOURCE {p.get('source_url')} | TITLE {p.get('source_title')} | "
            f"TYPE {p.get('document_type')} | REF {p.get('reference')}]\n"
            f"{p.get('excerpt')}"
        )
        for p in exact_packet[:12]
    )

    evaluation = ai_evaluate(
        task,
        collect_snapshot_evidence(task, sources),
        official_blob,
        exact_packet[0].get("source_url") or official_url,
    )

    return {
        "status": evaluation.get("status"),
        "evaluation": evaluation,
        "exact_candidates": len(exact_packet),
        "used_candidates": exact_packet[:12],
    }

def s45v77_run_task(task, worker_run_id, ranked_docs):
    item_insert = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task.get("requirement_id"),
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "AUTHORITATIVE_EVIDENCE_EXTRACTION",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v7.7"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item_insert:
        raise RuntimeError("Could not create v7.7 worker item.")

    worker_item_id = str(item_insert[0]["id"])

    packet, inspected = s45v77_build_evidence_packet(task, ranked_docs)
    evaluation_wrap = s45v77_evaluate_packet(task, packet)

    if evaluation_wrap.get("status") == "RESOLVED":
        evaluation = evaluation_wrap.get("evaluation") or {}
        used = evaluation_wrap.get("used_candidates") or []
        best = used[0] if used else {}

        worker_result = {
            "resolved_value": evaluation.get("resolved_value") or {},
            "evidence_source": "OFFICIAL_DOCUMENTATION",
            "evidence_reference": evaluation.get("evidence_reference") or best.get("reference") or task.get("requirement_label"),
            "evidence_url": evaluation.get("evidence_url") or best.get("source_url"),
            "evidence_excerpt": evaluation.get("evidence_excerpt") or normalize_text(best.get("excerpt"))[:5000],
            "confidence": evaluation.get("confidence") or "High",
            "reason": evaluation.get("reason") or "Explicit authoritative evidence verified by v7.7.",
        }

        update_execution_task_completed(task, worker_result, "VERIFIED")

        supabase.table("locked_evidence_worker_items").update({
            "worker_status": "RESOLVED",
            "resolved_value": worker_result["resolved_value"],
            "evidence_source": worker_result["evidence_source"],
            "evidence_reference": worker_result["evidence_reference"],
            "evidence_url": worker_result["evidence_url"],
            "evidence_excerpt": worker_result["evidence_excerpt"],
            "confidence": worker_result["confidence"],
            "official_verified": True,
            "reason": worker_result["reason"],
            "next_action": "RETURN_TO_STAGE_44",
            "documents_checked": inspected,
            "searches_attempted": [],
            "transport_attempts": [],
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "retrieved_at": now_iso(),
            "exact_topic_verified": True,
            "authoritative_source_verified": True,
            "explicit_evidence_verified": True,
            "official_document_status": "VERIFIED",
            "official_document_payload": {
                "version": "v7.7",
                "inspected": inspected,
                "evidence_packet": packet,
                "evaluation": evaluation,
            },
            "resolved_at": now_iso(),
            "updated_at": now_iso(),
        }).eq("id", worker_item_id).eq("user_id", user_id).execute()

        return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": inspected,
        "searches_attempted": [],
        "transport_attempts": [],
        "missing_evidence_reason": (
            "Authoritative documents were ranked and inspected, but no explicit exact-topic "
            "evidence was sufficient to resolve this requirement."
        ),
        "next_action": "Remain WAITING_OFFICIAL; review ranked documents and extracted evidence packet.",
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(packet),
        "exact_topic_verified": any(p.get("exact_topic") for p in packet),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "version": "v7.7",
            "inspected": inspected,
            "evidence_packet": packet,
            "evaluation": evaluation_wrap,
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()

    return "WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v7.7 — Authoritative Evidence Extractor")
st.info(
    "v7.7 nu mai repară transportul. Procesează documentele oficiale deja descoperite, "
    "le clasează după relevanță și caută pasaje explicite pentru cele 3 cerințe OFFICIAL."
)

v77_docs = s45v77_load_documents()

d77a, d77b, d77c = st.columns(3)
d77a.metric("Stored official rows", len(v77_docs))
d77b.metric("Exact-topic ranked docs", sum(1 for d in v77_docs if s45v7_exact_topic(
    " ".join([
        normalize_text(d.get("source_url")),
        normalize_text(d.get("source_title")),
        normalize_text(d.get("evidence_excerpt")),
    ])
)))
d77c.metric("Verified document rows", sum(
    1 for d in v77_docs
    if normalize_text(d.get("retrieval_status")).upper() == "VERIFIED"
))

with st.expander("Top authoritative document ranking", expanded=False):
    st.dataframe(
        [
            {
                "Score": d.get("_score"),
                "Exact topic": bool(d.get("exact_topic_verified")) or s45v7_exact_topic(
                    " ".join([
                        normalize_text(d.get("source_url")),
                        normalize_text(d.get("source_title")),
                        normalize_text(d.get("evidence_excerpt")),
                    ])
                ),
                "Authority": d.get("source_authority"),
                "Status": d.get("retrieval_status"),
                "Type": d.get("document_type"),
                "Title": d.get("source_title"),
                "URL": d.get("source_url"),
                "Evidence": normalize_text(d.get("evidence_excerpt"))[:1200],
            }
            for d in v77_docs[:60]
        ],
        use_container_width=True,
        hide_index=True,
    )

v77_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

if v77_tasks and st.button(
    "📚 Run Stage 45 v7.7 authoritative evidence extraction",
    type="primary",
    use_container_width=True,
    key="stage45_v77_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v77_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.7",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {
                "stage": 45,
                "version": "v7.7",
                "stored_document_count": len(v77_docs),
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.7 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v77_tasks, 1):
            try:
                state = s45v77_run_task(task, run_id, v77_docs)
                if normalize_text(state).upper() == "RESOLVED":
                    resolved += 1
                else:
                    waiting += 1

            except Exception as exc:
                failed += 1
                diagnostic = s45v71_log_error(
                    task=task,
                    worker_run_id=run_id,
                    worker_item_id=None,
                    error_stage="V77_TASK_EXECUTION",
                    exc=exc,
                    error_url=None,
                    request_payload={
                        "identity": identity,
                        "requirement": task.get("requirement_label"),
                    },
                    diagnostic_payload={
                        "stage": 45,
                        "version": "v7.7",
                        "function": "s45v77_run_task",
                    },
                )
                s45v71_update_run_diagnostics(run_id, task, diagnostic)
                st.error(
                    f"{task.get('requirement_label')} — "
                    f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                )

            bar.progress(idx / len(v77_tasks))

        final = (
            "FAILED" if failed and resolved == 0 and waiting == 0
            else "PARTIAL_FAILURE" if failed
            else "COMPLETED" if resolved == len(v77_tasks)
            else "WAITING"
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": "FAILED" if final == "FAILED" else ("COMPLETED" if final == "COMPLETED" else "WAITING"),
            "diagnostic_status": (
                "FAILED" if final == "FAILED"
                else "PARTIAL_FAILURE" if final == "PARTIAL_FAILURE"
                else "CLEAN"
            ),
            "official_documents_checked": len(v77_docs),
            "official_sources_found": len(v77_docs),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7.7",
            "provenance_summary": {
                "stored_documents": len(v77_docs),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.7: {final} — Resolved {resolved}, Waiting {waiting}, Failed {failed}."
        )
        st.rerun()

v77_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)

v77_runs = [
    r for r in v77_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.7"
]

if v77_runs:
    latest_v77 = v77_runs[0]

    st.subheader("Latest Stage 45 v7.7 Result")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Status", latest_v77.get("worker_status") or "—")
    q2.metric("Official resolved", latest_v77.get("official_tasks_resolved") or 0)
    q3.metric("Official waiting", latest_v77.get("official_tasks_waiting") or 0)
    q4.metric("Documents ranked", latest_v77.get("official_documents_checked") or 0)

    q5, q6 = st.columns(2)
    q5.metric("Diagnostic status", latest_v77.get("diagnostic_status") or "—")
    q6.metric("Error count", latest_v77.get("error_count") or 0)

st.caption(
    "v7.7 invariant: document ranking is not evidence. COMPLETED requires an explicit "
    "authoritative passage applicable to the exact locked topic and requirement."
)
# =====================================================================
# END STAGE 45 v7.7
# =====================================================================



# =====================================================================
# STAGE 45 v7.8 — OFFICIAL DOCUMENT CONTENT EXTRACTOR
# =====================================================================

def s45v78_safe_text(value):
    return re.sub(r"\s+", " ", normalize_text(value)).strip()

def s45v78_requirement_profile(task):
    family = s45v7_requirement_family(task)

    profiles = {
        "applicant": {
            "strong": [
                "eligible applicants",
                "eligible entities",
                "eligible participants",
                "legal entities eligible",
                "eligibility conditions",
                "conditions for participation",
                "eligible for funding",
                "beneficiaries are eligible",
                "applicants must",
            ],
            "supporting": [
                "applicant",
                "beneficiary",
                "legal entity",
                "legal entities",
                "member states",
                "associated countries",
                "third countries",
                "sme",
                "for-profit",
                "non-profit",
                "eligible countries",
            ],
        },
        "consortium": {
            "strong": [
                "at least three independent legal entities",
                "minimum number of participants",
                "minimum consortium",
                "consortium must",
                "consortium shall",
                "independent legal entities",
                "consortium composition",
                "single beneficiary",
            ],
            "supporting": [
                "consortium",
                "beneficiaries",
                "participants",
                "partners",
                "legal entities",
                "member states",
                "associated countries",
                "independent",
            ],
        },
        "trl": {
            "strong": [
                "technology readiness level",
                "starting trl",
                "target trl",
                "expected trl",
                "trl 3",
                "trl 4",
                "trl 5",
                "trl 6",
                "trl 7",
                "trl 8",
                "trl 9",
            ],
            "supporting": [
                "trl",
                "technology maturity",
                "readiness level",
                "maturity level",
                "demonstration",
                "prototype",
            ],
        },
        "funding": {
            "strong": [
                "funding rate",
                "reimbursement rate",
                "maximum grant amount",
                "eligible costs",
                "funding conditions",
            ],
            "supporting": [
                "budget",
                "grant",
                "funding",
                "reimbursement",
                "eligible costs",
            ],
        },
        "geographic": {
            "strong": [
                "eligible countries",
                "eligible for funding",
                "member states",
                "associated countries",
                "geographical eligibility",
            ],
            "supporting": [
                "country",
                "countries",
                "member state",
                "associated country",
                "region",
            ],
        },
    }

    return family, profiles.get(
        family,
        {
            "strong": s45v7_needles(family),
            "supporting": s45v7_needles(family),
        },
    )

def s45v78_fetch_raw(url, timeout=40):
    result = {
        "ok": False,
        "url": url,
        "final_url": url,
        "status": None,
        "content_type": "",
        "content": b"",
        "text": "",
        "error": "",
    }

    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "GreenRise/Stage45-v7.8",
                "Accept": "application/pdf,application/json,text/html,text/plain,*/*",
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "close",
            },
        )

        result["status"] = response.status_code
        result["final_url"] = normalize_text(getattr(response, "url", "")) or url
        result["content_type"] = normalize_text(response.headers.get("content-type", ""))
        result["content"] = response.content or b""

        try:
            result["text"] = response.text or ""
        except Exception:
            result["text"] = ""

        result["ok"] = bool(response.ok and result["content"])
        if not response.ok:
            result["error"] = f"HTTP {response.status_code}"

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)}"

    return result

def s45v78_extract_pdf_text(content, max_pages=250, max_chars=1200000):
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

def s45v78_extract_html_text(html_text, max_chars=1200000):
    if not html_text:
        return ""

    if BeautifulSoup is None:
        # conservative fallback
        text_only = re.sub(r"<script.*?</script>", " ", html_text, flags=re.I | re.S)
        text_only = re.sub(r"<style.*?</style>", " ", text_only, flags=re.I | re.S)
        text_only = re.sub(r"<[^>]+>", " ", text_only)
        return re.sub(r"\s+", " ", text_only)[:max_chars]

    try:
        soup = BeautifulSoup(html_text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()

        chunks = []

        # Preserve useful structural breaks.
        for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
            txt = " ".join(node.stripped_strings).strip()
            if txt:
                chunks.append(txt)

        if not chunks:
            chunks = list(soup.stripped_strings)

        return "\n".join(chunks)[:max_chars]

    except Exception:
        return ""

def s45v78_extract_json_text(content, fallback_text="", max_chars=1200000):
    payload = None

    try:
        if isinstance(content, bytes):
            payload = json.loads(content.decode("utf-8", errors="ignore"))
        elif isinstance(content, str):
            payload = json.loads(content)
    except Exception:
        try:
            payload = json.loads(fallback_text)
        except Exception:
            payload = None

    if payload is None:
        return ""

    flattened = s45v7_flatten_strings(payload)
    lines = [f"{path}: {value}" for path, value in flattened]
    return "\n".join(lines)[:max_chars]

def s45v78_extract_document_text(fetch_result):
    ctype = normalize_text(fetch_result.get("content_type")).lower()
    content = fetch_result.get("content") or b""
    raw_text = fetch_result.get("text") or ""

    if "pdf" in ctype or normalize_text(fetch_result.get("final_url")).lower().endswith(".pdf"):
        extracted = s45v78_extract_pdf_text(content)
        return extracted, "PDF"

    if "json" in ctype:
        extracted = s45v78_extract_json_text(content, raw_text)
        return extracted, "JSON"

    if "html" in ctype or "<html" in raw_text[:5000].lower():
        extracted = s45v78_extract_html_text(raw_text)
        return extracted, "HTML"

    if raw_text:
        return raw_text[:1200000], "TEXT"

    return "", "UNKNOWN"

def s45v78_chunk_text(text, chunk_size=3500, overlap=500):
    text = text or ""
    if not text.strip():
        return []

    # Prefer paragraphs/lines, then pack them into bounded chunks.
    units = [u.strip() for u in re.split(r"\n{1,}", text) if u.strip()]
    chunks = []
    current = []
    current_len = 0

    for unit in units:
        if current and current_len + len(unit) + 1 > chunk_size:
            chunk = "\n".join(current)
            chunks.append(chunk)

            # Overlap: retain a suffix of previous chunk.
            suffix = chunk[-overlap:] if overlap else ""
            current = [suffix, unit] if suffix else [unit]
            current_len = len(suffix) + len(unit)

        else:
            current.append(unit)
            current_len += len(unit) + 1

    if current:
        chunks.append("\n".join(current))

    # Large unbroken text fallback.
    if not chunks:
        step = max(1, chunk_size - overlap)
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), step)]

    return chunks[:600]

def s45v78_topic_context_score(text, source_url="", doc=None):
    score = 0
    hay = " ".join([
        normalize_text(text),
        normalize_text(source_url),
        normalize_text((doc or {}).get("source_title")),
        normalize_text((doc or {}).get("evidence_excerpt")),
    ])

    if s45v7_exact_topic(hay):
        score += 100

    if doc and doc.get("exact_topic_verified"):
        score += 50

    return score

def s45v78_requirement_score(chunk, task):
    family, profile = s45v78_requirement_profile(task)
    low = chunk.lower()

    strong_hits = [term for term in profile["strong"] if term.lower() in low]
    supporting_hits = [term for term in profile["supporting"] if term.lower() in low]

    score = (len(strong_hits) * 20) + (len(supporting_hits) * 4)

    # Numeric TRL patterns carry strong signal.
    if family == "trl":
        trl_nums = re.findall(r"\btrl\s*[-:]?\s*([1-9])\b", low, flags=re.I)
        if trl_nums:
            score += 35

    return score, strong_hits, supporting_hits

def s45v78_exact_topic_reference_map(ranked_docs):
    """
    Build a map of official generic documents explicitly referenced from
    exact-topic documents. This allows a traceable topic -> general rule chain.
    """
    exact_docs = []
    for doc in ranked_docs:
        hay = " ".join([
            normalize_text(doc.get("source_url")),
            normalize_text(doc.get("source_title")),
            normalize_text(doc.get("evidence_excerpt")),
        ])
        if doc.get("exact_topic_verified") or s45v7_exact_topic(hay):
            exact_docs.append(doc)

    reference_map = {}

    for topic_doc in exact_docs[:10]:
        fetched = s45v78_fetch_raw(topic_doc.get("source_url"))
        if not fetched.get("ok"):
            continue

        text, _ = s45v78_extract_document_text(fetched)
        low = text.lower()

        for candidate in ranked_docs[:120]:
            candidate_url = normalize_text(candidate.get("source_url"))
            if not candidate_url:
                continue

            # Direct URL mention.
            if candidate_url.lower() in low:
                reference_map[s45v77_normalize_url(candidate_url)] = {
                    "topic_source_url": topic_doc.get("source_url"),
                    "reason": "Exact-topic official document explicitly references this official source URL.",
                }
                continue

            # Title mention (only when reasonably distinctive).
            title = s45v78_safe_text(candidate.get("source_title"))
            if len(title) >= 30 and title.lower() in low:
                reference_map[s45v77_normalize_url(candidate_url)] = {
                    "topic_source_url": topic_doc.get("source_url"),
                    "reason": "Exact-topic official document explicitly references this official document title.",
                }

    return reference_map

def s45v78_is_authoritative_url(url):
    host = urlparse(normalize_text(url)).netloc.lower()
    return (
        host.endswith("europa.eu")
        or host.endswith("ec.europa.eu")
        or host.endswith("funding-tenders.ec.europa.eu")
    )

def s45v78_extract_candidates_for_task(task, ranked_docs, reference_map):
    candidates = []
    inspected = []

    for doc in ranked_docs[:80]:
        url = normalize_text(doc.get("source_url"))
        if not url or not s45v78_is_authoritative_url(url):
            continue

        fetched = s45v78_fetch_raw(url)
        if not fetched.get("ok"):
            inspected.append({
                "url": url,
                "fetch_ok": False,
                "status": fetched.get("status"),
                "error": fetched.get("error"),
                "document_score": doc.get("_score"),
            })
            continue

        content_text, content_kind = s45v78_extract_document_text(fetched)
        chunks = s45v78_chunk_text(content_text)

        exact_doc = bool(doc.get("exact_topic_verified")) or s45v7_exact_topic(
            " ".join([
                url,
                normalize_text(doc.get("source_title")),
                normalize_text(doc.get("evidence_excerpt")),
                content_text[:25000],
            ])
        )

        ref = reference_map.get(s45v77_normalize_url(url))
        applicable_by_reference = bool(ref)

        doc_candidate_count = 0

        for chunk_index, chunk in enumerate(chunks):
            req_score, strong_hits, supporting_hits = s45v78_requirement_score(chunk, task)
            if req_score <= 0:
                continue

            topic_score = s45v78_topic_context_score(chunk, url, doc)
            exact_chunk = topic_score >= 100

            applicable = exact_doc or exact_chunk or applicable_by_reference
            if not applicable:
                continue

            score = (
                req_score
                + topic_score
                + int(doc.get("_score") or 0)
                + (40 if applicable_by_reference else 0)
            )

            candidates.append({
                "source_url": fetched.get("final_url") or url,
                "source_title": doc.get("source_title"),
                "document_type": content_kind or doc.get("document_type"),
                "document_id": doc.get("id"),
                "chunk_index": chunk_index,
                "excerpt": chunk[:6000],
                "score": score,
                "requirement_score": req_score,
                "topic_score": topic_score,
                "exact_topic": bool(exact_doc or exact_chunk),
                "applicable_by_reference": applicable_by_reference,
                "reference_source_url": ref.get("topic_source_url") if ref else None,
                "applicability_reason": (
                    "Exact-topic official document."
                    if exact_doc or exact_chunk
                    else ref.get("reason") if ref
                    else ""
                ),
                "strong_hits": strong_hits,
                "supporting_hits": supporting_hits,
            })

            doc_candidate_count += 1

        inspected.append({
            "url": url,
            "fetch_ok": True,
            "status": fetched.get("status"),
            "content_type": fetched.get("content_type"),
            "content_kind": content_kind,
            "text_chars": len(content_text),
            "chunks": len(chunks),
            "candidate_count": doc_candidate_count,
            "exact_doc": exact_doc,
            "applicable_by_reference": applicable_by_reference,
            "document_score": doc.get("_score"),
        })

    candidates.sort(key=lambda x: x.get("score") or 0, reverse=True)

    # Deduplicate near-identical excerpts.
    dedup = []
    seen = set()

    for c in candidates:
        excerpt_key = s45v78_safe_text(c.get("excerpt"))[:1800].lower()
        key = (s45v77_normalize_url(c.get("source_url")), excerpt_key)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)

    return dedup[:40], inspected

def s45v78_evaluate_candidates(task, candidates):
    if not candidates:
        return {
            "status": "WAITING_OFFICIAL",
            "reason": "No authoritative applicable content passage matched this requirement.",
            "used_candidates": [],
        }

    # Require either exact-topic evidence or an explicit reference chain.
    usable = [
        c for c in candidates
        if c.get("exact_topic") or c.get("applicable_by_reference")
    ][:12]

    if not usable:
        return {
            "status": "WAITING_OFFICIAL",
            "reason": "Candidate passages were found, but exact-topic applicability was not established.",
            "used_candidates": [],
        }

    official_blob = "\n\n".join(
        (
            f"[SOURCE {c.get('source_url')} | TITLE {c.get('source_title')} | "
            f"TYPE {c.get('document_type')} | CHUNK {c.get('chunk_index')} | "
            f"APPLICABILITY {c.get('applicability_reason')}]\n"
            f"{c.get('excerpt')}"
        )
        for c in usable
    )

    evaluation = ai_evaluate(
        task,
        collect_snapshot_evidence(task, sources),
        official_blob,
        usable[0].get("source_url") or official_url,
    )

    return {
        "status": evaluation.get("status"),
        "evaluation": evaluation,
        "used_candidates": usable,
    }

def s45v78_persist_candidate_documents(task, worker_run_id, worker_item_id, candidates):
    for c in candidates[:12]:
        try:
            s45v6_save_document(
                task,
                worker_run_id,
                worker_item_id,
                c.get("source_url"),
                c.get("excerpt"),
                status="VERIFIED" if c.get("exact_topic") else "FETCHED",
                payload={
                    "version": "v7.8",
                    "chunk_index": c.get("chunk_index"),
                    "score": c.get("score"),
                    "requirement_score": c.get("requirement_score"),
                    "topic_score": c.get("topic_score"),
                    "exact_topic": c.get("exact_topic"),
                    "applicable_by_reference": c.get("applicable_by_reference"),
                    "reference_source_url": c.get("reference_source_url"),
                    "applicability_reason": c.get("applicability_reason"),
                    "strong_hits": c.get("strong_hits"),
                    "supporting_hits": c.get("supporting_hits"),
                },
            )
        except Exception:
            pass

def s45v78_run_task(task, worker_run_id, ranked_docs, reference_map):
    item_insert = (
        supabase.table("locked_evidence_worker_items")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "worker_run_id": worker_run_id,
            "execution_task_id": task["id"],
            "requirement_id": task.get("requirement_id"),
            "opportunity_identity": identity,
            "requirement_key": task.get("requirement_key"),
            "requirement_category": task.get("requirement_category"),
            "requirement_label": task.get("requirement_label"),
            "route_type": task.get("route_type"),
            "destination_module": task.get("destination_module"),
            "worker_action": "OFFICIAL_DOCUMENT_CONTENT_EXTRACTION",
            "worker_status": "WAITING_OFFICIAL",
            "topic_identity": identity,
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "official_document_status": "SEARCHING",
            "metadata": {"stage": 45, "version": "v7.8"},
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not item_insert:
        raise RuntimeError("Could not create v7.8 worker item.")

    worker_item_id = str(item_insert[0]["id"])

    candidates, inspected = s45v78_extract_candidates_for_task(
        task,
        ranked_docs,
        reference_map,
    )

    s45v78_persist_candidate_documents(
        task,
        worker_run_id,
        worker_item_id,
        candidates,
    )

    evaluation_wrap = s45v78_evaluate_candidates(task, candidates)

    if evaluation_wrap.get("status") == "RESOLVED":
        evaluation = evaluation_wrap.get("evaluation") or {}
        used = evaluation_wrap.get("used_candidates") or []
        best = used[0] if used else {}

        worker_result = {
            "resolved_value": evaluation.get("resolved_value") or {},
            "evidence_source": "OFFICIAL_DOCUMENTATION",
            "evidence_reference": (
                evaluation.get("evidence_reference")
                or f"{best.get('document_type')} chunk {best.get('chunk_index')}"
            ),
            "evidence_url": evaluation.get("evidence_url") or best.get("source_url"),
            "evidence_excerpt": (
                evaluation.get("evidence_excerpt")
                or normalize_text(best.get("excerpt"))[:5000]
            ),
            "confidence": evaluation.get("confidence") or "High",
            "reason": evaluation.get("reason") or (
                "Explicit authoritative content passage verified by Stage 45 v7.8."
            ),
        }

        update_execution_task_completed(task, worker_result, "VERIFIED")

        provenance = []
        for c in used[:12]:
            chain = []
            if c.get("reference_source_url"):
                chain.append(c.get("reference_source_url"))
            chain.append(c.get("source_url"))
            provenance.append({
                "chain": chain,
                "applicability_reason": c.get("applicability_reason"),
                "chunk_index": c.get("chunk_index"),
            })

        supabase.table("locked_evidence_worker_items").update({
            "worker_status": "RESOLVED",
            "resolved_value": worker_result["resolved_value"],
            "evidence_source": worker_result["evidence_source"],
            "evidence_reference": worker_result["evidence_reference"],
            "evidence_url": worker_result["evidence_url"],
            "evidence_excerpt": worker_result["evidence_excerpt"],
            "confidence": worker_result["confidence"],
            "official_verified": True,
            "reason": worker_result["reason"],
            "next_action": "RETURN_TO_STAGE_44",
            "documents_checked": inspected,
            "searches_attempted": [],
            "transport_attempts": [],
            "resolution_method": "OFFICIAL_DOCUMENTATION",
            "retrieved_at": now_iso(),
            "exact_topic_verified": any(c.get("exact_topic") for c in used),
            "authoritative_source_verified": True,
            "explicit_evidence_verified": True,
            "official_document_status": "VERIFIED",
            "provenance_chain": provenance,
            "official_document_payload": {
                "version": "v7.8",
                "inspected": inspected,
                "candidate_count": len(candidates),
                "top_candidates": candidates[:12],
                "evaluation": evaluation,
            },
            "resolved_at": now_iso(),
            "updated_at": now_iso(),
        }).eq("id", worker_item_id).eq("user_id", user_id).execute()

        return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "documents_checked": inspected,
        "searches_attempted": [],
        "transport_attempts": [],
        "missing_evidence_reason": (
            evaluation_wrap.get("reason")
            or "No explicit authoritative content passage was sufficient for this requirement."
        ),
        "next_action": (
            "Remain WAITING_OFFICIAL; review extracted content candidates and applicability chain."
        ),
        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(candidates),
        "exact_topic_verified": any(c.get("exact_topic") for c in candidates),
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "official_document_payload": {
            "version": "v7.8",
            "inspected": inspected,
            "candidate_count": len(candidates),
            "top_candidates": candidates[:12],
            "evaluation": evaluation_wrap,
        },
        "updated_at": now_iso(),
    }).eq("id", worker_item_id).eq("user_id", user_id).execute()

    return "WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v7.8 — Official Document Content Extractor")
st.info(
    "v7.8 descarcă efectiv conținutul documentelor oficiale stocate, extrage text din HTML/JSON/PDF, "
    "îl segmentează, caută semantic cele 3 cerințe și păstrează provenance. "
    "Nu marchează COMPLETED fără pasaj oficial explicit și aplicabil."
)

v78_docs = s45v77_load_documents()

v78_reference_map = {}
try:
    v78_reference_map = s45v78_exact_topic_reference_map(v78_docs)
except Exception as ref_exc:
    st.warning(
        f"Reference-chain discovery warning: {type(ref_exc).__name__}: {str(ref_exc)[:500]}"
    )

v78_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
    and normalize_text(t.get("task_status")).upper() != "COMPLETED"
]

v78a, v78b, v78c, v78d = st.columns(4)
v78a.metric("Stored official docs", len(v78_docs))
v78b.metric(
    "Exact-topic docs",
    sum(
        1
        for d in v78_docs
        if d.get("exact_topic_verified")
        or s45v7_exact_topic(
            " ".join([
                normalize_text(d.get("source_url")),
                normalize_text(d.get("source_title")),
                normalize_text(d.get("evidence_excerpt")),
            ])
        )
    ),
)
v78c.metric("Reference-chain docs", len(v78_reference_map))
v78d.metric("Unresolved OFFICIAL", len(v78_tasks))

with st.expander("Reference-chain mapping", expanded=False):
    if v78_reference_map:
        st.dataframe(
            [
                {
                    "Referenced document": url,
                    "Topic source": data.get("topic_source_url"),
                    "Reason": data.get("reason"),
                }
                for url, data in v78_reference_map.items()
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No explicit topic → general-document reference chain detected yet.")

if v78_tasks and st.button(
    "🧠 Run Stage 45 v7.8 content extraction + evidence verification",
    type="primary",
    use_container_width=True,
    key="stage45_v78_run",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(v78_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.8",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {
                "stage": 45,
                "version": "v7.8",
                "stored_document_count": len(v78_docs),
                "reference_chain_count": len(v78_reference_map),
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.8 run.")

    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)

        for idx, task in enumerate(v78_tasks, 1):
            try:
                state = s45v78_run_task(
                    task,
                    run_id,
                    v78_docs,
                    v78_reference_map,
                )

                if normalize_text(state).upper() == "RESOLVED":
                    resolved += 1
                else:
                    waiting += 1

            except Exception as exc:
                failed += 1

                diagnostic = s45v71_log_error(
                    task=task,
                    worker_run_id=run_id,
                    worker_item_id=None,
                    error_stage="V78_TASK_EXECUTION",
                    exc=exc,
                    error_url=None,
                    request_payload={
                        "identity": identity,
                        "requirement": task.get("requirement_label"),
                    },
                    diagnostic_payload={
                        "stage": 45,
                        "version": "v7.8",
                        "function": "s45v78_run_task",
                    },
                )

                s45v71_update_run_diagnostics(run_id, task, diagnostic)

                st.error(
                    f"{task.get('requirement_label')} — "
                    f"{diagnostic['error_type']}: {diagnostic['error_message']}"
                )

            bar.progress(idx / len(v78_tasks))

        final = (
            "FAILED"
            if failed and resolved == 0 and waiting == 0
            else "PARTIAL_FAILURE"
            if failed
            else "COMPLETED"
            if resolved == len(v78_tasks)
            else "WAITING"
        )

        run_items = rows(
            "locked_evidence_worker_items",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "worker_run_id": run_id,
            },
            "created_at",
            100,
        )

        total_candidates = 0
        total_inspected = 0

        for item in run_items:
            payload = item.get("official_document_payload") or {}
            total_candidates += int(payload.get("candidate_count") or 0)
            total_inspected += len(payload.get("inspected") or [])

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved,
            "waiting_tasks": waiting,
            "failed_tasks": failed,
            "worker_status": (
                "FAILED"
                if final == "FAILED"
                else "COMPLETED"
                if final == "COMPLETED"
                else "WAITING"
            ),
            "diagnostic_status": (
                "FAILED"
                if final == "FAILED"
                else "PARTIAL_FAILURE"
                if final == "PARTIAL_FAILURE"
                else "CLEAN"
            ),
            "official_documents_checked": total_inspected,
            "official_sources_found": len(v78_docs),
            "official_tasks_resolved": resolved,
            "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7.8",
            "provenance_summary": {
                "stored_documents": len(v78_docs),
                "documents_inspected": total_inspected,
                "evidence_candidates": total_candidates,
                "reference_chains": len(v78_reference_map),
                "resolved": resolved,
                "waiting": waiting,
                "failed": failed,
            },
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None,
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.8: {final} — "
            f"Resolved {resolved}, Waiting {waiting}, Failed {failed}, "
            f"Candidates {total_candidates}."
        )

        st.rerun()

v78_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)

v78_runs = [
    r
    for r in v78_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.8"
]

if v78_runs:
    latest_v78 = v78_runs[0]

    st.subheader("Latest Stage 45 v7.8 Result")

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Status", latest_v78.get("worker_status") or "—")
    a2.metric("Official resolved", latest_v78.get("official_tasks_resolved") or 0)
    a3.metric("Official waiting", latest_v78.get("official_tasks_waiting") or 0)
    a4.metric(
        "Documents inspected",
        (latest_v78.get("provenance_summary") or {}).get("documents_inspected", 0),
    )

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Diagnostic status", latest_v78.get("diagnostic_status") or "—")
    b2.metric("Error count", latest_v78.get("error_count") or 0)
    b3.metric(
        "Evidence candidates",
        (latest_v78.get("provenance_summary") or {}).get("evidence_candidates", 0),
    )
    b4.metric(
        "Reference chains",
        (latest_v78.get("provenance_summary") or {}).get("reference_chains", 0),
    )

    latest_v78_items = rows(
        "locked_evidence_worker_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "worker_run_id": str(latest_v78.get("id")),
        },
        "created_at",
        100,
    )

    if latest_v78_items:
        st.subheader("Requirement evidence results")

        st.dataframe(
            [
                {
                    "Requirement": i.get("requirement_label"),
                    "Status": i.get("worker_status"),
                    "Exact topic": i.get("exact_topic_verified"),
                    "Authoritative": i.get("authoritative_source_verified"),
                    "Explicit evidence": i.get("explicit_evidence_verified"),
                    "Evidence URL": i.get("evidence_url"),
                    "Evidence excerpt": normalize_text(i.get("evidence_excerpt"))[:1200],
                    "Reason": i.get("reason") or i.get("missing_evidence_reason"),
                }
                for i in latest_v78_items
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Top extracted evidence candidates")

        for item in latest_v78_items:
            payload = item.get("official_document_payload") or {}
            top_candidates = payload.get("top_candidates") or []

            with st.expander(
                f"{item.get('requirement_label') or 'Requirement'} — "
                f"{item.get('worker_status') or '—'}",
                expanded=False,
            ):
                if not top_candidates:
                    st.info("No content candidate was retained for this requirement.")
                else:
                    st.dataframe(
                        [
                            {
                                "Score": c.get("score"),
                                "Exact topic": c.get("exact_topic"),
                                "By reference": c.get("applicable_by_reference"),
                                "Type": c.get("document_type"),
                                "Source": c.get("source_url"),
                                "Chunk": c.get("chunk_index"),
                                "Strong hits": ", ".join(c.get("strong_hits") or []),
                                "Excerpt": normalize_text(c.get("excerpt"))[:1800],
                            }
                            for c in top_candidates[:12]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

st.caption(
    "v7.8 invariant: content extraction and semantic matching are not sufficient by themselves. "
    "COMPLETED requires an explicit authoritative passage plus exact-topic applicability "
    "or a traceable exact-topic → official-rule reference chain."
)
# =====================================================================
# END STAGE 45 v7.8
# =====================================================================

# =====================================================================
# STAGE 45 v7.9 — REFERENCE CHAIN EVIDENCE RESOLVER
# =====================================================================
# v7.9 extends v7.8. It follows authoritative references recursively from the
# exact locked topic, materialises referenced EC/EU documents, extracts their
# content and evaluates the three OFFICIAL requirements fail-closed.

from urllib.parse import urljoin as s45v79_urljoin


def s45v79_extract_links(base_url, fetch_result):
    """Return unique authoritative links explicitly present in a fetched document."""
    out = []
    raw_text = normalize_text(fetch_result.get("text"))
    content_type = normalize_text(fetch_result.get("content_type")).lower()

    # HTML hrefs are preferable because relative links can be resolved safely.
    if "html" in content_type and BeautifulSoup is not None and raw_text:
        try:
            soup = BeautifulSoup(raw_text, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = normalize_text(tag.get("href"))
                if href:
                    out.append(s45v79_urljoin(base_url, href))
        except Exception:
            pass

    # Also inspect raw/extracted text for absolute official URLs.
    extracted, _ = s45v78_extract_document_text(fetch_result)
    for corpus in (raw_text, extracted):
        for u in re.findall(r'https?://[^\s|<>"\'\]\[(){}]+', corpus or ""):
            out.append(u.rstrip(".,;:"))

    dedup = []
    seen = set()
    for u in out:
        u = normalize_text(u)
        key = s45v77_normalize_url(u)
        if not u or not key or key in seen or not s45v78_is_authoritative_url(u):
            continue
        seen.add(key)
        dedup.append(u)
    return dedup


def s45v79_seed_documents(ranked_docs):
    """Select exact-topic official documents as the root of the reference graph."""
    seeds = []
    for doc in ranked_docs:
        url = normalize_text(doc.get("source_url"))
        hay = " ".join([
            url,
            normalize_text(doc.get("source_title")),
            normalize_text(doc.get("evidence_excerpt")),
        ])
        if url and s45v78_is_authoritative_url(url) and (
            bool(doc.get("exact_topic_verified")) or s45v7_exact_topic(hay)
        ):
            seeds.append(doc)
    return seeds[:12]


def s45v79_build_reference_graph(ranked_docs, max_depth=3, max_nodes=80):
    """Breadth-first exact-topic -> official-reference graph with traceable parents."""
    seeds = s45v79_seed_documents(ranked_docs)
    queue = []
    graph = {}
    fetch_cache = {}

    for doc in seeds:
        url = normalize_text(doc.get("source_url"))
        key = s45v77_normalize_url(url)
        if key and key not in graph:
            graph[key] = {
                "url": url, "parent_url": None, "root_url": url, "depth": 0,
                "reason": "Exact locked-topic official source.", "exact_topic_root": True,
            }
            queue.append(key)

    while queue and len(graph) < max_nodes:
        key = queue.pop(0)
        node = graph[key]
        if int(node.get("depth") or 0) >= max_depth:
            continue

        url = node["url"]
        fetched = s45v78_fetch_raw(url)
        fetch_cache[key] = fetched
        if not fetched.get("ok"):
            node["fetch_ok"] = False
            node["fetch_status"] = fetched.get("status")
            node["fetch_error"] = fetched.get("error")
            continue

        node["fetch_ok"] = True
        node["fetch_status"] = fetched.get("status")
        links = s45v79_extract_links(url, fetched)
        node["outgoing_links"] = len(links)

        for child_url in links:
            if len(graph) >= max_nodes:
                break
            child_key = s45v77_normalize_url(child_url)
            if not child_key or child_key in graph:
                continue
            graph[child_key] = {
                "url": child_url,
                "parent_url": url,
                "root_url": node.get("root_url") or url,
                "depth": int(node.get("depth") or 0) + 1,
                "reason": "Explicit official hyperlink/reference followed from an exact-topic chain.",
                "exact_topic_root": False,
            }
            queue.append(child_key)

    return graph, fetch_cache


def s45v79_chain_for_node(graph, node_key):
    node = graph.get(node_key) or {}
    chain = []
    seen = set()
    current = node
    while current and len(chain) < 8:
        url = normalize_text(current.get("url"))
        if not url or url in seen:
            break
        seen.add(url)
        chain.append(url)
        parent = normalize_text(current.get("parent_url"))
        if not parent:
            break
        current = graph.get(s45v77_normalize_url(parent)) or {"url": parent}
    return list(reversed(chain))


def s45v79_candidates_for_task(task, graph, fetch_cache):
    candidates = []
    inspected = []

    for key, node in graph.items():
        url = node.get("url")
        fetched = fetch_cache.get(key)
        if fetched is None:
            fetched = s45v78_fetch_raw(url)
            fetch_cache[key] = fetched

        if not fetched.get("ok"):
            inspected.append({
                "url": url, "depth": node.get("depth"), "fetch_ok": False,
                "status": fetched.get("status"), "error": fetched.get("error"),
                "chain": s45v79_chain_for_node(graph, key),
            })
            continue

        text, kind = s45v78_extract_document_text(fetched)
        chunks = s45v78_chunk_text(text)
        count = 0

        for idx, chunk in enumerate(chunks):
            req_score, strong_hits, supporting_hits = s45v78_requirement_score(chunk, task)
            if req_score <= 0:
                continue

            exact_here = s45v7_exact_topic(" ".join([url, chunk]))
            chain = s45v79_chain_for_node(graph, key)
            # Applicability is established only by an explicit chain rooted in an exact-topic source.
            applicable_by_chain = bool(chain and node.get("root_url") and len(chain) >= 1)
            if not (exact_here or applicable_by_chain):
                continue

            depth = int(node.get("depth") or 0)
            score = req_score + (120 if exact_here else 0) + max(0, 70 - depth * 12)
            candidates.append({
                "source_url": fetched.get("final_url") or url,
                "source_title": None,
                "document_type": kind,
                "chunk_index": idx,
                "excerpt": chunk[:6000],
                "score": score,
                "requirement_score": req_score,
                "exact_topic": exact_here,
                "applicable_by_reference": applicable_by_chain,
                "reference_source_url": node.get("parent_url"),
                "reference_chain": chain,
                "reference_depth": depth,
                "applicability_reason": (
                    "Requirement passage occurs in the exact-topic official source."
                    if exact_here else
                    "Requirement passage occurs in an authoritative document reached by an explicit reference chain rooted at the exact locked topic."
                ),
                "strong_hits": strong_hits,
                "supporting_hits": supporting_hits,
            })
            count += 1

        inspected.append({
            "url": url, "depth": node.get("depth"), "fetch_ok": True,
            "status": fetched.get("status"), "content_type": fetched.get("content_type"),
            "content_kind": kind, "text_chars": len(text), "chunks": len(chunks),
            "candidate_count": count, "chain": s45v79_chain_for_node(graph, key),
        })

    candidates.sort(key=lambda x: x.get("score") or 0, reverse=True)
    dedup, seen = [], set()
    for c in candidates:
        k = (s45v77_normalize_url(c.get("source_url")), normalize_text(c.get("excerpt"))[:1800].lower())
        if k in seen:
            continue
        seen.add(k)
        dedup.append(c)
    return dedup[:50], inspected


def s45v79_evaluate(task, candidates):
    usable = [c for c in candidates if c.get("exact_topic") or c.get("applicable_by_reference")][:14]
    if not usable:
        return {"status": "WAITING_OFFICIAL", "reason": "No explicit authoritative passage was found through the exact-topic reference graph.", "used_candidates": []}

    blob = "\n\n".join(
        f"[OFFICIAL SOURCE {c.get('source_url')} | CHAIN {' -> '.join(c.get('reference_chain') or [])} | "
        f"CHUNK {c.get('chunk_index')} | APPLICABILITY {c.get('applicability_reason')}]\n{c.get('excerpt')}"
        for c in usable
    )
    evaluation = ai_evaluate(task, collect_snapshot_evidence(task, sources), blob, usable[0].get("source_url") or official_url)
    return {"status": evaluation.get("status"), "evaluation": evaluation, "used_candidates": usable}


def s45v79_run_task(task, worker_run_id, graph, fetch_cache):
    inserted = (supabase.table("locked_evidence_worker_items").insert({
        "user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id, "worker_run_id": worker_run_id,
        "execution_task_id": task["id"], "requirement_id": task.get("requirement_id"),
        "opportunity_identity": identity, "requirement_key": task.get("requirement_key"),
        "requirement_category": task.get("requirement_category"), "requirement_label": task.get("requirement_label"),
        "route_type": task.get("route_type"), "destination_module": task.get("destination_module"),
        "worker_action": "REFERENCE_CHAIN_EVIDENCE_RESOLUTION", "worker_status": "WAITING_OFFICIAL",
        "topic_identity": identity, "resolution_method": "OFFICIAL_DOCUMENTATION",
        "official_document_status": "SEARCHING", "metadata": {"stage": 45, "version": "v7.10.1"},
        "updated_at": now_iso(),
    }).execute()).data or []
    if not inserted:
        raise RuntimeError("Could not create v7.9 worker item.")
    item_id = str(inserted[0]["id"])

    candidates, inspected = s45v79_candidates_for_task(task, graph, fetch_cache)
    s45v78_persist_candidate_documents(task, worker_run_id, item_id, candidates)
    wrap = s45v79_evaluate(task, candidates)

    if wrap.get("status") == "RESOLVED":
        ev = wrap.get("evaluation") or {}
        used = wrap.get("used_candidates") or []
        best = used[0] if used else {}
        worker_result = {
            "resolved_value": ev.get("resolved_value") or {},
            "evidence_source": "OFFICIAL_REFERENCE_CHAIN",
            "evidence_reference": ev.get("evidence_reference") or f"reference-chain chunk {best.get('chunk_index')}",
            "evidence_url": ev.get("evidence_url") or best.get("source_url"),
            "evidence_excerpt": ev.get("evidence_excerpt") or normalize_text(best.get("excerpt"))[:5000],
            "confidence": ev.get("confidence") or "High",
            "reason": ev.get("reason") or "Explicit authoritative evidence verified through an exact-topic reference chain.",
        }
        update_execution_task_completed(task, worker_result, "VERIFIED")
        supabase.table("locked_evidence_worker_items").update({
            "worker_status": "RESOLVED", "resolved_value": worker_result["resolved_value"],
            "evidence_source": worker_result["evidence_source"], "evidence_reference": worker_result["evidence_reference"],
            "evidence_url": worker_result["evidence_url"], "evidence_excerpt": worker_result["evidence_excerpt"],
            "confidence": worker_result["confidence"], "official_verified": True, "reason": worker_result["reason"],
            "next_action": "RETURN_TO_STAGE_44", "documents_checked": inspected,
            "resolution_method": "OFFICIAL_DOCUMENTATION", "retrieved_at": now_iso(),
            "exact_topic_verified": any(c.get("exact_topic") for c in used),
            "authoritative_source_verified": True, "explicit_evidence_verified": True,
            "official_document_status": "VERIFIED",
            "provenance_chain": [c.get("reference_chain") for c in used[:12]],
            "official_document_payload": {"version": "v7.10.1", "inspected": inspected, "candidate_count": len(candidates), "top_candidates": candidates[:12], "evaluation": ev},
            "resolved_at": now_iso(), "updated_at": now_iso(),
        }).eq("id", item_id).eq("user_id", user_id).execute()
        return "RESOLVED"

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL", "documents_checked": inspected,
        "missing_evidence_reason": wrap.get("reason") or "No explicit authoritative evidence was established through the reference chain.",
        "next_action": "Remain WAITING_OFFICIAL; authoritative reference traversal found no sufficient explicit passage.",
        "resolution_method": "OFFICIAL_DOCUMENTATION", "retrieved_at": now_iso(),
        "authoritative_source_verified": bool(candidates),
        "exact_topic_verified": any(c.get("exact_topic") for c in candidates),
        "explicit_evidence_verified": False, "official_document_status": "WAITING_OFFICIAL",
        "provenance_chain": [c.get("reference_chain") for c in candidates[:12]],
        "official_document_payload": {"version": "v7.10.1", "inspected": inspected, "candidate_count": len(candidates), "top_candidates": candidates[:12], "evaluation": wrap},
        "updated_at": now_iso(),
    }).eq("id", item_id).eq("user_id", user_id).execute()
    return 
# ============================================================
# STAGE 45 v7.10 — TOPIC DOCUMENT & ANNEX RESOLVER
# ============================================================
# Adds a stricter evidence layer on top of the v7.9 reference graph:
#   exact locked topic -> exact official topic document -> official annex /
#   work programme / call document -> explicit requirement passage.
#
# IMPORTANT: this layer remains fail-closed. A source being authoritative is
# not enough. COMPLETED requires exact-topic applicability + explicit evidence.

S45V710_REQUIREMENT_TERMS = {
    "APPLICANT_ELIGIBILITY": [
        "eligible applicants", "eligible applicant", "eligibility conditions",
        "eligible entities", "legal entities", "applicants must",
        "beneficiaries must", "eligible for funding", "may apply",
    ],
    "CONSORTIUM_REQUIREMENTS": [
        "consortium", "consortia", "minimum number", "at least three",
        "independent legal entities", "beneficiaries", "participants",
        "composition of the consortium", "consortium composition",
    ],
    "TRL_REQUIREMENTS": [
        "technology readiness level", "technology readiness levels",
        "trl", "starting trl", "target trl", "expected trl",
        "activities are expected to start at", "reach trl",
    ],
}

def s45v710_topic_tokens(topic_identity):
    raw = normalize_text(topic_identity).upper()
    if not raw:
        return []
    parts = [p for p in re.split(r"[^A-Z0-9]+", raw) if p]
    return [p for p in parts if len(p) >= 2]

def s45v710_exact_topic_match(text_value, topic_identity):
    hay = normalize_text(text_value).upper()
    topic = normalize_text(topic_identity).upper()
    if not hay or not topic:
        return False
    if topic in hay:
        return True
    toks = s45v710_topic_tokens(topic)
    # Require a strong identity match, not merely programme-level overlap.
    significant = [t for t in toks if len(t) >= 3]
    if not significant:
        return False
    hits = sum(1 for t in significant if t in hay)
    return hits >= max(3, len(significant) - 1)

def s45v710_document_kind(url, title="", content_type=""):
    s = " ".join([
        normalize_text(url).lower(),
        normalize_text(title).lower(),
        normalize_text(content_type).lower(),
    ])
    if ".pdf" in s or "application/pdf" in s:
        return "PDF"
    if "annex" in s:
        return "ANNEX"
    if "work programme" in s or "work-programme" in s or "work_programme" in s:
        return "WORK_PROGRAMME"
    if "topic" in s or "topic-details" in s or "topic_details" in s:
        return "TOPIC_PAGE"
    if "call" in s:
        return "CALL_DOCUMENT"
    return "OFFICIAL_DOCUMENT"

def s45v710_official_ec_source(url):
    host = urlparse(normalize_text(url)).netloc.lower()
    return bool(
        host == "ec.europa.eu"
        or host.endswith(".ec.europa.eu")
        or host == "commission.europa.eu"
        or host.endswith(".commission.europa.eu")
        or host == "funding-tenders.ec.europa.eu"
        or host.endswith(".funding-tenders.ec.europa.eu")
    )

def s45v710_extract_requirement_passage(content, requirement_key, topic_identity):
    body = normalize_text(content)
    if not body:
        return None

    key = normalize_text(requirement_key).upper()
    terms = S45V710_REQUIREMENT_TERMS.get(key, [])
    lower = body.lower()

    best = None
    for term in terms:
        start = 0
        needle = term.lower()
        while True:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            left = max(0, idx - 900)
            right = min(len(body), idx + len(term) + 1400)
            excerpt = body[left:right].strip()

            # Topic identity may be established by the root/chain rather than
            # repeated in every annex paragraph. Score it when locally present.
            exact_local = s45v710_exact_topic_match(excerpt, topic_identity)
            score = 10 + (20 if exact_local else 0)
            score += min(10, sum(1 for t in terms if t.lower() in excerpt.lower()))

            candidate = {
                "excerpt": excerpt[:5000],
                "matched_term": term,
                "local_exact_topic": exact_local,
                "score": score,
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
            start = idx + len(needle)

    return best

def s45v710_rank_topic_documents(rows, topic_identity):
    ranked = []
    for row in rows or []:
        url = normalize_text(row.get("source_url"))
        if not url or not s45v710_official_ec_source(url):
            continue
        title = normalize_text(row.get("source_title"))
        payload = row.get("evidence_payload") or {}
        content = " ".join([
            title,
            normalize_text(row.get("evidence_excerpt")),
            normalize_text(payload.get("text") if isinstance(payload, dict) else ""),
            normalize_text(payload.get("content") if isinstance(payload, dict) else ""),
            url,
        ])
        exact = bool(row.get("exact_topic_verified")) or s45v710_exact_topic_match(content, topic_identity)
        kind = s45v710_document_kind(url, title, normalize_text(row.get("content_type")))
        score = 0
        score += 100 if exact else 0
        score += 25 if kind in {"TOPIC_PAGE", "PDF", "ANNEX", "WORK_PROGRAMME", "CALL_DOCUMENT"} else 0
        score += 10 if bool(row.get("applicability_verified")) else 0
        ranked.append({
            **row,
            "_v710_exact": exact,
            "_v710_kind": kind,
            "_v710_score": score,
        })
    ranked.sort(key=lambda x: x["_v710_score"], reverse=True)
    return ranked

def s45v710_resolve_from_documents(requirement_key, topic_identity, rows):
    ranked = s45v710_rank_topic_documents(rows, topic_identity)
    exact_roots = [r for r in ranked if r.get("_v710_exact")]

    # No exact-topic root => cannot establish applicability.
    if not exact_roots:
        return {
            "status": "WAITING_OFFICIAL",
            "exact_topic_verified": False,
            "authoritative_source_verified": bool(ranked),
            "explicit_evidence_verified": False,
            "evidence_url": None,
            "evidence_excerpt": None,
            "reason": "No exact official topic document was verified.",
            "resolution_method": "OFFICIAL_DOCUMENTATION",
        }

    # Once an exact official root exists, inspect exact root and its stored
    # official descendants/annexes. Never infer the substantive rule.
    candidates = []
    for row in ranked:
        content_parts = [
            normalize_text(row.get("source_title")),
            normalize_text(row.get("evidence_excerpt")),
        ]
        payload = row.get("evidence_payload") or {}
        if isinstance(payload, dict):
            content_parts.extend([
                normalize_text(payload.get("text")),
                normalize_text(payload.get("content")),
                normalize_text(payload.get("raw_text")),
                normalize_text(payload.get("body")),
            ])
        content = "\n".join(p for p in content_parts if p)
        passage = s45v710_extract_requirement_passage(content, requirement_key, topic_identity)
        if not passage:
            continue

        # Applicability is accepted only when this is the exact root itself or
        # the stored provenance chain traces it to an exact root.
        chain = row.get("provenance_chain") or []
        chain_text = json.dumps(chain, ensure_ascii=False) if chain else ""
        traceable = bool(row.get("_v710_exact")) or s45v710_exact_topic_match(chain_text, topic_identity)
        if not traceable:
            continue

        candidates.append((passage["score"], row, passage))

    if not candidates:
        return {
            "status": "WAITING_OFFICIAL",
            "exact_topic_verified": True,
            "authoritative_source_verified": True,
            "explicit_evidence_verified": False,
            "evidence_url": None,
            "evidence_excerpt": None,
            "reason": "Exact official topic root verified, but no explicit authoritative passage was found for this requirement.",
            "resolution_method": "OFFICIAL_DOCUMENTATION",
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, row, passage = candidates[0]
    return {
        "status": "RESOLVED",
        "exact_topic_verified": True,
        "authoritative_source_verified": True,
        "explicit_evidence_verified": True,
        "evidence_url": normalize_text(row.get("source_url")) or None,
        "evidence_excerpt": passage["excerpt"],
        "reason": f'Explicit authoritative passage matched via {row.get("_v710_kind")}: {passage["matched_term"]}.',
        "resolution_method": "OFFICIAL_DOCUMENTATION",
    }


"WAITING_OFFICIAL"


st.divider()
st.subheader("Stage 45 v7.10 — Topic Document & Annex Resolver")
st.info(
    "v7.10 folosește resolution_method canonic OFFICIAL_DOCUMENTATION și pornește de la documentul oficial al topicului blocat, urmărește recursiv referințele oficiale până la 3 niveluri, "
    "extrage conținutul documentelor referite și caută pasajul explicit pentru fiecare cerință. Rămâne fail-closed."
)

v79_docs = s45v77_load_documents()
v79_graph, v79_fetch_cache = {}, {}
try:
    v79_graph, v79_fetch_cache = s45v79_build_reference_graph(v79_docs, max_depth=3, max_nodes=80)
except Exception as exc:
    st.warning(f"v7.10 topic document + annex graph warning: {type(exc).__name__}: {str(exc)[:500]}")

v79_tasks = [t for t in current_tasks if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION" and normalize_text(t.get("task_status")).upper() != "COMPLETED"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Unresolved OFFICIAL", len(v79_tasks))
m2.metric("Reference graph nodes", len(v79_graph))
m3.metric("Exact-topic roots", sum(1 for n in v79_graph.values() if n.get("depth") == 0))
m4.metric("Max reference depth", max([int(n.get("depth") or 0) for n in v79_graph.values()] or [0]))

with st.expander("v7.10 topic document + annex graph", expanded=False):
    if v79_graph:
        st.dataframe([{"Depth": n.get("depth"), "Root": n.get("root_url"), "Parent": n.get("parent_url"), "URL": n.get("url"), "Reason": n.get("reason")} for n in v79_graph.values()], use_container_width=True, hide_index=True)
    else:
        st.info("No authoritative reference graph is currently available.")

if v79_tasks and st.button("🧬 Run Stage 45 v7.10.1 reference-chain resolution", type="primary", use_container_width=True, key="stage45_v79_run"):
    run = (supabase.table("locked_evidence_worker_runs").insert({
        "user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id, "opportunity_identity": identity,
        "total_tasks": len(v79_tasks), "worker_status": "RUNNING", "deep_resolution_version": "v7.10.1",
        "started_at": now_iso(), "summary": {"stage": 45, "version": "v7.10.1", "reference_graph_nodes": len(v79_graph)},
        "updated_at": now_iso(),
    }).execute()).data or []
    if not run:
        st.error("Nu am putut crea Stage 45 v7.9 run.")
    else:
        run_id = str(run[0]["id"])
        resolved = waiting = failed = 0
        bar = st.progress(0)
        for idx, task in enumerate(v79_tasks, 1):
            try:
                state = s45v79_run_task(task, run_id, v79_graph, v79_fetch_cache)
                resolved += int(state == "RESOLVED")
                waiting += int(state != "RESOLVED")
            except Exception as exc:
                failed += 1
                try:
                    diagnostic = s45v71_log_error(task=task, worker_run_id=run_id, worker_item_id=None, error_stage="V79_TASK_EXECUTION", exc=exc, error_url=None, request_payload={"identity": identity, "requirement": task.get("requirement_label")}, diagnostic_payload={"stage": 45, "version": "v7.10.1", "function": "s45v79_run_task"})
                    s45v71_update_run_diagnostics(run_id, task, diagnostic)
                except Exception:
                    pass
                st.error(f"{task.get('requirement_label')} — {type(exc).__name__}: {str(exc)[:500]}")
            bar.progress(idx / len(v79_tasks))

        final = "FAILED" if failed and not resolved and not waiting else "COMPLETED" if resolved == len(v79_tasks) else "WAITING"
        run_items = rows("locked_evidence_worker_items", {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "worker_run_id": run_id}, "created_at", 100)
        total_candidates = sum(int((i.get("official_document_payload") or {}).get("candidate_count") or 0) for i in run_items)
        total_inspected = sum(len((i.get("official_document_payload") or {}).get("inspected") or []) for i in run_items)
        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": resolved, "waiting_tasks": waiting, "failed_tasks": failed,
            "worker_status": final, "diagnostic_status": "FAILED" if final == "FAILED" else "PARTIAL_FAILURE" if failed else "CLEAN",
            "official_documents_checked": total_inspected, "official_sources_found": len(v79_graph),
            "official_tasks_resolved": resolved, "official_tasks_waiting": waiting,
            "deep_resolution_version": "v7.10.1",
            "provenance_summary": {"reference_graph_nodes": len(v79_graph), "documents_inspected": total_inspected, "evidence_candidates": total_candidates, "resolved": resolved, "waiting": waiting, "failed": failed},
            "completed_at": now_iso() if final in {"COMPLETED", "FAILED"} else None, "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()
        st.success(f"Stage 45 v7.9: {final} — Resolved {resolved}, Waiting {waiting}, Failed {failed}, Candidates {total_candidates}.")
        st.rerun()

v79_runs = rows("locked_evidence_worker_runs", {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id}, "created_at", 50)
v79_runs = [r for r in v79_runs if normalize_text(r.get("deep_resolution_version")).lower() in {"v7.9", "v7.10", "v7.10.1"}]
if v79_runs:
    latest = v79_runs[0]
    st.subheader("Latest Stage 45 v7.10.1 Result")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Status", latest.get("worker_status") or "—")
    q2.metric("Official resolved", latest.get("official_tasks_resolved") or 0)
    q3.metric("Official waiting", latest.get("official_tasks_waiting") or 0)
    q4.metric("Reference graph nodes", (latest.get("provenance_summary") or {}).get("reference_graph_nodes", 0))
    latest_items = rows("locked_evidence_worker_items", {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id, "worker_run_id": str(latest.get("id"))}, "created_at", 100)
    if latest_items:
        st.dataframe([{
            "Requirement": i.get("requirement_label"), "Status": i.get("worker_status"),
            "Exact topic": i.get("exact_topic_verified"), "Authoritative": i.get("authoritative_source_verified"),
            "Explicit evidence": i.get("explicit_evidence_verified"), "Evidence URL": i.get("evidence_url"),
            "Evidence excerpt": normalize_text(i.get("evidence_excerpt"))[:1200],
            "Reason": i.get("reason") or i.get("missing_evidence_reason"),
        } for i in latest_items], use_container_width=True, hide_index=True)

st.caption("v7.10 invariant: exact-topic identity plus a traceable official document chain establishes applicability; COMPLETED still requires an explicit authoritative passage. COMPLETED still requires an explicit authoritative passage that answers the locked requirement.")
# =====================================================================
# END STAGE 45 v7.9
# =====================================================================


# ============================================================
# v7.10 TOPIC DOCUMENT + ANNEX DIAGNOSTICS
# ============================================================
try:
    st.divider()
    st.subheader("Stage 45 v7.10.1 — Topic Document & Annex Diagnostics")
    st.caption(
        "Verifică separat identitatea exactă a topicului și caută pasajele explicite "
        "în documentele oficiale/annex-urile deja stocate. Acest panou este fail-closed."
    )

    _v710_docs = []
    try:
        _v710_q = (
            supabase.table("locked_evidence_official_documents")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_lock_id", lock_id)
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        _v710_docs = _v710_q.data or []
    except Exception as _v710_exc:
        st.warning(f"v7.10 document diagnostic unavailable: {_v710_exc}")

    _v710_ranked = s45v710_rank_topic_documents(_v710_docs, identity)
    _v710_exact = [d for d in _v710_ranked if d.get("_v710_exact")]

    c1, c2, c3 = st.columns(3)
    c1.metric("Official documents loaded", len(_v710_docs))
    c2.metric("Exact-topic documents", len(_v710_exact))
    c3.metric("Traceable official candidates", len(_v710_ranked))

    _v710_rows = []
    for _rk, _label in [
        ("APPLICANT_ELIGIBILITY", "Applicant eligibility"),
        ("CONSORTIUM_REQUIREMENTS", "Consortium requirements"),
        ("TRL_REQUIREMENTS", "TRL requirements"),
    ]:
        _res = s45v710_resolve_from_documents(_rk, identity, _v710_docs)
        _v710_rows.append({
            "Requirement": _label,
            "Status": _res["status"],
            "Exact topic": _res["exact_topic_verified"],
            "Authoritative": _res["authoritative_source_verified"],
            "Explicit evidence": _res["explicit_evidence_verified"],
            "Evidence URL": _res["evidence_url"],
            "Reason": _res["reason"],
        })

    st.dataframe(_v710_rows, use_container_width=True, hide_index=True)
except Exception as _v710_ui_exc:
    st.warning(f"Stage 45 v7.10 diagnostics could not be rendered: {_v710_ui_exc}")



# =====================================================================
# STAGE 45 v7.10.2 — EVIDENCE PROVENANCE & FALSE-POSITIVE GUARD
# =====================================================================
# Goals:
#   1) Reject CAS/login/auth/error pages as evidence.
#   2) Validate the final URL after redirects.
#   3) Require an official EC/EU final host.
#   4) Require the cited excerpt to exist in freshly fetched source content.
#   5) Require exact-topic applicability OR a traceable exact-topic provenance chain.
#   6) Persist SHA-256 fingerprints for source content and excerpt.
#   7) Keep fail-closed semantics: no verified provenance => WAITING_OFFICIAL.
#
# IMPORTANT:
#   This layer does not trust an earlier RESOLVED flag by itself.
#   It revalidates substantive evidence independently.

import hashlib


def s45v7102_sha256_text(value):
    data = normalize_text(value).encode("utf-8", errors="ignore")
    return hashlib.sha256(data).hexdigest() if data else None


def s45v7102_normalize_body(value):
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def s45v7102_is_auth_or_error_url(url):
    url_l = normalize_text(url).lower()
    if not url_l:
        return True

    bad_markers = (
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
        "error",
        "logout",
    )
    return any(marker in url_l for marker in bad_markers)


def s45v7102_is_auth_or_error_content(text, title=""):
    corpus = " ".join([normalize_text(title), normalize_text(text)]).lower()

    bad_phrases = (
        "central authentication service",
        "sign in",
        "log in",
        "login",
        "authentication required",
        "access denied",
        "you must authenticate",
        "session expired",
        "single sign-on",
        "single sign on",
        "an error occurred",
        "page not found",
        "404 not found",
        "403 forbidden",
    )

    # Require at least one strong auth/error signal.
    return any(p in corpus for p in bad_phrases)


def s45v7102_official_final_url(url):
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


def s45v7102_fetch_for_verification(url, timeout=45):
    result = {
        "ok": False,
        "requested_url": normalize_text(url),
        "final_url": normalize_text(url),
        "status": None,
        "content_type": "",
        "text": "",
        "content_kind": "UNKNOWN",
        "error": "",
        "redirected": False,
    }

    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "GreenRise/Stage45-v7.10.2",
                "Accept": "application/pdf,application/json,text/html,text/plain,*/*",
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "close",
            },
        )

        result["status"] = r.status_code
        result["final_url"] = normalize_text(getattr(r, "url", "")) or normalize_text(url)
        result["redirected"] = (
            s45v77_normalize_url(result["final_url"])
            != s45v77_normalize_url(url)
        )
        result["content_type"] = normalize_text(r.headers.get("content-type", ""))

        raw = {
            "ok": bool(r.ok and (r.content or b"")),
            "url": result["final_url"],
            "final_url": result["final_url"],
            "status": r.status_code,
            "content_type": result["content_type"],
            "content": r.content or b"",
            "text": "",
            "error": "" if r.ok else f"HTTP {r.status_code}",
        }

        try:
            raw["text"] = r.text or ""
        except Exception:
            raw["text"] = ""

        extracted_text, content_kind = s45v78_extract_document_text(raw)
        result["text"] = extracted_text or raw["text"] or ""
        result["content_kind"] = content_kind
        result["ok"] = bool(r.ok and result["text"].strip())

        if not r.ok:
            result["error"] = f"HTTP {r.status_code}"

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)}"

    return result


def s45v7102_excerpt_in_source(excerpt, source_text):
    excerpt_n = s45v7102_normalize_body(excerpt).lower()
    source_n = s45v7102_normalize_body(source_text).lower()

    if not excerpt_n or not source_n:
        return False

    if excerpt_n in source_n:
        return True

    # Conservative fuzzy containment fallback:
    # require several sizeable exact fragments from the excerpt to exist.
    words = excerpt_n.split()
    if len(words) < 16:
        return False

    fragment_size = 12
    fragments = []

    for i in range(0, len(words) - fragment_size + 1, fragment_size):
        frag = " ".join(words[i:i + fragment_size]).strip()
        if len(frag) >= 60:
            fragments.append(frag)

    if not fragments:
        return False

    hits = sum(1 for frag in fragments[:8] if frag in source_n)

    # Strict: at least 2 matching fragments and at least half of sampled fragments.
    required = max(2, (min(len(fragments), 8) + 1) // 2)
    return hits >= required


def s45v7102_chain_is_traceable(chain, topic_identity):
    if not chain:
        return False

    try:
        chain_text = json.dumps(chain, ensure_ascii=False, default=str)
    except Exception:
        chain_text = normalize_text(chain)

    if not s45v710_exact_topic_match(chain_text, topic_identity):
        return False

    # Every material URL in the chain must remain official.
    urls = re.findall(r'https?://[^\s"\'<>\]\[(){}]+', chain_text)
    if not urls:
        return False

    return all(s45v7102_official_final_url(u.rstrip(".,;:")) for u in urls)


def s45v7102_evidence_guard(
    *,
    topic_identity,
    evidence_url,
    evidence_excerpt,
    provenance_chain=None,
    source_title="",
):
    requested_url = normalize_text(evidence_url)
    excerpt = normalize_text(evidence_excerpt)

    verdict = {
        "guard_status": "REJECTED",
        "requested_url": requested_url,
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
        "excerpt_sha256": s45v7102_sha256_text(excerpt),
        "reason": "",
    }

    if not requested_url or not excerpt:
        verdict["reason"] = "Missing evidence URL or excerpt."
        return verdict

    fetched = s45v7102_fetch_for_verification(requested_url)

    verdict["final_url"] = fetched.get("final_url")
    verdict["http_status"] = fetched.get("status")
    verdict["content_type"] = fetched.get("content_type")
    verdict["content_kind"] = fetched.get("content_kind")
    verdict["redirected"] = bool(fetched.get("redirected"))

    if not fetched.get("ok"):
        verdict["reason"] = (
            "Evidence source could not be freshly fetched for provenance verification: "
            + normalize_text(fetched.get("error"))
        )
        return verdict

    final_url = normalize_text(fetched.get("final_url"))
    source_text = fetched.get("text") or ""

    verdict["official_final_host"] = s45v7102_official_final_url(final_url)
    verdict["auth_or_error_url"] = s45v7102_is_auth_or_error_url(final_url)
    verdict["auth_or_error_content"] = s45v7102_is_auth_or_error_content(
        source_text,
        source_title,
    )
    verdict["excerpt_present_in_source"] = s45v7102_excerpt_in_source(
        excerpt,
        source_text,
    )
    verdict["exact_topic_in_source"] = s45v710_exact_topic_match(
        " ".join([final_url, source_title, source_text[:150000]]),
        topic_identity,
    )
    verdict["traceable_topic_chain"] = s45v7102_chain_is_traceable(
        provenance_chain,
        topic_identity,
    )

    verdict["document_sha256"] = s45v7102_sha256_text(
        s45v7102_normalize_body(source_text)
    )

    if not verdict["official_final_host"]:
        verdict["reason"] = "Final URL is not on an allowed official EC/EU host."
        return verdict

    if verdict["auth_or_error_url"]:
        verdict["reason"] = "Final URL appears to be CAS/login/auth/error infrastructure, not substantive evidence."
        return verdict

    if verdict["auth_or_error_content"]:
        verdict["reason"] = "Fetched page content appears to be authentication/error content."
        return verdict

    if not verdict["excerpt_present_in_source"]:
        verdict["reason"] = "The cited evidence excerpt was not found in the freshly fetched source content."
        return verdict

    if not (
        verdict["exact_topic_in_source"]
        or verdict["traceable_topic_chain"]
    ):
        verdict["reason"] = "Exact-topic applicability was not proven by the source or provenance chain."
        return verdict

    verdict["guard_status"] = "VERIFIED"
    verdict["reason"] = (
        "Evidence provenance verified: official final host, non-auth substantive content, "
        "excerpt present in source, and exact-topic applicability established."
    )
    return verdict


def s45v7102_load_latest_evidence_items():
    # Load recent items for the current locked opportunity.
    items = rows(
        "locked_evidence_worker_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        500,
    )

    # Keep newest item per requirement label/key.
    newest = {}
    for item in items:
        label = (
            normalize_text(item.get("requirement_key"))
            or normalize_text(item.get("requirement_label"))
            or normalize_text(item.get("requirement_id"))
        )
        if not label:
            continue
        if label not in newest:
            newest[label] = item

    return list(newest.values())


def s45v7102_requirement_guard_rows():
    guarded = []

    for item in s45v7102_load_latest_evidence_items():
        route = normalize_text(item.get("route_type")).upper()
        if route != "OFFICIAL_VERIFICATION":
            continue

        status = normalize_text(item.get("worker_status")).upper()

        # Only guard rows that already claim substantive evidence or resolution.
        if status != "RESOLVED" and not item.get("explicit_evidence_verified"):
            continue

        guard = s45v7102_evidence_guard(
            topic_identity=identity,
            evidence_url=item.get("evidence_url"),
            evidence_excerpt=item.get("evidence_excerpt"),
            provenance_chain=item.get("provenance_chain"),
            source_title=item.get("source_title"),
        )

        guarded.append({
            "item": item,
            "guard": guard,
        })

    return guarded


def s45v7102_demote_execution_task_if_unverified(item, guard):
    """
    If an earlier Stage 45 worker resolved a task using evidence that fails the
    new provenance guard, return the execution task to WAITING_OFFICIAL.
    This is deliberately conservative.
    """
    if guard.get("guard_status") == "VERIFIED":
        return False

    task_id = item.get("execution_task_id")
    if not task_id:
        return False

    supabase.table("locked_evidence_execution_tasks").update({
        "task_status": "WAITING_OFFICIAL",
        "completion_status": None,
        "completion_source": None,
        "completion_reference": None,
        "completed_at": None,
        "completion_payload": {
            "stage": 45,
            "status": "WAITING_OFFICIAL",
            "reason": (
                "Previous evidence resolution was revoked by Stage 45 v7.10.2 "
                "because provenance verification failed."
            ),
            "provenance_guard": guard,
        },
        "updated_at": now_iso(),
    }).eq("id", task_id).eq("user_id", user_id).execute()

    supabase.table("locked_evidence_worker_items").update({
        "worker_status": "WAITING_OFFICIAL",
        "official_verified": False,
        "explicit_evidence_verified": False,
        "official_document_status": "WAITING_OFFICIAL",
        "missing_evidence_reason": (
            "Previous RESOLVED evidence failed Stage 45 v7.10.2 provenance verification."
        ),
        "next_action": (
            "Find a substantive official source whose cited excerpt can be verified in the fetched document."
        ),
        "official_document_payload": {
            **(item.get("official_document_payload") or {}),
            "v7_10_2_provenance_guard": guard,
        },
        "updated_at": now_iso(),
    }).eq("id", item.get("id")).eq("user_id", user_id).execute()

    return True


def s45v7102_mark_worker_item_verified(item, guard):
    if guard.get("guard_status") != "VERIFIED":
        return False

    payload = item.get("official_document_payload") or {}
    payload["v7_10_2_provenance_guard"] = guard

    supabase.table("locked_evidence_worker_items").update({
        "official_verified": True,
        "exact_topic_verified": True,
        "authoritative_source_verified": True,
        "explicit_evidence_verified": True,
        "official_document_status": "VERIFIED",
        "retrieved_at": now_iso(),
        "official_document_payload": payload,
        "updated_at": now_iso(),
    }).eq("id", item.get("id")).eq("user_id", user_id).execute()

    return True


st.divider()
st.subheader("Stage 45 v7.10.2 — Evidence Provenance & False-Positive Guard")
st.info(
    "v7.10.2 reverifică dovezile RESOLVED: urmărește redirect-ul final, respinge CAS/login/error pages, "
    "verifică faptul că pasajul citat există efectiv în document și cere exact-topic sau provenance chain trasabil."
)

_v7102_guarded = s45v7102_requirement_guard_rows()

g1, g2, g3 = st.columns(3)
g1.metric("Evidence rows to guard", len(_v7102_guarded))
g2.metric(
    "Provenance verified",
    sum(1 for x in _v7102_guarded if x["guard"].get("guard_status") == "VERIFIED"),
)
g3.metric(
    "Rejected / needs review",
    sum(1 for x in _v7102_guarded if x["guard"].get("guard_status") != "VERIFIED"),
)

if _v7102_guarded:
    st.dataframe(
        [
            {
                "Requirement": x["item"].get("requirement_label"),
                "Worker status": x["item"].get("worker_status"),
                "Guard": x["guard"].get("guard_status"),
                "HTTP": x["guard"].get("http_status"),
                "Final URL": x["guard"].get("final_url"),
                "Official final host": x["guard"].get("official_final_host"),
                "Auth/error URL": x["guard"].get("auth_or_error_url"),
                "Auth/error content": x["guard"].get("auth_or_error_content"),
                "Excerpt in source": x["guard"].get("excerpt_present_in_source"),
                "Exact topic in source": x["guard"].get("exact_topic_in_source"),
                "Traceable chain": x["guard"].get("traceable_topic_chain"),
                "Reason": x["guard"].get("reason"),
            }
            for x in _v7102_guarded
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Nu există încă dovezi RESOLVED care necesită provenance guard.")

if st.button(
    "🛡️ Run Stage 45 v7.10.2 provenance guard",
    type="primary",
    use_container_width=True,
    key="stage45_v7102_guard",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(_v7102_guarded),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.10.2",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "diagnostics": [],
            "started_at": now_iso(),
            "summary": {
                "stage": 45,
                "version": "v7.10.2",
                "guarded_items": len(_v7102_guarded),
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.10.2 run.")
    else:
        run_id = str(run[0]["id"])

        verified = rejected = failed = 0
        result_rows = []

        for entry in _v7102_guarded:
            item = entry["item"]

            try:
                guard = s45v7102_evidence_guard(
                    topic_identity=identity,
                    evidence_url=item.get("evidence_url"),
                    evidence_excerpt=item.get("evidence_excerpt"),
                    provenance_chain=item.get("provenance_chain"),
                    source_title=item.get("source_title"),
                )

                if guard.get("guard_status") == "VERIFIED":
                    verified += 1
                    s45v7102_mark_worker_item_verified(item, guard)
                else:
                    rejected += 1
                    s45v7102_demote_execution_task_if_unverified(item, guard)

                result_rows.append({
                    "requirement": item.get("requirement_label"),
                    "worker_item_id": item.get("id"),
                    "execution_task_id": item.get("execution_task_id"),
                    "guard": guard,
                })

            except Exception as exc:
                failed += 1
                result_rows.append({
                    "requirement": item.get("requirement_label"),
                    "worker_item_id": item.get("id"),
                    "execution_task_id": item.get("execution_task_id"),
                    "guard": {
                        "guard_status": "ERROR",
                        "reason": f"{type(exc).__name__}: {str(exc)}",
                    },
                })

        final = (
            "FAILED"
            if failed and not verified and not rejected
            else "PARTIAL_FAILURE"
            if failed
            else "COMPLETED"
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": verified,
            "waiting_tasks": rejected,
            "failed_tasks": failed,
            "worker_status": "FAILED" if final == "FAILED" else "COMPLETED",
            "diagnostic_status": (
                "FAILED"
                if final == "FAILED"
                else "PARTIAL_FAILURE"
                if final == "PARTIAL_FAILURE"
                else "CLEAN"
            ),
            "official_tasks_resolved": verified,
            "official_tasks_waiting": rejected,
            "deep_resolution_version": "v7.10.2",
            "provenance_summary": {
                "verified": verified,
                "rejected": rejected,
                "failed": failed,
                "results": result_rows,
            },
            "completed_at": now_iso(),
            "updated_at": now_iso(),
        }).eq("id", run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.10.2: {final} — provenance verified {verified}, "
            f"rejected {rejected}, failed {failed}."
        )
        st.rerun()


_v7102_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    50,
)

_v7102_runs = [
    r
    for r in _v7102_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.10.2"
]

if _v7102_runs:
    _latest_v7102 = _v7102_runs[0]

    st.subheader("Latest Stage 45 v7.10.2 Result")

    z1, z2, z3, z4 = st.columns(4)
    z1.metric("Status", _latest_v7102.get("worker_status") or "—")
    z2.metric(
        "Provenance verified",
        (_latest_v7102.get("provenance_summary") or {}).get("verified", 0),
    )
    z3.metric(
        "Rejected",
        (_latest_v7102.get("provenance_summary") or {}).get("rejected", 0),
    )
    z4.metric(
        "Failed",
        (_latest_v7102.get("provenance_summary") or {}).get("failed", 0),
    )

    with st.expander("v7.10.2 provenance details", expanded=False):
        st.json((_latest_v7102.get("provenance_summary") or {}).get("results", []))

st.caption(
    "v7.10.2 invariant: RESOLVED evidence is trusted only after fresh-source provenance verification. "
    "CAS/login/auth/error pages, unverifiable excerpts, or non-traceable applicability are rejected."
)
# =====================================================================
# END STAGE 45 v7.10.2
# =====================================================================



# =====================================================================
# STAGE 45 v7.10.3 — PERSISTED EVIDENCE HANDOFF TO STAGE 46
# =====================================================================
# Purpose:
#   - Reuse the strict Topic Document & Annex Resolver already present.
#   - Persist only strict RESOLVED results into locked_evidence_worker_items.
#   - Persist exact requirement identity + URL + excerpt + provenance.
#   - Do NOT treat Stage 45 candidate evidence as final provenance validation.
#   - Stage 46 remains the independent trust gate.
#
# Fail-closed invariant:
#   WAITING stays WAITING. Only exact-topic + authoritative + explicit evidence
#   is persisted as a RESOLVED Stage 45 candidate for Stage 46.

def s45v7103_requirement_key(task):
    raw = " ".join([
        normalize_text(task.get("requirement_key")),
        normalize_text(task.get("requirement_category")),
        normalize_text(task.get("requirement_label")),
    ]).lower()

    if any(x in raw for x in ("applicant", "eligib", "beneficiar")):
        return "APPLICANT_ELIGIBILITY"

    if any(x in raw for x in ("consortium", "partner")):
        return "CONSORTIUM_REQUIREMENTS"

    if any(x in raw for x in ("trl", "technology readiness", "readiness level")):
        return "TRL_REQUIREMENTS"

    return None


def s45v7103_source_row_for_result(result, documents):
    evidence_url = normalize_text(result.get("evidence_url"))

    if not evidence_url:
        return None

    # Prefer exact URL match.
    for row in documents or []:
        if normalize_text(row.get("source_url")).rstrip("/") == evidence_url.rstrip("/"):
            return row

    # Conservative fallback: compare without fragments.
    clean = evidence_url.split("#", 1)[0].rstrip("/")
    for row in documents or []:
        row_url = normalize_text(row.get("source_url")).split("#", 1)[0].rstrip("/")
        if row_url and row_url == clean:
            return row

    return None


def s45v7103_worker_payload(task, worker_run_id, result, source_row):
    provenance_chain = []

    if source_row:
        chain = source_row.get("provenance_chain") or []
        if isinstance(chain, list):
            provenance_chain = chain
        elif chain:
            provenance_chain = [chain]

    evidence_url = normalize_text(result.get("evidence_url"))

    if evidence_url and evidence_url not in provenance_chain:
        provenance_chain.append(evidence_url)

    evidence_excerpt = normalize_text(result.get("evidence_excerpt"))[:10000]

    return {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "execution_run_id": execution_run_id,
        "worker_run_id": worker_run_id,
        "execution_task_id": task.get("id"),
        "requirement_id": task.get("requirement_id"),
        "opportunity_identity": identity,
        "requirement_key": task.get("requirement_key"),
        "requirement_category": task.get("requirement_category"),
        "requirement_label": task.get("requirement_label"),
        "route_type": task.get("route_type"),
        "destination_module": task.get("destination_module"),

        "worker_action": "STAGE46_EVIDENCE_HANDOFF",
        "worker_status": "RESOLVED",

        "resolved_value": {
            "stage45_candidate": True,
            "requirement": task.get("requirement_label"),
        },

        "evidence_source": "OFFICIAL_DOCUMENTATION",
        "evidence_reference": (
            normalize_text(source_row.get("evidence_reference"))
            if source_row
            else normalize_text(task.get("requirement_label"))
        ),
        "evidence_url": evidence_url,
        "evidence_excerpt": evidence_excerpt,
        "confidence": "High",

        # Stage 45 has established the candidate evidence conditions,
        # but Stage 46 still independently re-fetches/revalidates provenance.
        "official_verified": False,
        "reason": normalize_text(result.get("reason"))[:5000],
        "next_action": "VALIDATE_IN_STAGE_46",

        "source_title": (
            normalize_text(source_row.get("source_title"))
            if source_row
            else "Official European Commission source"
        ),
        "document_type": (
            normalize_text(source_row.get("document_type"))
            if source_row
            else "OFFICIAL_EC_DOCUMENT"
        ),
        "source_authority": (
            normalize_text(source_row.get("source_authority"))
            if source_row
            else "EUROPEAN_COMMISSION"
        ),

        "topic_identity": identity,
        "provenance_chain": provenance_chain,

        "documents_checked": [
            normalize_text(source_row.get("source_url"))
        ] if source_row else [evidence_url],

        "searches_attempted": [],
        "transport_attempts": [],

        "resolution_method": "OFFICIAL_DOCUMENTATION",
        "retrieved_at": now_iso(),

        "exact_topic_verified": bool(result.get("exact_topic_verified")),
        "authoritative_source_verified": bool(result.get("authoritative_source_verified")),
        "explicit_evidence_verified": bool(result.get("explicit_evidence_verified")),

        # EVIDENCE_FOUND deliberately means "candidate ready for Stage 46",
        # not final provenance trust.
        "official_document_status": "EVIDENCE_FOUND",

        "official_document_payload": {
            "stage": 45,
            "version": "v7.10.3",
            "handoff_target": "STAGE_46",
            "stage45_resolution": result,
            "source_document_id": source_row.get("id") if source_row else None,
            "source_document_status": source_row.get("retrieval_status") if source_row else None,
        },

        "resolved_at": now_iso(),
        "updated_at": now_iso(),
    }


def s45v7103_persist_candidate(task, worker_run_id, result, documents):
    # Strict persistence guard.
    if normalize_text(result.get("status")).upper() != "RESOLVED":
        return "WAITING_OFFICIAL", None

    if not (
        bool(result.get("exact_topic_verified"))
        and bool(result.get("authoritative_source_verified"))
        and bool(result.get("explicit_evidence_verified"))
        and normalize_text(result.get("evidence_url"))
        and normalize_text(result.get("evidence_excerpt"))
    ):
        return "WAITING_OFFICIAL", None

    source_row = s45v7103_source_row_for_result(result, documents)
    payload = s45v7103_worker_payload(task, worker_run_id, result, source_row)

    inserted = (
        supabase.table("locked_evidence_worker_items")
        .insert(payload)
        .execute()
    ).data or []

    if not inserted:
        raise RuntimeError("Stage 45 v7.10.3 could not persist the resolved evidence handoff.")

    return "RESOLVED", inserted[0]


def s45v7103_update_execution_task_candidate(task, worker_item):
    """
    Preserve Stage 46 as the final provenance gate.
    The execution task remains WAITING_OFFICIAL, but its payload records that
    Stage 45 has produced a candidate evidence item ready for Stage 46.
    """
    existing_payload = as_dict(task.get("completion_payload"))

    existing_payload["stage45_evidence_candidate"] = {
        "stage": 45,
        "version": "v7.10.3",
        "worker_item_id": worker_item.get("id"),
        "status": "RESOLVED",
        "evidence_url": worker_item.get("evidence_url"),
        "evidence_reference": worker_item.get("evidence_reference"),
        "evidence_excerpt": worker_item.get("evidence_excerpt"),
        "exact_topic_verified": worker_item.get("exact_topic_verified"),
        "authoritative_source_verified": worker_item.get("authoritative_source_verified"),
        "explicit_evidence_verified": worker_item.get("explicit_evidence_verified"),
        "handoff": "STAGE_46",
        "created_at": now_iso(),
    }

    supabase.table("locked_evidence_execution_tasks").update({
        "task_status": "WAITING_OFFICIAL",
        "completion_status": None,
        "completion_source": None,
        "completion_reference": None,
        "completed_at": None,
        "completion_payload": existing_payload,
        "updated_at": now_iso(),
    }).eq("id", task.get("id")).eq("user_id", user_id).execute()


st.divider()
st.subheader("Stage 45 v7.10.3 — Persisted Evidence Handoff to Stage 46")
st.info(
    "v7.10.3 repară strict persistența Stage 45 → Stage 46. "
    "Numai rezultatele care au simultan exact-topic, sursă autoritativă, pasaj explicit, "
    "evidence URL și evidence excerpt sunt salvate ca worker_status=RESOLVED. "
    "Execution task-ul rămâne WAITING_OFFICIAL până când Etapa 46 validează provenance."
)

_v7103_docs = []
try:
    _v7103_docs = (
        supabase.table("locked_evidence_official_documents")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
    ).data or []
except Exception as _v7103_docs_exc:
    st.warning(f"v7.10.3 document load warning: {_v7103_docs_exc}")

_v7103_tasks = [
    t for t in current_tasks
    if normalize_text(t.get("route_type")).upper() == "OFFICIAL_VERIFICATION"
]

_v7103_preview = []

for _task in _v7103_tasks:
    _rk = s45v7103_requirement_key(_task)

    if not _rk:
        _res = {
            "status": "WAITING_OFFICIAL",
            "exact_topic_verified": False,
            "authoritative_source_verified": False,
            "explicit_evidence_verified": False,
            "evidence_url": None,
            "evidence_excerpt": None,
            "reason": "Requirement family is not handled by v7.10.3 persistence.",
        }
    else:
        _res = s45v710_resolve_from_documents(
            _rk,
            identity,
            _v7103_docs,
        )

    _v7103_preview.append({
        "task": _task,
        "requirement_key_v7103": _rk,
        "result": _res,
    })

v31, v32, v33, v34 = st.columns(4)
v31.metric("OFFICIAL requirements", len(_v7103_tasks))
v32.metric(
    "Strict RESOLVED candidates",
    sum(1 for x in _v7103_preview if normalize_text(x["result"].get("status")).upper() == "RESOLVED"),
)
v33.metric(
    "Candidates with URL + excerpt",
    sum(
        1 for x in _v7103_preview
        if normalize_text(x["result"].get("evidence_url"))
        and normalize_text(x["result"].get("evidence_excerpt"))
    ),
)
v34.metric("Official documents loaded", len(_v7103_docs))

if _v7103_preview:
    st.dataframe(
        [
            {
                "Requirement": x["task"].get("requirement_label"),
                "Resolver key": x["requirement_key_v7103"],
                "Status": x["result"].get("status"),
                "Exact topic": x["result"].get("exact_topic_verified"),
                "Authoritative": x["result"].get("authoritative_source_verified"),
                "Explicit evidence": x["result"].get("explicit_evidence_verified"),
                "Evidence URL": x["result"].get("evidence_url"),
                "Excerpt chars": len(normalize_text(x["result"].get("evidence_excerpt"))),
                "Reason": x["result"].get("reason"),
            }
            for x in _v7103_preview
        ],
        use_container_width=True,
        hide_index=True,
    )

if st.button(
    "📤 Run Stage 45 v7.10.3 persisted handoff",
    type="primary",
    use_container_width=True,
    key="stage45_v7103_handoff",
):
    run = (
        supabase.table("locked_evidence_worker_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "execution_run_id": execution_run_id,
            "opportunity_identity": identity,
            "total_tasks": len(_v7103_tasks),
            "worker_status": "RUNNING",
            "deep_resolution_version": "v7.10.3",
            "diagnostic_status": "CLEAN",
            "error_count": 0,
            "started_at": now_iso(),
            "summary": {
                "stage": 45,
                "version": "v7.10.3",
                "purpose": "PERSISTED_STAGE46_HANDOFF",
            },
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not run:
        st.error("Nu am putut crea Stage 45 v7.10.3 run.")
    else:
        _run_id = str(run[0]["id"])
        _resolved = 0
        _waiting = 0
        _failed = 0
        _handoff_items = []

        _bar = st.progress(0)

        for _idx, _entry in enumerate(_v7103_preview, 1):
            _task = _entry["task"]
            _res = _entry["result"]

            try:
                _state, _worker_item = s45v7103_persist_candidate(
                    _task,
                    _run_id,
                    _res,
                    _v7103_docs,
                )

                if _state == "RESOLVED" and _worker_item:
                    _resolved += 1
                    _handoff_items.append(_worker_item)
                    s45v7103_update_execution_task_candidate(
                        _task,
                        _worker_item,
                    )
                else:
                    _waiting += 1

            except Exception as _exc:
                _failed += 1
                st.warning(
                    f"{_task.get('requirement_label')} — "
                    f"{type(_exc).__name__}: {str(_exc)[:500]}"
                )

            _bar.progress(_idx / max(1, len(_v7103_preview)))

        _final = (
            "FAILED"
            if _failed and _resolved == 0 and _waiting == 0
            else "PARTIAL_FAILURE"
            if _failed
            else "COMPLETED"
            if (_resolved + _waiting) == len(_v7103_tasks)
            else "WAITING"
        )

        supabase.table("locked_evidence_worker_runs").update({
            "resolved_tasks": _resolved,
            "waiting_tasks": _waiting,
            "failed_tasks": _failed,
            "worker_status": _final,
            "diagnostic_status": (
                "FAILED"
                if _final == "FAILED"
                else "PARTIAL_FAILURE"
                if _final == "PARTIAL_FAILURE"
                else "CLEAN"
            ),
            "official_tasks_resolved": _resolved,
            "official_tasks_waiting": _waiting,
            "deep_resolution_version": "v7.10.3",
            "provenance_summary": {
                "stage": 45,
                "version": "v7.10.3",
                "handoff_target": "STAGE_46",
                "resolved_candidates_persisted": _resolved,
                "waiting": _waiting,
                "failed": _failed,
                "worker_item_ids": [
                    x.get("id") for x in _handoff_items
                ],
            },
            "completed_at": now_iso(),
            "updated_at": now_iso(),
        }).eq("id", _run_id).eq("user_id", user_id).execute()

        st.success(
            f"Stage 45 v7.10.3: {_final} — "
            f"persisted RESOLVED {_resolved}, waiting {_waiting}, failed {_failed}."
        )
        st.rerun()


_v7103_runs = rows(
    "locked_evidence_worker_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

_v7103_runs = [
    r for r in _v7103_runs
    if normalize_text(r.get("deep_resolution_version")).lower() == "v7.10.3"
]

if _v7103_runs:
    _latest_v7103 = _v7103_runs[0]

    st.subheader("Latest Stage 45 v7.10.3 Result")

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Status", _latest_v7103.get("worker_status") or "—")
    p2.metric("Persisted RESOLVED", _latest_v7103.get("official_tasks_resolved") or 0)
    p3.metric("Waiting", _latest_v7103.get("official_tasks_waiting") or 0)
    p4.metric("Failed", _latest_v7103.get("failed_tasks") or 0)

    _v7103_items = rows(
        "locked_evidence_worker_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "worker_run_id": str(_latest_v7103.get("id")),
        },
        "created_at",
        100,
    )

    if _v7103_items:
        st.subheader("Stage 46 handoff rows")
        st.dataframe(
            [
                {
                    "Requirement": i.get("requirement_label"),
                    "Worker status": i.get("worker_status"),
                    "Execution task": i.get("execution_task_id"),
                    "Requirement id": i.get("requirement_id"),
                    "Requirement key": i.get("requirement_key"),
                    "Exact topic": i.get("exact_topic_verified"),
                    "Authoritative": i.get("authoritative_source_verified"),
                    "Explicit evidence": i.get("explicit_evidence_verified"),
                    "Document status": i.get("official_document_status"),
                    "Evidence URL": i.get("evidence_url"),
                    "Excerpt chars": len(normalize_text(i.get("evidence_excerpt"))),
                }
                for i in _v7103_items
            ],
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "v7.10.3 invariant: Stage 45 persists only strict substantive evidence candidates. "
    "Final provenance trust remains the responsibility of Stage 46."
)
# =====================================================================
# END STAGE 45 v7.10.3
# =====================================================================

# =====================================================================
# STAGE 45 v7.10.7 — DIAGNOSTIC FETCH + OFFICIAL TOPIC DOCUMENT DISCOVERY
# =====================================================================
# This replaces v7.10.4 discovery behavior.
#
# Core invariants:
#   1) The ACTIVE lock canonical URL is the primary seed.
#   2) Historical Stage 45 documents are NOT re-crawled as discovery seeds.
#   3) Search API endpoints are discovery-only and can never be evidence.
#   4) CAS/EU Login/auth/error pages and static assets are rejected BEFORE promotion.
#   5) Redirects to auth/error destinations are rejected immediately.
#   6) Only substantive official EC/EU documents/pages can prove exact-topic applicability.
#   7) Exact topic must be present in the freshly fetched substantive source itself.
#   8) Applicant / consortium / TRL require an explicit passage in that same source.
#   9) Unproven requirements remain WAITING_OFFICIAL. Stage 46 remains the final gate.


def s45v7107_requirement_key(task):
    return s45v7103_requirement_key(task)


def s45v7107_host(url):
    try:
        return (urlparse(normalize_text(url)).hostname or '').lower()
    except Exception:
        return ''


def s45v7107_is_search_api(url):
    host = s45v7107_host(url)
    low = normalize_text(url).lower()
    return host == 'api.tech.ec.europa.eu' and '/search-api/' in low


def s45v7107_allowed_official_host(url):
    host = s45v7107_host(url)
    return bool(
        host == 'europa.eu'
        or host.endswith('.europa.eu')
        or host == 'ec.europa.eu'
        or host.endswith('.ec.europa.eu')
        or host == 'commission.europa.eu'
        or host.endswith('.commission.europa.eu')
    )


def s45v7107_is_static_asset(url):
    low = normalize_text(url).lower().split('?', 1)[0].split('#', 1)[0]
    return any(low.endswith(ext) for ext in (
        '.js', '.css', '.map', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp',
        '.ico', '.woff', '.woff2', '.ttf', '.eot', '.xml', '.rss', '.atom',
    ))


def s45v7107_bad_url(url):
    low = normalize_text(url).lower()
    host = s45v7107_host(url)
    if not low or not host:
        return True

    bad_hosts = (
        'webgate.',
        'trusted-digital-identity.',
        'ecas.',
        'login.',
        'authentication.',
    )
    if any(host.startswith(x) for x in bad_hosts):
        return True

    bad_markers = (
        '/cas/', '/login', '/signin', '/sign-in', '/oauth', '/authorize',
        '/authentication', '/auth/', 'privacyStatement', 'privacystatement',
        'loginrequestid=', 'ecas_sessionid', '__secure-ecas', '/logout',
        'access-denied', 'access_denied', 'sessionexpired', 'session-expired',
        '/error', 'error=', '/account', '/iam/', '/identity-and-access',
    )
    if any(x.lower() in low for x in bad_markers):
        return True

    return s45v7107_is_static_asset(url)


def s45v7107_bad_content(text, title=''):
    corpus = re.sub(r'\s+', ' ', f'{normalize_text(title)} {normalize_text(text)}').lower()
    strong = (
        'central authentication service',
        'eu login one account',
        'eu login user portal',
        'identity and access management service',
        'authentication required',
        'please sign in',
        'please log in',
        'login required',
        'access denied',
        'session expired',
        'privacy statement for users registered with the european commission',
        'request could not be processed',
        'page not found',
    )
    return any(x in corpus for x in strong)


def s45v7107_pdf_text(content):
    if not content or PdfReader is None:
        return ''
    try:
        reader = PdfReader(BytesIO(content))
        parts = []
        for page in reader.pages[:350]:
            try:
                txt = page.extract_text() or ''
            except Exception:
                txt = ''
            if txt:
                parts.append(txt)
            if sum(len(x) for x in parts) >= 350000:
                break
        return re.sub(r'\s+', ' ', '\n'.join(parts))[:350000]
    except Exception:
        return ''


def s45v7107_html_text(raw):
    raw = raw or ''
    if BeautifulSoup is None:
        return re.sub(r'\s+', ' ', raw)
    try:
        soup = BeautifulSoup(raw, 'html.parser')
        for node in soup(['script', 'style', 'noscript', 'svg', 'template']):
            node.decompose()
        return re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))
    except Exception:
        return re.sub(r'\s+', ' ', raw)


def s45v7107_fetch(url, timeout=45):
    requested = normalize_text(url)
    out = {
        'ok': False,
        'requested_url': requested,
        'final_url': requested,
        'status': None,
        'content_type': '',
        'title': '',
        'text': '',
        'raw': '',
        'json': None,
        'error': '',
        'rejected_reason': '',
        'bytes_received': 0,
        'text_chars': 0,
        'parser': '',
        'topic_token_found': False,
    }

    if not requested or not s45v7107_allowed_official_host(requested):
        out['rejected_reason'] = 'NON_OFFICIAL_OR_MISSING_URL'
        return out
    if s45v7107_bad_url(requested):
        out['rejected_reason'] = 'AUTH_ERROR_OR_STATIC_ASSET_URL'
        return out

    try:
        r = requests.get(
            requested,
            timeout=timeout,
            allow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 GreenRise/Stage45-v7.10.7',
                'Accept': 'application/json,text/html,application/xhtml+xml,text/plain,application/pdf,*/*',
                'Accept-Language': 'en-GB,en;q=0.9',
                'Cache-Control': 'no-cache',
            },
        )
        out['status'] = r.status_code
        out['final_url'] = normalize_text(r.url) or requested
        out['content_type'] = normalize_text(r.headers.get('content-type'))
        out['bytes_received'] = len(r.content or b'')

        if not s45v7107_allowed_official_host(out['final_url']):
            out['rejected_reason'] = 'REDIRECTED_TO_NON_OFFICIAL_HOST'
            return out
        if s45v7107_bad_url(out['final_url']):
            out['rejected_reason'] = 'REDIRECTED_TO_AUTH_ERROR_OR_STATIC_ASSET'
            return out
        if not r.ok:
            out['error'] = f'HTTP {r.status_code}'
            return out

        ctype = out['content_type'].lower()
        if 'application/pdf' in ctype or out['final_url'].lower().split('?', 1)[0].endswith('.pdf'):
            out['parser'] = 'PDF'
            out['text'] = s45v7107_pdf_text(r.content)
            out['raw'] = ''
        else:
            raw = r.text or ''
            out['raw'] = raw[:600000]
            if 'json' in ctype:
                out['parser'] = 'JSON'
                try:
                    out['json'] = r.json()
                    out['text'] = json.dumps(out['json'], ensure_ascii=False, default=str)[:350000]
                except Exception:
                    out['text'] = re.sub(r'\s+', ' ', raw)[:350000]
            elif 'html' in ctype or '<html' in raw[:3000].lower():
                out['parser'] = 'HTML'
                if BeautifulSoup is not None:
                    try:
                        soup = BeautifulSoup(raw, 'html.parser')
                        if soup.title:
                            out['title'] = normalize_text(soup.title.get_text(' ', strip=True))
                    except Exception:
                        pass
                out['text'] = s45v7107_html_text(raw)[:350000]
            else:
                out['parser'] = 'TEXT'
                out['text'] = re.sub(r'\s+', ' ', raw)[:350000]

        out['text_chars'] = len(out['text'] or '')
        out['topic_token_found'] = bool(normalize_text(identity) and normalize_text(identity).lower() in (out['text'] or '').lower())

        if s45v7107_bad_content(out['text'], out['title']):
            out['rejected_reason'] = 'AUTH_OR_ERROR_CONTENT'
            return out

        out['ok'] = bool(out['text'].strip())
        if not out['ok']:
            out['error'] = 'EMPTY_SUBSTANTIVE_CONTENT'
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {str(exc)[:1000]}'

    return out


def s45v7107_url_from_lock_context():
    urls = []

    def add(url):
        url = normalize_text(url)
        if not url or url in urls:
            return
        if not s45v7107_allowed_official_host(url):
            return
        if s45v7107_bad_url(url):
            return
        urls.append(url)

    # Canonical lock URL is always seed #1.
    add(lock.get('official_source_url'))
    add(lock.get('official_source_reference'))

    # Only exact-topic URLs from the lock verification snapshot are admitted.
    snap = as_dict(lock.get('verification_snapshot'))
    for _, value in walk(snap):
        if not isinstance(value, str):
            continue
        value_text = normalize_text(value)
        if normalize_text(identity).lower() not in value_text.lower():
            continue
        for u in re.findall(r'https?://[^\s|<>"\']+', value_text):
            add(u.rstrip('.,;)\'"'))

    return urls


def s45v7107_authoritative_reference_urls():
    """Fresh EC reference documents derived from the locked Horizon topic identity.
    These are authoritative evidence candidates, not historical DB seeds.
    """
    topic = normalize_text(identity).upper()
    urls = []
    # General Annexes are the canonical source for Horizon eligibility/consortium rules.
    if topic.startswith('HORIZON-'):
        urls.append('https://ec.europa.eu/info/funding-tenders/opportunities/docs/2021-2027/horizon/wp-call/2026-2027/wp-15-general-annexes_horizon-2026-2027_en.pdf')
    cluster_files = {
        'CL1': 'wp-4-health_horizon-2026-2027_en.pdf',
        'CL2': 'wp-5-culture-creativity-and-inclusive-society_horizon-2026-2027_en.pdf',
        'CL3': 'wp-6-civil-security-for-society_horizon-2026-2027_en.pdf',
        'CL4': 'wp-7-digital-industry-and-space_horizon-2026-2027_en.pdf',
        'CL5': 'wp-8-climate-energy-and-mobility_horizon-2026-2027_en.pdf',
        'CL6': 'wp-9-food-bioeconomy-natural-resources-agriculture-and-environment_horizon-2026-2027_en.pdf',
    }
    m = re.search(r'HORIZON-(CL[1-6])-', topic)
    if m and m.group(1) in cluster_files:
        urls.insert(0, 'https://ec.europa.eu/info/funding-tenders/opportunities/docs/2021-2027/horizon/wp-call/2026-2027/' + cluster_files[m.group(1)])
    return urls


def s45v7107_topic_section(text):
    """Bound the exact topic section up to the next HORIZON topic heading."""
    body = re.sub(r'\s+', ' ', normalize_text(text))
    topic = normalize_text(identity)
    if not body or not topic:
        return ''
    m = re.search(re.escape(topic), body, flags=re.I)
    if not m:
        return ''
    start = m.start()
    tail = body[m.end():]
    nxt = re.search(r'\bHORIZON-[A-Z0-9-]{8,}\b', tail, flags=re.I)
    end = m.end() + nxt.start() if nxt else min(len(body), start + 45000)
    return body[start:end].strip()[:50000]


def s45v7107_applicability_bridge(cluster_text):
    """Prove that the exact topic delegates eligibility to General Annex B."""
    section = s45v7107_topic_section(cluster_text)
    low = section.lower()
    return bool(section and 'eligibility' in low and 'general annex b' in low)


def s45v7107_negative_trl_evidence(cluster_text):
    """A bounded exact-topic section can prove that no topic-specific TRL condition is stated.
    This does not invent a TRL; it returns an explicit NOT_SPECIFIED result for Stage 46.
    """
    section = s45v7107_topic_section(cluster_text)
    if not section:
        return ''
    low = section.lower()
    trl_markers = ('technology readiness level', 'starting trl', 'target trl', 'reach trl', ' trl ')
    if any(x in low for x in trl_markers):
        return ''
    # Preserve enough of the canonical section for independent Stage 46 validation.
    return ('NO_TOPIC_SPECIFIC_TRL_STATED. Exact-topic section was parsed from the official EC '
            'Cluster work programme and contains no Technology Readiness Level/TRL condition. '
            'Stage 46 must independently re-check the bounded section. SECTION: ' + section[:9000])


def s45v7107_search_urls():
    topic = normalize_text(identity)
    if not topic:
        return []
    q = quote_plus(topic)
    return [
        f'https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text={q}&pageSize=50&pageNumber=1',
        f'https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text=%22{q}%22&pageSize=50&pageNumber=1',
    ]


def s45v7107_exact_topic_in_substantive_source(fetched):
    # Search API is discovery-only, never evidence.
    final_url = normalize_text(fetched.get('final_url'))
    if s45v7107_is_search_api(final_url):
        return False

    corpus = ' '.join([
        final_url,
        normalize_text(fetched.get('title')),
        normalize_text(fetched.get('text'))[:300000],
    ])
    return s45v710_exact_topic_match(corpus, identity)


def s45v7107_requirement_excerpt(text, requirement_key):
    body = re.sub(r'\s+', ' ', normalize_text(text))
    if not body:
        return ''

    key = normalize_text(requirement_key).upper()
    needles = {
        'APPLICANT_ELIGIBILITY': [
            'eligible applicants', 'eligible entities', 'eligible participants',
            'eligibility conditions', 'conditions for participation',
            'legal entities are eligible', 'who can apply', 'applicants must',
            'beneficiaries and affiliated entities',
        ],
        'CONSORTIUM_REQUIREMENTS': [
            'consortium', 'minimum number of', 'independent legal entities',
            'at least three independent legal entities', 'composition of the consortium',
            'consortium composition', 'beneficiaries',
        ],
        'TRL_REQUIREMENTS': [
            'technology readiness level', 'technology readiness levels',
            'starting trl', 'target trl', 'reach trl', 'trl ', '(trl',
        ],
    }.get(key, [])

    low = body.lower()
    best = ''
    best_score = -1
    topic = normalize_text(identity).lower()

    for needle in needles:
        start = 0
        while True:
            idx = low.find(needle.lower(), start)
            if idx < 0:
                break
            a = max(0, idx - 1200)
            b = min(len(body), idx + 3600)
            excerpt = body[a:b].strip()
            score = 1
            if topic and topic in excerpt.lower():
                score += 5
            # Prefer denser passages, but do not infer a rule.
            score += sum(1 for n in needles if n.lower() in excerpt.lower())
            if score > best_score or (score == best_score and len(excerpt) > len(best)):
                best = excerpt
                best_score = score
            start = idx + max(1, len(needle))

    return best[:12000]


def s45v7107_urls_from_exact_search_object(obj):
    topic = normalize_text(identity)
    if not topic:
        return []

    out = []

    def add(url):
        url = normalize_text(url).rstrip('.,;)\'"')
        if not url or url in out:
            return
        if not s45v7107_allowed_official_host(url):
            return
        if s45v7107_bad_url(url):
            return
        if s45v7107_is_search_api(url):
            return
        out.append(url)

    def visit(node):
        if isinstance(node, dict):
            serialized = json.dumps(node, ensure_ascii=False, default=str)
            if s45v710_exact_topic_match(serialized, topic):
                for u in re.findall(r'https?://[^\s|<>"\']+', serialized):
                    add(u)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(obj)
    return out[:40]


def s45v7107_discover_from_search_api(fetched):
    urls = []
    payload = fetched.get('json')
    if isinstance(payload, (dict, list)):
        urls.extend(s45v7107_urls_from_exact_search_object(payload))

    # Always construct the canonical topic page from the already verified topic identity.
    topic = normalize_text(identity)
    if topic:
        canonical = (
            'https://ec.europa.eu/info/funding-tenders/opportunities/portal/'
            'screen/opportunities/topic-details/' + topic
        )
        if canonical not in urls:
            urls.insert(0, canonical)

    return urls[:40]


def s45v7107_discover_links_from_substantive_source(fetched):
    # Very conservative: only document-like official URLs, not arbitrary page assets/navigation.
    raw = normalize_text(fetched.get('raw'))
    if not raw:
        return []

    out = []
    for u in re.findall(r'https?://[^\s<>"\']+', raw):
        u = u.rstrip('.,;)\'"')
        low = u.lower().split('?', 1)[0]
        if not s45v7107_allowed_official_host(u) or s45v7107_bad_url(u):
            continue
        if s45v7107_is_search_api(u):
            continue
        # Follow only document-looking links or exact-topic links.
        document_like = any(low.endswith(ext) for ext in ('.pdf', '.doc', '.docx', '.odt', '.txt'))
        exact_link = normalize_text(identity).lower() in u.lower()
        if (document_like or exact_link) and u not in out:
            out.append(u)
    return out[:30]


def s45v7107_discover_and_verify(task):
    requirement_key = s45v7107_requirement_key(task)
    if not requirement_key:
        return {'status':'WAITING_OFFICIAL','reason':'Requirement family is not handled by v7.10.7.','attempts':[]}

    queue=[]
    for u in s45v7107_url_from_lock_context() + s45v7107_authoritative_reference_urls() + s45v7107_search_urls():
        if u and u not in queue: queue.append(u)
    visited=set(); attempts=[]; max_fetches=24
    cluster_doc=None; annex_doc=None

    while queue and len(visited)<max_fetches:
        url=queue.pop(0)
        if not url or url in visited: continue
        visited.add(url)
        if s45v7107_bad_url(url):
            attempts.append({'requested_url':url,'final_url':url,'exact_topic':False,'explicit_evidence':False,'rejected_reason':'PRE_FETCH_REJECTED_URL'})
            continue
        fetched=s45v7107_fetch(url)
        final_url=normalize_text(fetched.get('final_url'))
        discovery_only=s45v7107_is_search_api(final_url) or s45v7107_is_search_api(url)
        audit={'requested_url':url,'final_url':final_url,'http_status':fetched.get('status'),'content_type':fetched.get('content_type'),
               'bytes_received':fetched.get('bytes_received',0),'parser':fetched.get('parser'),'text_chars':fetched.get('text_chars',0),
               'topic_token_found':fetched.get('topic_token_found',False),
               'official_host':s45v7107_allowed_official_host(final_url),'search_api_discovery_only':discovery_only,
               'auth_or_error_or_asset':bool(s45v7107_bad_url(final_url) or s45v7107_bad_content(fetched.get('text'),fetched.get('title'))),
               'exact_topic':False,'explicit_evidence':False,'rejected_reason':fetched.get('rejected_reason'),'error':fetched.get('error')}
        if not fetched.get('ok'):
            attempts.append(audit); continue
        if discovery_only:
            for d in s45v7107_discover_from_search_api(fetched):
                if d not in visited and d not in queue: queue.append(d)
            attempts.append(audit); continue

        text=normalize_text(fetched.get('text'))
        lowurl=final_url.lower()
        exact=s45v7107_exact_topic_in_substantive_source(fetched)
        audit['exact_topic']=exact
        if exact and ('wp-' in lowurl or 'work-programme' in lowurl or 'horizon-2026-2027' in lowurl):
            cluster_doc=fetched
        if 'general-annexes' in lowurl or 'general_annexes' in lowurl:
            annex_doc=fetched

        # Topic-local positive evidence (especially TRL or topic-specific consortium/eligibility exceptions).
        section=s45v7107_topic_section(text) if exact else ''
        excerpt=s45v7107_requirement_excerpt(section or text, requirement_key)
        audit['explicit_evidence']=bool(excerpt)
        attempts.append(audit)

        if exact and excerpt:
            return {'status':'RESOLVED','requirement_key':requirement_key,'exact_topic_verified':True,
                    'authoritative_source_verified':True,'explicit_evidence_verified':True,'evidence_url':final_url,
                    'evidence_excerpt':excerpt,'evidence_reference':requirement_key,'source_title':fetched.get('title') or 'Official European Commission work programme',
                    'document_type':'OFFICIAL_EC_WORK_PROGRAMME','source_authority':'EUROPEAN_COMMISSION','provenance_chain':[final_url],
                    'reason':'v7.10.7 resolved from the bounded exact-topic section of a freshly fetched official EC work programme.','attempts':attempts}

        for d in s45v7107_discover_links_from_substantive_source(fetched):
            if d not in visited and d not in queue: queue.append(d)

    # Cross-document applicability: exact topic WP -> General Annex B.
    if cluster_doc:
        cluster_text=normalize_text(cluster_doc.get('text'))
        bridge=s45v7107_applicability_bridge(cluster_text)
        if requirement_key=='TRL_REQUIREMENTS':
            neg=s45v7107_negative_trl_evidence(cluster_text)
            if neg:
                u=normalize_text(cluster_doc.get('final_url'))
                return {'status':'RESOLVED','requirement_key':requirement_key,'exact_topic_verified':True,
                        'authoritative_source_verified':True,'explicit_evidence_verified':True,'evidence_url':u,
                        'evidence_excerpt':neg,'evidence_reference':'TRL_NOT_SPECIFIED_IN_EXACT_TOPIC',
                        'source_title':cluster_doc.get('title') or 'Official European Commission work programme',
                        'document_type':'OFFICIAL_EC_WORK_PROGRAMME','source_authority':'EUROPEAN_COMMISSION','provenance_chain':[u],
                        'reason':'v7.10.7 parsed the complete bounded exact-topic section and found no topic-specific TRL condition; no TRL value was inferred. Stage 46 must independently verify this negative finding.','attempts':attempts}
        if bridge and annex_doc and requirement_key in ('APPLICANT_ELIGIBILITY','CONSORTIUM_REQUIREMENTS'):
            annex_excerpt=s45v7107_requirement_excerpt(annex_doc.get('text'), requirement_key)
            if annex_excerpt:
                cu=normalize_text(cluster_doc.get('final_url')); au=normalize_text(annex_doc.get('final_url'))
                topic_section=s45v7107_topic_section(cluster_text)
                bridge_excerpt=s45v7107_requirement_excerpt(topic_section,'APPLICANT_ELIGIBILITY') or topic_section[:5000]
                combined=('APPLICABILITY BRIDGE — exact topic delegates eligibility conditions to General Annex B. '
                          'TOPIC SOURCE: '+bridge_excerpt[:5000]+' GENERAL ANNEX B EVIDENCE: '+annex_excerpt[:7000])
                return {'status':'RESOLVED','requirement_key':requirement_key,'exact_topic_verified':True,
                        'authoritative_source_verified':True,'explicit_evidence_verified':True,'evidence_url':au,
                        'evidence_excerpt':combined[:12000],'evidence_reference':'EXACT_TOPIC_TO_GENERAL_ANNEX_B',
                        'source_title':annex_doc.get('title') or 'Horizon Europe General Annexes',
                        'document_type':'OFFICIAL_EC_GENERAL_ANNEXES','source_authority':'EUROPEAN_COMMISSION',
                        'provenance_chain':[cu,au],
                        'reason':'v7.10.7 proved applicability through the exact-topic work programme delegation to General Annex B, then extracted the requirement from the freshly fetched official General Annexes.','attempts':attempts}

    return {'status':'WAITING_OFFICIAL','requirement_key':requirement_key,'exact_topic_verified':False,
            'authoritative_source_verified':False,'explicit_evidence_verified':False,'evidence_url':None,'evidence_excerpt':None,
            'reason':'Fresh official EC topic/work-programme/general-annex discovery did not produce a provenance-complete requirement proof. Requirement remains WAITING_OFFICIAL.',
            'attempts':attempts}

def s45v7107_save_verified_document(task, result):
    url = normalize_text(result.get('evidence_url'))
    excerpt = normalize_text(result.get('evidence_excerpt'))
    if not url or not excerpt:
        return None

    row = {
        'user_id': user_id,
        'project_id': project_id,
        'opportunity_lock_id': lock_id,
        'execution_run_id': execution_run_id,
        'execution_task_id': task.get('id'),
        'requirement_id': task.get('requirement_id'),
        'opportunity_identity': identity,
        'requirement_key': task.get('requirement_key'),
        'requirement_category': task.get('requirement_category'),
        'requirement_label': task.get('requirement_label'),
        'source_url': url,
        'source_title': result.get('source_title') or 'Official European Commission source',
        'document_type': result.get('document_type') or 'OFFICIAL_EC_DOCUMENT',
        'source_authority': result.get('source_authority') or 'EUROPEAN_COMMISSION',
        'topic_identity': identity,
        'exact_topic_verified': True,
        'applicability_verified': True,
        'applicability_reason': 'v7.10.7 strict canonical exact-topic verification passed.',
        'evidence_found': True,
        'evidence_excerpt': excerpt[:10000],
        'evidence_reference': result.get('evidence_reference') or task.get('requirement_label'),
        'evidence_payload': {
            'stage': 45,
            'version': 'v7.10.7',
            'strict_canonical_resolver': True,
            'history_used_as_seed': False,
            'search_api_is_evidence': False,
            'attempts': result.get('attempts', [])[-30:],
        },
        'provenance_chain': result.get('provenance_chain') or [url],
        'retrieval_status': 'VERIFIED',
        'retrieved_at': now_iso(),
        'updated_at': now_iso(),
    }

    existing = (
        supabase.table('locked_evidence_official_documents')
        .select('id')
        .eq('user_id', user_id)
        .eq('opportunity_lock_id', lock_id)
        .eq('execution_task_id', task.get('id'))
        .eq('source_url', url)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        saved = (
            supabase.table('locked_evidence_official_documents')
            .update(row)
            .eq('id', existing[0]['id'])
            .eq('user_id', user_id)
            .execute()
        ).data or []
        return saved[0] if saved else {**row, 'id': existing[0]['id']}

    saved = supabase.table('locked_evidence_official_documents').insert(row).execute().data or []
    return saved[0] if saved else None


def s45v7107_worker_payload(task, worker_run_id, result, source_row):
    evidence_url = normalize_text(result.get('evidence_url'))
    excerpt = normalize_text(result.get('evidence_excerpt'))[:10000]
    chain = result.get('provenance_chain') or []
    if not isinstance(chain, list):
        chain = [chain] if chain else []
    if evidence_url and evidence_url not in chain:
        chain.append(evidence_url)

    return {
        'user_id': user_id,
        'project_id': project_id,
        'opportunity_lock_id': lock_id,
        'execution_run_id': execution_run_id,
        'worker_run_id': worker_run_id,
        'execution_task_id': task.get('id'),
        'requirement_id': task.get('requirement_id'),
        'opportunity_identity': identity,
        'requirement_key': task.get('requirement_key'),
        'requirement_category': task.get('requirement_category'),
        'requirement_label': task.get('requirement_label'),
        'route_type': task.get('route_type'),
        'destination_module': task.get('destination_module'),
        'worker_action': 'STRICT_CANONICAL_STAGE46_HANDOFF',
        'worker_status': 'RESOLVED',
        'resolved_value': {
            'stage45_candidate': True,
            'requirement': task.get('requirement_label'),
            'strict_canonical_source_verified': True,
        },
        'evidence_source': 'OFFICIAL_DOCUMENTATION',
        'evidence_reference': result.get('evidence_reference') or task.get('requirement_label'),
        'evidence_url': evidence_url,
        'evidence_excerpt': excerpt,
        'confidence': 'High',
        'official_verified': False,
        'reason': result.get('reason'),
        'next_action': 'VALIDATE_IN_STAGE_46',
        'source_title': result.get('source_title') or 'Official European Commission source',
        'document_type': result.get('document_type') or 'OFFICIAL_EC_DOCUMENT',
        'source_authority': result.get('source_authority') or 'EUROPEAN_COMMISSION',
        'topic_identity': identity,
        'provenance_chain': chain,
        'documents_checked': [evidence_url],
        'searches_attempted': s45v7107_search_urls(),
        'transport_attempts': result.get('attempts', [])[-30:],
        # Use the existing DB-allowed resolution_method value.
        'resolution_method': 'OFFICIAL_DOCUMENTATION',
        'retrieved_at': now_iso(),
        'exact_topic_verified': True,
        'authoritative_source_verified': True,
        'explicit_evidence_verified': True,
        'official_document_status': 'EVIDENCE_FOUND',
        'official_document_payload': {
            'stage': 45,
            'version': 'v7.10.7',
            'handoff_target': 'STAGE_46',
            'strict_canonical_resolver': True,
            'history_used_as_seed': False,
            'search_api_is_evidence': False,
            'source_document_id': source_row.get('id') if source_row else None,
            'source_document_status': source_row.get('retrieval_status') if source_row else None,
            'attempts': result.get('attempts', [])[-30:],
        },
        'resolved_at': now_iso(),
        'updated_at': now_iso(),
    }


def s45v7107_persist_worker_item(task, worker_run_id, result, source_row):
    if normalize_text(result.get('status')).upper() != 'RESOLVED':
        return None

    payload = s45v7107_worker_payload(task, worker_run_id, result, source_row)
    existing = (
        supabase.table('locked_evidence_worker_items')
        .select('id')
        .eq('user_id', user_id)
        .eq('worker_run_id', worker_run_id)
        .eq('execution_task_id', task.get('id'))
        .limit(1)
        .execute()
    ).data or []

    if existing:
        saved = (
            supabase.table('locked_evidence_worker_items')
            .update(payload)
            .eq('id', existing[0]['id'])
            .eq('user_id', user_id)
            .execute()
        ).data or []
        return saved[0] if saved else {**payload, 'id': existing[0]['id']}

    saved = supabase.table('locked_evidence_worker_items').insert(payload).execute().data or []
    if not saved:
        raise RuntimeError('v7.10.7 could not persist Stage 46 handoff worker item.')
    return saved[0]


def s45v7107_update_execution_task(task, worker_item):
    payload = as_dict(task.get('completion_payload'))
    payload['stage45_evidence_candidate'] = {
        'stage': 45,
        'version': 'v7.10.7',
        'worker_item_id': worker_item.get('id'),
        'status': 'RESOLVED',
        'evidence_url': worker_item.get('evidence_url'),
        'evidence_reference': worker_item.get('evidence_reference'),
        'evidence_excerpt': worker_item.get('evidence_excerpt'),
        'exact_topic_verified': True,
        'authoritative_source_verified': True,
        'explicit_evidence_verified': True,
        'strict_canonical_source_verified': True,
        'handoff': 'STAGE_46',
        'created_at': now_iso(),
    }

    supabase.table('locked_evidence_execution_tasks').update({
        'task_status': 'WAITING_OFFICIAL',
        'completion_status': None,
        'completion_source': None,
        'completion_reference': None,
        'completed_at': None,
        'completion_payload': payload,
        'updated_at': now_iso(),
    }).eq('id', task.get('id')).eq('user_id', user_id).execute()


st.divider()
st.subheader('Stage 45 v7.10.7 — Official Topic Document Discovery')
st.info(
    'v7.10.7 pornește numai din lock-ul canonic și discovery exact-topic, nu reciclează cele 128 de '
    'documente istorice. Search API este discovery-only; CAS/EU Login/auth/static assets sunt respinse. '
    'Un requirement devine candidat pentru Stage 46 numai dacă aceeași sursă oficială proaspăt citită '
    'conține topicul exact și pasajul explicit.'
)

_v7107_tasks = [
    t for t in current_tasks
    if normalize_text(t.get('route_type')).upper() == 'OFFICIAL_VERIFICATION'
]

_v7107_seed_urls = s45v7107_url_from_lock_context()

q1, q2, q3, q4 = st.columns(4)
q1.metric('OFFICIAL requirements', len(_v7107_tasks))
q2.metric('Canonical lock seeds', len(_v7107_seed_urls))
q3.metric('Historical docs as seeds', '0')
q4.metric('Stage 46 target', 'STRICT')

if _v7107_seed_urls:
    st.caption('Primary canonical seed: ' + _v7107_seed_urls[0])
else:
    st.warning('Nu există canonical seed valid în ACTIVE lock; v7.10.7 va rămâne fail-closed.')

if st.button(
    '🧪 Run Stage 45 v7.10.7 diagnostic fetch',
    type='primary',
    use_container_width=True,
    key='stage45_v7107_run',
):
    _run = (
        supabase.table('locked_evidence_worker_runs')
        .insert({
            'user_id': user_id,
            'project_id': project_id,
            'opportunity_lock_id': lock_id,
            'execution_run_id': execution_run_id,
            'opportunity_identity': identity,
            'total_tasks': len(_v7107_tasks),
            'worker_status': 'RUNNING',
            'deep_resolution_version': 'v7.10.7',
            'diagnostic_status': 'CLEAN',
            'error_count': 0,
            'started_at': now_iso(),
            'summary': {
                'stage': 45,
                'version': 'v7.10.7',
                'purpose': 'DIAGNOSTIC_FETCH_AND_CANONICAL_DOCUMENT_RESOLVER',
                'canonical_seed_urls': _v7107_seed_urls,
                'history_used_as_seed': False,
                'search_api_is_evidence': False,
            },
            'updated_at': now_iso(),
        })
        .execute()
    ).data or []

    if not _run:
        st.error('Nu am putut crea Stage 45 v7.10.7 run.')
    else:
        _run_id = str(_run[0]['id'])
        _resolved = 0
        _waiting = 0
        _failed = 0
        _audit_rows = []
        _bar = st.progress(0)

        for _idx, _task in enumerate(_v7107_tasks, 1):
            try:
                _result = s45v7107_discover_and_verify(_task)
                _attempts = _result.get('attempts', []) or []
                _rejected_auth_assets = sum(
                    1 for a in _attempts
                    if a.get('auth_or_error_or_asset') or a.get('rejected_reason')
                )
                _search_discovery = sum(
                    1 for a in _attempts if a.get('search_api_discovery_only')
                )

                _audit_rows.append({
                    'Requirement': _task.get('requirement_label'),
                    'Status': _result.get('status'),
                    'Evidence URL': _result.get('evidence_url'),
                    'Exact topic': _result.get('exact_topic_verified'),
                    'Authoritative': _result.get('authoritative_source_verified'),
                    'Explicit evidence': _result.get('explicit_evidence_verified'),
                    'Attempts': len(_attempts),
                    'Search discovery only': _search_discovery,
                    'Rejected auth/assets': _rejected_auth_assets,
                    'Reason': _result.get('reason'),
                    'Transport diagnostics': _attempts,
                })

                if normalize_text(_result.get('status')).upper() == 'RESOLVED':
                    _source_row = s45v7107_save_verified_document(_task, _result)
                    _worker_item = s45v7107_persist_worker_item(
                        _task, _run_id, _result, _source_row
                    )
                    s45v7107_update_execution_task(_task, _worker_item)
                    _resolved += 1
                else:
                    _waiting += 1

            except Exception as _exc:
                _failed += 1
                _audit_rows.append({
                    'Requirement': _task.get('requirement_label'),
                    'Status': 'FAILED',
                    'Evidence URL': None,
                    'Exact topic': False,
                    'Authoritative': False,
                    'Explicit evidence': False,
                    'Attempts': 0,
                    'Search discovery only': 0,
                    'Rejected auth/assets': 0,
                    'Reason': f'{type(_exc).__name__}: {str(_exc)[:1000]}',
                })

            _bar.progress(_idx / max(1, len(_v7107_tasks)))

        _final = (
            'FAILED'
            if _failed and _resolved == 0 and _waiting == 0
            else 'PARTIAL_FAILURE'
            if _failed
            else 'COMPLETED'
        )

        supabase.table('locked_evidence_worker_runs').update({
            'resolved_tasks': _resolved,
            'waiting_tasks': _waiting,
            'failed_tasks': _failed,
            'worker_status': _final,
            'diagnostic_status': (
                'FAILED' if _final == 'FAILED'
                else 'PARTIAL_FAILURE' if _final == 'PARTIAL_FAILURE'
                else 'CLEAN'
            ),
            'official_tasks_resolved': _resolved,
            'official_tasks_waiting': _waiting,
            'deep_resolution_version': 'v7.10.7',
            'provenance_summary': {
                'stage': 45,
                'version': 'v7.10.7',
                'strict_canonical_resolver': True,
                'handoff_target': 'STAGE_46',
                'canonical_seed_urls': _v7107_seed_urls,
                'history_used_as_seed': False,
                'search_api_is_evidence': False,
                'resolved': _resolved,
                'waiting': _waiting,
                'failed': _failed,
                'audit': _audit_rows,
            },
            'completed_at': now_iso(),
            'updated_at': now_iso(),
        }).eq('id', _run_id).eq('user_id', user_id).execute()

        st.success(
            f'Stage 45 v7.10.7: {_final} — canonical RESOLVED {_resolved}, '
            f'waiting {_waiting}, failed {_failed}.'
        )
        if _audit_rows:
            st.dataframe(_audit_rows, use_container_width=True, hide_index=True)
        st.rerun()


_v7107_runs = rows(
    'locked_evidence_worker_runs',
    {
        'user_id': user_id,
        'project_id': project_id,
        'opportunity_lock_id': lock_id,
    },
    'created_at',
    100,
)
_v7107_runs = [
    r for r in _v7107_runs
    if normalize_text(r.get('deep_resolution_version')).lower() == 'v7.10.7'
]

if _v7107_runs:
    _latest_v7107 = _v7107_runs[0]
    st.subheader('Latest Stage 45 v7.10.7 Result')

    z1, z2, z3, z4 = st.columns(4)
    z1.metric('Status', _latest_v7107.get('worker_status') or '—')
    z2.metric('Canonical RESOLVED', _latest_v7107.get('official_tasks_resolved') or 0)
    z3.metric('Waiting', _latest_v7107.get('official_tasks_waiting') or 0)
    z4.metric('Failed', _latest_v7107.get('failed_tasks') or 0)

    _summary = _latest_v7107.get('provenance_summary') or {}
    if isinstance(_summary, dict) and _summary.get('audit'):
        with st.expander('v7.10.7 diagnostic summary', expanded=False):
            st.dataframe(_summary.get('audit'), use_container_width=True, hide_index=True)

        with st.expander('v7.10.7 URL-by-URL transport diagnostics', expanded=True):
            _diag_rows = []
            for _req in (_summary.get('audit') or []):
                for _a in (_req.get('Transport diagnostics') or []):
                    _diag_rows.append({
                        'Requirement': _req.get('Requirement'),
                        'Requested URL': _a.get('requested_url'),
                        'Final URL': _a.get('final_url'),
                        'HTTP': _a.get('http_status'),
                        'Content-Type': _a.get('content_type'),
                        'Bytes': _a.get('bytes_received'),
                        'Parser': _a.get('parser'),
                        'Text chars': _a.get('text_chars'),
                        'Topic token': _a.get('topic_token_found'),
                        'Exact topic': _a.get('exact_topic'),
                        'Explicit evidence': _a.get('explicit_evidence'),
                        'Search only': _a.get('search_api_discovery_only'),
                        'Rejected': _a.get('rejected_reason'),
                        'Error': _a.get('error'),
                    })
            if _diag_rows:
                st.dataframe(_diag_rows, use_container_width=True, hide_index=True)

                # Flat export: one URL attempt per CSV row.
                # Avoids nested objects becoming "[object Object]" in exports.
                import csv as _csv
                import io as _io

                _flat_buf = _io.StringIO()
                _flat_fields = [
                    'Requirement', 'Requested URL', 'Final URL', 'HTTP',
                    'Content-Type', 'Bytes', 'Parser', 'Text chars',
                    'Topic token', 'Exact topic', 'Explicit evidence',
                    'Search only', 'Rejected', 'Error'
                ]
                _flat_writer = _csv.DictWriter(
                    _flat_buf,
                    fieldnames=_flat_fields,
                    extrasaction='ignore'
                )
                _flat_writer.writeheader()
                for _flat_row in _diag_rows:
                    _flat_writer.writerow({
                        _k: (
                            '' if _flat_row.get(_k) is None
                            else _flat_row.get(_k)
                        )
                        for _k in _flat_fields
                    })

                st.download_button(
                    '⬇️ Export flat transport diagnostics CSV',
                    data=_flat_buf.getvalue().encode('utf-8-sig'),
                    file_name='stage45_v7_10_7_transport_diagnostics_flat.csv',
                    mime='text/csv',
                    use_container_width=True,
                )

                # Compact failure view: surfaces the decisive transport fields
                # without requiring horizontal scrolling.
                _compact_diag = []
                for _d in _diag_rows:
                    _compact_diag.append({
                        'Requirement': _d.get('Requirement'),
                        'HTTP': _d.get('HTTP'),
                        'Type': _d.get('Content-Type'),
                        'Bytes': _d.get('Bytes'),
                        'Parser': _d.get('Parser'),
                        'Chars': _d.get('Text chars'),
                        'Topic': _d.get('Topic token'),
                        'Exact': _d.get('Exact topic'),
                        'Evidence': _d.get('Explicit evidence'),
                        'Rejected': _d.get('Rejected'),
                        'Error': _d.get('Error'),
                    })
                st.caption('Compact transport diagnosis')
                st.dataframe(_compact_diag, use_container_width=True, hide_index=True)
            else:
                st.info('No URL-level diagnostic rows were persisted for this run.')

    _items = rows(
        'locked_evidence_worker_items',
        {
            'user_id': user_id,
            'project_id': project_id,
            'opportunity_lock_id': lock_id,
            'worker_run_id': str(_latest_v7107.get('id')),
        },
        'created_at',
        100,
    )

    if _items:
        st.subheader('Stage 46 strict canonical handoff rows')
        st.dataframe(
            [
                {
                    'Requirement': i.get('requirement_label'),
                    'Worker status': i.get('worker_status'),
                    'Exact topic': i.get('exact_topic_verified'),
                    'Authoritative': i.get('authoritative_source_verified'),
                    'Explicit evidence': i.get('explicit_evidence_verified'),
                    'Document status': i.get('official_document_status'),
                    'Evidence URL': i.get('evidence_url'),
                    'Excerpt chars': len(normalize_text(i.get('evidence_excerpt'))),
                }
                for i in _items
            ],
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    'v7.10.7 invariant: Search API, CAS/EU Login, auth/error pages, static assets and historical '
    'worker documents are never evidence seeds. Only a freshly fetched substantive official source '
    'that proves both the exact topic and the explicit requirement can be handed to Stage 46.'
)
# =====================================================================
# END STAGE 45 v7.10.7
# =====================================================================
