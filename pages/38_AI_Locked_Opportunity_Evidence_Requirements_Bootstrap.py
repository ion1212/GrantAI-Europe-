import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Locked Opportunity Evidence & Requirements Bootstrap",
    page_icon="🧩",
    layout="wide",
)

st.title("🧩 Etapa 38 — AI Locked Opportunity Evidence & Requirements Bootstrap")
st.caption(
    "Consumă exclusiv lock-ul ACTIVE din Etapa 37 și pregătește registrul canonic de "
    "cerințe/dovezi. Nu schimbă oportunitatea și nu inventează cerințe lipsă."
)

DESTINATION_MODULE = "AI Evidence Requirement Resolver"


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


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            obj = json.loads(value)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        d = date.fromisoformat(str(value)[:10])
        return d >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def project_label(p: dict) -> str:
    return f"{p.get('name') or 'Project'} — {str(p.get('id') or '')[:8]}"


supabase = get_supabase()
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

# ------------------------------------------------------------------
# HARD GATE: Stage 37 ACTIVE lock
# ------------------------------------------------------------------
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
title = normalize_text(lock.get("official_title") or lock.get("opportunity_title"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

# Matching READY handoff is mandatory.
try:
    handoffs = rows(
        "selected_opportunity_handoffs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "destination_module": DESTINATION_MODULE,
            "handoff_status": "READY",
        },
        "created_at",
        20,
    )
except Exception as exc:
    st.error(f"Nu pot citi handoff-ul Etapei 37: {exc}")
    st.stop()

handoff = handoffs[0] if handoffs else None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Handoff", "READY" if handoff else "MISSING")
c4.metric("Deadline", str(deadline or "—")[:10])

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Official identity:** {official_identity or '—'}")
st.write(f"**Title:** {title or '—'}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")) == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and bool(handoff)
    and future_deadline(deadline)
)

if not hard_gate_ok:
    reasons = []
    if normalize_text(lock.get("lock_status")) != "ACTIVE":
        reasons.append("lock-ul nu este ACTIVE")
    if not workflow_allowed:
        reasons.append("workflow_allowed nu este true")
    if not identity:
        reasons.append("identity lipsește")
    if not handoff:
        reasons.append(f"lipsește handoff READY către {DESTINATION_MODULE}")
    if not future_deadline(deadline):
        reasons.append("deadline-ul oficial lipsește sau nu mai este viitor")
    st.error("Etapa 38 este BLOCKED: " + "; ".join(reasons) + ".")
    st.stop()

st.success("Hard gate Etapa 37: PASS. Etapa 38 poate consuma oportunitatea blocată.")

# ------------------------------------------------------------------
# Existing canonical bootstrap
# ------------------------------------------------------------------
try:
    existing_runs = rows(
        "locked_opportunity_requirement_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        20,
    )
except Exception as exc:
    st.error(
        "Lipsește infrastructura Etapei 38 sau nu poate fi citită. "
        f"Rulează SQL-ul furnizat pentru Etapa 38. Detaliu: {exc}"
    )
    st.stop()

latest_run = existing_runs[0] if existing_runs else None

if latest_run:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Bootstrap", normalize_text(latest_run.get("run_status")) or "—")
    r2.metric("Requirements", latest_run.get("total_requirements") or 0)
    r3.metric("Evidence gaps", latest_run.get("evidence_gaps") or 0)
    r4.metric("Ready", latest_run.get("ready_requirements") or 0)

# ------------------------------------------------------------------
# Conservative bootstrap
# ------------------------------------------------------------------
st.subheader("Requirements bootstrap")
st.info(
    "Etapa 38 salvează numai cerințe susținute de snapshot-ul/handoff-ul existent. "
    "Câmpurile critice fără dovadă devin MISSING_EVIDENCE; nu sunt completate prin presupuneri."
)

verification = as_dict(lock.get("verification_snapshot"))
opportunity = as_dict(lock.get("opportunity_snapshot"))
scoring = as_dict(lock.get("scoring_snapshot"))
handoff_payload = as_dict(handoff.get("payload")) if handoff else {}

# Canonical requirements are intentionally evidence-bound.
candidate_requirements = [
    {
        "key": "official_identity",
        "category": "IDENTITY",
        "label": "Official opportunity identity",
        "value": official_identity,
        "evidence_source": "selected_opportunity_locks.official_identity",
        "critical": True,
    },
    {
        "key": "official_deadline",
        "category": "DEADLINE",
        "label": "Official deadline",
        "value": str(deadline or "")[:10],
        "evidence_source": "selected_opportunity_locks.official_deadline",
        "critical": True,
    },
    {
        "key": "programme",
        "category": "PROGRAMME",
        "label": "Programme",
        "value": normalize_text(lock.get("programme")),
        "evidence_source": "selected_opportunity_locks.programme",
        "critical": True,
    },
    {
        "key": "official_source_url",
        "category": "SOURCE",
        "label": "Official source URL",
        "value": normalize_text(lock.get("official_source_url")),
        "evidence_source": "selected_opportunity_locks.official_source_url",
        "critical": True,
    },
    {
        "key": "applicant_eligibility",
        "category": "ELIGIBILITY",
        "label": "Applicant eligibility",
        "value": verification.get("applicant_eligibility") or verification.get("applicant"),
        "evidence_source": "verification_snapshot",
        "critical": True,
    },
    {
        "key": "consortium_requirements",
        "category": "CONSORTIUM",
        "label": "Consortium requirements",
        "value": verification.get("consortium_requirements") or verification.get("consortium"),
        "evidence_source": "verification_snapshot",
        "critical": True,
    },
    {
        "key": "trl_requirements",
        "category": "TRL",
        "label": "TRL requirements",
        "value": verification.get("trl_requirements") or verification.get("trl"),
        "evidence_source": "verification_snapshot",
        "critical": False,
    },
    {
        "key": "funding_conditions",
        "category": "FUNDING",
        "label": "Funding conditions",
        "value": verification.get("funding_conditions") or verification.get("funding"),
        "evidence_source": "verification_snapshot",
        "critical": True,
    },
    {
        "key": "geographic_eligibility",
        "category": "GEOGRAPHIC",
        "label": "Geographic eligibility",
        "value": verification.get("geographic_eligibility") or verification.get("geographic"),
        "evidence_source": "verification_snapshot",
        "critical": True,
    },
]

preview = []
for req in candidate_requirements:
    value = req["value"]
    has_evidence = value not in (None, "", [], {})
    preview.append({
        "Requirement": req["label"],
        "Category": req["category"],
        "Evidence": "FOUND" if has_evidence else "MISSING",
        "Critical": "YES" if req["critical"] else "NO",
        "Value": str(value)[:180] if has_evidence else "—",
    })

st.dataframe(preview, use_container_width=True, hide_index=True)

confirm = st.checkbox(
    "Confirm că Etapa 38 trebuie să creeze registrul canonic de requirements/evidence gaps pentru acest lock."
)

if st.button(
    "🧩 Bootstrap requirements & evidence",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    try:
        # Idempotency: if a COMPLETED run already exists for this exact lock, reuse it.
        completed = next(
            (
                r for r in existing_runs
                if normalize_text(r.get("run_status")) == "COMPLETED"
            ),
            None,
        )

        if completed:
            run = completed
            run_id = str(run["id"])
        else:
            insert_run = (
                supabase.table("locked_opportunity_requirement_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                    "handoff_id": str(handoff["id"]),
                    "opportunity_identity": identity,
                    "run_status": "RUNNING",
                    "total_requirements": 0,
                    "ready_requirements": 0,
                    "evidence_gaps": 0,
                    "summary": {
                        "stage": 38,
                        "project_name": project_name,
                        "official_identity": official_identity,
                        "deadline": str(deadline or "")[:10],
                    },
                    "updated_at": now_iso(),
                })
                .execute()
            ).data or []
            if not insert_run:
                raise RuntimeError("Nu am putut crea run-ul Etapei 38.")
            run = insert_run[0]
            run_id = str(run["id"])

            ready = 0
            gaps = 0
            for req in candidate_requirements:
                value = req["value"]
                has_evidence = value not in (None, "", [], {})
                status = "EVIDENCE_FOUND" if has_evidence else "MISSING_EVIDENCE"
                ready += 1 if has_evidence else 0
                gaps += 0 if has_evidence else 1

                supabase.table("locked_opportunity_requirements").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_lock_id": lock_id,
                    "requirement_run_id": run_id,
                    "opportunity_identity": identity,
                    "requirement_key": req["key"],
                    "requirement_category": req["category"],
                    "requirement_label": req["label"],
                    "requirement_value": (
                        value if isinstance(value, (dict, list))
                        else {"value": value} if has_evidence else {}
                    ),
                    "requirement_status": status,
                    "is_critical": bool(req["critical"]),
                    "evidence_source": req["evidence_source"],
                    "evidence_reference": normalize_text(lock.get("official_source_reference")),
                    "evidence_url": normalize_text(lock.get("official_source_url")),
                    "metadata": {
                        "stage": 38,
                        "lock_id": lock_id,
                        "handoff_id": str(handoff["id"]),
                        "scoring_snapshot_present": bool(scoring),
                        "opportunity_snapshot_present": bool(opportunity),
                        "handoff_payload_present": bool(handoff_payload),
                    },
                    "updated_at": now_iso(),
                }).execute()

            final_status = "COMPLETED"
            supabase.table("locked_opportunity_requirement_runs").update({
                "run_status": final_status,
                "total_requirements": len(candidate_requirements),
                "ready_requirements": ready,
                "evidence_gaps": gaps,
                "summary": {
                    "stage": 38,
                    "project_name": project_name,
                    "opportunity_identity": identity,
                    "official_identity": official_identity,
                    "deadline": str(deadline or "")[:10],
                    "ready_requirements": ready,
                    "evidence_gaps": gaps,
                    "next_action": (
                        "RESOLVE_EVIDENCE_GAPS" if gaps else "REQUIREMENTS_READY"
                    ),
                },
                "updated_at": now_iso(),
            }).eq("id", run_id).eq("user_id", user_id).execute()

            # Consume only this module's READY handoff.
            supabase.table("selected_opportunity_handoffs").update({
                "handoff_status": "CONSUMED",
                "updated_at": now_iso(),
            }).eq("id", handoff["id"]).eq("user_id", user_id).execute()

        st.success(
            "Etapa 38 a creat registrul canonic de requirements/evidence. "
            "Cerințele fără dovadă au rămas explicit MISSING_EVIDENCE."
        )
        st.rerun()

    except Exception as exc:
        st.error(f"Etapa 38 nu a putut finaliza bootstrap-ul: {exc}")

# ------------------------------------------------------------------
# Latest results
# ------------------------------------------------------------------
try:
    latest_runs = rows(
        "locked_opportunity_requirement_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        20,
    )
except Exception:
    latest_runs = []

if latest_runs:
    latest = latest_runs[0]
    st.subheader("Latest Stage 38 Result")
    a, b, c, d = st.columns(4)
    a.metric("Status", normalize_text(latest.get("run_status")) or "—")
    b.metric("Total", latest.get("total_requirements") or 0)
    c.metric("Evidence found", latest.get("ready_requirements") or 0)
    d.metric("Evidence gaps", latest.get("evidence_gaps") or 0)

    try:
        reqs = rows(
            "locked_opportunity_requirements",
            {
                "user_id": user_id,
                "requirement_run_id": str(latest["id"]),
            },
            "created_at",
            200,
        )
    except Exception:
        reqs = []

    if reqs:
        st.dataframe(
            [
                {
                    "Requirement": r.get("requirement_label"),
                    "Category": r.get("requirement_category"),
                    "Status": r.get("requirement_status"),
                    "Critical": r.get("is_critical"),
                    "Evidence source": r.get("evidence_source"),
                }
                for r in reqs
            ],
            use_container_width=True,
            hide_index=True,
        )

    gaps = int(latest.get("evidence_gaps") or 0)
    if gaps:
        st.warning(
            f"Etapa 38 este COMPLETED cu {gaps} evidence gap(s). "
            "Următorul modul trebuie să rezolve aceste lipsuri din surse oficiale/documente, "
            "nu prin presupuneri."
        )
    else:
        st.success("Toate cerințele bootstrap au dovadă disponibilă. Requirements READY.")

st.caption(
    "Invariantă Etapa 38: aceeași opportunity_lock_id trebuie păstrată în toate modulele următoare. "
    "Niciun modul downstream nu are voie să schimbe opportunity_identity."
)
