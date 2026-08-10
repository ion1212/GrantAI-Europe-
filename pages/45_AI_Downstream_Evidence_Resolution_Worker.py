import requests
import os
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client


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
