import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import requests
import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 51 v1.0 — AI PROPOSAL DRAFTING EXECUTION
#
# Purpose:
#   Consume ONLY a persisted Stage 50 INITIALIZED build run for the same
#   user/project/ACTIVE opportunity lock and execute evidence-bounded drafting.
#
# Safety / quality invariants:
#   - Stage 51 verifies Stage 50 + Stage 49 fingerprints and upstream run binding.
#   - Drafting uses persisted project facts + verified Stage 46 evidence only.
#   - Unsupported facts must remain UNKNOWN / TO CONFIRM; they must not be invented.
#   - No external submission, portal login, legal signature or financial commitment.
#   - Each generated draft is persisted with its prompt/source snapshot and SHA256.
#
# Persistence:
#   stage51_drafting_runs
#   stage51_drafting_items
# =====================================================================

st.set_page_config(
    page_title="Stage 51 v1.0 — Proposal Drafting Execution",
    page_icon="✍️",
    layout="wide",
)

st.title("✍️ Etapa 51 v1.0 — AI Proposal Drafting Execution")
st.caption(
    "Etapa 51 redactează numai din fapte persistate și dovezi oficiale validate. "
    "Orice informație nesusținută trebuie marcată TO CONFIRM, nu inventată."
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
    st.error("Stage 51 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox("Project", list(project_map.keys()), key="stage51_v1_project")
project = project_map[selected_project]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 51 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 50 and upstream binding
# ---------------------------------------------------------------------

stage50_runs = rows(
    "stage50_proposal_build_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)
stage50 = stage50_runs[0] if stage50_runs else None

if stage50:
    stage50_run_id = str(stage50.get("id") or "")
    stage50_status = normalize_text(stage50.get("build_status")).upper()
    stage50_fingerprint = normalize_text(stage50.get("build_fingerprint"))
    stage49_run_id = str(stage50.get("stage49_run_id") or "")
    stage48_run_id = str(stage50.get("stage48_run_id") or "")
    stage47_run_id = str(stage50.get("stage47_run_id") or "")
    stage46_run_id = str(stage50.get("stage46_run_id") or "")
    stage50_scope = as_dict(stage50.get("authorization_scope"))
else:
    stage50_run_id = ""
    stage50_status = "MISSING"
    stage50_fingerprint = ""
    stage49_run_id = ""
    stage48_run_id = ""
    stage47_run_id = ""
    stage46_run_id = ""
    stage50_scope = {}

stage50_items = rows(
    "stage50_proposal_build_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage50_run_id": stage50_run_id,
    },
    "sequence_no",
    100,
) if stage50_run_id else []

stage49_rows = rows(
    "stage49_application_authorization_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage49_run_id else []
stage49 = next((r for r in stage49_rows if str(r.get("id") or "") == stage49_run_id), None)

stage46_items = rows(
    "locked_evidence_provenance_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "provenance_run_id": stage46_run_id,
    },
    "created_at",
    500,
) if stage46_run_id else []


# ---------------------------------------------------------------------
# Recompute fingerprints
# ---------------------------------------------------------------------

stage50_payload = as_dict(stage50.get("build_payload")) if stage50 else {}
recomputed_stage50_fingerprint = stable_sha256(stage50_payload) if stage50_payload else ""

stage49_payload = as_dict(stage49.get("authorization_payload")) if stage49 else {}
recomputed_stage49_fingerprint = stable_sha256(stage49_payload) if stage49_payload else ""
stored_stage49_fingerprint = normalize_text(stage49.get("authorization_fingerprint")) if stage49 else ""


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})


add_check("ACTIVE lock", normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
          normalize_text(lock.get("lock_status")).upper())
add_check("Workflow allowed", workflow_allowed, f"workflow_allowed={workflow_allowed}")
add_check("Deadline valid", future_deadline(deadline), str(deadline or "")[:10])
add_check("Stage 50 exists", bool(stage50), stage50_run_id or "MISSING")
add_check("Stage 50 INITIALIZED", stage50_status == "INITIALIZED", stage50_status)
add_check("Stage 50 proposal_build scope", bool_true(stage50_scope.get("proposal_build")),
          f"proposal_build={bool_true(stage50_scope.get('proposal_build'))}")
add_check("Stage 50 external submission disabled", not bool_true(stage50_scope.get("external_submission")),
          f"external_submission={bool_true(stage50_scope.get('external_submission'))}")
add_check("Stage 50 fingerprint stable",
          bool(stage50_fingerprint) and stage50_fingerprint == recomputed_stage50_fingerprint,
          f"stored={stage50_fingerprint[:16]}..., recomputed={recomputed_stage50_fingerprint[:16]}...")
add_check("Stage 49 bound run exists", bool(stage49), stage49_run_id or "MISSING")
add_check("Stage 49 AUTHORIZED",
          bool(stage49) and normalize_text(stage49.get("authorization_status")).upper() == "AUTHORIZED",
          normalize_text(stage49.get("authorization_status")).upper() if stage49 else "MISSING")
add_check("Stage 49 fingerprint stable",
          bool(stored_stage49_fingerprint) and stored_stage49_fingerprint == recomputed_stage49_fingerprint,
          f"stored={stored_stage49_fingerprint[:16]}..., recomputed={recomputed_stage49_fingerprint[:16]}...")
add_check("Stage 50 build items = 8", len(stage50_items) == 8, f"count={len(stage50_items)}")

all_checks_pass = all(c["PASS"] for c in checks)
stage51_gate = "READY" if all_checks_pass else "BLOCKED"

if stage51_gate == "READY":
    gate_reason = "Stage 50 build workspace and Stage 49 authorization are persisted, bound and fingerprint-stable."
else:
    gate_reason = "Stage 51 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])


# ---------------------------------------------------------------------
# Evidence-bounded drafting context
# ---------------------------------------------------------------------

verified_evidence = []
for item in stage46_items:
    if normalize_text(item.get("validation_status")).upper() != "VERIFIED":
        continue
    verified_evidence.append({
        "requirement": item.get("requirement_label") or item.get("requirement_category"),
        "evidence_url": item.get("final_url") or item.get("evidence_url"),
        "evidence_excerpt": item.get("evidence_excerpt") or item.get("stage45_excerpt") or "",
        "validation_reason": item.get("validation_reason") or "",
    })

project_snapshot = {
    k: v
    for k, v in project.items()
    if k not in {
        "user_id",
        "created_at",
        "updated_at",
    }
}

context_snapshot = {
    "project": project_snapshot,
    "locked_opportunity": {
        "identity": identity,
        "deadline": str(deadline or "")[:10],
        "lock_id": lock_id,
    },
    "verified_official_evidence": verified_evidence,
}

context_fingerprint = stable_sha256(context_snapshot)


# ---------------------------------------------------------------------
# Drafting plan
# ---------------------------------------------------------------------

DRAFTABLE = {
    "project_facts": {
        "title": "Project facts and applicant baseline",
        "instruction": (
            "Create a concise verified fact base. Separate VERIFIED FACTS from TO CONFIRM. "
            "Do not infer missing legal, financial, technical, staffing, consortium or budget facts."
        ),
    },
    "excellence": {
        "title": "Excellence",
        "instruction": (
            "Draft an evidence-bounded Excellence section skeleton and first draft. "
            "Use only supported project facts and official call evidence. Mark missing claims TO CONFIRM."
        ),
    },
    "impact": {
        "title": "Impact",
        "instruction": (
            "Draft an evidence-bounded Impact section skeleton and first draft. "
            "Do not fabricate KPIs, market size, beneficiaries, exploitation commitments or quantified outcomes."
        ),
    },
    "implementation": {
        "title": "Quality and efficiency of implementation",
        "instruction": (
            "Draft an implementation section skeleton and first draft. "
            "Do not invent partners, work packages, person-months, staff capacity, equipment or timelines."
        ),
    },
    "budget_and_resources": {
        "title": "Budget and resources",
        "instruction": (
            "Prepare a budget/resource narrative template from known facts only. "
            "Do not invent costs or financial commitments. Unknown amounts must be TO CONFIRM."
        ),
    },
    "ethics_security_compliance": {
        "title": "Ethics, security and compliance",
        "instruction": (
            "Prepare an ethics/security/compliance checklist and narrative based only on known project facts. "
            "Do not assert compliance where evidence is missing; use TO CONFIRM."
        ),
    },
}


# ---------------------------------------------------------------------
# AI call
# ---------------------------------------------------------------------

def generate_with_openai(section_key: str, section_title: str, section_instruction: str) -> dict:
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from Streamlit Secrets.")
    if not model:
        raise RuntimeError("OPENAI_MODEL is missing from Streamlit Secrets.")

    system_rules = (
        "You are drafting a European funding proposal from a bounded evidence pack. "
        "Never invent facts. Never treat absence of evidence as evidence of compliance. "
        "Whenever a claim is unsupported, write [TO CONFIRM: ...]. "
        "Distinguish official-call requirements from applicant/project facts. "
        "Do not authorize submission, signatures, portal actions or financial commitments. "
        "Return clear professional proposal prose with a short 'Evidence/assumptions audit' at the end."
    )

    prompt_payload = {
        "section_key": section_key,
        "section_title": section_title,
        "instruction": section_instruction,
        "context": context_snapshot,
    }

    request_body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_rules},
            {
                "role": "user",
                "content": (
                    "Draft the requested proposal workspace section from this JSON evidence pack:\n"
                    + json.dumps(prompt_payload, ensure_ascii=False, default=str)
                ),
            },
        ],
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=request_body,
        timeout=120,
    )

    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text}

    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI API HTTP {response.status_code}: "
            + normalize_text(payload.get("error") or payload)[:1800]
        )

    text = extract_response_text(payload)
    if not text:
        raise RuntimeError("OpenAI response contained no draft text.")

    return {
        "model": model,
        "request_payload": prompt_payload,
        "response_text": text,
        "response_id": payload.get("id"),
        "response_payload": payload,
    }


# ---------------------------------------------------------------------
# Stable Stage 51 run fingerprint
# ---------------------------------------------------------------------

run_basis = {
    "stage": 51,
    "fingerprint_contract": "stage51-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage46_run_id": stage46_run_id,
    "stage49_run_id": stage49_run_id,
    "stage50_run_id": stage50_run_id,
    "stage49_authorization_fingerprint": stored_stage49_fingerprint,
    "stage50_build_fingerprint": stage50_fingerprint,
    "context_fingerprint": context_fingerprint,
    "draftable_sections": sorted(DRAFTABLE.keys()),
    "stage51_gate": stage51_gate,
}

stage51_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage51_run():
    if not stage51_run_fingerprint:
        return None
    data = (
        supabase.table("stage51_drafting_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage50_run_id", stage50_run_id)
        .eq("run_fingerprint", stage51_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []
    return data[0] if data else None


def initialize_stage51_run():
    if stage51_gate != "READY":
        raise RuntimeError("Stage 51 is BLOCKED.")

    existing = load_existing_stage51_run()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage46_run_id": stage46_run_id,
        "stage49_run_id": stage49_run_id,
        "stage50_run_id": stage50_run_id,
        "stage": 51,
        "drafter_version": "stage51-v1.0",
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,
        "stage49_authorization_fingerprint": stored_stage49_fingerprint,
        "stage50_build_fingerprint": stage50_fingerprint,
        "context_fingerprint": context_fingerprint,
        "run_status": "INITIALIZED",
        "run_fingerprint": stage51_run_fingerprint,
        "context_payload": context_snapshot,
        "run_payload": run_basis,
        "initialized_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = supabase.table("stage51_drafting_runs").insert(payload).execute().data or []
    if not data:
        raise RuntimeError("Could not create Stage 51 drafting run.")
    return data[0]


def upsert_draft_item(run_id: str, section_key: str, result: dict):
    config = DRAFTABLE[section_key]
    draft_text = result["response_text"]
    draft_hash = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()

    payload = {
        "stage51_run_id": run_id,
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage50_run_id": stage50_run_id,
        "section_key": section_key,
        "section_title": config["title"],
        "draft_status": "DRAFTED",
        "source_status": "EVIDENCE_BOUNDED",
        "model_name": result.get("model"),
        "prompt_payload": result.get("request_payload") or {},
        "source_payload": context_snapshot,
        "draft_text": draft_text,
        "draft_sha256": draft_hash,
        "response_id": result.get("response_id"),
        "response_payload": result.get("response_payload") or {},
        "generated_at": now_iso(),
        "updated_at": now_iso(),
    }

    existing = (
        supabase.table("stage51_drafting_items")
        .select("id")
        .eq("user_id", user_id)
        .eq("stage51_run_id", run_id)
        .eq("section_key", section_key)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        data = (
            supabase.table("stage51_drafting_items")
            .update(payload)
            .eq("id", existing[0]["id"])
            .eq("user_id", user_id)
            .execute()
        ).data or []
        return data[0] if data else {**payload, "id": existing[0]["id"]}

    payload["created_at"] = now_iso()
    data = supabase.table("stage51_drafting_items").insert(payload).execute().data or []
    if not data:
        raise RuntimeError(f"Could not persist draft item {section_key}.")
    return data[0]


existing_run = load_existing_stage51_run()
draft_items = []
if existing_run:
    draft_items = rows(
        "stage51_drafting_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage51_run_id": existing_run.get("id"),
        },
        "created_at",
        100,
    )


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 50 → Stage 51 drafting binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 50", stage50_status)
m2.metric("Build items", len(stage50_items))
m3.metric("Verified evidence", len(verified_evidence))
m4.metric(
    "Fingerprints",
    "VERIFIED"
    if (
        stage50_fingerprint == recomputed_stage50_fingerprint
        and stored_stage49_fingerprint == recomputed_stage49_fingerprint
        and bool(stage50_fingerprint)
        and bool(stored_stage49_fingerprint)
    )
    else "FAILED",
)

with st.expander("Stage 51 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Final Stage 51 Gate")

g1, g2, g3 = st.columns(3)
g1.metric("Gate", stage51_gate)
g2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
g3.metric("Draftable sections", len(DRAFTABLE))

if stage51_gate == "READY":
    st.success("Etapa 51: READY. Evidence-bounded drafting poate începe.")
else:
    st.error("Etapa 51: BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage51_run_fingerprint, language=None)

st.divider()
st.subheader("Stage 51 run")

if existing_run:
    st.success(f"Stage 51 este inițializată. Run ID: {existing_run.get('id')}")
else:
    st.info("Inițializează Stage 51 înainte de generarea drafturilor.")

if st.button(
    "✍️ Initialize Stage 51 drafting run",
    type="primary",
    use_container_width=True,
    key="stage51_v1_initialize",
    disabled=(stage51_gate != "READY"),
):
    try:
        saved = initialize_stage51_run()
        st.session_state["stage51_run_id"] = str(saved.get("id"))
        st.success(f"Stage 51 initialized — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 51 initialization failed. Rulează mai întâi SQL-ul Stage 51 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1500]}"
        )

if existing_run:
    st.divider()
    st.subheader("Drafting execution")

    drafted_by_key = {
        normalize_text(i.get("section_key")): i
        for i in draft_items
    }

    section_key = st.selectbox(
        "Section to draft",
        list(DRAFTABLE.keys()),
        format_func=lambda k: DRAFTABLE[k]["title"],
        key="stage51_section",
    )

    cfg = DRAFTABLE[section_key]
    st.write(f"**Instruction:** {cfg['instruction']}")

    existing_item = drafted_by_key.get(section_key)
    if existing_item:
        st.success(
            f"Existing draft: {existing_item.get('draft_status')} — "
            f"SHA256 {normalize_text(existing_item.get('draft_sha256'))[:18]}..."
        )
        with st.expander("Current persisted draft", expanded=False):
            st.write(existing_item.get("draft_text") or "")

    ai_ready = bool(secret("OPENAI_API_KEY")) and bool(secret("OPENAI_MODEL"))
    if not ai_ready:
        st.warning(
            "Pentru generare automată trebuie configurate OPENAI_API_KEY și OPENAI_MODEL în Streamlit Secrets."
        )

    if st.button(
        "🤖 Generate / regenerate evidence-bounded draft",
        type="primary",
        use_container_width=True,
        key="stage51_generate",
        disabled=(not ai_ready),
    ):
        try:
            with st.spinner("Generating evidence-bounded draft..."):
                result = generate_with_openai(
                    section_key,
                    cfg["title"],
                    cfg["instruction"],
                )
                saved_item = upsert_draft_item(
                    str(existing_run["id"]),
                    section_key,
                    result,
                )

                # Advance corresponding Stage 50 workspace item without marking FINAL.
                (
                    supabase.table("stage50_proposal_build_items")
                    .update({
                        "item_status": "IN_PROGRESS",
                        "content_status": "DRAFTED",
                        "source_status": "VERIFIED",
                        "updated_at": now_iso(),
                    })
                    .eq("user_id", user_id)
                    .eq("stage50_run_id", stage50_run_id)
                    .eq("section_key", section_key)
                    .execute()
                )

                st.success(
                    f"Draft persisted: {cfg['title']} — "
                    f"{normalize_text(saved_item.get('draft_sha256'))[:18]}..."
                )
                st.rerun()
        except Exception as exc:
            st.error(f"Draft generation failed: {type(exc).__name__}: {str(exc)[:1800]}")

    if draft_items:
        st.divider()
        st.subheader("Persisted Stage 51 drafts")
        st.dataframe(
            [
                {
                    "Section": i.get("section_title"),
                    "Status": i.get("draft_status"),
                    "Source": i.get("source_status"),
                    "Model": i.get("model_name"),
                    "Generated": i.get("generated_at"),
                    "SHA256": normalize_text(i.get("draft_sha256"))[:18] + "...",
                }
                for i in draft_items
            ],
            use_container_width=True,
            hide_index=True,
        )

        drafted_count = sum(
            1 for i in draft_items
            if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
        )

        if drafted_count >= len(DRAFTABLE):
            st.success(
                "Toate secțiunile Stage 51 au draft persistent. O viitoare Stage 52 poate face "
                "cross-section review, consistency checking și evidence-gap review."
            )
        else:
            st.info(
                f"Drafted {drafted_count}/{len(DRAFTABLE)} secțiuni. "
                "Continuă până când toate secțiunile necesare au draft persistent."
            )

st.caption(
    "Invariantă Stage 51 v1.0: AI-ul poate redacta numai în scope-ul Stage 49/50 și din "
    "contextul persistat. Faptele lipsă rămân TO CONFIRM. External submission rămâne neautorizat."
)

# =====================================================================
# END STAGE 51 v1.0
# =====================================================================
