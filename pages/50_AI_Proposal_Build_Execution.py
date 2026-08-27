import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 50 v1.0 — PROPOSAL BUILD EXECUTION
#
# Purpose:
#   Consume ONLY a persisted Stage 49 AUTHORIZED run for the same user/project/
#   ACTIVE opportunity lock and initialize a durable proposal-build workspace.
#
# Stage 50:
#   - verifies the complete Stage 49 -> 48 -> 47 -> 46 chain;
#   - verifies Stage 49 authorization_fingerprint from persisted payload;
#   - enforces Stage 49 scope: proposal_build must be true;
#   - creates a persistent build run and canonical proposal section work items;
#   - never submits externally, signs, logs into the portal, or commits funds.
#
# Final statuses:
#   READY       -> proposal build workspace can be created/persisted.
#   INITIALIZED -> persisted Stage 50 build run exists.
#   BLOCKED     -> upstream authorization/binding is invalid.
#
# Persistence:
#   stage50_proposal_build_runs
#   stage50_proposal_build_items
# =====================================================================

st.set_page_config(
    page_title="Stage 50 v1.0 — Proposal Build Execution",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ Etapa 50 v1.0 — AI Proposal Build Execution")
st.caption(
    "Etapa 50 inițializează spațiul de lucru pentru construirea propunerii numai după "
    "o autorizație Stage 49 persistată și verificată. Nu efectuează external submission."
)


# ---------------------------------------------------------------------
# Helpers
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
    st.error("Stage 50 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage50_v1_project",
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
    st.error("Stage 50 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load latest persisted Stage 49 authorization
# ---------------------------------------------------------------------

stage49_runs = rows(
    "stage49_application_authorization_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage49 = stage49_runs[0] if stage49_runs else None

if stage49:
    stage49_run_id = str(stage49.get("id") or "")
    stage49_status = normalize_text(stage49.get("authorization_status")).upper()
    stage49_fingerprint = normalize_text(stage49.get("authorization_fingerprint"))
    stage48_run_id = str(stage49.get("stage48_run_id") or "")
    stage47_run_id = str(stage49.get("stage47_run_id") or "")
    stage46_run_id = str(stage49.get("stage46_run_id") or "")
    auth_scope = as_dict(stage49.get("authorization_scope"))
else:
    stage49_run_id = ""
    stage49_status = "MISSING"
    stage49_fingerprint = ""
    stage48_run_id = ""
    stage47_run_id = ""
    stage46_run_id = ""
    auth_scope = {}


# ---------------------------------------------------------------------
# Load upstream persisted rows
# ---------------------------------------------------------------------

stage48_rows = rows(
    "stage48_handoff_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage48_run_id else []

stage48 = next(
    (r for r in stage48_rows if str(r.get("id") or "") == stage48_run_id),
    None,
)

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
# Recompute Stage 49 authorization fingerprint
# ---------------------------------------------------------------------

authorization_payload = as_dict(stage49.get("authorization_payload")) if stage49 else {}
recomputed_stage49_fingerprint = (
    stable_sha256(authorization_payload)
    if authorization_payload
    else ""
)


# ---------------------------------------------------------------------
# Stage 50 hard gate
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
    "Persisted Stage 49 exists",
    bool(stage49),
    stage49_run_id or "No Stage 49 run",
)

add_check(
    "Stage 49 AUTHORIZED",
    stage49_status == "AUTHORIZED",
    f"authorization_status={stage49_status}",
)

add_check(
    "Stage 49 proposal_build scope",
    bool_true(auth_scope.get("proposal_build")),
    f"proposal_build={bool_true(auth_scope.get('proposal_build'))}",
)

add_check(
    "Stage 49 external submission remains disabled",
    not bool_true(auth_scope.get("external_submission")),
    f"external_submission={bool_true(auth_scope.get('external_submission'))}",
)

add_check(
    "Stage 49 fingerprint stable",
    bool(stage49_fingerprint)
    and stage49_fingerprint == recomputed_stage49_fingerprint,
    (
        f"stored={stage49_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage49_fingerprint[:16]}..."
        if stage49_fingerprint
        else "Missing Stage 49 fingerprint"
    ),
)

add_check(
    "Stage 48 bound run exists",
    bool(stage48),
    stage48_run_id or "No Stage 48 run",
)

add_check(
    "Stage 48 PASS",
    bool(stage48)
    and normalize_text(stage48.get("stage48_status")).upper() == "PASS",
    normalize_text(stage48.get("stage48_status")).upper() if stage48 else "MISSING",
)

add_check(
    "Stage 47 bound run exists",
    bool(stage47),
    stage47_run_id or "No Stage 47 run",
)

add_check(
    "Stage 47 ELIGIBLE",
    bool(stage47)
    and normalize_text(stage47.get("decision")).upper() == "ELIGIBLE",
    normalize_text(stage47.get("decision")).upper() if stage47 else "MISSING",
)

add_check(
    "Stage 46 bound run exists",
    bool(stage46),
    stage46_run_id or "No Stage 46 run",
)

add_check(
    "Stage 46 PASS",
    bool(stage46)
    and normalize_text(stage46.get("run_status")).upper() == "PASS",
    normalize_text(stage46.get("run_status")).upper() if stage46 else "MISSING",
)

add_check(
    "Stage 49 lock binding",
    bool(stage49)
    and str(stage49.get("opportunity_lock_id") or "") == lock_id,
    str(stage49.get("opportunity_lock_id") or "") if stage49 else "MISSING",
)

add_check(
    "Stage 49 opportunity binding",
    bool(stage49)
    and normalize_text(stage49.get("opportunity_identity")).lower() == identity.lower(),
    normalize_text(stage49.get("opportunity_identity")) if stage49 else "MISSING",
)

add_check(
    "Stage 49 deadline binding",
    bool(stage49)
    and str(stage49.get("official_deadline") or "")[:10] == str(deadline or "")[:10],
    (
        f"stage49={str(stage49.get('official_deadline') or '')[:10]}, "
        f"lock={str(deadline or '')[:10]}"
        if stage49
        else "MISSING"
    ),
)

all_checks_pass = all(c["PASS"] for c in checks)

stage50_gate = "READY" if all_checks_pass else "BLOCKED"

if stage50_gate == "READY":
    gate_reason = (
        "Stage 49 authorization is persisted and stable, proposal_build is explicitly "
        "authorized, external submission remains disabled, and Stage 46–48 bindings remain valid."
    )
else:
    gate_reason = (
        "Stage 50 fail-closed gate failed: "
        + "; ".join(c["Check"] for c in checks if not c["PASS"])
    )


# ---------------------------------------------------------------------
# Canonical build plan
# ---------------------------------------------------------------------
#
# These are workspace-level sections only. They do not claim that every call
# uses identical headings/page limits. A later drafting stage must load the
# official call/template before generating final proposal text.
# ---------------------------------------------------------------------

BUILD_ITEMS = [
    {
        "section_key": "call_and_template_snapshot",
        "section_title": "Official call/template snapshot",
        "sequence_no": 10,
        "item_type": "SOURCE_BINDING",
        "purpose": "Bind the proposal build to the locked opportunity and verified official source set.",
    },
    {
        "section_key": "project_facts",
        "section_title": "Project facts and applicant baseline",
        "sequence_no": 20,
        "item_type": "FACT_BASE",
        "purpose": "Collect only persisted project facts needed for drafting; unknown facts stay unresolved.",
    },
    {
        "section_key": "excellence",
        "section_title": "Excellence",
        "sequence_no": 30,
        "item_type": "PROPOSAL_SECTION",
        "purpose": "Workspace for objectives, concept/methodology, ambition and relevant excellence criteria.",
    },
    {
        "section_key": "impact",
        "section_title": "Impact",
        "sequence_no": 40,
        "item_type": "PROPOSAL_SECTION",
        "purpose": "Workspace for pathways to impact, outcomes, exploitation/dissemination and communication.",
    },
    {
        "section_key": "implementation",
        "section_title": "Quality and efficiency of implementation",
        "sequence_no": 50,
        "item_type": "PROPOSAL_SECTION",
        "purpose": "Workspace for work plan, resources, consortium roles, risks and implementation capacity.",
    },
    {
        "section_key": "budget_and_resources",
        "section_title": "Budget and resources",
        "sequence_no": 60,
        "item_type": "BUDGET_WORKSPACE",
        "purpose": "Prepare budget/resource evidence without making financial commitments.",
    },
    {
        "section_key": "ethics_security_compliance",
        "section_title": "Ethics, security and compliance",
        "sequence_no": 70,
        "item_type": "COMPLIANCE_WORKSPACE",
        "purpose": "Track required declarations, ethics/security issues and compliance evidence.",
    },
    {
        "section_key": "submission_pack",
        "section_title": "Submission pack assembly",
        "sequence_no": 80,
        "item_type": "PACKAGING",
        "purpose": "Assemble validated outputs for later review; external submission remains unauthorized.",
    },
]


# ---------------------------------------------------------------------
# Stable Stage 50 build fingerprint
# ---------------------------------------------------------------------

build_basis = {
    "stage": 50,
    "fingerprint_contract": "stage50-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage46_run_id": stage46_run_id,
    "stage47_run_id": stage47_run_id,
    "stage48_run_id": stage48_run_id,
    "stage49_run_id": stage49_run_id,
    "stage49_authorization_fingerprint": stage49_fingerprint,
    "stage50_gate": stage50_gate,
    "authorization_scope": auth_scope,
    "build_items": BUILD_ITEMS,
}

build_fingerprint = stable_sha256(build_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage50():
    if not build_fingerprint:
        return None

    data = (
        supabase.table("stage50_proposal_build_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage49_run_id", stage49_run_id)
        .eq("build_fingerprint", build_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage50():
    if stage50_gate != "READY":
        raise RuntimeError("Stage 50 is BLOCKED and cannot initialize a build run.")

    run_payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage46_run_id": stage46_run_id,
        "stage47_run_id": stage47_run_id,
        "stage48_run_id": stage48_run_id,
        "stage49_run_id": stage49_run_id,

        "stage": 50,
        "builder_version": "stage50-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage49_authorization_fingerprint": stage49_fingerprint,
        "build_status": "INITIALIZED",
        "build_reason": gate_reason,
        "authorization_scope": auth_scope,
        "build_fingerprint": build_fingerprint,

        "build_payload": build_basis,

        "initialized_at": now_iso(),
        "updated_at": now_iso(),
    }

    existing = load_existing_stage50()

    if existing:
        run_id = str(existing["id"])
        data = (
            supabase.table("stage50_proposal_build_runs")
            .update(run_payload)
            .eq("id", run_id)
            .eq("user_id", user_id)
            .execute()
        ).data or []
        run_row = data[0] if data else {**existing, **run_payload}
    else:
        run_payload["created_at"] = now_iso()
        data = (
            supabase.table("stage50_proposal_build_runs")
            .insert(run_payload)
            .execute()
        ).data or []

        if not data:
            raise RuntimeError("Could not persist Stage 50 proposal build run.")

        run_row = data[0]
        run_id = str(run_row["id"])

    for item in BUILD_ITEMS:
        item_payload = {
            "stage50_run_id": run_id,
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "section_key": item["section_key"],
            "section_title": item["section_title"],
            "sequence_no": item["sequence_no"],
            "item_type": item["item_type"],
            "purpose": item["purpose"],

            "item_status": "PENDING",
            "content_status": "NOT_STARTED",
            "source_status": "BOUND" if item["section_key"] == "call_and_template_snapshot" else "PENDING",

            "source_payload": {
                "opportunity_identity": identity,
                "stage46_run_id": stage46_run_id,
                "stage47_run_id": stage47_run_id,
                "stage48_run_id": stage48_run_id,
                "stage49_run_id": stage49_run_id,
                "authorization_fingerprint": stage49_fingerprint,
            },

            "updated_at": now_iso(),
        }

        existing_item = (
            supabase.table("stage50_proposal_build_items")
            .select("id")
            .eq("user_id", user_id)
            .eq("stage50_run_id", run_id)
            .eq("section_key", item["section_key"])
            .limit(1)
            .execute()
        ).data or []

        if existing_item:
            (
                supabase.table("stage50_proposal_build_items")
                .update(item_payload)
                .eq("id", existing_item[0]["id"])
                .eq("user_id", user_id)
                .execute()
            )
        else:
            item_payload["created_at"] = now_iso()
            (
                supabase.table("stage50_proposal_build_items")
                .insert(item_payload)
                .execute()
            )

    return run_row


existing_stage50 = load_existing_stage50()

persisted_items = []
if existing_stage50:
    persisted_items = rows(
        "stage50_proposal_build_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage50_run_id": existing_stage50.get("id"),
        },
        "sequence_no",
        100,
    )


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 49 → Stage 50 authorization binding")

b1, b2, b3, b4 = st.columns(4)
b1.metric("Stage 49", stage49_status)
b2.metric("Proposal build", "AUTHORIZED" if bool_true(auth_scope.get("proposal_build")) else "NO")
b3.metric("External submission", "DISABLED" if not bool_true(auth_scope.get("external_submission")) else "ENABLED")
b4.metric(
    "Authorization fingerprint",
    "VERIFIED" if stage49_fingerprint == recomputed_stage49_fingerprint and bool(stage49_fingerprint) else "FAILED",
)

with st.expander("Stage 50 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Stage 50 Proposal Build Plan")

st.dataframe(
    [
        {
            "Order": item["sequence_no"],
            "Section": item["section_title"],
            "Type": item["item_type"],
            "Purpose": item["purpose"],
        }
        for item in BUILD_ITEMS
    ],
    use_container_width=True,
    hide_index=True,
)

st.divider()
st.subheader("Final Stage 50 Gate")

g1, g2, g3 = st.columns(3)
g1.metric("Gate", stage50_gate)
g2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
g3.metric("Build work items", len(BUILD_ITEMS))

if stage50_gate == "READY":
    st.success(
        "Etapa 50: READY. Proposal-build workspace poate fi inițializat și persistat."
    )
else:
    st.error(
        "Etapa 50: BLOCKED. Proposal-build workspace nu poate fi inițializat."
    )

st.write(f"**Reason:** {gate_reason}")
st.code(build_fingerprint, language=None)

with st.expander("Stage 50 build payload", expanded=False):
    st.json({
        **build_basis,
        "build_fingerprint": build_fingerprint,
    })

st.divider()
st.subheader("Stage 50 persistence")

if existing_stage50:
    st.success(
        f"Stage 50 este deja inițializată în Supabase. Run ID: {existing_stage50.get('id')}"
    )

    if persisted_items:
        st.dataframe(
            [
                {
                    "Order": i.get("sequence_no"),
                    "Section": i.get("section_title"),
                    "Item status": i.get("item_status"),
                    "Content status": i.get("content_status"),
                    "Source status": i.get("source_status"),
                }
                for i in sorted(persisted_items, key=lambda x: int(x.get("sequence_no") or 0))
            ],
            use_container_width=True,
            hide_index=True,
        )
else:
    st.info(
        "Stage 50 nu este încă persistată. Inițializează build run-ul înainte de o etapă de drafting."
    )

if st.button(
    "🏗️ Initialize & persist Stage 50 proposal build",
    type="primary",
    use_container_width=True,
    key="stage50_v1_initialize",
    disabled=(stage50_gate != "READY"),
):
    try:
        saved = persist_stage50()
        st.session_state["stage50_run_id"] = str(saved.get("id"))
        st.success(
            f"Stage 50 initialized: {saved.get('build_status')} — run {saved.get('id')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 50 persistence failed. Rulează mai întâi SQL-ul Stage 50 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1200]}"
        )

if existing_stage50 and stage50_gate == "READY":
    st.success(
        "Stage 50 poate preda controlul unei viitoare Etape 51 pentru drafting execution. "
        "Stage 51 trebuie să verifice stage50_run_id + stage49_run_id + lock_id + "
        "build_fingerprint + authorization_fingerprint."
    )
else:
    st.info("Nu există încă handoff pozitiv către Stage 51.")

st.caption(
    "Invariantă Stage 50 v1.0: proposal_build este permis numai în scope-ul Stage 49. "
    "External submission, portal login automation, legal signature și financial commitment "
    "rămân neautorizate."
)

# =====================================================================
# END STAGE 50 v1.0
# =====================================================================
