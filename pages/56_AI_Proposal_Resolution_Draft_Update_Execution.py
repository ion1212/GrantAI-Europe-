import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import requests
import streamlit as st
from supabase import create_client

# =====================================================================
# STAGE 56 v1.0 — AI PROPOSAL RESOLUTION DRAFT UPDATE EXECUTION
# =====================================================================

st.set_page_config(
    page_title="Stage 56 v1.0 — Resolution Draft Update Execution",
    page_icon="🛠️",
    layout="wide",
)

st.title("🛠️ Etapa 56 v1.0 — AI Proposal Resolution Draft Update Execution")
st.caption(
    "Aplică rezoluțiile persistate din Stage 55 asupra drafturilor Stage 51, "
    "fără a suprascrie draftul sursă și fără a inventa fapte."
)

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
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
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
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 56 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage56_project")]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)
if not locks:
    st.error("Stage 56 BLOCKED: nu există opportunity lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")

stage55_candidates = rows(
    "stage55_confirmation_resolution_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)
stage55 = next(
    (
        r for r in stage55_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("resolution_outcome")).upper()
        in {"REQUIRES_DRAFT_UPDATE", "READY_FOR_SUBMISSION_PREP"}
    ),
    stage55_candidates[0] if stage55_candidates else None,
)

if stage55:
    stage55_run_id = str(stage55.get("id") or "")
    stage55_status = normalize_text(stage55.get("run_status")).upper()
    stage55_outcome = normalize_text(stage55.get("resolution_outcome")).upper()
    stage55_run_fingerprint = normalize_text(stage55.get("run_fingerprint"))
    stage55_resolution_fingerprint = normalize_text(stage55.get("resolution_fingerprint"))
    stage54_run_id = str(stage55.get("stage54_run_id") or "")
else:
    stage55_run_id = stage54_run_id = ""
    stage55_status = stage55_outcome = "MISSING"
    stage55_run_fingerprint = stage55_resolution_fingerprint = ""

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

stage51_run_id = str(stage54.get("stage51_run_id") or "") if stage54 else ""
stage51_candidates = rows(
    "stage51_drafting_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage51_run_id else []
stage51 = next((r for r in stage51_candidates if str(r.get("id") or "") == stage51_run_id), None)

stage51_items = rows(
    "stage51_drafting_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage51_run_id": stage51_run_id,
    },
    "created_at",
    500,
) if stage51_run_id else []

drafted_stage51_items = [
    i for i in stage51_items
    if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
]

stage55_run_payload = as_dict(stage55.get("run_payload")) if stage55 else {}
recomputed_stage55_run_fingerprint = stable_sha256(stage55_run_payload) if stage55_run_payload else ""

stage55_resolution_payload = as_dict(stage55.get("resolution_payload")) if stage55 else {}
recomputed_stage55_resolution_fingerprint = stable_sha256(stage55_resolution_payload) if stage55_resolution_payload else ""

stage54_payload = as_dict(stage54.get("readiness_payload")) if stage54 else {}
stored_stage54_fingerprint = normalize_text(stage54.get("readiness_fingerprint")) if stage54 else ""
recomputed_stage54_fingerprint = stable_sha256(stage54_payload) if stage54_payload else ""

resolved_items = [i for i in stage55_items if normalize_text(i.get("resolution_status")).upper() != "OPEN"]
open_items = [i for i in stage55_items if normalize_text(i.get("resolution_status")).upper() == "OPEN"]

checks = []
def add_check(name, passed, detail):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})

add_check("ACTIVE lock", normalize_text(lock.get("lock_status")).upper() == "ACTIVE", normalize_text(lock.get("lock_status")).upper())
add_check("Workflow allowed", workflow_allowed, f"workflow_allowed={workflow_allowed}")
add_check("Deadline valid", future_deadline(deadline), str(deadline or "")[:10])
add_check("Stage 55 exists", bool(stage55), stage55_run_id or "MISSING")
add_check("Stage 55 COMPLETED", stage55_status == "COMPLETED", stage55_status)
add_check("Stage 55 outcome accepted", stage55_outcome in {"REQUIRES_DRAFT_UPDATE", "READY_FOR_SUBMISSION_PREP"}, stage55_outcome)
add_check("Stage 55 run fingerprint stable",
          bool(stage55_run_fingerprint) and stage55_run_fingerprint == recomputed_stage55_run_fingerprint,
          f"stored={stage55_run_fingerprint[:16]}..., recomputed={recomputed_stage55_run_fingerprint[:16]}...")
add_check("Stage 55 resolution fingerprint stable",
          bool(stage55_resolution_fingerprint) and stage55_resolution_fingerprint == recomputed_stage55_resolution_fingerprint,
          f"stored={stage55_resolution_fingerprint[:16]}..., recomputed={recomputed_stage55_resolution_fingerprint[:16]}...")
add_check("Stage 55 open items = 0", len(open_items) == 0, f"open={len(open_items)}")
add_check("Stage 54 bound run exists", bool(stage54), stage54_run_id or "MISSING")
add_check("Stage 54 fingerprint stable",
          bool(stored_stage54_fingerprint) and stored_stage54_fingerprint == recomputed_stage54_fingerprint,
          f"stored={stored_stage54_fingerprint[:16]}..., recomputed={recomputed_stage54_fingerprint[:16]}...")
add_check("Stage 51 source drafts exist", len(drafted_stage51_items) > 0, f"drafts={len(drafted_stage51_items)}")

stage56_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"
gate_reason = (
    "Stage 55 resolutions are complete and upstream fingerprints are stable."
    if stage56_gate == "READY"
    else "Stage 56 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)

draft_by_section = {normalize_text(i.get("section_key")): i for i in drafted_stage51_items}
resolution_by_section = {}
for item in resolved_items:
    resolution_by_section.setdefault(normalize_text(item.get("section_key")), []).append(item)

run_basis = {
    "stage": 56,
    "fingerprint_contract": "stage56-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage51_run_id": stage51_run_id,
    "stage54_run_id": stage54_run_id,
    "stage55_run_id": stage55_run_id,
    "stage54_readiness_fingerprint": stored_stage54_fingerprint,
    "stage55_run_fingerprint": stage55_run_fingerprint,
    "stage55_resolution_fingerprint": stage55_resolution_fingerprint,
    "stage55_outcome": stage55_outcome,
    "source_inventory": [
        {
            "id": str(i.get("id") or ""),
            "section_key": normalize_text(i.get("section_key")),
            "draft_sha256": normalize_text(i.get("draft_sha256")),
        }
        for i in drafted_stage51_items
    ],
    "resolution_inventory": [
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
        for i in resolved_items
    ],
    "stage56_gate": stage56_gate,
}
stage56_run_fingerprint = stable_sha256(run_basis)

def update_section_with_openai(section_title: str, original_text: str, section_resolutions: list) -> dict:
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")
    if not model:
        raise RuntimeError("OPENAI_MODEL is missing.")

    payload = []
    for r in section_resolutions:
        payload.append({
            "claim_no": int(r.get("claim_no") or 0),
            "claim_text": normalize_text(r.get("claim_text")),
            "resolution_status": normalize_text(r.get("resolution_status")).upper(),
            "resolution_basis": normalize_text(r.get("resolution_basis")).upper(),
            "resolution_value": normalize_text(r.get("resolution_value")),
            "resolution_note": normalize_text(r.get("resolution_note")),
            "resolved_source_ids": as_list(r.get("resolved_source_ids")),
        })

    system_rules = (
        "You are performing a controlled proposal correction. Never invent facts. "
        "Preserve unaffected text. CONFIRMED may use only exact persisted confirmed values. "
        "REJECTED must remove unsupported positive factual assertions or replace them with a neutral "
        "'requires verification before submission' statement. REMOVED must remove the assertion. "
        "Do not add partners, qualifications, financial facts, technical capabilities, budgets, metrics, or legal conclusions. "
        "Return strict JSON with corrected_text and actions."
    )

    request = {
        "model": model,
        "input": [
            {"role": "system", "content": system_rules},
            {
                "role": "user",
                "content": json.dumps({
                    "section_title": section_title,
                    "original_text": original_text,
                    "resolutions": payload,
                    "required_output": {
                        "corrected_text": "string",
                        "actions": [
                            {
                                "claim_no": 0,
                                "action": "KEEP|REWRITE|REMOVE|FLAG_FOR_SUBMISSION_EVIDENCE",
                                "reason": "string",
                            }
                        ],
                    },
                }, ensure_ascii=False, default=str),
            },
        ],
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=request,
        timeout=120,
    )

    try:
        raw = response.json()
    except Exception:
        raw = {"raw_text": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API HTTP {response.status_code}: {normalize_text(raw)[:1800]}")

    parsed = extract_json_object(extract_response_text(raw))
    corrected_text = normalize_text(parsed.get("corrected_text"))
    if not corrected_text:
        raise RuntimeError("Corrected draft text is empty.")

    allowed = {"KEEP", "REWRITE", "REMOVE", "FLAG_FOR_SUBMISSION_EVIDENCE"}
    actions = []
    for action in parsed.get("actions") or []:
        if not isinstance(action, dict):
            continue
        name = normalize_text(action.get("action")).upper()
        if name not in allowed:
            name = "FLAG_FOR_SUBMISSION_EVIDENCE"
        actions.append({
            "claim_no": int(action.get("claim_no") or 0),
            "action": name,
            "reason": normalize_text(action.get("reason")),
        })

    return {
        "corrected_text": corrected_text,
        "actions": actions,
        "model": model,
        "response_id": raw.get("id"),
        "response_payload": raw,
    }

def load_existing_stage56():
    if not stage56_run_fingerprint:
        return None
    data = (
        supabase.table("stage56_resolution_update_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage55_run_id", stage55_run_id)
        .eq("run_fingerprint", stage56_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []
    return data[0] if data else None

def initialize_stage56():
    if stage56_gate != "READY":
        raise RuntimeError("Stage 56 is BLOCKED.")
    existing = load_existing_stage56()
    if existing:
        return existing

    initial_outcome = "NO_UPDATE_REQUIRED" if stage55_outcome == "READY_FOR_SUBMISSION_PREP" else "PENDING_UPDATE"
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage51_run_id": stage51_run_id,
        "stage54_run_id": stage54_run_id,
        "stage55_run_id": stage55_run_id,
        "stage": 56,
        "updater_version": "stage56-v1.0",
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,
        "stage54_readiness_fingerprint": stored_stage54_fingerprint,
        "stage55_run_fingerprint": stage55_run_fingerprint,
        "stage55_resolution_fingerprint": stage55_resolution_fingerprint,
        "run_status": "INITIALIZED",
        "update_outcome": initial_outcome,
        "source_section_count": len(drafted_stage51_items),
        "affected_resolution_count": len(resolved_items),
        "updated_section_count": 0,
        "run_fingerprint": stage56_run_fingerprint,
        "run_payload": run_basis,
        "initialized_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    data = supabase.table("stage56_resolution_update_runs").insert(payload).execute().data or []
    if not data:
        raise RuntimeError("Could not create Stage 56 run.")
    return data[0]

def upsert_corrected_section(run_id: str, source_item: dict, result: dict, section_resolutions: list):
    section_key = normalize_text(source_item.get("section_key"))
    original_text = normalize_text(source_item.get("draft_text"))
    original_sha = normalize_text(source_item.get("draft_sha256")) or text_sha256(original_text)
    corrected_text = result["corrected_text"]
    corrected_sha = text_sha256(corrected_text)

    item_fingerprint = stable_sha256({
        "stage56_run_id": run_id,
        "source_item_id": str(source_item.get("id") or ""),
        "section_key": section_key,
        "source_draft_sha256": original_sha,
        "corrected_draft_sha256": corrected_sha,
        "stage55_resolution_fingerprint": stage55_resolution_fingerprint,
        "resolution_ids": [str(r.get("id") or "") for r in section_resolutions],
        "actions": result["actions"],
    })

    payload = {
        "stage56_run_id": run_id,
        "stage55_run_id": stage55_run_id,
        "stage51_run_id": stage51_run_id,
        "stage51_draft_item_id": source_item.get("id"),
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "section_key": section_key,
        "section_title": source_item.get("section_title"),
        "source_draft_sha256": original_sha,
        "corrected_draft_sha256": corrected_sha,
        "original_text": original_text,
        "corrected_text": corrected_text,
        "update_status": "UPDATED",
        "item_fingerprint": item_fingerprint,
        "model_name": result.get("model"),
        "response_id": result.get("response_id"),
        "response_payload": result.get("response_payload") or {},
        "updated_at": now_iso(),
    }

    existing = (
        supabase.table("stage56_corrected_drafts")
        .select("id")
        .eq("user_id", user_id)
        .eq("stage56_run_id", run_id)
        .eq("section_key", section_key)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        data = (
            supabase.table("stage56_corrected_drafts")
            .update(payload)
            .eq("id", existing[0]["id"])
            .eq("user_id", user_id)
            .execute()
        ).data or []
        corrected = data[0] if data else {**payload, "id": existing[0]["id"]}
    else:
        payload["created_at"] = now_iso()
        data = supabase.table("stage56_corrected_drafts").insert(payload).execute().data or []
        if not data:
            raise RuntimeError(f"Could not persist corrected section {section_key}.")
        corrected = data[0]

    corrected_id = corrected["id"]

    (
        supabase.table("stage56_resolution_update_items")
        .delete()
        .eq("user_id", user_id)
        .eq("stage56_run_id", run_id)
        .eq("section_key", section_key)
        .execute()
    )

    action_map = {int(a.get("claim_no") or 0): a for a in result["actions"]}

    for r in section_resolutions:
        claim_no = int(r.get("claim_no") or 0)
        action = action_map.get(claim_no, {})
        action_name = normalize_text(action.get("action")).upper()
        if not action_name:
            action_name = "REWRITE" if normalize_text(r.get("resolution_status")).upper() == "CONFIRMED" else "REMOVE"

        row = {
            "stage56_run_id": run_id,
            "stage56_corrected_draft_id": corrected_id,
            "stage55_run_id": stage55_run_id,
            "stage55_resolution_item_id": r.get("id"),
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "section_key": section_key,
            "claim_no": claim_no,
            "resolution_status": normalize_text(r.get("resolution_status")).upper(),
            "resolution_basis": normalize_text(r.get("resolution_basis")).upper(),
            "original_claim_text": normalize_text(r.get("claim_text")),
            "resolution_value": normalize_text(r.get("resolution_value")),
            "resolution_note": normalize_text(r.get("resolution_note")),
            "action": action_name,
            "action_reason": normalize_text(action.get("reason")),
            "resolved_source_ids": as_list(r.get("resolved_source_ids")),
            "item_fingerprint": stable_sha256({
                "resolution_item_id": str(r.get("id") or ""),
                "claim_no": claim_no,
                "action": action_name,
                "corrected_sha": corrected_sha,
            }),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        supabase.table("stage56_resolution_update_items").insert(row).execute()

    return corrected

def recompute_stage56_outcome(run_id: str):
    corrected = rows(
        "stage56_corrected_drafts",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage56_run_id": run_id,
        },
        "created_at",
        500,
    )

    affected_sections = {
        normalize_text(i.get("section_key"))
        for i in resolved_items
        if normalize_text(i.get("section_key"))
    }
    corrected_sections = {
        normalize_text(i.get("section_key"))
        for i in corrected
        if normalize_text(i.get("update_status")).upper() == "UPDATED"
    }

    if stage55_outcome == "READY_FOR_SUBMISSION_PREP":
        outcome, run_status = "NO_UPDATE_REQUIRED", "COMPLETED"
    elif affected_sections and affected_sections.issubset(corrected_sections):
        outcome, run_status = "DRAFT_UPDATED", "COMPLETED"
    else:
        outcome, run_status = "PENDING_UPDATE", "UPDATING"

    update_payload = {
        "stage56_run_id": run_id,
        "stage55_run_id": stage55_run_id,
        "stage55_resolution_fingerprint": stage55_resolution_fingerprint,
        "corrected_sections": [
            {
                "section_key": normalize_text(i.get("section_key")),
                "source_draft_sha256": normalize_text(i.get("source_draft_sha256")),
                "corrected_draft_sha256": normalize_text(i.get("corrected_draft_sha256")),
                "item_fingerprint": normalize_text(i.get("item_fingerprint")),
            }
            for i in corrected
        ],
        "update_outcome": outcome,
    }
    update_fingerprint = stable_sha256(update_payload)

    (
        supabase.table("stage56_resolution_update_runs")
        .update({
            "run_status": run_status,
            "update_outcome": outcome,
            "updated_section_count": len(corrected_sections),
            "update_fingerprint": update_fingerprint,
            "update_payload": update_payload,
            "completed_at": now_iso() if run_status == "COMPLETED" else None,
            "updated_at": now_iso(),
        })
        .eq("id", run_id)
        .eq("user_id", user_id)
        .execute()
    )
    return outcome, update_fingerprint

existing_stage56 = load_existing_stage56()
corrected_drafts = rows(
    "stage56_corrected_drafts",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage56_run_id": str(existing_stage56.get("id") or ""),
    },
    "created_at",
    500,
) if existing_stage56 else []

st.divider()
st.subheader("Stage 55 → Stage 56 controlled update binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 55", stage55_outcome)
m2.metric("Resolved items", len(resolved_items))
m3.metric("Open items", len(open_items))
m4.metric("Integrity", "VERIFIED" if stage56_gate == "READY" else "FAILED")

with st.expander("Stage 56 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Stage 56 execution gate")

g1, g2, g3 = st.columns(3)
g1.metric("Gate", stage56_gate)
g2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
g3.metric("Source drafts", len(drafted_stage51_items))

if stage56_gate == "READY":
    st.success("Etapa 56: READY. Controlled draft update poate începe.")
else:
    st.error("Etapa 56: BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage56_run_fingerprint, language=None)

st.divider()
st.subheader("Stage 56 persistence")

if existing_stage56:
    st.success(
        f"Stage 56 este inițializată. Run ID: {existing_stage56.get('id')} — "
        f"Outcome: {existing_stage56.get('update_outcome')}"
    )
else:
    st.info("Inițializează Stage 56 înainte de aplicarea rezoluțiilor.")

if st.button(
    "🛠️ Initialize Stage 56 controlled update",
    type="primary",
    use_container_width=True,
    key="stage56_initialize",
    disabled=(stage56_gate != "READY"),
):
    try:
        saved = initialize_stage56()
        st.session_state["stage56_run_id"] = str(saved.get("id"))
        st.success(f"Stage 56 initialized — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 56 initialization failed. Rulează mai întâi SQL-ul Stage 56 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage56:
    st.divider()
    st.subheader("Resolution application")

    affected_sections = sorted({
        normalize_text(i.get("section_key"))
        for i in resolved_items
        if normalize_text(i.get("section_key"))
    })

    if not affected_sections and stage55_outcome == "READY_FOR_SUBMISSION_PREP":
        st.success("Stage 55 requires no draft update.")
    elif affected_sections:
        section_key = st.selectbox("Section to update", affected_sections, key="stage56_section")
        source_item = draft_by_section.get(section_key)
        section_resolutions = resolution_by_section.get(section_key, [])

        if not source_item:
            st.error(f"No matching Stage 51 draft found for section: {section_key}")
        else:
            st.write(f"**Source section:** {source_item.get('section_title')}")
            st.write(f"**Source SHA256:** `{normalize_text(source_item.get('draft_sha256'))}`")

            with st.expander("Stage 55 resolutions", expanded=True):
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

            with st.expander("Original Stage 51 draft", expanded=False):
                st.write(source_item.get("draft_text") or "")

            existing_corrected = next(
                (i for i in corrected_drafts if normalize_text(i.get("section_key")) == section_key),
                None,
            )
            if existing_corrected:
                st.success(
                    f"Corrected draft already persisted — SHA256 "
                    f"{normalize_text(existing_corrected.get('corrected_draft_sha256'))[:18]}..."
                )
                with st.expander("Current corrected draft", expanded=False):
                    st.write(existing_corrected.get("corrected_text") or "")

            ai_ready = bool(secret("OPENAI_API_KEY")) and bool(secret("OPENAI_MODEL"))
            if not ai_ready:
                st.warning("OPENAI_API_KEY și OPENAI_MODEL trebuie configurate în Streamlit Secrets.")

            if st.button(
                "🧠 Apply Stage 55 resolutions & persist corrected draft",
                type="primary",
                use_container_width=True,
                key="stage56_apply",
                disabled=(not ai_ready),
            ):
                try:
                    with st.spinner("Applying controlled resolutions..."):
                        result = update_section_with_openai(
                            normalize_text(source_item.get("section_title")),
                            normalize_text(source_item.get("draft_text")),
                            section_resolutions,
                        )
                        corrected_row = upsert_corrected_section(
                            str(existing_stage56["id"]),
                            source_item,
                            result,
                            section_resolutions,
                        )
                        outcome, _ = recompute_stage56_outcome(str(existing_stage56["id"]))
                        st.success(
                            f"Corrected draft persisted — "
                            f"{normalize_text(corrected_row.get('corrected_draft_sha256'))[:18]}... "
                            f"Outcome: {outcome}"
                        )
                        st.rerun()
                except Exception as exc:
                    st.error(f"Stage 56 update failed: {type(exc).__name__}: {str(exc)[:1800]}")

    latest_data = (
        supabase.table("stage56_resolution_update_runs")
        .select("*")
        .eq("id", existing_stage56["id"])
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    ).data or [existing_stage56]
    latest_run = latest_data[0]

    corrected_drafts = rows(
        "stage56_corrected_drafts",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage56_run_id": str(existing_stage56["id"]),
        },
        "created_at",
        500,
    )

    st.divider()
    st.subheader("Stage 56 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", latest_run.get("update_outcome"))
    o2.metric("Source sections", latest_run.get("source_section_count"))
    o3.metric("Affected resolutions", latest_run.get("affected_resolution_count"))
    o4.metric("Updated sections", latest_run.get("updated_section_count"))

    if corrected_drafts:
        st.dataframe(
            [
                {
                    "Section": i.get("section_title"),
                    "Status": i.get("update_status"),
                    "Source SHA256": normalize_text(i.get("source_draft_sha256"))[:16] + "...",
                    "Corrected SHA256": normalize_text(i.get("corrected_draft_sha256"))[:16] + "...",
                }
                for i in corrected_drafts
            ],
            use_container_width=True,
            hide_index=True,
        )

    outcome = normalize_text(latest_run.get("update_outcome")).upper()
    if outcome == "DRAFT_UPDATED":
        st.success(
            "Stage 56 corrected draft is persisted. It may be handed to Stage 57 "
            "for post-update evidence revalidation."
        )
    elif outcome == "NO_UPDATE_REQUIRED":
        st.success("Stage 56: NO_UPDATE_REQUIRED.")
    else:
        st.warning("Stage 56 is not complete yet.")

st.caption(
    "Invariantă Stage 56 v1.0: Stage 51 source drafts remain immutable. "
    "REJECTED/REMOVED claims cannot remain as positive confirmed facts. "
    "Every corrected draft receives a new SHA256; Stage 57 revalidation is mandatory."
)
