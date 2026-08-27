import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 49 v1.0 — APPLICATION EXECUTION AUTHORIZATION GATE
#
# Purpose:
#   Consume ONLY a persisted Stage 48 PASS for the same user/project/ACTIVE
#   opportunity lock and authorize downstream application-building work.
#
# Stage 49 verifies:
#   - same ACTIVE lock / project / opportunity identity / deadline;
#   - persisted Stage 48 run exists and remains PASS;
#   - Stage 48 -> Stage 47 -> Stage 46 run IDs remain bound together;
#   - Stage 47 decision remains ELIGIBLE;
#   - Stage 46 remains PASS;
#   - Stage 47 and Stage 48 fingerprints still match their persisted payloads;
#   - all four canonical eligibility items remain accepted/provenance-valid.
#
# Final statuses:
#   AUTHORIZED -> downstream proposal build may proceed.
#   BLOCKED    -> no downstream application execution is authorized.
#
# Important:
#   Stage 49 does NOT submit anything externally.
#   It creates a persisted authorization token for a future Stage 50.
#
# Persistence:
#   stage49_application_authorization_runs
# =====================================================================

st.set_page_config(
    page_title="Stage 49 v1.0 — Application Authorization",
    page_icon="🚦",
    layout="wide",
)

st.title("🚦 Etapa 49 v1.0 — AI Application Execution Authorization Gate")
st.caption(
    "Etapa 49 autorizează numai construirea downstream a aplicației. "
    "Nu trimite nimic către portalul de finanțare și nu poate ocoli Stage 46–48."
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
# Auth / project / ACTIVE lock
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Stage 49 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage49_v1_project",
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
    st.error("Stage 49 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load latest persisted Stage 48 PASS candidate
# ---------------------------------------------------------------------

stage48_runs = rows(
    "stage48_handoff_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage48 = stage48_runs[0] if stage48_runs else None

if stage48:
    stage48_run_id = str(stage48.get("id") or "")
    stage48_status = normalize_text(stage48.get("stage48_status")).upper()
    stage48_fingerprint = normalize_text(stage48.get("stage48_handoff_fingerprint"))
    stage47_run_id = str(stage48.get("stage47_run_id") or "")
    stage46_run_id = str(stage48.get("stage46_run_id") or "")
else:
    stage48_run_id = ""
    stage48_status = "MISSING"
    stage48_fingerprint = ""
    stage47_run_id = ""
    stage46_run_id = ""


# ---------------------------------------------------------------------
# Load bound Stage 47 / Stage 46 persisted rows
# ---------------------------------------------------------------------

stage47_rows = rows(
    "final_opportunity_eligibility_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage47_run_id else []

stage47 = next(
    (r for r in stage47_rows if str(r.get("id") or "") == stage47_run_id),
    None,
)

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
) if stage47_run_id else []

stage46_rows = rows(
    "locked_evidence_provenance_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage46_run_id else []

stage46 = next(
    (r for r in stage46_rows if str(r.get("id") or "") == stage46_run_id),
    None,
)


# ---------------------------------------------------------------------
# Recompute Stage 47 stable fingerprint
# ---------------------------------------------------------------------

if stage47:
    stored_mandatory = as_dict(stage47.get("mandatory_requirements"))
    stored_checks47 = stage47.get("checks")
    if not isinstance(stored_checks47, list):
        stored_checks47 = []

    stage47_basis = {
        "stage": 47,
        "fingerprint_contract": "stage47-v1.2-stable",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "opportunity_identity": normalize_text(stage47.get("opportunity_identity")),
        "official_deadline": str(stage47.get("official_deadline") or "")[:10],
        "stage46_run_id": str(stage47.get("stage46_run_id") or ""),
        "stage46_status": normalize_text(stage47.get("stage46_status")).upper(),
        "decision": normalize_text(stage47.get("decision")).upper(),
        "mandatory_requirements": stored_mandatory,
        "checks": stored_checks47,
    }
    recomputed_stage47_fingerprint = stable_sha256(stage47_basis)
    stored_stage47_fingerprint = normalize_text(stage47.get("decision_fingerprint"))
else:
    stage47_basis = {}
    recomputed_stage47_fingerprint = ""
    stored_stage47_fingerprint = ""


# ---------------------------------------------------------------------
# Recompute Stage 48 fingerprint from persisted handoff payload
# ---------------------------------------------------------------------

if stage48:
    handoff48 = as_dict(stage48.get("handoff_payload"))
    recomputed_stage48_fingerprint = stable_sha256(handoff48) if handoff48 else ""
else:
    handoff48 = {}
    recomputed_stage48_fingerprint = ""


# ---------------------------------------------------------------------
# Canonical requirement item integrity
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
# Stage 49 authorization checks
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
    identity or "Missing opportunity identity",
)

add_check(
    "Deadline still valid",
    future_deadline(deadline),
    str(deadline or "Missing deadline")[:10],
)

add_check(
    "Persisted Stage 48 run exists",
    bool(stage48),
    stage48_run_id or "No Stage 48 run",
)

add_check(
    "Stage 48 remains PASS",
    stage48_status == "PASS",
    f"stage48_status={stage48_status}",
)

add_check(
    "Stage 48 lock binding",
    bool(stage48)
    and str(stage48.get("opportunity_lock_id") or "") == lock_id,
    str(stage48.get("opportunity_lock_id") or "") if stage48 else "MISSING",
)

add_check(
    "Stage 48 opportunity identity binding",
    bool(stage48)
    and normalize_text(stage48.get("opportunity_identity")).lower() == identity.lower(),
    normalize_text(stage48.get("opportunity_identity")) if stage48 else "MISSING",
)

add_check(
    "Stage 48 deadline binding",
    bool(stage48)
    and str(stage48.get("official_deadline") or "")[:10] == str(deadline or "")[:10],
    (
        f"stage48={str(stage48.get('official_deadline') or '')[:10]}, "
        f"lock={str(deadline or '')[:10]}"
        if stage48
        else "MISSING"
    ),
)

add_check(
    "Stage 47 persisted run exists",
    bool(stage47),
    stage47_run_id or "No Stage 47 run",
)

add_check(
    "Stage 47 remains ELIGIBLE",
    bool(stage47)
    and normalize_text(stage47.get("decision")).upper() == "ELIGIBLE",
    normalize_text(stage47.get("decision")).upper() if stage47 else "MISSING",
)

add_check(
    "Stage 47 -> Stage 46 run binding",
    bool(stage47)
    and str(stage47.get("stage46_run_id") or "") == stage46_run_id,
    (
        f"stage47.stage46={str(stage47.get('stage46_run_id') or '')}, "
        f"stage48.stage46={stage46_run_id}"
        if stage47
        else "MISSING"
    ),
)

add_check(
    "Stage 46 persisted run exists",
    bool(stage46),
    stage46_run_id or "No Stage 46 run",
)

add_check(
    "Stage 46 remains PASS",
    bool(stage46)
    and normalize_text(stage46.get("run_status")).upper() == "PASS",
    normalize_text(stage46.get("run_status")).upper() if stage46 else "MISSING",
)

add_check(
    "Stage 47 fingerprint stable",
    bool(stored_stage47_fingerprint)
    and stored_stage47_fingerprint == recomputed_stage47_fingerprint,
    (
        f"stored={stored_stage47_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage47_fingerprint[:16]}..."
        if stored_stage47_fingerprint
        else "Missing Stage 47 fingerprint"
    ),
)

add_check(
    "Stage 48 fingerprint stable",
    bool(stage48_fingerprint)
    and stage48_fingerprint == recomputed_stage48_fingerprint,
    (
        f"stored={stage48_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage48_fingerprint[:16]}..."
        if stage48_fingerprint
        else "Missing Stage 48 fingerprint"
    ),
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
# Final authorization
# ---------------------------------------------------------------------

all_checks_pass = all(c["PASS"] for c in checks)

authorization_status = "AUTHORIZED" if all_checks_pass else "BLOCKED"

if authorization_status == "AUTHORIZED":
    authorization_reason = (
        "Persisted Stage 48 PASS is authentic and stable, Stage 47 remains ELIGIBLE, "
        "Stage 46 remains PASS, the ACTIVE lock/deadline bindings match, and all four "
        "canonical eligibility items remain verified and provenance-valid."
    )
else:
    failed_checks = [c["Check"] for c in checks if not c["PASS"]]
    authorization_reason = (
        "Stage 49 fail-closed authorization failed: "
        + "; ".join(failed_checks)
    )


# ---------------------------------------------------------------------
# Authorization scope and stable token fingerprint
# ---------------------------------------------------------------------

AUTHORIZATION_SCOPE = {
    "proposal_build": True,
    "proposal_review": True,
    "proposal_optimization": True,
    "submission_pack_build": True,

    # Explicitly NOT authorized here:
    "external_submission": False,
    "portal_login_automation": False,
    "legal_signature": False,
    "financial_commitment": False,
}

authorization_basis = {
    "stage": 49,
    "fingerprint_contract": "stage49-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage46_run_id": stage46_run_id,
    "stage47_run_id": stage47_run_id,
    "stage48_run_id": stage48_run_id,
    "stage47_decision_fingerprint": stored_stage47_fingerprint,
    "stage48_handoff_fingerprint": stage48_fingerprint,
    "authorization_status": authorization_status,
    "authorization_scope": AUTHORIZATION_SCOPE,
    "checks": checks,
}

authorization_fingerprint = stable_sha256(authorization_basis)


# ---------------------------------------------------------------------
# Stage 49 persistence
# ---------------------------------------------------------------------

def load_existing_stage49():
    if not authorization_fingerprint:
        return None

    data = (
        supabase.table("stage49_application_authorization_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage48_run_id", stage48_run_id)
        .eq("authorization_fingerprint", authorization_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage49():
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage46_run_id": stage46_run_id,
        "stage47_run_id": stage47_run_id,
        "stage48_run_id": stage48_run_id,

        "stage": 49,
        "validator_version": "stage49-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage47_decision_fingerprint": stored_stage47_fingerprint,
        "stage48_handoff_fingerprint": stage48_fingerprint,

        "authorization_status": authorization_status,
        "authorization_reason": authorization_reason,
        "authorization_scope": AUTHORIZATION_SCOPE,
        "authorization_fingerprint": authorization_fingerprint,

        "checks": checks,
        "authorization_payload": authorization_basis,

        "completed_at": now_iso(),
        "updated_at": now_iso(),
    }

    existing = load_existing_stage49()

    if existing:
        data = (
            supabase.table("stage49_application_authorization_runs")
            .update(payload)
            .eq("id", existing["id"])
            .eq("user_id", user_id)
            .execute()
        ).data or []

        return data[0] if data else {**existing, **payload}

    payload["created_at"] = now_iso()

    data = (
        supabase.table("stage49_application_authorization_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 49 authorization run.")

    return data[0]


existing_stage49 = load_existing_stage49()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 48 → Stage 49 authorization binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 48", stage48_status)
m2.metric(
    "Stage 47",
    normalize_text(stage47.get("decision")).upper() if stage47 else "MISSING",
)
m3.metric(
    "Stage 46",
    normalize_text(stage46.get("run_status")).upper() if stage46 else "MISSING",
)
m4.metric(
    "Fingerprints",
    "VERIFIED"
    if (
        stored_stage47_fingerprint == recomputed_stage47_fingerprint
        and stage48_fingerprint == recomputed_stage48_fingerprint
        and bool(stored_stage47_fingerprint)
        and bool(stage48_fingerprint)
    )
    else "FAILED",
)

st.dataframe(
    [
        {
            "Requirement": req,
            "Stage 46 verdict": item_by_requirement.get(req, {}).get("stage46_verdict"),
            "Stage 47 accepted": bool_true(
                item_by_requirement.get(req, {}).get("stage47_accepted")
            ),
            "Provenance": bool_true(
                item_by_requirement.get(req, {}).get("provenance_ok")
            ),
        }
        for req in sorted(MANDATORY_REQUIREMENTS)
    ],
    use_container_width=True,
    hide_index=True,
)

with st.expander("Stage 49 authorization checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Final Stage 49 Authorization")

a1, a2, a3 = st.columns(3)
a1.metric("Authorization", authorization_status)
a2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
a3.metric("External submission", "NOT AUTHORIZED")

if authorization_status == "AUTHORIZED":
    st.success(
        "Etapa 49: AUTHORIZED. Sistemul poate continua cu construirea, revizuirea, "
        "optimizarea și pregătirea submission pack-ului pentru oportunitatea blocată."
    )
else:
    st.error(
        "Etapa 49: BLOCKED. Nicio execuție downstream nu este autorizată."
    )

st.write(f"**Reason:** {authorization_reason}")

st.subheader("Authorization scope")
st.json(AUTHORIZATION_SCOPE)

st.code(authorization_fingerprint, language=None)
st.caption(
    "Authorization fingerprint SHA256 leagă Stage 46/47/48, ACTIVE lock, deadline, "
    "ambele fingerprint-uri upstream și scope-ul exact autorizat."
)

with st.expander("Stage 49 authorization payload", expanded=False):
    st.json({
        **authorization_basis,
        "authorization_fingerprint": authorization_fingerprint,
    })

st.divider()
st.subheader("Stage 49 persistence")

if existing_stage49:
    st.success(
        f"Stage 49 este deja persistată în Supabase. Run ID: {existing_stage49.get('id')}"
    )
else:
    st.info(
        "Autorizația Stage 49 nu este încă persistată. Persistența este necesară "
        "înainte ca Stage 50 să consume această autorizație."
    )

if st.button(
    "💾 Persist Stage 49 application authorization",
    type="primary",
    use_container_width=True,
    key="stage49_v1_persist",
):
    try:
        saved = persist_stage49()
        st.session_state["stage49_persisted_run_id"] = str(saved.get("id"))
        st.success(
            f"Stage 49 persisted: {saved.get('authorization_status')} — run {saved.get('id')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 49 persistence failed. Rulează mai întâi SQL-ul Stage 49 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1200]}"
        )

if authorization_status == "AUTHORIZED" and existing_stage49:
    st.success(
        "Stage 49 poate preda controlul unei viitoare Etape 50 pentru proposal-build execution. "
        "Stage 50 trebuie să verifice stage49_run_id + stage48_run_id + stage47_run_id + "
        "stage46_run_id + lock_id + authorization_fingerprint."
    )
elif authorization_status == "AUTHORIZED":
    st.warning(
        "Stage 49 este AUTHORIZED logic, dar nu poate preda controlul până la persistență."
    )
else:
    st.info("Nu există handoff pozitiv către Stage 50.")

st.caption(
    "Invariantă Stage 49 v1.0: AUTHORIZED permite doar activitățile din authorization_scope. "
    "External submission, portal login automation, legal signature și financial commitment "
    "rămân explicit neautorizate."
)

# =====================================================================
# END STAGE 49 v1.0
# =====================================================================
