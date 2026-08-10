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
