import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import requests
import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 58 v1.0 — AI REMAINING EVIDENCE GAP RESOLUTION WORKFLOW
#
# Purpose:
#   Consume ONLY a persisted Stage 57 NEEDS_EVIDENCE verdict and resolve
#   the remaining claim-level evidence gaps without inventing facts.
#
# Resolution channels:
#   1) PERSISTED_EVIDENCE
#      AI may resolve a gap only when it cites one or more valid source_ids
#      from the persisted Stage 57 / Stage 52 source registry.
#
#   2) USER_PROVIDED_EVIDENCE
#      The authenticated user may provide an explicit factual value + provenance
#      note. Stage 58 persists it as new user-supplied evidence metadata.
#
#   3) UNRESOLVED
#      Missing evidence remains OPEN.
#
# Outcomes:
#   NEEDS_EVIDENCE
#   READY_FOR_STAGE57_REAUDIT
#   REQUIRES_DRAFT_UPDATE
#   BLOCKED
#
# IMPORTANT:
#   Stage 58 does not mutate Stage 57 audit rows, Stage 56 corrected drafts,
#   or Stage 51 source drafts. It produces a separate persisted resolution
#   package for a subsequent Stage 57 re-audit pass.
# =====================================================================

st.set_page_config(
    page_title="Stage 58 v1.0 — Remaining Evidence Gap Resolution",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 Etapa 58 v1.0 — AI Remaining Evidence Gap Resolution")
st.caption(
    "Etapa 58 preia numai claims NEEDS_EVIDENCE din Stage 57, încearcă rezolvarea lor "
    "cu surse valide sau confirmare explicită și pregătește handoff-ul pentru re-audit."
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


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


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


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


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
    st.error("Stage 58 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage58_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 58 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 57 NEEDS_EVIDENCE
# ---------------------------------------------------------------------

stage57_candidates = rows(
    "stage57_revalidation_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)

stage57 = next(
    (
        r for r in stage57_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("global_verdict")).upper() == "NEEDS_EVIDENCE"
    ),
    stage57_candidates[0] if stage57_candidates else None,
)

if stage57:
    stage57_run_id = str(stage57.get("id") or "")
    stage57_status = normalize_text(stage57.get("run_status")).upper()
    stage57_verdict = normalize_text(stage57.get("global_verdict")).upper()
    stage57_run_fingerprint = normalize_text(stage57.get("run_fingerprint"))
    stage57_result_fingerprint = normalize_text(stage57.get("result_fingerprint"))
    stage56_run_id = str(stage57.get("stage56_run_id") or "")
    stage55_run_id = str(stage57.get("stage55_run_id") or "")
    stage52_run_id = str(stage57.get("stage52_run_id") or "")
else:
    stage57_run_id = ""
    stage57_status = "MISSING"
    stage57_verdict = "MISSING"
    stage57_run_fingerprint = ""
    stage57_result_fingerprint = ""
    stage56_run_id = ""
    stage55_run_id = ""
    stage52_run_id = ""

stage57_claims = rows(
    "stage57_claim_audits",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage57_run_id": stage57_run_id,
    },
    "claim_no",
    5000,
) if stage57_run_id else []

needs_claims = [
    c for c in stage57_claims
    if normalize_text(c.get("classification")).upper() == "NEEDS_EVIDENCE"
]


# ---------------------------------------------------------------------
# Upstream chain
# ---------------------------------------------------------------------

stage56_candidates = rows(
    "stage56_resolution_update_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage56_run_id else []
stage56 = next((r for r in stage56_candidates if str(r.get("id") or "") == stage56_run_id), None)

stage55_candidates = rows(
    "stage55_confirmation_resolution_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage55_run_id else []
stage55 = next((r for r in stage55_candidates if str(r.get("id") or "") == stage55_run_id), None)

stage52_candidates = rows(
    "stage52_validation_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage52_run_id else []
stage52 = next((r for r in stage52_candidates if str(r.get("id") or "") == stage52_run_id), None)

source_registry = as_dict(stage57.get("source_registry")) if stage57 else {}
if not source_registry and stage52:
    source_registry = as_dict(stage52.get("source_registry"))


# ---------------------------------------------------------------------
# Fingerprint verification
# ---------------------------------------------------------------------

stage57_run_payload = as_dict(stage57.get("run_payload")) if stage57 else {}
recomputed_stage57_run_fingerprint = stable_sha256(stage57_run_payload) if stage57_run_payload else ""

stage57_result_payload = as_dict(stage57.get("result_payload")) if stage57 else {}
recomputed_stage57_result_fingerprint = stable_sha256(stage57_result_payload) if stage57_result_payload else ""

stage56_update_payload = as_dict(stage56.get("update_payload")) if stage56 else {}
stored_stage56_update_fingerprint = normalize_text(stage56.get("update_fingerprint")) if stage56 else ""
recomputed_stage56_update_fingerprint = stable_sha256(stage56_update_payload) if stage56_update_payload else ""

stage55_resolution_payload = as_dict(stage55.get("resolution_payload")) if stage55 else {}
stored_stage55_resolution_fingerprint = normalize_text(stage55.get("resolution_fingerprint")) if stage55 else ""
recomputed_stage55_resolution_fingerprint = (
    stable_sha256(stage55_resolution_payload) if stage55_resolution_payload else ""
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
    "Stage 57 exists",
    bool(stage57),
    stage57_run_id or "MISSING",
)

add_check(
    "Stage 57 COMPLETED",
    stage57_status == "COMPLETED",
    stage57_status,
)

add_check(
    "Stage 57 verdict NEEDS_EVIDENCE",
    stage57_verdict == "NEEDS_EVIDENCE",
    stage57_verdict,
)

add_check(
    "Stage 57 run fingerprint stable",
    bool(stage57_run_fingerprint)
    and stage57_run_fingerprint == recomputed_stage57_run_fingerprint,
    f"stored={stage57_run_fingerprint[:16]}..., recomputed={recomputed_stage57_run_fingerprint[:16]}...",
)

add_check(
    "Stage 57 result fingerprint stable",
    bool(stage57_result_fingerprint)
    and stage57_result_fingerprint == recomputed_stage57_result_fingerprint,
    f"stored={stage57_result_fingerprint[:16]}..., recomputed={recomputed_stage57_result_fingerprint[:16]}...",
)

add_check(
    "Stage 56 exists",
    bool(stage56),
    stage56_run_id or "MISSING",
)

add_check(
    "Stage 56 update fingerprint stable",
    bool(stored_stage56_update_fingerprint)
    and stored_stage56_update_fingerprint == recomputed_stage56_update_fingerprint,
    f"stored={stored_stage56_update_fingerprint[:16]}..., recomputed={recomputed_stage56_update_fingerprint[:16]}...",
)

add_check(
    "Stage 55 exists",
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
    "Remaining NEEDS_EVIDENCE claims exist",
    len(needs_claims) > 0,
    f"needs_evidence={len(needs_claims)}",
)

stage58_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

gate_reason = (
    "Stage 57 evidence gaps are persisted and upstream fingerprints are stable."
    if stage58_gate == "READY"
    else "Stage 58 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Stable Stage 58 run fingerprint
# ---------------------------------------------------------------------

gap_inventory = [
    {
        "stage57_claim_audit_id": str(c.get("id") or ""),
        "section_key": normalize_text(c.get("section_key")),
        "claim_no": int(c.get("claim_no") or 0),
        "claim_text": normalize_text(c.get("claim_text")),
        "classification": normalize_text(c.get("classification")).upper(),
        "source_ids": as_list(c.get("source_ids")),
        "related_stage55_claim_no": c.get("related_stage55_claim_no"),
        "violation_type": normalize_text(c.get("violation_type")),
        "reason": normalize_text(c.get("reason")),
    }
    for c in sorted(needs_claims, key=lambda x: (normalize_text(x.get("section_key")), int(x.get("claim_no") or 0)))
]

run_basis = {
    "stage": 58,
    "fingerprint_contract": "stage58-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage52_run_id": stage52_run_id,
    "stage55_run_id": stage55_run_id,
    "stage56_run_id": stage56_run_id,
    "stage57_run_id": stage57_run_id,

    "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
    "stage56_update_fingerprint": stored_stage56_update_fingerprint,
    "stage57_run_fingerprint": stage57_run_fingerprint,
    "stage57_result_fingerprint": stage57_result_fingerprint,

    "gap_inventory": gap_inventory,
    "source_registry_fingerprint": stable_sha256(source_registry),
    "stage58_gate": stage58_gate,
}

stage58_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# AI resolver
# ---------------------------------------------------------------------

def resolve_gap_from_persisted_evidence(item: dict) -> dict:
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")
    if not model:
        raise RuntimeError("OPENAI_MODEL is missing.")

    rules = (
        "You are a fail-closed evidence-gap resolver for a Horizon Europe proposal. "
        "Evaluate ONLY the provided claim against the persisted source registry. "
        "Do not infer applicant facts from general call rules. "
        "Return strict JSON with resolution, source_ids, value, reason. "
        "resolution must be EVIDENCE_CONFIRMED, EVIDENCE_CONTRADICTED, or STILL_NEEDS_EVIDENCE. "
        "EVIDENCE_CONFIRMED requires direct support from at least one valid source_id. "
        "EVIDENCE_CONTRADICTED requires direct contradicting evidence. "
        "If evidence is incomplete or generic, return STILL_NEEDS_EVIDENCE."
    )

    request_payload = {
        "claim_text": normalize_text(item.get("claim_text")),
        "claim_reason": normalize_text(item.get("reason")),
        "source_registry": source_registry,
        "required_output": {
            "resolution": "EVIDENCE_CONFIRMED|EVIDENCE_CONTRADICTED|STILL_NEEDS_EVIDENCE",
            "source_ids": ["valid IDs from source_registry only"],
            "value": "exact supported/contradicting value if present",
            "reason": "short evidence-based explanation",
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
        raise RuntimeError("Resolver model did not return valid JSON.")

    resolution = normalize_text(parsed.get("resolution")).upper()
    if resolution not in {"EVIDENCE_CONFIRMED", "EVIDENCE_CONTRADICTED", "STILL_NEEDS_EVIDENCE"}:
        resolution = "STILL_NEEDS_EVIDENCE"

    source_ids = [sid for sid in as_list(parsed.get("source_ids")) if sid in source_registry]

    if resolution in {"EVIDENCE_CONFIRMED", "EVIDENCE_CONTRADICTED"} and not source_ids:
        resolution = "STILL_NEEDS_EVIDENCE"

    return {
        "resolution": resolution,
        "source_ids": source_ids,
        "value": normalize_text(parsed.get("value")),
        "reason": normalize_text(parsed.get("reason")),
        "model": model,
        "response_id": raw.get("id"),
        "response_payload": raw,
    }


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage58():
    if not stage58_run_fingerprint:
        return None

    data = (
        supabase.table("stage58_evidence_gap_resolution_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage57_run_id", stage57_run_id)
        .eq("run_fingerprint", stage58_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def initialize_stage58():
    if stage58_gate != "READY":
        raise RuntimeError("Stage 58 is BLOCKED.")

    existing = load_existing_stage58()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage52_run_id": stage52_run_id,
        "stage55_run_id": stage55_run_id,
        "stage56_run_id": stage56_run_id,
        "stage57_run_id": stage57_run_id,

        "stage": 58,
        "resolver_version": "stage58-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage55_resolution_fingerprint": stored_stage55_resolution_fingerprint,
        "stage56_update_fingerprint": stored_stage56_update_fingerprint,
        "stage57_run_fingerprint": stage57_run_fingerprint,
        "stage57_result_fingerprint": stage57_result_fingerprint,

        "run_status": "INITIALIZED",
        "resolution_outcome": "NEEDS_EVIDENCE",

        "total_gaps": len(gap_inventory),
        "resolved_gaps": 0,
        "open_gaps": len(gap_inventory),

        "run_fingerprint": stage58_run_fingerprint,
        "source_registry": source_registry,
        "run_payload": run_basis,

        "initialized_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = (
        supabase.table("stage58_evidence_gap_resolution_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not create Stage 58 run.")

    run = data[0]
    run_id = str(run["id"])

    for gap in gap_inventory:
        row = {
            "stage58_run_id": run_id,
            "stage57_run_id": stage57_run_id,
            "stage57_claim_audit_id": gap["stage57_claim_audit_id"],

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "section_key": gap["section_key"],
            "claim_no": gap["claim_no"],
            "claim_text": gap["claim_text"],
            "gap_reason": gap["reason"],

            "resolution_status": "OPEN",
            "resolution_basis": "NONE",

            "resolved_source_ids": [],
            "resolution_value": None,
            "resolution_note": None,

            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        supabase.table("stage58_evidence_gap_resolution_items").insert(row).execute()

    return run


def apply_ai_resolution(run_id: str, item: dict, result: dict):
    resolution = result["resolution"]

    if resolution == "EVIDENCE_CONFIRMED":
        status = "RESOLVED"
        basis = "PERSISTED_EVIDENCE"
        requires_draft_update = False
    elif resolution == "EVIDENCE_CONTRADICTED":
        status = "CONTRADICTED"
        basis = "PERSISTED_EVIDENCE"
        requires_draft_update = True
    else:
        status = "OPEN"
        basis = "NONE"
        requires_draft_update = False

    (
        supabase.table("stage58_evidence_gap_resolution_items")
        .update({
            "resolution_status": status,
            "resolution_basis": basis,
            "resolved_source_ids": result.get("source_ids") or [],
            "resolution_value": result.get("value") or None,
            "resolution_note": result.get("reason") or None,
            "requires_draft_update": requires_draft_update,

            "resolver_model": result.get("model"),
            "resolver_response_id": result.get("response_id"),
            "resolver_response_payload": result.get("response_payload") or {},

            "resolved_at": now_iso() if status != "OPEN" else None,
            "updated_at": now_iso(),
        })
        .eq("id", item["id"])
        .eq("user_id", user_id)
        .eq("stage58_run_id", run_id)
        .execute()
    )


def apply_user_evidence(
    run_id: str,
    item: dict,
    value: str,
    provenance_note: str,
    evidence_label: str,
):
    value = normalize_text(value)
    provenance_note = normalize_text(provenance_note)
    evidence_label = normalize_text(evidence_label)

    if not value:
        raise RuntimeError("User-provided evidence requires a factual value.")
    if not provenance_note:
        raise RuntimeError("User-provided evidence requires an explicit provenance note.")

    evidence_id = f"user58:{stable_sha256({'run': run_id, 'item': item['id'], 'value': value, 'note': provenance_note})[:24]}"

    (
        supabase.table("stage58_evidence_gap_resolution_items")
        .update({
            "resolution_status": "RESOLVED",
            "resolution_basis": "USER_PROVIDED_EVIDENCE",

            "resolved_source_ids": [evidence_id],
            "resolution_value": value,
            "resolution_note": provenance_note,

            "user_evidence_id": evidence_id,
            "user_evidence_label": evidence_label or "User-provided evidence",

            "requires_draft_update": False,
            "resolved_at": now_iso(),
            "updated_at": now_iso(),
        })
        .eq("id", item["id"])
        .eq("user_id", user_id)
        .eq("stage58_run_id", run_id)
        .execute()
    )


def recompute_stage58_outcome(run_id: str):
    items = rows(
        "stage58_evidence_gap_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage58_run_id": run_id,
        },
        "claim_no",
        5000,
    )

    open_count = sum(
        1 for i in items
        if normalize_text(i.get("resolution_status")).upper() == "OPEN"
    )

    contradicted_count = sum(
        1 for i in items
        if normalize_text(i.get("resolution_status")).upper() == "CONTRADICTED"
    )

    resolved_count = len(items) - open_count

    if open_count > 0:
        outcome = "NEEDS_EVIDENCE"
        run_status = "IN_PROGRESS"
    elif contradicted_count > 0:
        outcome = "REQUIRES_DRAFT_UPDATE"
        run_status = "COMPLETED"
    else:
        outcome = "READY_FOR_STAGE57_REAUDIT"
        run_status = "COMPLETED"

    result_basis = {
        "stage58_run_id": run_id,
        "stage57_run_id": stage57_run_id,
        "stage57_result_fingerprint": stage57_result_fingerprint,
        "items": [
            {
                "id": str(i.get("id") or ""),
                "claim_no": int(i.get("claim_no") or 0),
                "resolution_status": normalize_text(i.get("resolution_status")).upper(),
                "resolution_basis": normalize_text(i.get("resolution_basis")).upper(),
                "resolved_source_ids": as_list(i.get("resolved_source_ids")),
                "resolution_value": normalize_text(i.get("resolution_value")),
                "resolution_note": normalize_text(i.get("resolution_note")),
                "requires_draft_update": bool(i.get("requires_draft_update")),
            }
            for i in items
        ],
        "resolution_outcome": outcome,
    }

    result_fingerprint = stable_sha256(result_basis)

    (
        supabase.table("stage58_evidence_gap_resolution_runs")
        .update({
            "run_status": run_status,
            "resolution_outcome": outcome,

            "resolved_gaps": resolved_count,
            "open_gaps": open_count,

            "result_fingerprint": result_fingerprint,
            "result_payload": result_basis,

            "completed_at": now_iso() if run_status == "COMPLETED" else None,
            "updated_at": now_iso(),
        })
        .eq("id", run_id)
        .eq("user_id", user_id)
        .execute()
    )

    return outcome, result_fingerprint


existing_stage58 = load_existing_stage58()


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 57 → Stage 58 evidence-gap binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 57", stage57_verdict)
m2.metric("Remaining gaps", len(needs_claims))
m3.metric("Evidence sources", len(source_registry))
m4.metric("Integrity", "VERIFIED" if stage58_gate == "READY" else "FAILED")

with st.expander("Stage 58 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Stage 58 execution gate")

if stage58_gate == "READY":
    st.success("Etapa 58: READY. Evidence gap resolution poate începe.")
else:
    st.error("Etapa 58: BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage58_run_fingerprint, language=None)

st.divider()
st.subheader("Stage 58 persistence")

if existing_stage58:
    st.success(
        f"Stage 58 este inițializată. Run ID: {existing_stage58.get('id')} — "
        f"Outcome: {existing_stage58.get('resolution_outcome')}"
    )
else:
    st.info("Inițializează Stage 58 pentru a copia cele remaining NEEDS_EVIDENCE claims într-o coadă de rezoluție.")

if st.button(
    "🧾 Initialize Stage 58 evidence-gap resolution",
    type="primary",
    use_container_width=True,
    key="stage58_initialize",
    disabled=(stage58_gate != "READY"),
):
    try:
        saved = initialize_stage58()
        st.session_state["stage58_run_id"] = str(saved.get("id"))
        st.success(f"Stage 58 initialized — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 58 initialization failed. Rulează mai întâi SQL-ul Stage 58 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage58:
    run_id = str(existing_stage58["id"])

    items = rows(
        "stage58_evidence_gap_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage58_run_id": run_id,
        },
        "claim_no",
        5000,
    )

    st.divider()
    st.subheader("Remaining evidence gaps")

    if items:
        st.dataframe(
            [
                {
                    "Section": i.get("section_key"),
                    "Claim #": i.get("claim_no"),
                    "Claim": i.get("claim_text"),
                    "Status": i.get("resolution_status"),
                    "Basis": i.get("resolution_basis"),
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

        item_map = {
            f"#{i.get('claim_no')} — {normalize_text(i.get('claim_text'))[:110]}": i
            for i in items
        }

        selected_label = st.selectbox(
            "Evidence gap item",
            list(item_map.keys()),
            key="stage58_item",
        )
        item = item_map[selected_label]

        st.write(f"**Claim:** {item.get('claim_text')}")
        st.write(f"**Gap reason:** {item.get('gap_reason') or '—'}")
        st.write(
            f"**Current resolution:** "
            f"{item.get('resolution_status')} / {item.get('resolution_basis')}"
        )

        with st.expander("Current resolved source IDs", expanded=False):
            st.json(as_list(item.get("resolved_source_ids")))

        ai_ready = bool(secret("OPENAI_API_KEY")) and bool(secret("OPENAI_MODEL"))
        if not ai_ready:
            st.warning("OPENAI_API_KEY și OPENAI_MODEL trebuie configurate în Streamlit Secrets.")

        if st.button(
            "🔎 Try resolution from persisted evidence",
            type="primary",
            use_container_width=True,
            key="stage58_ai_resolve",
            disabled=(not ai_ready),
        ):
            try:
                with st.spinner("Checking persisted evidence..."):
                    result = resolve_gap_from_persisted_evidence(item)
                    apply_ai_resolution(run_id, item, result)
                    outcome, _ = recompute_stage58_outcome(run_id)
                    st.success(f"Evidence resolution processed — outcome {outcome}")
                    st.rerun()
            except Exception as exc:
                st.error(
                    f"Evidence resolution failed: {type(exc).__name__}: {str(exc)[:1800]}"
                )

        st.markdown("### Explicit user-provided evidence")

        evidence_label = st.text_input(
            "Evidence label",
            key="stage58_user_evidence_label",
            placeholder="e.g. Applicant registration certificate / bank statement / CV / partner letter",
        )

        user_value = st.text_area(
            "Confirmed factual value",
            key="stage58_user_value",
            help="Introduce numai informația factuală pe care o poți susține.",
        )

        user_note = st.text_area(
            "Evidence provenance note",
            key="stage58_user_note",
            help="Descrie exact documentul/sursa pe baza căreia confirmi informația.",
        )

        if st.button(
            "👤 Save user-provided evidence",
            use_container_width=True,
            key="stage58_user_resolve",
        ):
            try:
                apply_user_evidence(
                    run_id,
                    item,
                    user_value,
                    user_note,
                    evidence_label,
                )
                outcome, _ = recompute_stage58_outcome(run_id)
                st.success(f"User evidence persisted — outcome {outcome}")
                st.rerun()
            except Exception as exc:
                st.error(
                    f"User evidence save failed: {type(exc).__name__}: {str(exc)[:1800]}"
                )

    latest = (
        supabase.table("stage58_evidence_gap_resolution_runs")
        .select("*")
        .eq("id", run_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    ).data or [existing_stage58]

    latest_run = latest[0]

    st.divider()
    st.subheader("Stage 58 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", latest_run.get("resolution_outcome"))
    o2.metric("Total gaps", latest_run.get("total_gaps"))
    o3.metric("Resolved", latest_run.get("resolved_gaps"))
    o4.metric("Open", latest_run.get("open_gaps"))

    outcome = normalize_text(latest_run.get("resolution_outcome")).upper()

    if outcome == "READY_FOR_STAGE57_REAUDIT":
        st.success(
            "Stage 58 READY_FOR_STAGE57_REAUDIT. Toate evidence gaps au fost rezolvate fără contradicții. "
            "Stage 57 trebuie rerulat pe draftul corectat folosind acest Stage 58 result_fingerprint."
        )
    elif outcome == "REQUIRES_DRAFT_UPDATE":
        st.warning(
            "Stage 58 REQUIRES_DRAFT_UPDATE. Cel puțin o nouă dovadă contrazice draftul curent. "
            "Este necesară o nouă corecție înainte de re-audit."
        )
    else:
        st.warning(
            "Stage 58 NEEDS_EVIDENCE. Mai există evidence gaps OPEN."
        )

st.caption(
    "Invariantă Stage 58 v1.0: lipsa dovezii rămâne OPEN; persisted evidence trebuie să aibă source_id valid; "
    "user-provided evidence trebuie să includă valoare factuală + provenance note; Stage 58 nu modifică direct draftul."
)
