import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import requests
import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 57 v1.1 — AI POST-UPDATE EVIDENCE REVALIDATION GATE
#
# Purpose:
#   Consume ONLY a persisted Stage 56 corrected draft and re-audit it
#   against the persisted evidence registry + Stage 55 resolutions.
#
# Core invariants:
#   - Stage 57 never audits the old Stage 51 text when a corrected Stage 56
#     draft exists.
#   - Stage 56 must be COMPLETED with DRAFT_UPDATED or NO_UPDATE_REQUIRED.
#   - Stage 56 run_fingerprint + update_fingerprint must be stable.
#   - The corrected draft SHA256 must match the persisted corrected text.
#   - REJECTED/REMOVED Stage 55 claims may not reappear as positive facts.
#   - CONFIRMED Stage 55 claims may appear only within their persisted
#     confirmed value/provenance.
#   - Any SUPPORTED factual claim requires a valid source_id from the
#     persisted Stage 52 source registry.
#   - Missing evidence remains NEEDS_EVIDENCE.
#   - Contradictions remain FAIL.
#   - Stage 57 does not submit externally.
#
# Global verdicts:
#   PASS
#   NEEDS_EVIDENCE
#   FAIL
#   BLOCKED
#
# Persistence:
#   stage57_revalidation_runs
#   stage57_revalidation_items
#   stage57_claim_audits
# =====================================================================

st.set_page_config(
    page_title="Stage 57 v1.1 — Post-Update Evidence Revalidation",
    page_icon="🔁",
    layout="wide",
)

st.title("🔁 Etapa 57 v1.1 — AI Post-Update Evidence Revalidation Gate")
st.caption(
    "Etapa 57 reauditează draftul corectat Stage 56. "
    "Nu permite ca afirmațiile respinse să reapară și nu transformă lipsa dovezilor în fapte confirmate."
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


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


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


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


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


def extract_response_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()

    texts = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"}:
                value = content.get("text")
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, dict) and isinstance(value.get("value"), str):
                    texts.append(value["value"])
    return "\n".join(texts).strip()


def extract_json_object(text: str) -> dict:
    text = normalize_text(text)
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}

    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


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
    st.error("Stage 57 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage57_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 57 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load latest valid Stage 56
# ---------------------------------------------------------------------

stage56_candidates = rows(
    "stage56_resolution_update_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)

stage56 = next(
    (
        r for r in stage56_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("update_outcome")).upper() in {"DRAFT_UPDATED", "NO_UPDATE_REQUIRED"}
    ),
    stage56_candidates[0] if stage56_candidates else None,
)

if stage56:
    stage56_run_id = str(stage56.get("id") or "")
    stage56_status = normalize_text(stage56.get("run_status")).upper()
    stage56_outcome = normalize_text(stage56.get("update_outcome")).upper()
    stage56_run_fingerprint = normalize_text(stage56.get("run_fingerprint"))
    stage56_update_fingerprint = normalize_text(stage56.get("update_fingerprint"))
    stage55_run_id = str(stage56.get("stage55_run_id") or "")
    stage54_run_id = str(stage56.get("stage54_run_id") or "")
    stage51_run_id = str(stage56.get("stage51_run_id") or "")
else:
    stage56_run_id = ""
    stage56_status = "MISSING"
    stage56_outcome = "MISSING"
    stage56_run_fingerprint = ""
    stage56_update_fingerprint = ""
    stage55_run_id = ""
    stage54_run_id = ""
    stage51_run_id = ""

stage56_corrected = rows(
    "stage56_corrected_drafts",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage56_run_id": stage56_run_id,
    },
    "created_at",
    500,
) if stage56_run_id else []


# ---------------------------------------------------------------------
# Load upstream Stage 55 / 54 / 52
# ---------------------------------------------------------------------

stage55_candidates = rows(
    "stage55_confirmation_resolution_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage55_run_id else []
stage55 = next((r for r in stage55_candidates if str(r.get("id") or "") == stage55_run_id), None)

stage55_items = rows(
    "stage55_confirmation_resolution_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage55_run_id": stage55_run_id,
    },
    "claim_no",
    1000,
) if stage55_run_id else []

stage54_candidates = rows(
    "stage54_final_readiness_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage54_run_id else []
stage54 = next((r for r in stage54_candidates if str(r.get("id") or "") == stage54_run_id), None)

stage52_run_id = str(stage55.get("stage52_run_id") or "") if stage55 else ""

stage52_candidates = rows(
    "stage52_validation_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage52_run_id else []
stage52 = next((r for r in stage52_candidates if str(r.get("id") or "") == stage52_run_id), None)

base_source_registry = as_dict(stage52.get("source_registry")) if stage52 else {}

# ---------------------------------------------------------------------
# Optional Stage 58 re-audit handoff
# ---------------------------------------------------------------------
# Stage 58 points to the PRIOR Stage 57 run whose NEEDS_EVIDENCE claims
# were resolved. Stage 57 v1.1 must never delete or overwrite that prior
# audit trail. Instead, a READY_FOR_STAGE57_REAUDIT Stage 58 run creates
# a NEW Stage 57 fingerprint/run generation.

stage58_candidates = rows(
    "stage58_evidence_gap_resolution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage56_run_id": stage56_run_id,
    },
    "created_at",
    100,
)

stage58 = next(
    (
        r for r in stage58_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("resolution_outcome")).upper() == "READY_FOR_STAGE57_REAUDIT"
        and normalize_text(r.get("result_fingerprint"))
    ),
    None,
)

if stage58:
    stage58_run_id = str(stage58.get("id") or "")
    parent_stage57_run_id = str(stage58.get("stage57_run_id") or "")
    stage58_result_fingerprint = normalize_text(stage58.get("result_fingerprint"))
    stage58_result_payload = as_dict(stage58.get("result_payload"))
    recomputed_stage58_result_fingerprint = (
        stable_sha256(stage58_result_payload) if stage58_result_payload else ""
    )
    stage58_resolution_outcome = normalize_text(stage58.get("resolution_outcome")).upper()
    stage58_items = rows(
        "stage58_evidence_gap_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage58_run_id": stage58_run_id,
        },
        "claim_no",
        5000,
    )
else:
    stage58_run_id = ""
    parent_stage57_run_id = ""
    stage58_result_fingerprint = ""
    stage58_result_payload = {}
    recomputed_stage58_result_fingerprint = ""
    stage58_resolution_outcome = "NONE"
    stage58_items = []

# Build an effective registry for this Stage 57 generation.
# Persisted Stage 52 source IDs are retained. Stage 58 user-provided
# evidence is added as traceable synthetic source entries using the exact
# user58:* IDs that Stage 58 persisted.
effective_source_registry = dict(base_source_registry)

for item in stage58_items:
    if normalize_text(item.get("resolution_status")).upper() != "RESOLVED":
        continue

    basis = normalize_text(item.get("resolution_basis")).upper()
    for source_id in as_list(item.get("resolved_source_ids")):
        source_id = normalize_text(source_id)
        if not source_id:
            continue

        if source_id in effective_source_registry:
            # Existing Stage 52 source; keep the canonical record.
            continue

        if basis == "USER_PROVIDED_EVIDENCE" and source_id.startswith("user58:"):
            effective_source_registry[source_id] = {
                "source_id": source_id,
                "source_type": "USER_PROVIDED_EVIDENCE",
                "stage": 58,
                "stage58_run_id": stage58_run_id,
                "stage58_item_id": str(item.get("id") or ""),
                "section_key": normalize_text(item.get("section_key")),
                "claim_no": int(item.get("claim_no") or 0),
                "claim_text": normalize_text(item.get("claim_text")),
                "evidence_label": normalize_text(item.get("user_evidence_label")),
                "confirmed_value": normalize_text(item.get("resolution_value")),
                "provenance_note": normalize_text(item.get("resolution_note")),
                "resolution_basis": basis,
            }

# Stage 58 resolutions are supplied separately to the auditor as a
# downstream evidence-resolution overlay. They do not rewrite Stage 55.
stage58_resolution_overlay = [
    {
        "stage58_item_id": str(item.get("id") or ""),
        "section_key": normalize_text(item.get("section_key")),
        "claim_no": int(item.get("claim_no") or 0),
        "claim_text": normalize_text(item.get("claim_text")),
        "resolution_status": normalize_text(item.get("resolution_status")).upper(),
        "resolution_basis": normalize_text(item.get("resolution_basis")).upper(),
        "resolved_source_ids": as_list(item.get("resolved_source_ids")),
        "resolution_value": normalize_text(item.get("resolution_value")),
        "resolution_note": normalize_text(item.get("resolution_note")),
        "requires_draft_update": bool(item.get("requires_draft_update")),
    }
    for item in stage58_items
]


# ---------------------------------------------------------------------
# Recompute fingerprints
# ---------------------------------------------------------------------

stage56_run_payload = as_dict(stage56.get("run_payload")) if stage56 else {}
recomputed_stage56_run_fingerprint = stable_sha256(stage56_run_payload) if stage56_run_payload else ""

stage56_update_payload = as_dict(stage56.get("update_payload")) if stage56 else {}
recomputed_stage56_update_fingerprint = stable_sha256(stage56_update_payload) if stage56_update_payload else ""

stage55_resolution_payload = as_dict(stage55.get("resolution_payload")) if stage55 else {}
stored_stage55_resolution_fingerprint = normalize_text(stage55.get("resolution_fingerprint")) if stage55 else ""
recomputed_stage55_resolution_fingerprint = (
    stable_sha256(stage55_resolution_payload) if stage55_resolution_payload else ""
)

stage54_payload = as_dict(stage54.get("readiness_payload")) if stage54 else {}
stored_stage54_fingerprint = normalize_text(stage54.get("readiness_fingerprint")) if stage54 else ""
recomputed_stage54_fingerprint = stable_sha256(stage54_payload) if stage54_payload else ""


# ---------------------------------------------------------------------
# Corrected draft integrity
# ---------------------------------------------------------------------

corrected_sha_ok = bool(stage56_corrected) and all(
    normalize_text(i.get("corrected_draft_sha256"))
    == text_sha256(normalize_text(i.get("corrected_text")))
    for i in stage56_corrected
)

source_sha_present = bool(stage56_corrected) and all(
    bool(normalize_text(i.get("source_draft_sha256")))
    for i in stage56_corrected
)

stage55_open = [
    i for i in stage55_items
    if normalize_text(i.get("resolution_status")).upper() == "OPEN"
]

stage55_rejected = [
    i for i in stage55_items
    if normalize_text(i.get("resolution_status")).upper() == "REJECTED"
]

stage55_removed = [
    i for i in stage55_items
    if normalize_text(i.get("resolution_status")).upper() == "REMOVED"
]

stage55_confirmed = [
    i for i in stage55_items
    if normalize_text(i.get("resolution_status")).upper() == "CONFIRMED"
]


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
    "Stage 56 run exists",
    bool(stage56),
    stage56_run_id or "MISSING",
)

add_check(
    "Stage 56 COMPLETED",
    stage56_status == "COMPLETED",
    stage56_status,
)

add_check(
    "Stage 56 outcome valid",
    stage56_outcome in {"DRAFT_UPDATED", "NO_UPDATE_REQUIRED"},
    stage56_outcome,
)

add_check(
    "Stage 56 run fingerprint stable",
    bool(stage56_run_fingerprint)
    and stage56_run_fingerprint == recomputed_stage56_run_fingerprint,
    f"stored={stage56_run_fingerprint[:16]}..., recomputed={recomputed_stage56_run_fingerprint[:16]}...",
)

add_check(
    "Stage 56 update fingerprint stable",
    bool(stage56_update_fingerprint)
    and stage56_update_fingerprint == recomputed_stage56_update_fingerprint,
    f"stored={stage56_update_fingerprint[:16]}..., recomputed={recomputed_stage56_update_fingerprint[:16]}...",
)

add_check(
    "Stage 55 bound run exists",
    bool(stage55),
    stage55_run_id or "MISSING",
)

add_check(
    "Stage 55 resolution fingerprint stable",
    bool(stored_stage55_resolution_fingerprint)
    and stored_stage55_resolution_fingerprint == recomputed_stage55_resolution_fingerprint,
    f"stored={stored_stage55_resolution_fingerprint[:16]}..., recomputed={recomputed_stage55_resolution_fingerprint[:16]}...",
)

add_check(
    "Stage 55 open items = 0",
    len(stage55_open) == 0,
    f"open={len(stage55_open)}",
)

if stage58:
    add_check(
        "Stage 58 READY_FOR_STAGE57_REAUDIT",
        stage58_resolution_outcome == "READY_FOR_STAGE57_REAUDIT",
        stage58_resolution_outcome,
    )
    add_check(
        "Stage 58 result fingerprint stable",
        bool(stage58_result_fingerprint)
        and stage58_result_fingerprint == recomputed_stage58_result_fingerprint,
        f"stored={stage58_result_fingerprint[:16]}..., recomputed={recomputed_stage58_result_fingerprint[:16]}...",
    )
    add_check(
        "Stage 58 has zero open gaps",
        all(
            normalize_text(i.get("resolution_status")).upper() != "OPEN"
            for i in stage58_items
        ),
        f"items={len(stage58_items)}",
    )
    add_check(
        "Stage 58 requires no draft update",
        not any(bool(i.get("requires_draft_update")) for i in stage58_items),
        f"requires_update={sum(1 for i in stage58_items if bool(i.get('requires_draft_update')))}",
    )

add_check(
    "Stage 54 bound run exists",
    bool(stage54),
    stage54_run_id or "MISSING",
)

add_check(
    "Stage 54 fingerprint stable",
    bool(stored_stage54_fingerprint)
    and stored_stage54_fingerprint == recomputed_stage54_fingerprint,
    f"stored={stored_stage54_fingerprint[:16]}..., recomputed={recomputed_stage54_fingerprint[:16]}...",
)

add_check(
    "Corrected drafts exist",
    len(stage56_corrected) > 0 or stage56_outcome == "NO_UPDATE_REQUIRED",
    f"corrected={len(stage56_corrected)}",
)

add_check(
    "Corrected draft SHA256 stable",
    corrected_sha_ok or stage56_outcome == "NO_UPDATE_REQUIRED",
    f"sha_ok={corrected_sha_ok}",
)

add_check(
    "Source draft SHA256 retained",
    source_sha_present or stage56_outcome == "NO_UPDATE_REQUIRED",
    f"source_sha_present={source_sha_present}",
)

stage57_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Stage 56 corrected draft chain and Stage 55 resolutions are stable."
    if stage57_gate == "READY"
    else "Stage 57 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Stage 57 run fingerprint
# ---------------------------------------------------------------------

run_basis = {
    "stage": 57,
    "fingerprint_contract": "stage57-v1.1-stage58-reaudit",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage52_run_id": stage52_run_id,
    "stage54_run_id": stage54_run_id,
    "stage55_run_id": stage55_run_id,
    "stage56_run_id": stage56_run_id,
    "parent_stage57_run_id": parent_stage57_run_id or None,
    "stage58_run_id": stage58_run_id or None,

    "stage54_readiness_fingerprint": stored_stage54_fingerprint,
    "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
    "stage56_run_fingerprint": stage56_run_fingerprint,
    "stage56_update_fingerprint": stage56_update_fingerprint,
    "stage58_result_fingerprint": stage58_result_fingerprint or None,

    "corrected_inventory": [
        {
            "id": str(i.get("id") or ""),
            "section_key": normalize_text(i.get("section_key")),
            "source_draft_sha256": normalize_text(i.get("source_draft_sha256")),
            "corrected_draft_sha256": normalize_text(i.get("corrected_draft_sha256")),
            "item_fingerprint": normalize_text(i.get("item_fingerprint")),
        }
        for i in sorted(stage56_corrected, key=lambda x: normalize_text(x.get("section_key")))
    ],

    "stage55_resolution_inventory": [
        {
            "id": str(i.get("id") or ""),
            "section_key": normalize_text(i.get("section_key")),
            "claim_no": int(i.get("claim_no") or 0),
            "resolution_status": normalize_text(i.get("resolution_status")).upper(),
            "resolution_basis": normalize_text(i.get("resolution_basis")).upper(),
            "resolution_value": normalize_text(i.get("resolution_value")),
            "resolution_note": normalize_text(i.get("resolution_note")),
            "resolved_source_ids": as_list(i.get("resolved_source_ids")),
        }
        for i in stage55_items
    ],

    "source_registry_fingerprint": stable_sha256(effective_source_registry),
    "stage58_resolution_overlay_fingerprint": (
        stable_sha256(stage58_resolution_overlay) if stage58_resolution_overlay else None
    ),
    "stage57_gate": stage57_gate,
}

stage57_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# AI re-audit
# ---------------------------------------------------------------------

def audit_corrected_section(corrected_row: dict, section_resolutions: list) -> dict:
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")
    if not model:
        raise RuntimeError("OPENAI_MODEL is missing.")

    corrected_text = normalize_text(corrected_row.get("corrected_text"))

    rules = (
        "You are a fail-closed evidence auditor for a corrected Horizon Europe proposal draft. "
        "Audit ONLY the provided corrected text against the provided effective source registry, persisted Stage 55 resolutions, and any Stage 58 evidence-resolution overlay. "
        "Do not infer missing facts. "
        "Every factual claim must be classified as SUPPORTED, NEEDS_EVIDENCE, CONTRADICTED, or RESOLUTION_COMPLIANT. "
        "SUPPORTED requires one or more exact valid source_ids from the effective source registry. Stage 58 user-provided evidence may support only the exact factual value it records and must not be expanded into broader eligibility, consortium, financial, or technical conclusions. "
        "RESOLUTION_COMPLIANT is allowed only when the claim is directly constrained by a Stage 55 CONFIRMED/REJECTED/REMOVED resolution. "
        "A REJECTED or REMOVED claim may not reappear as a positive factual assertion. "
        "If it reappears positively, classify CONTRADICTED with violation_type=REJECTED_FACT_REINTRODUCED. "
        "A CONFIRMED claim may not be expanded beyond its exact persisted confirmed value/provenance. "
        "If expanded, classify NEEDS_EVIDENCE. "
        "Return strict JSON with claims and section_verdict. "
        "section_verdict must be PASS, NEEDS_EVIDENCE, or FAIL."
    )

    request_payload = {
        "section_key": corrected_row.get("section_key"),
        "section_title": corrected_row.get("section_title"),
        "corrected_text": corrected_text,
        "source_registry": effective_source_registry,
        "stage58_resolution_overlay": [
            r for r in stage58_resolution_overlay
            if normalize_text(r.get("section_key")) == normalize_text(corrected_row.get("section_key"))
        ],
        "stage55_resolutions": [
            {
                "claim_no": int(r.get("claim_no") or 0),
                "claim_text": normalize_text(r.get("claim_text")),
                "resolution_status": normalize_text(r.get("resolution_status")).upper(),
                "resolution_basis": normalize_text(r.get("resolution_basis")).upper(),
                "resolution_value": normalize_text(r.get("resolution_value")),
                "resolution_note": normalize_text(r.get("resolution_note")),
                "resolved_source_ids": as_list(r.get("resolved_source_ids")),
            }
            for r in section_resolutions
        ],
        "required_output": {
            "section_verdict": "PASS|NEEDS_EVIDENCE|FAIL",
            "summary": "short string",
            "claims": [
                {
                    "claim_no": 1,
                    "claim_text": "string",
                    "classification": "SUPPORTED|NEEDS_EVIDENCE|CONTRADICTED|RESOLUTION_COMPLIANT",
                    "source_ids": ["valid source registry ids only"],
                    "related_stage55_claim_no": 0,
                    "violation_type": "NONE|REJECTED_FACT_REINTRODUCED|REMOVED_FACT_REINTRODUCED|CONFIRMED_SCOPE_EXPANSION|UNSUPPORTED_FACT|CONTRADICTION",
                    "reason": "string",
                }
            ],
        },
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": [
                {"role": "system", "content": rules},
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False, default=str)},
            ],
        },
        timeout=120,
    )

    try:
        raw = response.json()
    except Exception:
        raw = {"raw_text": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API HTTP {response.status_code}: {normalize_text(raw)[:1800]}")

    parsed = extract_json_object(extract_response_text(raw))
    if not parsed:
        raise RuntimeError("Revalidation model did not return valid JSON.")

    verdict = normalize_text(parsed.get("section_verdict")).upper()
    if verdict not in {"PASS", "NEEDS_EVIDENCE", "FAIL"}:
        verdict = "NEEDS_EVIDENCE"

    claims = []
    valid_classifications = {"SUPPORTED", "NEEDS_EVIDENCE", "CONTRADICTED", "RESOLUTION_COMPLIANT"}
    valid_violations = {
        "NONE",
        "REJECTED_FACT_REINTRODUCED",
        "REMOVED_FACT_REINTRODUCED",
        "CONFIRMED_SCOPE_EXPANSION",
        "UNSUPPORTED_FACT",
        "CONTRADICTION",
    }

    for idx, claim in enumerate(parsed.get("claims") or [], start=1):
        if not isinstance(claim, dict):
            continue

        classification = normalize_text(claim.get("classification")).upper()
        if classification not in valid_classifications:
            classification = "NEEDS_EVIDENCE"

        source_ids = [sid for sid in as_list(claim.get("source_ids")) if sid in effective_source_registry]

        if classification == "SUPPORTED" and not source_ids:
            classification = "NEEDS_EVIDENCE"

        violation = normalize_text(claim.get("violation_type")).upper() or "NONE"
        if violation not in valid_violations:
            violation = "NONE"

        claims.append({
            "claim_no": int(claim.get("claim_no") or idx),
            "claim_text": normalize_text(claim.get("claim_text")),
            "classification": classification,
            "source_ids": source_ids,
            "related_stage55_claim_no": int(claim.get("related_stage55_claim_no") or 0),
            "violation_type": violation,
            "reason": normalize_text(claim.get("reason")),
        })

    # Deterministic fail-closed section verdict override
    if any(c["classification"] == "CONTRADICTED" for c in claims):
        verdict = "FAIL"
    elif any(c["classification"] == "NEEDS_EVIDENCE" for c in claims) and verdict == "PASS":
        verdict = "NEEDS_EVIDENCE"

    return {
        "section_verdict": verdict,
        "summary": normalize_text(parsed.get("summary")),
        "claims": claims,
        "model": model,
        "response_id": raw.get("id"),
        "response_payload": raw,
    }


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage57():
    if not stage57_run_fingerprint:
        return None

    data = (
        supabase.table("stage57_revalidation_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage56_run_id", stage56_run_id)
        .eq("run_fingerprint", stage57_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def initialize_stage57():
    if stage57_gate != "READY":
        raise RuntimeError("Stage 57 is BLOCKED.")

    existing = load_existing_stage57()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage52_run_id": stage52_run_id,
        "stage54_run_id": stage54_run_id,
        "stage55_run_id": stage55_run_id,
        "stage56_run_id": stage56_run_id,
        "parent_stage57_run_id": parent_stage57_run_id or None,
        "stage58_run_id": stage58_run_id or None,
        "stage58_result_fingerprint": stage58_result_fingerprint or None,
        "revalidation_generation": 1 if stage58 else 0,

        "stage": 57,
        "validator_version": "stage57-v1.1",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage54_readiness_fingerprint": stored_stage54_fingerprint,
        "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
        "stage56_run_fingerprint": stage56_run_fingerprint,
        "stage56_update_fingerprint": stage56_update_fingerprint,

        "run_status": "INITIALIZED",
        "global_verdict": "PENDING",

        "section_count": len(stage56_corrected),
        "audited_sections": 0,

        "supported_claims": 0,
        "needs_evidence_claims": 0,
        "contradicted_claims": 0,
        "resolution_compliant_claims": 0,

        "run_fingerprint": stage57_run_fingerprint,
        "source_registry": effective_source_registry,
        "run_payload": run_basis,

        "initialized_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = supabase.table("stage57_revalidation_runs").insert(payload).execute().data or []
    if not data:
        raise RuntimeError("Could not create Stage 57 run.")
    return data[0]


def persist_section_audit(run_id: str, corrected_row: dict, result: dict):
    section_key = normalize_text(corrected_row.get("section_key"))

    audit_basis = {
        "stage57_run_id": run_id,
        "stage56_corrected_draft_id": str(corrected_row.get("id") or ""),
        "section_key": section_key,
        "corrected_draft_sha256": normalize_text(corrected_row.get("corrected_draft_sha256")),
        "section_verdict": result["section_verdict"],
        "claims": result["claims"],
    }
    audit_sha256 = stable_sha256(audit_basis)

    existing = (
        supabase.table("stage57_revalidation_items")
        .select("id")
        .eq("user_id", user_id)
        .eq("stage57_run_id", run_id)
        .eq("section_key", section_key)
        .limit(1)
        .execute()
    ).data or []

    payload = {
        "stage57_run_id": run_id,
        "stage56_run_id": stage56_run_id,
        "stage56_corrected_draft_id": corrected_row.get("id"),

        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "section_key": section_key,
        "section_title": corrected_row.get("section_title"),

        "corrected_draft_sha256": corrected_row.get("corrected_draft_sha256"),
        "section_verdict": result["section_verdict"],
        "summary": result["summary"],

        "supported_count": sum(1 for c in result["claims"] if c["classification"] == "SUPPORTED"),
        "needs_evidence_count": sum(1 for c in result["claims"] if c["classification"] == "NEEDS_EVIDENCE"),
        "contradicted_count": sum(1 for c in result["claims"] if c["classification"] == "CONTRADICTED"),
        "resolution_compliant_count": sum(1 for c in result["claims"] if c["classification"] == "RESOLUTION_COMPLIANT"),

        "audit_sha256": audit_sha256,

        "model_name": result.get("model"),
        "response_id": result.get("response_id"),
        "response_payload": result.get("response_payload") or {},

        "updated_at": now_iso(),
    }

    if existing:
        data = (
            supabase.table("stage57_revalidation_items")
            .update(payload)
            .eq("id", existing[0]["id"])
            .eq("user_id", user_id)
            .execute()
        ).data or []
        item_row = data[0] if data else {**payload, "id": existing[0]["id"]}
    else:
        payload["created_at"] = now_iso()
        data = supabase.table("stage57_revalidation_items").insert(payload).execute().data or []
        if not data:
            raise RuntimeError(f"Could not persist Stage 57 section audit for {section_key}.")
        item_row = data[0]

    item_id = item_row["id"]

    # IMPORTANT: do not delete prior claim rows.
    # Stage 58 may hold foreign keys to them. Preserve IDs and update
    # matching claim numbers in place; insert only genuinely new claims.
    existing_claim_rows = (
        supabase.table("stage57_claim_audits")
        .select("*")
        .eq("user_id", user_id)
        .eq("stage57_run_id", run_id)
        .eq("stage57_revalidation_item_id", item_id)
        .execute()
    ).data or []

    existing_claims_by_no = {
        int(row.get("claim_no") or 0): row
        for row in existing_claim_rows
    }

    for claim in result["claims"]:
        claim_payload = {
            "stage57_run_id": run_id,
            "stage57_revalidation_item_id": item_id,
            "stage56_corrected_draft_id": corrected_row.get("id"),

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "section_key": section_key,
            "claim_no": claim["claim_no"],
            "claim_text": claim["claim_text"],

            "classification": claim["classification"],
            "source_ids": claim["source_ids"],

            "related_stage55_claim_no": claim["related_stage55_claim_no"] or None,
            "violation_type": claim["violation_type"],
            "reason": claim["reason"],

            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        existing_claim = existing_claims_by_no.get(int(claim["claim_no"]))

        if existing_claim:
            update_payload = dict(claim_payload)
            update_payload.pop("created_at", None)

            (
                supabase.table("stage57_claim_audits")
                .update(update_payload)
                .eq("id", existing_claim["id"])
                .eq("user_id", user_id)
                .execute()
            )
        else:
            supabase.table("stage57_claim_audits").insert(claim_payload).execute()

    return item_row


def recompute_stage57_global(run_id: str):
    items = rows(
        "stage57_revalidation_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage57_run_id": run_id,
        },
        "created_at",
        500,
    )

    claims = rows(
        "stage57_claim_audits",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage57_run_id": run_id,
        },
        "claim_no",
        5000,
    )

    expected_sections = {
        normalize_text(i.get("section_key"))
        for i in stage56_corrected
    }
    audited_sections = {
        normalize_text(i.get("section_key"))
        for i in items
    }

    contradicted = sum(1 for c in claims if normalize_text(c.get("classification")).upper() == "CONTRADICTED")
    needs = sum(1 for c in claims if normalize_text(c.get("classification")).upper() == "NEEDS_EVIDENCE")
    supported = sum(1 for c in claims if normalize_text(c.get("classification")).upper() == "SUPPORTED")
    compliant = sum(1 for c in claims if normalize_text(c.get("classification")).upper() == "RESOLUTION_COMPLIANT")

    if expected_sections != audited_sections:
        verdict = "PENDING"
        run_status = "IN_PROGRESS"
    elif contradicted > 0 or any(normalize_text(i.get("section_verdict")).upper() == "FAIL" for i in items):
        verdict = "FAIL"
        run_status = "COMPLETED"
    elif needs > 0 or any(normalize_text(i.get("section_verdict")).upper() == "NEEDS_EVIDENCE" for i in items):
        verdict = "NEEDS_EVIDENCE"
        run_status = "COMPLETED"
    else:
        verdict = "PASS"
        run_status = "COMPLETED"

    result_basis = {
        "stage57_run_id": run_id,
        "stage56_run_id": stage56_run_id,
        "stage56_update_fingerprint": stage56_update_fingerprint,
        "items": [
            {
                "section_key": normalize_text(i.get("section_key")),
                "section_verdict": normalize_text(i.get("section_verdict")).upper(),
                "audit_sha256": normalize_text(i.get("audit_sha256")),
                "corrected_draft_sha256": normalize_text(i.get("corrected_draft_sha256")),
            }
            for i in sorted(items, key=lambda x: normalize_text(x.get("section_key")))
        ],
        "counts": {
            "supported": supported,
            "needs_evidence": needs,
            "contradicted": contradicted,
            "resolution_compliant": compliant,
        },
        "global_verdict": verdict,
    }

    result_fingerprint = stable_sha256(result_basis)

    (
        supabase.table("stage57_revalidation_runs")
        .update({
            "run_status": run_status,
            "global_verdict": verdict,

            "audited_sections": len(audited_sections),

            "supported_claims": supported,
            "needs_evidence_claims": needs,
            "contradicted_claims": contradicted,
            "resolution_compliant_claims": compliant,

            "result_fingerprint": result_fingerprint,
            "result_payload": result_basis,

            "completed_at": now_iso() if run_status == "COMPLETED" else None,
            "updated_at": now_iso(),
        })
        .eq("id", run_id)
        .eq("user_id", user_id)
        .execute()
    )

    return verdict, result_fingerprint


existing_stage57 = load_existing_stage57()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 56 → Stage 57 revalidation binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 56", stage56_outcome)
m2.metric("Corrected sections", len(stage56_corrected))
m3.metric("Stage 55 rejected/removed", len(stage55_rejected) + len(stage55_removed))
m4.metric("Integrity", "VERIFIED" if stage57_gate == "READY" else "FAILED")

with st.expander("Stage 57 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Stage 57 execution gate")

g1, g2, g3 = st.columns(3)
g1.metric("Gate", stage57_gate)
g2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
g3.metric("Evidence sources", len(effective_source_registry))

if stage57_gate == "READY":
    st.success("Etapa 57: READY. Post-update evidence revalidation poate începe.")
else:
    st.error("Etapa 57: BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage57_run_fingerprint, language=None)

if stage58:
    st.success(
        f"Stage 58 handoff detected — Run ID: {stage58_run_id} — "
        f"READY_FOR_STAGE57_REAUDIT. A new Stage 57 generation is used; "
        f"the prior Stage 57 audit remains immutable."
    )
    st.write(f"**Parent Stage 57 run:** `{parent_stage57_run_id}`")
    st.write(f"**Stage 58 result fingerprint:** `{stage58_result_fingerprint}`")

st.divider()
st.subheader("Stage 57 persistence")

if existing_stage57:
    st.success(
        f"Stage 57 este inițializată. Run ID: {existing_stage57.get('id')} — "
        f"Verdict: {existing_stage57.get('global_verdict')}"
    )
else:
    st.info("Inițializează Stage 57 înainte de revalidare.")

if st.button(
    (
        "🔁 Initialize Stage 57 re-audit after Stage 58"
        if stage58
        else "🔁 Initialize Stage 57 revalidation"
    ),
    type="primary",
    use_container_width=True,
    key="stage57_initialize",
    disabled=(stage57_gate != "READY"),
):
    try:
        saved = initialize_stage57()
        st.session_state["stage57_run_id"] = str(saved.get("id"))
        st.success(f"Stage 57 initialized — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 57 initialization failed. Rulează mai întâi SQL-ul Stage 57 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage57:
    st.divider()
    st.subheader("Corrected draft re-audit")

    corrected_map = {
        f"{i.get('section_title') or i.get('section_key')} — {normalize_text(i.get('corrected_draft_sha256'))[:10]}": i
        for i in stage56_corrected
    }

    if not corrected_map and stage56_outcome == "NO_UPDATE_REQUIRED":
        st.info("Stage 56 had NO_UPDATE_REQUIRED. Add unchanged-draft handoff handling before production use.")
    elif corrected_map:
        label = st.selectbox(
            "Corrected section to revalidate",
            list(corrected_map.keys()),
            key="stage57_section",
        )
        corrected_row = corrected_map[label]
        section_key = normalize_text(corrected_row.get("section_key"))

        section_resolutions = [
            i for i in stage55_items
            if normalize_text(i.get("section_key")) == section_key
        ]

        st.write(f"**Corrected SHA256:** `{normalize_text(corrected_row.get('corrected_draft_sha256'))}`")
        st.write(f"**Source SHA256:** `{normalize_text(corrected_row.get('source_draft_sha256'))}`")

        with st.expander("Corrected Stage 56 draft", expanded=False):
            st.write(corrected_row.get("corrected_text") or "")

        with st.expander("Stage 55 resolutions bound to this section", expanded=False):
            st.dataframe(
                [
                    {
                        "Claim #": r.get("claim_no"),
                        "Status": r.get("resolution_status"),
                        "Basis": r.get("resolution_basis"),
                        "Claim": r.get("claim_text"),
                        "Value": r.get("resolution_value"),
                        "Note": r.get("resolution_note"),
                    }
                    for r in section_resolutions
                ],
                use_container_width=True,
                hide_index=True,
            )

        existing_item = (
            supabase.table("stage57_revalidation_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("stage57_run_id", existing_stage57["id"])
            .eq("section_key", section_key)
            .limit(1)
            .execute()
        ).data or []

        if existing_item:
            item = existing_item[0]
            st.success(
                f"Existing Stage 57 section verdict: {item.get('section_verdict')} — "
                f"Audit SHA256 {normalize_text(item.get('audit_sha256'))[:16]}..."
            )

        ai_ready = bool(secret("OPENAI_API_KEY")) and bool(secret("OPENAI_MODEL"))
        if not ai_ready:
            st.warning("OPENAI_API_KEY și OPENAI_MODEL trebuie configurate în Streamlit Secrets.")

        if st.button(
            "🔎 Re-audit corrected draft against evidence & resolutions",
            type="primary",
            use_container_width=True,
            key="stage57_audit",
            disabled=(not ai_ready),
        ):
            try:
                with st.spinner("Revalidating corrected draft..."):
                    result = audit_corrected_section(corrected_row, section_resolutions)
                    persist_section_audit(
                        str(existing_stage57["id"]),
                        corrected_row,
                        result,
                    )
                    verdict, _ = recompute_stage57_global(str(existing_stage57["id"]))
                    st.success(
                        f"Section revalidated: {result['section_verdict']} — global verdict {verdict}"
                    )
                    st.rerun()
            except Exception as exc:
                st.error(
                    f"Stage 57 revalidation failed: {type(exc).__name__}: {str(exc)[:1800]}"
                )

    latest_run_data = (
        supabase.table("stage57_revalidation_runs")
        .select("*")
        .eq("id", existing_stage57["id"])
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    ).data or [existing_stage57]
    latest_run = latest_run_data[0]

    section_items = rows(
        "stage57_revalidation_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage57_run_id": str(existing_stage57["id"]),
        },
        "created_at",
        500,
    )

    st.divider()
    st.subheader("Stage 57 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Verdict", latest_run.get("global_verdict"))
    o2.metric("Audited sections", latest_run.get("audited_sections"))
    o3.metric("Needs evidence", latest_run.get("needs_evidence_claims"))
    o4.metric("Contradicted", latest_run.get("contradicted_claims"))

    if section_items:
        st.dataframe(
            [
                {
                    "Section": i.get("section_title"),
                    "Verdict": i.get("section_verdict"),
                    "Supported": i.get("supported_count"),
                    "Needs evidence": i.get("needs_evidence_count"),
                    "Contradicted": i.get("contradicted_count"),
                    "Resolution compliant": i.get("resolution_compliant_count"),
                    "Audit SHA256": normalize_text(i.get("audit_sha256"))[:16] + "...",
                }
                for i in section_items
            ],
            use_container_width=True,
            hide_index=True,
        )

    global_verdict = normalize_text(latest_run.get("global_verdict")).upper()

    if global_verdict == "PASS":
        st.success(
            "Stage 57 PASS. Corrected draft is revalidated after all applicable evidence overlays "
            "and may be handed to Stage 59 for submission-readiness finalization."
        )
    elif global_verdict == "NEEDS_EVIDENCE":
        st.warning(
            "Stage 57 NEEDS_EVIDENCE. Draftul corectat nu are contradicții, dar unele afirmații "
            "încă necesită dovezi explicite înainte de submission readiness."
        )
    elif global_verdict == "FAIL":
        st.error(
            "Stage 57 FAIL. Cel puțin o afirmație este contradictorie sau o rezoluție Stage 55 "
            "a fost încălcată. Draftul trebuie corectat din nou înainte de etapa următoare."
        )
    else:
        st.info("Stage 57 is still in progress.")

st.caption(
    "Invariantă Stage 57 v1.1: auditurile anterioare sunt append-only; Stage 58 nu poate fi pierdut prin DELETE; "
    "SUPPORTED necesită source_id valid din registrul efectiv; user-provided evidence susține numai valoarea exactă "
    "declarată; REJECTED/REMOVED nu pot reapărea ca fapte pozitive; orice contradicție produce FAIL."
)
