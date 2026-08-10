import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="Locked Opportunity Evidence Gap Resolver",
    page_icon="🧠",
    layout="wide",
)

st.title("🧠 Etapa 39 — AI Locked Opportunity Evidence Gap Resolver")
st.caption(
    "Rezolvă numai gap-urile MISSING_EVIDENCE create în Etapa 38 pentru același lock ACTIVE. "
    "Nu schimbă opportunity_identity și nu inventează eligibilitate, TRL, funding, consorțiu sau reguli oficiale."
)

NEXT_MODULE = "AI Evidence Resolution Validation"


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


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


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
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, value
            yield from walk(value, child_path)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            child_path = f"{path}[{i}]"
            yield child_path, value
            yield from walk(value, child_path)


def compact_value(value: Any, limit: int = 800) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = normalize_text(value)
    return text[:limit]


def collect_candidate_evidence(requirement: dict, sources: dict) -> list[dict]:
    """
    Conservative keyword-based retrieval from already-stored snapshots.
    This does NOT create evidence; it only surfaces existing values for AI review.
    """
    key = normalize_text(requirement.get("requirement_key")).lower()
    category = normalize_text(requirement.get("requirement_category")).lower()
    label = normalize_text(requirement.get("requirement_label")).lower()

    aliases = {
        "eligibility": ["eligib", "applicant", "participant", "entity", "beneficiary"],
        "consortium": ["consortium", "partner", "minimum partners", "participants"],
        "trl": ["trl", "technology readiness", "readiness level"],
        "funding": ["funding", "budget", "grant", "cofund", "co-financing", "financing", "funding rate"],
        "geographic": ["country", "countries", "region", "geographic", "eligible countries", "location"],
    }

    tokens = set()
    for piece in (key, category, label):
        for token in piece.replace("_", " ").replace("-", " ").split():
            if len(token) >= 4:
                tokens.add(token)

    for alias_key, vals in aliases.items():
        if alias_key in category or alias_key in key or alias_key in label:
            tokens.update(vals)

    matches = []
    for source_name, source_obj in sources.items():
        for path, value in walk(source_obj):
            haystack = f"{path} {compact_value(value, 1200)}".lower()
            if any(token.lower() in haystack for token in tokens):
                if value not in (None, "", [], {}):
                    matches.append({
                        "source": source_name,
                        "path": path,
                        "value": compact_value(value),
                    })
            if len(matches) >= 20:
                break
        if len(matches) >= 20:
            break

    return matches[:20]


# ---------------------------------------------------------------------
# AI resolver
# ---------------------------------------------------------------------
SYSTEM = """You are a strict evidence-gap resolver for an EU grant workflow.

You receive ONE missing requirement from Stage 38 and a small set of candidate evidence
retrieved only from already-stored project/opportunity/verification snapshots.

STRICT RULES:
- Never invent facts, call rules, eligibility, TRL, funding, consortium composition,
  geographic eligibility, thresholds, or official requirements.
- A candidate snippet is evidence only if it explicitly answers the missing requirement.
- If stored evidence is insufficient, do not infer the missing fact.
- OFFICIAL_SOURCE means an official call/source still needs to be checked.
- USER_EVIDENCE means the applicant must provide a factual/project-specific value or document.
- AI_DRAFTABLE means wording can be drafted from facts already present, without inventing new facts.
- DATABASE_EVIDENCE is allowed only when the supplied stored snapshots explicitly contain the answer.
- NOT_APPLICABLE requires explicit evidence that the requirement does not apply.
- BLOCKED requires explicit evidence of a critical incompatibility.
- For call-level eligibility/consortium/funding/geographic/TRL requirements, prefer
  NEEDS_OFFICIAL_VERIFICATION when the official rule is not present in supplied evidence.
- Return JSON only.

Schema:
{
  "resolution_route": "DATABASE_EVIDENCE|OFFICIAL_SOURCE|USER_EVIDENCE|AI_DRAFTABLE|NOT_APPLICABLE|BLOCKED|UNCLASSIFIED",
  "resolution_status": "RESOLVED|PARTIAL|NEEDS_OFFICIAL_VERIFICATION|NEEDS_USER_EVIDENCE|NOT_APPLICABLE|BLOCKED|UNRESOLVED",
  "resolved_value": {},
  "evidence_source": "",
  "evidence_reference": "",
  "evidence_url": "",
  "evidence_excerpt": "",
  "confidence": "Low|Medium|High",
  "requires_user_confirmation": false,
  "official_verification_required": false,
  "resolution_reason": "",
  "next_action": ""
}
"""


def resolve_gap(requirement: dict, sources: dict, candidate_evidence: list[dict]) -> dict:
    client = get_openai()

    payload = {
        "current_date": datetime.now(timezone.utc).date().isoformat(),
        "requirement": {
            "id": requirement.get("id"),
            "key": requirement.get("requirement_key"),
            "category": requirement.get("requirement_category"),
            "label": requirement.get("requirement_label"),
            "is_critical": requirement.get("is_critical"),
            "original_status": requirement.get("requirement_status"),
            "original_value": requirement.get("requirement_value"),
            "evidence_source": requirement.get("evidence_source"),
            "evidence_reference": requirement.get("evidence_reference"),
            "evidence_url": requirement.get("evidence_url"),
        },
        "candidate_evidence": candidate_evidence,
        "source_availability": {
            key: bool(value) for key, value in sources.items()
        },
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

    allowed_routes = {
        "DATABASE_EVIDENCE",
        "OFFICIAL_SOURCE",
        "USER_EVIDENCE",
        "AI_DRAFTABLE",
        "NOT_APPLICABLE",
        "BLOCKED",
        "UNCLASSIFIED",
    }
    allowed_statuses = {
        "RESOLVED",
        "PARTIAL",
        "NEEDS_OFFICIAL_VERIFICATION",
        "NEEDS_USER_EVIDENCE",
        "NOT_APPLICABLE",
        "BLOCKED",
        "UNRESOLVED",
    }

    route = normalize_text(result.get("resolution_route")).upper()
    status = normalize_text(result.get("resolution_status")).upper()

    if route not in allowed_routes:
        route = "UNCLASSIFIED"
    if status not in allowed_statuses:
        status = "UNRESOLVED"

    # Hard safety normalization.
    if route == "DATABASE_EVIDENCE" and not candidate_evidence:
        route = "UNCLASSIFIED"
        status = "UNRESOLVED"

    if route == "OFFICIAL_SOURCE":
        status = "NEEDS_OFFICIAL_VERIFICATION"
        result["official_verification_required"] = True

    if route == "USER_EVIDENCE":
        status = "NEEDS_USER_EVIDENCE"
        result["requires_user_confirmation"] = True

    if route == "BLOCKED":
        status = "BLOCKED"

    if route == "NOT_APPLICABLE":
        status = "NOT_APPLICABLE"

    confidence = normalize_text(result.get("confidence")).title()
    if confidence not in {"Low", "Medium", "High"}:
        confidence = "Low"

    resolved_value = result.get("resolved_value")
    if not isinstance(resolved_value, dict):
        resolved_value = {}

    # Do not allow RESOLVED with an empty value unless NOT_APPLICABLE.
    if status == "RESOLVED" and not resolved_value:
        status = "PARTIAL" if candidate_evidence else "UNRESOLVED"

    return {
        "resolution_route": route,
        "resolution_status": status,
        "resolved_value": resolved_value,
        "evidence_source": normalize_text(result.get("evidence_source")),
        "evidence_reference": normalize_text(result.get("evidence_reference")),
        "evidence_url": normalize_text(result.get("evidence_url")),
        "evidence_excerpt": normalize_text(result.get("evidence_excerpt"))[:4000],
        "confidence": confidence,
        "requires_user_confirmation": bool(result.get("requires_user_confirmation")),
        "official_verification_required": bool(result.get("official_verification_required")),
        "resolution_reason": normalize_text(result.get("resolution_reason"))[:4000],
        "next_action": normalize_text(result.get("next_action"))[:4000],
        "ai_result": result,
    }


# ---------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------
try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase nu este configurat corect: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Nu am putut identifica utilizatorul autentificat.")
    st.stop()

try:
    projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
except Exception as exc:
    st.error(f"Nu pot citi projects: {exc}")
    st.stop()

if not projects:
    st.warning("Nu există proiecte disponibile.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project_label = st.selectbox("Project", list(project_map.keys()))
project = project_map[selected_project_label]
project_id = str(project["id"])
project_name = normalize_text(project.get("name"))
project_data = as_dict(project.get("data"))

# ---------------------------------------------------------------------
# Hard gate: ACTIVE lock + completed Stage 38 run
# ---------------------------------------------------------------------
try:
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
except Exception as exc:
    st.error(f"Nu pot citi lock-ul Etapei 37: {exc}")
    st.stop()

if not locks:
    st.warning("Nu există un lock ACTIVE din Etapa 37 pentru acest proiect.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
official_identity = normalize_text(lock.get("official_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

try:
    stage38_runs = rows(
        "locked_opportunity_requirement_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        50,
    )
except Exception as exc:
    st.error(f"Nu pot citi Etapa 38: {exc}")
    st.stop()

stage38_run = next(
    (r for r in stage38_runs if normalize_text(r.get("run_status")).upper() == "COMPLETED"),
    None,
)

if not stage38_run:
    st.warning("Nu există un run COMPLETED al Etapei 38 pentru lock-ul activ.")
    st.stop()

requirement_run_id = str(stage38_run["id"])

try:
    requirements = rows(
        "locked_opportunity_requirements",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "requirement_run_id": requirement_run_id,
        },
        "created_at",
        200,
    )
except Exception as exc:
    st.error(f"Nu pot citi requirements Etapa 38: {exc}")
    st.stop()

gaps = [
    r for r in requirements
    if normalize_text(r.get("requirement_status")).upper() == "MISSING_EVIDENCE"
]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 38", normalize_text(stage38_run.get("run_status")) or "—")
c4.metric("Evidence gaps", len(gaps))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Official identity:** {official_identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(stage38_run)
)

if not hard_gate_ok:
    st.error(
        "Etapa 39 este BLOCKED: se cere lock ACTIVE, workflow_allowed=true, "
        "deadline viitor și Etapa 38 COMPLETED pentru același lock."
    )
    st.stop()

st.success("Hard gate Etapa 39: PASS. Resolverul poate procesa numai gap-urile Etapei 38.")

if not gaps:
    st.success("Etapa 38 nu mai are MISSING_EVIDENCE. Nu există gap-uri de rezolvat.")
    st.stop()

st.subheader("Gap-uri de rezolvat")
st.dataframe(
    [
        {
            "Requirement": g.get("requirement_label"),
            "Category": g.get("requirement_category"),
            "Critical": g.get("is_critical"),
            "Status": g.get("requirement_status"),
        }
        for g in gaps
    ],
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------------
# Stored evidence sources
# ---------------------------------------------------------------------
sources = {
    "project.data": project_data,
    "verification_snapshot": as_dict(lock.get("verification_snapshot")),
    "opportunity_snapshot": as_dict(lock.get("opportunity_snapshot")),
    "scoring_snapshot": as_dict(lock.get("scoring_snapshot")),
}

# ---------------------------------------------------------------------
# Existing Stage 39 run
# ---------------------------------------------------------------------
try:
    prior_runs = rows(
        "locked_evidence_resolution_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "requirement_run_id": requirement_run_id,
        },
        "created_at",
        50,
    )
except Exception as exc:
    st.error(f"Nu pot citi infrastructura Etapei 39: {exc}")
    st.stop()

latest_run = prior_runs[0] if prior_runs else None

if latest_run:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Latest run", normalize_text(latest_run.get("run_status")) or "—")
    m2.metric("Resolved", latest_run.get("resolved_gaps") or 0)
    m3.metric("Official verification", latest_run.get("official_verification_needed") or 0)
    m4.metric("User evidence", latest_run.get("user_evidence_needed") or 0)

confirm = st.checkbox(
    "Confirm că Etapa 39 trebuie să proceseze numai gap-urile MISSING_EVIDENCE pentru acest lock."
)

if st.button(
    "🧠 Resolve evidence gaps",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    resolution_run_id = None

    with st.spinner(f"Procesez {len(gaps)} evidence gap(s)..."):
        try:
            # Always create a fresh audit run; results remain historically traceable.
            run_insert = (
                supabase.table("locked_evidence_resolution_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                    "requirement_run_id": requirement_run_id,
                    "opportunity_identity": identity,
                    "total_gaps": len(gaps),
                    "run_status": "RUNNING",
                    "started_at": now_iso(),
                    "summary": {
                        "stage": 39,
                        "project_name": project_name,
                        "official_identity": official_identity,
                        "stage38_run_id": requirement_run_id,
                    },
                    "updated_at": now_iso(),
                })
                .execute()
            ).data or []

            if not run_insert:
                raise RuntimeError("Nu am putut crea resolution run Etapa 39.")

            resolution_run_id = str(run_insert[0]["id"])

            counters = {
                "resolved_gaps": 0,
                "partial_gaps": 0,
                "unresolved_gaps": 0,
                "official_verification_needed": 0,
                "user_evidence_needed": 0,
                "blocked_gaps": 0,
            }

            progress = st.progress(0)
            status_box = st.empty()

            for idx, requirement in enumerate(gaps, start=1):
                status_box.write(
                    f"{idx}/{len(gaps)} — {requirement.get('requirement_label')}"
                )

                candidates = collect_candidate_evidence(requirement, sources)
                result = resolve_gap(requirement, sources, candidates)

                status = result["resolution_status"]

                if status == "RESOLVED":
                    counters["resolved_gaps"] += 1
                elif status == "PARTIAL":
                    counters["partial_gaps"] += 1
                elif status == "NEEDS_OFFICIAL_VERIFICATION":
                    counters["official_verification_needed"] += 1
                elif status == "NEEDS_USER_EVIDENCE":
                    counters["user_evidence_needed"] += 1
                elif status == "BLOCKED":
                    counters["blocked_gaps"] += 1
                elif status in ("UNRESOLVED", "NOT_APPLICABLE"):
                    if status == "UNRESOLVED":
                        counters["unresolved_gaps"] += 1

                supabase.table("locked_evidence_resolution_items").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                    "requirement_run_id": requirement_run_id,
                    "resolution_run_id": resolution_run_id,
                    "requirement_id": requirement["id"],
                    "opportunity_identity": identity,
                    "requirement_key": requirement.get("requirement_key"),
                    "requirement_category": requirement.get("requirement_category"),
                    "requirement_label": requirement.get("requirement_label"),
                    "original_status": requirement.get("requirement_status"),
                    "original_value": requirement.get("requirement_value") or {},
                    "resolution_route": result["resolution_route"],
                    "resolution_status": result["resolution_status"],
                    "resolved_value": result["resolved_value"],
                    "evidence_source": result["evidence_source"],
                    "evidence_reference": result["evidence_reference"],
                    "evidence_url": result["evidence_url"],
                    "evidence_excerpt": result["evidence_excerpt"],
                    "confidence": result["confidence"],
                    "is_critical": bool(requirement.get("is_critical")),
                    "requires_user_confirmation": result["requires_user_confirmation"],
                    "user_confirmed": False,
                    "official_verification_required": result["official_verification_required"],
                    "resolution_reason": result["resolution_reason"],
                    "next_action": result["next_action"],
                    "ai_result": result["ai_result"],
                    "metadata": {
                        "stage": 39,
                        "candidate_evidence": candidates,
                        "source_names": list(sources.keys()),
                    },
                    "resolved_at": now_iso() if status in ("RESOLVED", "NOT_APPLICABLE") else None,
                    "updated_at": now_iso(),
                }).execute()

                progress.progress(idx / len(gaps))

            remaining = (
                counters["partial_gaps"]
                + counters["unresolved_gaps"]
                + counters["official_verification_needed"]
                + counters["user_evidence_needed"]
            )

            if counters["blocked_gaps"] > 0:
                run_status = "BLOCKED"
            elif remaining > 0:
                run_status = "NEEDS_ATTENTION"
            else:
                run_status = "COMPLETED"

            summary = {
                "stage": 39,
                "project_name": project_name,
                "opportunity_identity": identity,
                "stage38_run_id": requirement_run_id,
                "next_action": (
                    "BLOCKED"
                    if counters["blocked_gaps"] > 0
                    else "VALIDATE_RESOLUTIONS"
                    if remaining == 0
                    else "OFFICIAL_OR_USER_EVIDENCE_REQUIRED"
                ),
            }

            supabase.table("locked_evidence_resolution_runs").update({
                **counters,
                "run_status": run_status,
                "summary": summary,
                "completed_at": now_iso(),
                "updated_at": now_iso(),
            }).eq("id", resolution_run_id).eq("user_id", user_id).execute()

            # Create/re-arm downstream handoff for Stage 40.
            existing_handoffs = rows(
                "selected_opportunity_handoffs",
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                },
                "created_at",
                500,
            )

            existing_next = next(
                (
                    h for h in existing_handoffs
                    if normalize_text(h.get("destination_module")) == NEXT_MODULE
                ),
                None,
            )

            handoff_payload = {
                "stage": 39,
                "resolution_run_id": resolution_run_id,
                "requirement_run_id": requirement_run_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "run_status": run_status,
                "summary": summary,
                "created_at": now_iso(),
            }

            if existing_next:
                supabase.table("selected_opportunity_handoffs").update({
                    "handoff_status": "READY",
                    "payload": handoff_payload,
                    "consumed_at": None,
                    "updated_at": now_iso(),
                }).eq("id", existing_next["id"]).eq("user_id", user_id).execute()
            else:
                supabase.table("selected_opportunity_handoffs").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                    "opportunity_identity": identity,
                    "destination_module": NEXT_MODULE,
                    "handoff_status": "READY",
                    "payload": handoff_payload,
                    "updated_at": now_iso(),
                }).execute()

            st.success(
                f"Etapa 39 finalizată: {counters['resolved_gaps']} resolved, "
                f"{counters['official_verification_needed']} official verification, "
                f"{counters['user_evidence_needed']} user evidence."
            )
            st.rerun()

        except Exception as exc:
            if resolution_run_id:
                try:
                    supabase.table("locked_evidence_resolution_runs").update({
                        "run_status": "FAILED",
                        "summary": {
                            "stage": 39,
                            "error": str(exc)[:4000],
                        },
                        "completed_at": now_iso(),
                        "updated_at": now_iso(),
                    }).eq("id", resolution_run_id).eq("user_id", user_id).execute()
                except Exception:
                    pass
            st.error(f"Etapa 39 nu a putut finaliza resolverul: {exc}")

# ---------------------------------------------------------------------
# Latest Stage 39 results
# ---------------------------------------------------------------------
try:
    all_runs = rows(
        "locked_evidence_resolution_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "requirement_run_id": requirement_run_id,
        },
        "created_at",
        50,
    )
except Exception:
    all_runs = []

if all_runs:
    latest = all_runs[0]
    st.divider()
    st.subheader("Latest Stage 39 Result")

    a, b, c, d, e = st.columns(5)
    a.metric("Status", normalize_text(latest.get("run_status")) or "—")
    b.metric("Resolved", latest.get("resolved_gaps") or 0)
    c.metric("Partial", latest.get("partial_gaps") or 0)
    d.metric("Official", latest.get("official_verification_needed") or 0)
    e.metric("User evidence", latest.get("user_evidence_needed") or 0)

    items = rows(
        "locked_evidence_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "resolution_run_id": str(latest["id"]),
        },
        "created_at",
        200,
    )

    if items:
        st.dataframe(
            [
                {
                    "Requirement": r.get("requirement_label"),
                    "Category": r.get("requirement_category"),
                    "Route": r.get("resolution_route"),
                    "Status": r.get("resolution_status"),
                    "Confidence": r.get("confidence"),
                    "Critical": r.get("is_critical"),
                    "Next action": r.get("next_action"),
                }
                for r in items
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Resolution details")
        for item in items:
            with st.expander(
                f"{item.get('requirement_label')} — {item.get('resolution_status')}"
            ):
                st.write(f"**Route:** {item.get('resolution_route')}")
                st.write(f"**Reason:** {item.get('resolution_reason') or '—'}")
                st.write(f"**Evidence source:** {item.get('evidence_source') or '—'}")
                st.write(f"**Evidence reference:** {item.get('evidence_reference') or '—'}")
                st.write(f"**Evidence URL:** {item.get('evidence_url') or '—'}")
                st.write(f"**Evidence excerpt:** {item.get('evidence_excerpt') or '—'}")
                st.write(f"**Next action:** {item.get('next_action') or '—'}")

    if normalize_text(latest.get("run_status")).upper() == "BLOCKED":
        st.error("Etapa 39 a identificat cel puțin un BLOCKED gap.")
    elif normalize_text(latest.get("run_status")).upper() == "NEEDS_ATTENTION":
        st.warning(
            "Etapa 39 este NEEDS_ATTENTION. Gap-urile nerezolvate trebuie tratate prin "
            "official verification sau user evidence înainte de validarea finală."
        )
    else:
        st.success("Gap-urile Etapei 39 sunt gata pentru validarea Etapei 40.")

with st.expander("Istoric Etapa 39"):
    if all_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "status": r.get("run_status"),
                    "total": r.get("total_gaps"),
                    "resolved": r.get("resolved_gaps"),
                    "partial": r.get("partial_gaps"),
                    "official": r.get("official_verification_needed"),
                    "user_evidence": r.get("user_evidence_needed"),
                    "blocked": r.get("blocked_gaps"),
                }
                for r in all_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 39.")

st.caption(
    "Invariantă Etapa 39: opportunity_lock_id și opportunity_identity sunt moștenite din Etapa 37/38 "
    "și nu pot fi schimbate de resolver."
)
