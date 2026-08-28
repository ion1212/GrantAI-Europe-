import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 61 v1.0 — AI FINAL HUMAN APPROVAL / CONTROLLED SUBMISSION HANDOFF
#
# Purpose:
#   Consume ONLY a persisted Stage 60 PACKAGE_READY package and require
#   explicit human approval before creating a controlled submission handoff.
#
# Stage 61 DOES NOT:
#   - log into Funding & Tenders
#   - submit externally
#   - sign legal declarations
#   - make financial commitments
#   - represent that the European Commission has received the proposal
#
# Stage 61 verifies:
#   - ACTIVE lock + valid deadline
#   - Stage 60 COMPLETED + PACKAGE_READY
#   - Stage 60 run fingerprint stable
#   - Stage 60 package SHA256 stable
#   - Stage 59 READY_FOR_SUBMISSION_PREP still bound
#   - package sections match persisted Stage 60 snapshot
#   - user gives explicit approval
#
# Outcomes:
#   AWAITING_HUMAN_APPROVAL
#   APPROVED_FOR_SUBMISSION_HANDOFF
#   REJECTED_BY_USER
#   BLOCKED
#
# Handoff:
#   Stage 62 may consume ONLY APPROVED_FOR_SUBMISSION_HANDOFF.
# =====================================================================

st.set_page_config(
    page_title="Stage 61 v1.0 — Final Human Approval",
    page_icon="🧑‍⚖️",
    layout="wide",
)

st.title("🧑‍⚖️ Etapa 61 v1.0 — Final Human Approval / Controlled Submission Handoff")
st.caption(
    "Verifică pachetul Stage 60 și cere aprobarea explicită a utilizatorului. "
    "Nu efectuează submission extern."
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


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


# ---------------------------------------------------------------------
# Supabase / auth
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Stage 61 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage61_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 61 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load latest Stage 60 PACKAGE_READY
# ---------------------------------------------------------------------

stage60_candidates = rows(
    "stage60_submission_package_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage60 = next(
    (
        r for r in stage60_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("package_outcome")).upper() == "PACKAGE_READY"
    ),
    None,
)

if stage60:
    stage60_run_id = str(stage60.get("id") or "")
    stage60_status = normalize_text(stage60.get("run_status")).upper()
    stage60_outcome = normalize_text(stage60.get("package_outcome")).upper()
    stage60_run_fingerprint = normalize_text(stage60.get("run_fingerprint"))
    stage60_package_sha256 = normalize_text(stage60.get("package_sha256"))

    stage59_run_id = str(stage60.get("stage59_run_id") or "")
    stage57_run_id = str(stage60.get("stage57_run_id") or "")
    stage58_run_id = str(stage60.get("stage58_run_id") or "")
    stage56_run_id = str(stage60.get("stage56_run_id") or "")
else:
    stage60_run_id = ""
    stage60_status = "MISSING"
    stage60_outcome = "MISSING"
    stage60_run_fingerprint = ""
    stage60_package_sha256 = ""

    stage59_run_id = ""
    stage57_run_id = ""
    stage58_run_id = ""
    stage56_run_id = ""


# ---------------------------------------------------------------------
# Load Stage 60 persisted package content
# ---------------------------------------------------------------------

stage60_sections = rows(
    "stage60_submission_package_sections",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage60_run_id": stage60_run_id,
    },
    "section_order",
    1000,
) if stage60_run_id else []

stage60_limitations = rows(
    "stage60_submission_package_limitations",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage60_run_id": stage60_run_id,
    },
    "limitation_order",
    1000,
) if stage60_run_id else []


# ---------------------------------------------------------------------
# Load Stage 59 for binding verification
# ---------------------------------------------------------------------

stage59_candidates = rows(
    "stage59_submission_readiness_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage59_run_id else []

stage59 = next(
    (r for r in stage59_candidates if str(r.get("id") or "") == stage59_run_id),
    None,
)


# ---------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------

stage60_run_payload = as_dict(stage60.get("run_payload")) if stage60 else {}
recomputed_stage60_run_fingerprint = (
    stable_sha256(stage60_run_payload) if stage60_run_payload else ""
)

stage60_manifest = as_dict(stage60.get("manifest")) if stage60 else {}
recomputed_stage60_package_sha256 = (
    stable_sha256(stage60_manifest) if stage60_manifest else ""
)

stage59_readiness_outcome = (
    normalize_text(stage59.get("readiness_outcome")).upper()
    if stage59 else "MISSING"
)

section_sha_ok = bool(stage60_sections) and all(
    normalize_text(s.get("final_text_sha256"))
    == text_sha256(normalize_text(s.get("final_text")))
    for s in stage60_sections
)

section_source_sha_present = bool(stage60_sections) and all(
    bool(normalize_text(s.get("source_corrected_draft_sha256")))
    for s in stage60_sections
)

all_stage57_section_verdicts_pass = bool(stage60_sections) and all(
    normalize_text(s.get("stage57_section_verdict")).upper() == "PASS"
    for s in stage60_sections
)


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})


add_check(
    "ACTIVE lock",
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
    normalize_text(lock.get("lock_status")).upper(),
)

add_check(
    "Workflow allowed",
    workflow_allowed,
    f"workflow_allowed={workflow_allowed}",
)

add_check(
    "Deadline valid",
    future_deadline(deadline),
    str(deadline or "")[:10],
)

add_check(
    "Stage 60 exists",
    bool(stage60),
    stage60_run_id or "MISSING",
)

add_check(
    "Stage 60 COMPLETED",
    stage60_status == "COMPLETED",
    stage60_status,
)

add_check(
    "Stage 60 PACKAGE_READY",
    stage60_outcome == "PACKAGE_READY",
    stage60_outcome,
)

add_check(
    "Stage 60 run fingerprint stable",
    bool(stage60_run_fingerprint)
    and stage60_run_fingerprint == recomputed_stage60_run_fingerprint,
    f"stored={stage60_run_fingerprint[:16]}..., recomputed={recomputed_stage60_run_fingerprint[:16]}...",
)

add_check(
    "Stage 60 package SHA256 stable",
    bool(stage60_package_sha256)
    and stage60_package_sha256 == recomputed_stage60_package_sha256,
    f"stored={stage60_package_sha256[:16]}..., recomputed={recomputed_stage60_package_sha256[:16]}...",
)

add_check(
    "Stage 60 package sections exist",
    bool(stage60_sections),
    f"sections={len(stage60_sections)}",
)

add_check(
    "Stage 60 final section SHA256 stable",
    section_sha_ok,
    f"sha_ok={section_sha_ok}",
)

add_check(
    "Stage 60 corrected-source SHA256 retained",
    section_source_sha_present,
    f"source_sha_present={section_source_sha_present}",
)

add_check(
    "Stage 60 section verdicts are PASS",
    all_stage57_section_verdicts_pass,
    f"sections={len(stage60_sections)}",
)

add_check(
    "Stage 59 bound run exists",
    bool(stage59),
    stage59_run_id or "MISSING",
)

add_check(
    "Stage 59 READY_FOR_SUBMISSION_PREP",
    stage59_readiness_outcome == "READY_FOR_SUBMISSION_PREP",
    stage59_readiness_outcome,
)

stage61_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Stage 60 package is stable, immutable, and bound to a valid Stage 59 readiness decision."
    if stage61_gate == "READY"
    else "Stage 61 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Approval package / fingerprint
# ---------------------------------------------------------------------

approval_basis = {
    "stage": 61,
    "approval_contract": "stage61-v1.0-human-approval",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage60_run_id": stage60_run_id,
    "stage60_run_fingerprint": stage60_run_fingerprint,
    "stage60_package_sha256": stage60_package_sha256,
    "stage59_run_id": stage59_run_id,

    "package_sections": [
        {
            "section_key": normalize_text(s.get("section_key")),
            "section_title": normalize_text(s.get("section_title")),
            "final_text_sha256": normalize_text(s.get("final_text_sha256")),
            "source_corrected_draft_sha256": normalize_text(s.get("source_corrected_draft_sha256")),
            "stage57_section_verdict": normalize_text(s.get("stage57_section_verdict")).upper(),
            "stage57_audit_sha256": normalize_text(s.get("stage57_audit_sha256")),
        }
        for s in stage60_sections
    ],

    "limitations": [
        normalize_text(i.get("limitation_text"))
        for i in stage60_limitations
    ],

    "stage61_gate": stage61_gate,
}

stage61_run_fingerprint = stable_sha256(approval_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage61():
    data = (
        supabase.table("stage61_human_approval_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage60_run_id", stage60_run_id)
        .eq("run_fingerprint", stage61_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_human_decision(
    decision: str,
    approval_phrase: str,
    approval_note: str,
    acknowledged_package: bool,
    acknowledged_limitations: bool,
    acknowledged_no_submission: bool,
):
    decision = normalize_text(decision).upper()
    approval_phrase = normalize_text(approval_phrase)
    approval_note = normalize_text(approval_note)

    if stage61_gate != "READY":
        raise RuntimeError("Stage 61 is BLOCKED.")

    if decision not in {"APPROVE", "REJECT"}:
        raise RuntimeError("Invalid decision.")

    if decision == "APPROVE":
        if not acknowledged_package:
            raise RuntimeError("Package acknowledgement is required.")
        if not acknowledged_limitations:
            raise RuntimeError("Limitations acknowledgement is required.")
        if not acknowledged_no_submission:
            raise RuntimeError("No-external-submission acknowledgement is required.")
        if approval_phrase != "APPROVE STAGE 61":
            raise RuntimeError('Type exactly: APPROVE STAGE 61')

        outcome = "APPROVED_FOR_SUBMISSION_HANDOFF"
    else:
        outcome = "REJECTED_BY_USER"

    decision_payload = {
        "decision": decision,
        "outcome": outcome,
        "approval_phrase": approval_phrase if decision == "APPROVE" else None,
        "approval_note": approval_note or None,

        "acknowledged_package": bool(acknowledged_package),
        "acknowledged_limitations": bool(acknowledged_limitations),
        "acknowledged_no_submission": bool(acknowledged_no_submission),

        "stage60_run_id": stage60_run_id,
        "stage60_package_sha256": stage60_package_sha256,
        "stage61_run_fingerprint": stage61_run_fingerprint,
    }

    approval_fingerprint = stable_sha256(decision_payload)

    existing = load_existing_stage61()
    if existing:
        current_outcome = normalize_text(existing.get("approval_outcome")).upper()
        if current_outcome in {"APPROVED_FOR_SUBMISSION_HANDOFF", "REJECTED_BY_USER"}:
            return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage57_run_id": stage57_run_id,
        "stage58_run_id": stage58_run_id or None,
        "stage59_run_id": stage59_run_id,
        "stage60_run_id": stage60_run_id,

        "stage": 61,
        "approval_version": "stage61-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage60_run_fingerprint": stage60_run_fingerprint,
        "stage60_package_sha256": stage60_package_sha256,

        "run_status": "COMPLETED",
        "approval_outcome": outcome,

        "human_decision": decision,
        "approval_note": approval_note or None,

        "acknowledged_package": bool(acknowledged_package),
        "acknowledged_limitations": bool(acknowledged_limitations),
        "acknowledged_no_submission": bool(acknowledged_no_submission),

        "run_fingerprint": stage61_run_fingerprint,
        "approval_fingerprint": approval_fingerprint,

        "run_payload": approval_basis,
        "decision_payload": decision_payload,

        "decided_at": now_iso(),
        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = (
        supabase.table("stage61_human_approval_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 61 decision.")

    return data[0]


existing_stage61 = load_existing_stage61()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 60 → Stage 61 approval binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 60", stage60_outcome)
m2.metric("Sections", len(stage60_sections))
m3.metric("Limitations", len(stage60_limitations))
m4.metric("Integrity", "VERIFIED" if stage61_gate == "READY" else "FAILED")

with st.expander("Stage 61 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.write(f"**Gate:** `{stage61_gate}`")
st.write(f"**Reason:** {gate_reason}")
st.write(f"**Stage 60 package SHA256:** `{stage60_package_sha256}`")
st.write(f"**Stage 61 run fingerprint:** `{stage61_run_fingerprint}`")


st.divider()
st.subheader("Package review")

if stage60_sections:
    st.dataframe(
        [
            {
                "Section": s.get("section_title") or s.get("section_key"),
                "Stage 57 verdict": s.get("stage57_section_verdict"),
                "Final SHA256": normalize_text(s.get("final_text_sha256"))[:16] + "...",
                "Audit SHA256": normalize_text(s.get("stage57_audit_sha256"))[:16] + "...",
            }
            for s in stage60_sections
        ],
        use_container_width=True,
        hide_index=True,
    )

    selected_section = st.selectbox(
        "Review final section",
        [
            f"{s.get('section_title') or s.get('section_key')} — {normalize_text(s.get('final_text_sha256'))[:10]}"
            for s in stage60_sections
        ],
        key="stage61_section_review",
    )

    selected_index = [
        f"{s.get('section_title') or s.get('section_key')} — {normalize_text(s.get('final_text_sha256'))[:10]}"
        for s in stage60_sections
    ].index(selected_section)

    with st.expander("Final persisted text", expanded=False):
        st.write(stage60_sections[selected_index].get("final_text") or "")

st.subheader("Persisted limitations")

if stage60_limitations:
    for item in stage60_limitations:
        st.write(f"- {item.get('limitation_text')}")
else:
    st.info("No Stage 60 limitation rows found.")


st.divider()
st.subheader("Explicit human approval")

if existing_stage61:
    st.success(
        f"Stage 61 este deja persistată. Run ID: {existing_stage61.get('id')} — "
        f"Outcome: {existing_stage61.get('approval_outcome')}"
    )

    if normalize_text(existing_stage61.get("approval_outcome")).upper() == "APPROVED_FOR_SUBMISSION_HANDOFF":
        st.success(
            "Stage 61 APPROVED_FOR_SUBMISSION_HANDOFF. Human approval is persisted. "
            "A future Stage 62 may consume this handoff. No external submission has occurred."
        )
    elif normalize_text(existing_stage61.get("approval_outcome")).upper() == "REJECTED_BY_USER":
        st.warning("Stage 61 was rejected by the user. No submission handoff is authorized.")

else:
    st.warning(
        "Aprobarea de mai jos este o aprobare internă pentru handoff. "
        "Nu trimite proiectul pe portal."
    )

    acknowledged_package = st.checkbox(
        "I have reviewed the Stage 60 package and approve the exact persisted package SHA256.",
        key="stage61_ack_package",
    )

    acknowledged_limitations = st.checkbox(
        "I have reviewed and accept the persisted limitations and evidence provenance.",
        key="stage61_ack_limitations",
    )

    acknowledged_no_submission = st.checkbox(
        "I understand Stage 61 does not submit externally, sign declarations, or create financial commitments.",
        key="stage61_ack_no_submission",
    )

    approval_phrase = st.text_input(
        "Approval phrase",
        placeholder="Type exactly: APPROVE STAGE 61",
        key="stage61_phrase",
    )

    approval_note = st.text_area(
        "Optional approval note",
        key="stage61_note",
        placeholder="Optional internal note about the approval decision.",
    )

    c1, c2 = st.columns(2)

    with c1:
        if st.button(
            "✅ Approve controlled submission handoff",
            type="primary",
            use_container_width=True,
            key="stage61_approve",
            disabled=(stage61_gate != "READY"),
        ):
            try:
                saved = persist_human_decision(
                    "APPROVE",
                    approval_phrase,
                    approval_note,
                    acknowledged_package,
                    acknowledged_limitations,
                    acknowledged_no_submission,
                )
                st.success(
                    f"Stage 61 approved — Run ID {saved.get('id')} — "
                    f"Outcome {saved.get('approval_outcome')}"
                )
                st.rerun()
            except Exception as exc:
                st.error(
                    "Stage 61 approval failed. Rulează mai întâi SQL-ul Stage 61 în Supabase. "
                    f"{type(exc).__name__}: {str(exc)[:1800]}"
                )

    with c2:
        if st.button(
            "⛔ Reject handoff",
            use_container_width=True,
            key="stage61_reject",
            disabled=(stage61_gate != "READY"),
        ):
            try:
                saved = persist_human_decision(
                    "REJECT",
                    "",
                    approval_note,
                    acknowledged_package,
                    acknowledged_limitations,
                    acknowledged_no_submission,
                )
                st.warning(
                    f"Stage 61 rejected — Run ID {saved.get('id')} — "
                    f"Outcome {saved.get('approval_outcome')}"
                )
                st.rerun()
            except Exception as exc:
                st.error(
                    f"Stage 61 rejection failed: {type(exc).__name__}: {str(exc)[:1800]}"
                )

st.caption(
    "Invariantă Stage 61 v1.0: human approval is explicit, immutable and SHA256-bound to the exact Stage 60 package. "
    "APPROVED_FOR_SUBMISSION_HANDOFF is not evidence of external submission or European Commission receipt."
)
