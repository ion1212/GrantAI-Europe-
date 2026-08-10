import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Evidence Resolution Validation",
    page_icon="✅",
    layout="wide",
)

st.title("✅ Etapa 40 — AI Evidence Resolution Validation")
st.caption(
    "Validează rezultatele Etapei 39 pentru același lock ACTIVE. "
    "Nu rezolvă din nou gap-urile și nu inventează dovezi."
)

NEXT_MODULE = "AI Evidence Resolution Routing"


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


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def project_label(p: dict) -> str:
    return f"{p.get('name') or 'Project'} — {str(p.get('id') or '')[:8]}"


def evidence_present(item: dict) -> bool:
    route = normalize_text(item.get("resolution_route")).upper()
    status = normalize_text(item.get("resolution_status")).upper()

    if route == "DATABASE_EVIDENCE":
        return bool(
            as_dict(item.get("resolved_value"))
            or normalize_text(item.get("evidence_source"))
            or normalize_text(item.get("evidence_reference"))
            or normalize_text(item.get("evidence_url"))
            or normalize_text(item.get("evidence_excerpt"))
        )

    if status == "RESOLVED":
        return bool(
            as_dict(item.get("resolved_value"))
            or normalize_text(item.get("evidence_source"))
            or normalize_text(item.get("evidence_reference"))
            or normalize_text(item.get("evidence_url"))
            or normalize_text(item.get("evidence_excerpt"))
        )

    # For NEEDS_* and PARTIAL, absence of final evidence is legitimate.
    return bool(
        normalize_text(item.get("resolution_reason"))
        or normalize_text(item.get("next_action"))
    )


def provenance_valid(item: dict) -> bool:
    route = normalize_text(item.get("resolution_route")).upper()
    status = normalize_text(item.get("resolution_status")).upper()

    if route == "DATABASE_EVIDENCE":
        return bool(
            normalize_text(item.get("evidence_source"))
            or normalize_text(item.get("evidence_reference"))
            or normalize_text(item.get("evidence_url"))
        )

    if status == "NEEDS_OFFICIAL_VERIFICATION":
        return bool(
            item.get("official_verification_required")
            or normalize_text(item.get("next_action"))
        )

    if status == "NEEDS_USER_EVIDENCE":
        return bool(
            item.get("requires_user_confirmation")
            or normalize_text(item.get("next_action"))
        )

    if status in {"PARTIAL", "UNRESOLVED"}:
        return bool(normalize_text(item.get("resolution_reason")))

    if status in {"BLOCKED", "NOT_APPLICABLE"}:
        return bool(normalize_text(item.get("resolution_reason")))

    return True


def detect_contradiction(stage38_req: dict, resolution_item: dict) -> bool:
    # Stage 38 marked these records as MISSING_EVIDENCE.
    # Contradiction exists if Stage 39 claims RESOLVED but gives no evidence/provenance.
    if normalize_text(stage38_req.get("requirement_status")).upper() != "MISSING_EVIDENCE":
        return True

    if normalize_text(resolution_item.get("resolution_status")).upper() == "RESOLVED":
        return not (
            evidence_present(resolution_item)
            and provenance_valid(resolution_item)
        )

    return False


def validate_item(stage38_req: dict, item: dict) -> dict:
    status = normalize_text(item.get("resolution_status")).upper()
    route = normalize_text(item.get("resolution_route")).upper()

    ev_present = evidence_present(item)
    prov_valid = provenance_valid(item)
    contradiction = detect_contradiction(stage38_req, item)

    validation_status = "VALID"
    reason = ""
    next_action = ""

    if contradiction:
        validation_status = "INVALID"
        reason = (
            "Etapa 39 marchează rezultatul ca rezolvat, dar dovada/proveniența "
            "nu susține suficient această concluzie."
        )
        next_action = "Return to Stage 39 and correct the resolution or evidence."

    elif status == "BLOCKED":
        validation_status = "BLOCKED"
        reason = normalize_text(item.get("resolution_reason")) or "Gap explicitly blocked."
        next_action = normalize_text(item.get("next_action")) or "Resolve blocking incompatibility."

    elif status == "RESOLVED":
        if ev_present and prov_valid:
            validation_status = "VALID"
            reason = "Resolved result is supported by evidence and provenance."
        else:
            validation_status = "INVALID"
            reason = "Resolved result lacks sufficient evidence/provenance."
            next_action = "Provide verifiable evidence or downgrade resolution status."

    elif status == "NEEDS_OFFICIAL_VERIFICATION":
        if prov_valid:
            validation_status = "VALID_NEEDS_ATTENTION"
            reason = "Official verification route is justified and traceable."
            next_action = normalize_text(item.get("next_action")) or "Verify against official call documentation."
        else:
            validation_status = "INVALID"
            reason = "Official verification route has no traceable justification."
            next_action = "Add official-verification rationale/source target."

    elif status == "NEEDS_USER_EVIDENCE":
        if prov_valid:
            validation_status = "VALID_NEEDS_ATTENTION"
            reason = "User-evidence route is justified."
            next_action = normalize_text(item.get("next_action")) or "Request factual applicant evidence."
        else:
            validation_status = "INVALID"
            reason = "User-evidence route lacks an actionable request."
            next_action = "Specify exactly what evidence the applicant must provide."

    elif status in {"PARTIAL", "UNRESOLVED"}:
        validation_status = "VALID_NEEDS_ATTENTION"
        reason = (
            normalize_text(item.get("resolution_reason"))
            or "Resolution remains incomplete."
        )
        next_action = (
            normalize_text(item.get("next_action"))
            or "Complete the missing verification/evidence."
        )

    elif status == "NOT_APPLICABLE":
        if prov_valid:
            validation_status = "VALID"
            reason = "Not-applicable status is supported by explicit reasoning."
        else:
            validation_status = "INVALID"
            reason = "NOT_APPLICABLE is unsupported."
            next_action = "Provide explicit evidence that the requirement does not apply."

    else:
        validation_status = "INVALID"
        reason = f"Unsupported Stage 39 resolution status: {status or 'EMPTY'}."
        next_action = "Return to Stage 39."

    return {
        "evidence_present": ev_present,
        "provenance_valid": prov_valid,
        "contradiction_detected": contradiction,
        "validation_status": validation_status,
        "validation_reason": reason,
        "required_next_action": next_action,
        "validated_value": as_dict(item.get("resolved_value")),
        "source_snapshot": {
            "evidence_source": item.get("evidence_source"),
            "evidence_reference": item.get("evidence_reference"),
            "evidence_url": item.get("evidence_url"),
            "evidence_excerpt": item.get("evidence_excerpt"),
            "resolution_reason": item.get("resolution_reason"),
            "next_action": item.get("next_action"),
        },
    }


# ---------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------
supabase = get_supabase()
restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Nu am putut identifica utilizatorul autentificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_label = st.selectbox("Project", list(project_map.keys()))
project = project_map[selected_label]
project_id = str(project["id"])
project_name = normalize_text(project.get("name"))

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
    st.warning("Nu există lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

resolution_runs = rows(
    "locked_evidence_resolution_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

resolution_run = next(
    (
        r for r in resolution_runs
        if normalize_text(r.get("run_status")).upper()
        in {"COMPLETED", "NEEDS_ATTENTION", "BLOCKED"}
    ),
    None,
)

if not resolution_run:
    st.warning("Nu există un rezultat Etapa 39 disponibil pentru validare.")
    st.stop()

resolution_run_id = str(resolution_run["id"])
requirement_run_id = str(resolution_run["requirement_run_id"])

resolution_items = rows(
    "locked_evidence_resolution_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "resolution_run_id": resolution_run_id,
    },
    "created_at",
    500,
)

stage38_requirements = rows(
    "locked_opportunity_requirements",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "requirement_run_id": requirement_run_id,
    },
    "created_at",
    500,
)

req_by_id = {
    str(r.get("id")): r
    for r in stage38_requirements
}

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lock", normalize_text(lock.get("lock_status")) or "—")
c2.metric("Workflow", "ALLOWED" if workflow_allowed else "BLOCKED")
c3.metric("Stage 39", normalize_text(resolution_run.get("run_status")) or "—")
c4.metric("Resolution items", len(resolution_items))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

hard_gate_ok = (
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE"
    and workflow_allowed
    and bool(identity)
    and future_deadline(deadline)
    and bool(resolution_items)
)

if not hard_gate_ok:
    st.error(
        "Etapa 40 este BLOCKED: se cere lock ACTIVE, workflow_allowed=true, "
        "deadline viitor și rezultate Etapa 39 pentru același lock."
    )
    st.stop()

st.success("Hard gate Etapa 40: PASS. Validarea poate începe.")

preview = []
for item in resolution_items:
    req = req_by_id.get(str(item.get("requirement_id"))) or {}
    v = validate_item(req, item)
    preview.append({
        "Requirement": item.get("requirement_label"),
        "Stage 39": item.get("resolution_status"),
        "Route": item.get("resolution_route"),
        "Validation": v["validation_status"],
        "Evidence": "YES" if v["evidence_present"] else "NO",
        "Provenance": "YES" if v["provenance_valid"] else "NO",
        "Contradiction": "YES" if v["contradiction_detected"] else "NO",
    })

st.subheader("Validation preview")
st.dataframe(preview, use_container_width=True, hide_index=True)

prior_validation_runs = rows(
    "locked_evidence_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "resolution_run_id": resolution_run_id,
    },
    "created_at",
    50,
)

if prior_validation_runs:
    latest_prior = prior_validation_runs[0]
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Latest validation", latest_prior.get("validation_status") or "—")
    p2.metric("Validated", latest_prior.get("validated_items") or 0)
    p3.metric("Attention", latest_prior.get("attention_items") or 0)
    p4.metric("Blocked/Invalid", (latest_prior.get("blocked_items") or 0) + (latest_prior.get("invalid_items") or 0))

confirm = st.checkbox(
    "Confirm că Etapa 40 trebuie să valideze rezultatul Etapei 39 fără a inventa dovezi noi."
)

if st.button(
    "✅ Validate Stage 39 resolutions",
    type="primary",
    use_container_width=True,
    disabled=not confirm,
):
    validation_run_id = None

    try:
        run_insert = (
            supabase.table("locked_evidence_validation_runs")
            .insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "opportunity_identity": identity,
                "total_items": len(resolution_items),
                "validation_status": "RUNNING",
                "started_at": now_iso(),
                "summary": {
                    "stage": 40,
                    "project_name": project_name,
                    "stage39_run_id": resolution_run_id,
                },
                "updated_at": now_iso(),
            })
            .execute()
        ).data or []

        if not run_insert:
            raise RuntimeError("Nu am putut crea validation run Etapa 40.")

        validation_run_id = str(run_insert[0]["id"])

        counts = {
            "validated_items": 0,
            "attention_items": 0,
            "blocked_items": 0,
            "invalid_items": 0,
        }

        progress = st.progress(0)

        for idx, item in enumerate(resolution_items, start=1):
            req = req_by_id.get(str(item.get("requirement_id"))) or {}
            v = validate_item(req, item)

            if v["validation_status"] == "VALID":
                counts["validated_items"] += 1
            elif v["validation_status"] == "VALID_NEEDS_ATTENTION":
                counts["attention_items"] += 1
            elif v["validation_status"] == "BLOCKED":
                counts["blocked_items"] += 1
            elif v["validation_status"] == "INVALID":
                counts["invalid_items"] += 1

            supabase.table("locked_evidence_validation_items").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "requirement_run_id": requirement_run_id,
                "resolution_run_id": resolution_run_id,
                "validation_run_id": validation_run_id,
                "resolution_item_id": item["id"],
                "requirement_id": item["requirement_id"],
                "opportunity_identity": identity,
                "requirement_key": item.get("requirement_key"),
                "requirement_category": item.get("requirement_category"),
                "requirement_label": item.get("requirement_label"),
                "resolution_route": item.get("resolution_route"),
                "resolution_status": item.get("resolution_status"),
                "evidence_present": v["evidence_present"],
                "provenance_valid": v["provenance_valid"],
                "contradiction_detected": v["contradiction_detected"],
                "validation_status": v["validation_status"],
                "validation_reason": v["validation_reason"],
                "required_next_action": v["required_next_action"],
                "validated_value": v["validated_value"],
                "source_snapshot": v["source_snapshot"],
                "metadata": {
                    "stage": 40,
                    "stage38_requirement_status": req.get("requirement_status"),
                    "is_critical": item.get("is_critical"),
                },
                "validated_at": now_iso(),
                "updated_at": now_iso(),
            }).execute()

            progress.progress(idx / len(resolution_items))

        if counts["blocked_items"] > 0:
            final_status = "BLOCKED"
        elif counts["invalid_items"] > 0 or counts["attention_items"] > 0:
            final_status = "NEEDS_ATTENTION"
        else:
            final_status = "PASS"

        summary = {
            "stage": 40,
            "project_name": project_name,
            "opportunity_identity": identity,
            "stage39_run_id": resolution_run_id,
            "next_action": (
                "STOP_AND_RESOLVE_BLOCKERS"
                if final_status == "BLOCKED"
                else "ROUTE_PENDING_EVIDENCE"
                if final_status == "NEEDS_ATTENTION"
                else "CONTINUE"
            ),
        }

        supabase.table("locked_evidence_validation_runs").update({
            **counts,
            "validation_status": final_status,
            "summary": summary,
            "completed_at": now_iso(),
            "updated_at": now_iso(),
        }).eq("id", validation_run_id).eq("user_id", user_id).execute()

        # Create / re-arm Stage 41 handoff.
        handoffs = rows(
            "selected_opportunity_handoffs",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
            },
            "created_at",
            500,
        )

        existing = next(
            (
                h for h in handoffs
                if normalize_text(h.get("destination_module")) == NEXT_MODULE
            ),
            None,
        )

        payload = {
            "stage": 40,
            "validation_run_id": validation_run_id,
            "resolution_run_id": resolution_run_id,
            "requirement_run_id": requirement_run_id,
            "opportunity_lock_id": lock_id,
            "opportunity_identity": identity,
            "validation_status": final_status,
            "summary": summary,
            "created_at": now_iso(),
        }

        if existing:
            supabase.table("selected_opportunity_handoffs").update({
                "handoff_status": "READY",
                "payload": payload,
                "consumed_at": None,
                "updated_at": now_iso(),
            }).eq("id", existing["id"]).eq("user_id", user_id).execute()
        else:
            supabase.table("selected_opportunity_handoffs").insert({
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,
                "opportunity_identity": identity,
                "destination_module": NEXT_MODULE,
                "handoff_status": "READY",
                "payload": payload,
                "updated_at": now_iso(),
            }).execute()

        st.success(
            f"Etapa 40 finalizată: {final_status}. "
            f"VALID={counts['validated_items']}, "
            f"ATTENTION={counts['attention_items']}, "
            f"INVALID={counts['invalid_items']}, "
            f"BLOCKED={counts['blocked_items']}."
        )
        st.rerun()

    except Exception as exc:
        if validation_run_id:
            try:
                supabase.table("locked_evidence_validation_runs").update({
                    "validation_status": "FAILED",
                    "summary": {
                        "stage": 40,
                        "error": str(exc)[:4000],
                    },
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                }).eq("id", validation_run_id).eq("user_id", user_id).execute()
            except Exception:
                pass

        st.error(f"Etapa 40 nu a putut finaliza validarea: {exc}")

# ---------------------------------------------------------------------
# Latest result
# ---------------------------------------------------------------------
validation_runs = rows(
    "locked_evidence_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "resolution_run_id": resolution_run_id,
    },
    "created_at",
    50,
)

if validation_runs:
    latest = validation_runs[0]

    st.divider()
    st.subheader("Latest Stage 40 Result")

    a, b, c, d, e = st.columns(5)
    a.metric("Status", latest.get("validation_status") or "—")
    b.metric("Valid", latest.get("validated_items") or 0)
    c.metric("Attention", latest.get("attention_items") or 0)
    d.metric("Invalid", latest.get("invalid_items") or 0)
    e.metric("Blocked", latest.get("blocked_items") or 0)

    items = rows(
        "locked_evidence_validation_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "validation_run_id": str(latest["id"]),
        },
        "created_at",
        500,
    )

    if items:
        st.dataframe(
            [
                {
                    "Requirement": r.get("requirement_label"),
                    "Stage 39": r.get("resolution_status"),
                    "Validation": r.get("validation_status"),
                    "Evidence": r.get("evidence_present"),
                    "Provenance": r.get("provenance_valid"),
                    "Contradiction": r.get("contradiction_detected"),
                    "Next action": r.get("required_next_action"),
                }
                for r in items
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Validation details")
        for item in items:
            with st.expander(
                f"{item.get('requirement_label')} — {item.get('validation_status')}"
            ):
                st.write(f"**Stage 39 status:** {item.get('resolution_status')}")
                st.write(f"**Validation reason:** {item.get('validation_reason') or '—'}")
                st.write(f"**Next action:** {item.get('required_next_action') or '—'}")
                st.write(f"**Evidence present:** {item.get('evidence_present')}")
                st.write(f"**Provenance valid:** {item.get('provenance_valid')}")
                st.write(f"**Contradiction:** {item.get('contradiction_detected')}")

    status = normalize_text(latest.get("validation_status")).upper()
    if status == "PASS":
        st.success("Etapa 40 PASS. Rezoluțiile sunt validate.")
    elif status == "NEEDS_ATTENTION":
        st.warning(
            "Etapa 40 NEEDS_ATTENTION. Clasificările Etapei 39 sunt valide, "
            "dar există încă official verification / user evidence / partial items."
        )
    elif status == "BLOCKED":
        st.error("Etapa 40 BLOCKED. Există cel puțin un blocker validat.")

with st.expander("Istoric Etapa 40"):
    if validation_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "status": r.get("validation_status"),
                    "total": r.get("total_items"),
                    "valid": r.get("validated_items"),
                    "attention": r.get("attention_items"),
                    "invalid": r.get("invalid_items"),
                    "blocked": r.get("blocked_items"),
                }
                for r in validation_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 40.")

st.caption(
    "Invariantă Etapa 40: validatorul nu modifică opportunity_lock_id, opportunity_identity "
    "sau verdictul factual al surselor. El validează doar calitatea și proveniența rezultatului Etapei 39."
)
