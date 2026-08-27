import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 48 v1.0 — STAGE 47 PERSISTED HANDOFF INTEGRITY GATE
#
# Purpose:
#   Stage 48 does NOT recalculate eligibility from scratch.
#   It verifies that the persisted Stage 47 ELIGIBLE decision is authentic,
#   stable, bound to the same ACTIVE opportunity lock and the same Stage 46
#   PASS run, and that all four Stage 47 requirement items remain accepted.
#
# Final statuses:
#   PASS    -> Stage 47 persisted ELIGIBLE handoff is internally consistent.
#   BLOCKED -> anything missing, stale, mismatched or unverifiable.
#
# Persistence:
#   stage48_handoff_validation_runs
#
# Future Stage 49 must bind to:
#   stage48_run_id + stage47_run_id + stage46_run_id +
#   opportunity_lock_id + stage47_decision_fingerprint +
#   stage48_handoff_fingerprint
# =====================================================================

st.set_page_config(
    page_title="Stage 48 v1.0 — Handoff Integrity Gate",
    page_icon="🔐",
    layout="wide",
)

st.title("🔐 Etapa 48 v1.0 — AI Stage 47 Handoff Integrity Gate")
st.caption(
    "Etapa 48 validează numai o decizie Stage 47 persistată în Supabase. "
    "Nu acceptă un rezultat din memorie/session_state și nu reconstruiește artificial ELIGIBLE."
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


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def bool_true(value: Any) -> bool:
    return value is True or normalize_text(value).lower() in {"true", "1", "yes"}


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------
# Auth / project / active lock
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Stage 48 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage48_v1_project",
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
    st.error("Stage 48 BLOCKED: nu există opportunity lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")


# ---------------------------------------------------------------------
# Load latest persisted Stage 47 decision
# ---------------------------------------------------------------------

stage47_runs = rows(
    "final_opportunity_eligibility_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage47 = stage47_runs[0] if stage47_runs else None

if stage47:
    stage47_run_id = str(stage47.get("id") or "")
    stage46_run_id = str(stage47.get("stage46_run_id") or "")
    stage47_decision = normalize_text(stage47.get("decision")).upper()
    stage47_fingerprint = normalize_text(stage47.get("decision_fingerprint"))
else:
    stage47_run_id = ""
    stage46_run_id = ""
    stage47_decision = "MISSING"
    stage47_fingerprint = ""

stage47_items = []
if stage47_run_id:
    stage47_items = rows(
        "final_opportunity_eligibility_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage47_run_id": stage47_run_id,
        },
        "created_at",
        100,
    )

stage46_runs = []
if stage46_run_id:
    stage46_runs = rows(
        "locked_evidence_provenance_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
        },
        "created_at",
        100,
    )

stage46 = next(
    (r for r in stage46_runs if str(r.get("id") or "") == stage46_run_id),
    None,
)


# ---------------------------------------------------------------------
# Recompute Stage 47 v1.2 stable fingerprint
# ---------------------------------------------------------------------

stored_mandatory = as_dict(stage47.get("mandatory_requirements")) if stage47 else {}
stored_checks = stage47.get("checks") if stage47 else []
if not isinstance(stored_checks, list):
    stored_checks = []

stage47_fingerprint_basis = {
    "stage": 47,
    "fingerprint_contract": "stage47-v1.2-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": normalize_text(stage47.get("opportunity_identity")) if stage47 else "",
    "official_deadline": str(stage47.get("official_deadline") or "")[:10] if stage47 else "",
    "stage46_run_id": stage46_run_id,
    "stage46_status": normalize_text(stage47.get("stage46_status")).upper() if stage47 else "",
    "decision": stage47_decision,
    "mandatory_requirements": stored_mandatory,
    "checks": stored_checks,
}

recomputed_stage47_fingerprint = stable_sha256(stage47_fingerprint_basis)


# ---------------------------------------------------------------------
# Canonical Stage 47 item integrity
# ---------------------------------------------------------------------

MANDATORY_REQUIREMENTS = {
    "applicant eligibility",
    "consortium requirements",
    "trl requirements",
    "geographic eligibility",
}

item_by_requirement = {}
for item in stage47_items:
    req = normalize_text(item.get("requirement_key")).lower()
    if req and req not in item_by_requirement:
        item_by_requirement[req] = item


# ---------------------------------------------------------------------
# Stage 48 independent binding checks
# ---------------------------------------------------------------------

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({
        "Check": name,
        "PASS": bool(passed),
        "Detail": detail,
    })


add_check(
    "ACTIVE lock",
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
    f"lock_status={normalize_text(lock.get('lock_status')).upper() or '—'}",
)

add_check(
    "Workflow allowed",
    workflow_allowed,
    f"workflow_allowed={workflow_allowed}",
)

add_check(
    "Opportunity identity present",
    bool(identity),
    identity or "Missing identity",
)

add_check(
    "Deadline still valid",
    future_deadline(deadline),
    str(deadline or "Missing deadline")[:10],
)

add_check(
    "Persisted Stage 47 run exists",
    bool(stage47),
    stage47_run_id or "No persisted Stage 47 run",
)

add_check(
    "Stage 47 decision is ELIGIBLE",
    stage47_decision == "ELIGIBLE",
    f"decision={stage47_decision}",
)

add_check(
    "Stage 47 lock binding",
    bool(stage47) and str(stage47.get("opportunity_lock_id") or "") == lock_id,
    f"stage47.lock={str(stage47.get('opportunity_lock_id') or '')}",
)

add_check(
    "Stage 47 project binding",
    bool(stage47) and str(stage47.get("project_id") or "") == project_id,
    f"stage47.project={str(stage47.get('project_id') or '')}",
)

add_check(
    "Stage 47 opportunity identity binding",
    bool(stage47)
    and normalize_text(stage47.get("opportunity_identity")).lower() == identity.lower(),
    normalize_text(stage47.get("opportunity_identity")) if stage47 else "Missing",
)

add_check(
    "Stage 47 deadline binding",
    bool(stage47)
    and str(stage47.get("official_deadline") or "")[:10] == str(deadline or "")[:10],
    f"stage47={str(stage47.get('official_deadline') or '')[:10]}, lock={str(deadline or '')[:10]}",
)

add_check(
    "Stage 47 fingerprint stable",
    bool(stage47_fingerprint)
    and stage47_fingerprint == recomputed_stage47_fingerprint,
    (
        f"stored={stage47_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage47_fingerprint[:16]}..."
        if stage47_fingerprint
        else "Missing Stage 47 fingerprint"
    ),
)

add_check(
    "Stage 46 run binding exists",
    bool(stage46),
    stage46_run_id or "Missing Stage 46 run id",
)

add_check(
    "Stage 46 run remains PASS",
    bool(stage46)
    and normalize_text(stage46.get("run_status")).upper() == "PASS",
    f"status={normalize_text(stage46.get('run_status')).upper() if stage46 else 'MISSING'}",
)

add_check(
    "Canonical Stage 47 item set = 4",
    set(item_by_requirement.keys()) == MANDATORY_REQUIREMENTS,
    f"found={sorted(item_by_requirement.keys())}",
)

for req in sorted(MANDATORY_REQUIREMENTS):
    item = item_by_requirement.get(req)

    add_check(
        f"{req}: Stage 46 VERIFIED",
        bool(item)
        and normalize_text(item.get("stage46_verdict")).upper() == "VERIFIED",
        normalize_text(item.get("stage46_verdict")) if item else "MISSING",
    )

    add_check(
        f"{req}: Stage 47 accepted",
        bool(item) and bool_true(item.get("stage47_accepted")),
        f"accepted={bool_true(item.get('stage47_accepted')) if item else False}",
    )

    add_check(
        f"{req}: provenance OK",
        bool(item) and bool_true(item.get("provenance_ok")),
        f"provenance_ok={bool_true(item.get('provenance_ok')) if item else False}",
    )


# ---------------------------------------------------------------------
# Final Stage 48 status
# ---------------------------------------------------------------------

all_checks_pass = all(row["PASS"] for row in checks)

stage48_status = "PASS" if all_checks_pass else "BLOCKED"

failed_checks = [row["Check"] for row in checks if not row["PASS"]]

if stage48_status == "PASS":
    stage48_reason = (
        "Persisted Stage 47 ELIGIBLE decision is authentic and stable, bound to "
        "the same project, ACTIVE lock, deadline and Stage 46 PASS run, with all "
        "four canonical Stage 47 requirement items accepted and provenance-valid."
    )
else:
    stage48_reason = (
        "Stage 48 fail-closed validation failed: "
        + "; ".join(failed_checks)
    )


# ---------------------------------------------------------------------
# Stable Stage 48 handoff fingerprint
# ---------------------------------------------------------------------

stage48_basis = {
    "stage": 48,
    "fingerprint_contract": "stage48-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage46_run_id": stage46_run_id,
    "stage47_run_id": stage47_run_id,
    "stage47_decision": stage47_decision,
    "stage47_decision_fingerprint": stage47_fingerprint,
    "stage48_status": stage48_status,
    "checks": checks,
}

stage48_handoff_fingerprint = stable_sha256(stage48_basis)


# ---------------------------------------------------------------------
# Stage 48 persistence
# ---------------------------------------------------------------------

def load_existing_stage48():
    if not stage48_handoff_fingerprint:
        return None

    data = (
        supabase.table("stage48_handoff_validation_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage47_run_id", stage47_run_id)
        .eq("stage48_handoff_fingerprint", stage48_handoff_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage48():
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage46_run_id": stage46_run_id,
        "stage47_run_id": stage47_run_id,
        "stage": 48,
        "validator_version": "stage48-v1.0",
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,
        "stage47_decision": stage47_decision,
        "stage47_decision_fingerprint": stage47_fingerprint,
        "stage48_status": stage48_status,
        "stage48_reason": stage48_reason,
        "stage48_handoff_fingerprint": stage48_handoff_fingerprint,
        "checks": checks,
        "handoff_payload": stage48_basis,
        "completed_at": now_iso(),
        "updated_at": now_iso(),
    }

    existing = load_existing_stage48()

    if existing:
        data = (
            supabase.table("stage48_handoff_validation_runs")
            .update(payload)
            .eq("id", existing["id"])
            .eq("user_id", user_id)
            .execute()
        ).data or []
        return data[0] if data else {**existing, **payload}

    payload["created_at"] = now_iso()

    data = (
        supabase.table("stage48_handoff_validation_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 48 validation run.")

    return data[0]


existing_stage48 = load_existing_stage48()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 47 → Stage 48 persisted handoff")

h1, h2, h3, h4 = st.columns(4)
h1.metric("Stage 47", stage47_decision)
h2.metric("Stage 47 items", len(stage47_items))
h3.metric(
    "Stage 46",
    normalize_text(stage46.get("run_status")).upper() if stage46 else "MISSING",
)
h4.metric("Fingerprint", "MATCH" if stage47_fingerprint == recomputed_stage47_fingerprint else "MISMATCH")

st.dataframe(
    [
        {
            "Requirement": req,
            "Stage 46 verdict": (
                item_by_requirement.get(req, {}).get("stage46_verdict")
            ),
            "Stage 47 accepted": bool_true(
                item_by_requirement.get(req, {}).get("stage47_accepted")
            ),
            "Provenance": bool_true(
                item_by_requirement.get(req, {}).get("provenance_ok")
            ),
            "Final URL": item_by_requirement.get(req, {}).get("final_url"),
        }
        for req in sorted(MANDATORY_REQUIREMENTS)
    ],
    use_container_width=True,
    hide_index=True,
)

with st.expander("Stage 48 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Final Stage 48 Result")

r1, r2, r3 = st.columns(3)
r1.metric("Gate", stage48_status)
r2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
r3.metric("Stage 47 fingerprint", "VERIFIED" if stage47_fingerprint == recomputed_stage47_fingerprint else "FAILED")

if stage48_status == "PASS":
    st.success(
        "Etapa 48: PASS. Handoff-ul Stage 47 persistat este autentic, stabil și "
        "poate fi folosit de o etapă downstream."
    )
else:
    st.error(
        "Etapa 48: BLOCKED. Handoff-ul Stage 47 nu trece toate verificările de integritate."
    )

st.write(f"**Reason:** {stage48_reason}")
st.code(stage48_handoff_fingerprint, language=None)

st.caption(
    "Stage 48 handoff fingerprint SHA256 leagă user/project/lock, opportunity identity, "
    "deadline, Stage 46 run, Stage 47 persisted run, Stage 47 fingerprint și toate check-urile Stage 48."
)

with st.expander("Stage 48 handoff payload", expanded=False):
    st.json({
        **stage48_basis,
        "stage48_handoff_fingerprint": stage48_handoff_fingerprint,
    })

st.divider()
st.subheader("Stage 48 persistence")

if existing_stage48:
    st.success(
        f"Stage 48 este deja persistată în Supabase. Run ID: {existing_stage48.get('id')}"
    )
else:
    st.info(
        "Rezultatul Stage 48 nu este încă persistat. Persistența este necesară "
        "înainte ca o etapă viitoare să consume acest gate."
    )

if st.button(
    "💾 Persist Stage 48 handoff validation",
    type="primary",
    use_container_width=True,
    key="stage48_v1_persist",
):
    try:
        saved = persist_stage48()
        st.session_state["stage48_persisted_run_id"] = str(saved.get("id"))
        st.success(
            f"Stage 48 persisted: {saved.get('stage48_status')} — run {saved.get('id')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 48 persistence failed. Rulează mai întâi SQL-ul Stage 48 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1200]}"
        )

if stage48_status == "PASS" and existing_stage48:
    st.success(
        "Stage 48 poate preda controlul unei viitoare Etape 49. Stage 49 trebuie "
        "să verifice stage48_run_id + stage47_run_id + stage46_run_id + lock_id + "
        "ambele fingerprint-uri."
    )
elif stage48_status == "PASS":
    st.warning(
        "Stage 48 este PASS logic, dar nu poate preda controlul până când rezultatul nu este persistat."
    )
else:
    st.info("Nu există handoff pozitiv către etapa următoare.")

st.caption(
    "Invariantă Stage 48 v1.0: Stage 47 ELIGIBLE trebuie să fie persistat și să treacă "
    "recalcularea fingerprint-ului. Orice mismatch produce BLOCKED."
)

# =====================================================================
# END STAGE 48 v1.0
# =====================================================================
