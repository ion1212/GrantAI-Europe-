import os, json, hashlib
from datetime import datetime, timezone, date
from urllib.parse import urlparse
import streamlit as st
from supabase import create_client

# STAGE 74 v1.1 — Final Pre-Submission Integrity Gate
# Fail-closed. This stage NEVER presses Submit and NEVER records a receipt.

st.set_page_config(page_title="Stage 74 v1.1 — Final Pre-Submission Integrity", page_icon="🛡️", layout="wide")
st.title("🛡️ Etapa 74 v1.1 — AI Final Pre-Submission Integrity Gate")
st.caption("Ultima verificare înaintea unei eventuale etape de execuție. Stage 74 NU apasă Submit.")

def norm(v): return str(v or "").strip()
def sha(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",",":"), default=str).encode()).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def secret(k):
    try: return str(st.secrets.get(k,""))
    except Exception: return os.getenv(k,"")
@st.cache_resource
def db(): return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"))
def rows(table, filters=None, order="created_at", limit=100):
    q=db().table(table).select("*")
    for k,v in (filters or {}).items():
        if v not in (None,""): q=q.eq(k,v)
    if order: q=q.order(order,desc=True)
    return q.limit(limit).execute().data or []
def uid():
    for k in ("user_id","auth_user_id"):
        if st.session_state.get(k): return str(st.session_state[k])
    for k in ("auth_user","user"):
        u=st.session_state.get(k)
        if isinstance(u,dict) and u.get("id"): return str(u["id"])
        if getattr(u,"id",None): return str(u.id)
    try:
        u=db().auth.get_user().user
        return str(u.id) if u else None
    except Exception: return None
def deadline_ok(v):
    try: return date.fromisoformat(str(v)[:10]) >= datetime.now(timezone.utc).date()
    except Exception: return False
def official_url(v):
    try:
        p=urlparse(norm(v)); h=(p.hostname or "").lower()
        return p.scheme=="https" and (h=="europa.eu" or h.endswith(".europa.eu"))
    except Exception: return False

user_id=uid()
if not user_id:
    st.error("AUTH_REQUIRED"); st.stop()

projects=rows("projects",{"user_id":user_id},"updated_at",200)
if not projects:
    st.error("No projects."); st.stop()
labels={f"{p.get('name') or 'Project'} — {str(p.get('id'))[:8]}":p for p in projects}
project=labels[st.selectbox("Project",list(labels),key="s74_project")]
project_id=str(project["id"])

locks=rows("selected_opportunity_locks",{"user_id":user_id,"project_id":project_id,"lock_status":"ACTIVE"},"created_at",10)
if not locks:
    st.error("BLOCKED — no ACTIVE opportunity lock."); st.stop()
lock=locks[0]; lock_id=str(lock["id"]); identity=norm(lock.get("opportunity_identity")); deadline=lock.get("official_deadline")
st.write(f"**Locked opportunity:** {identity}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")

s73s=rows("stage73_submission_package_completeness_runs",{"user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id})
s73=next((r for r in s73s if norm(r.get("run_status")).upper()=="COMPLETED" and norm(r.get("completeness_outcome")).upper()=="OPPORTUNITY_PACKAGE_COMPLETE" and not bool(r.get("external_submission_performed")) and not bool(r.get("external_receipt_obtained"))),None)
if not s73:
    st.error("BLOCKED — Stage 73 OPPORTUNITY_PACKAGE_COMPLETE not found."); st.stop()

s73_id=str(s73["id"]); s72_id=norm(s73.get("stage72_run_id")); s71_id=norm(s73.get("stage71_run_id"))
app_ref=norm(s73.get("application_reference")); portal_url=norm(s73.get("current_portal_url"))
manifest=s73.get("required_components") or []
manifest_sha=norm(s73.get("package_manifest_sha256"))
evidence_sha=norm(s73.get("completeness_evidence_sha256"))
run_fp=norm(s73.get("run_fingerprint"))
payload=s73.get("completeness_payload") or {}
run_payload=s73.get("run_payload") or {}

s72=(rows("stage72_final_submission_authorization_runs",{"id":s72_id,"user_id":user_id},None,1) or [None])[0] if s72_id else None
s71=(rows("stage71_final_submission_readiness_runs",{"id":s71_id,"user_id":user_id},None,1) or [None])[0] if s71_id else None

checks=[
("ACTIVE lock",norm(lock.get("lock_status")).upper()=="ACTIVE"),
("Workflow allowed",bool(lock.get("workflow_allowed"))),
("Deadline valid",deadline_ok(deadline)),
("Stage 73 complete",norm(s73.get("completeness_outcome")).upper()=="OPPORTUNITY_PACKAGE_COMPLETE"),
("Stage 73 manifest SHA stable",bool(manifest_sha) and manifest_sha==sha(manifest)),
("Stage 73 evidence SHA stable",bool(evidence_sha) and evidence_sha==sha(payload)),
("Stage 73 fingerprint stable",bool(run_fp) and run_fp==sha(run_payload)),
("Stage 72 authorization intact",bool(s72) and norm(s72.get("authorization_outcome")).upper()=="FINAL_SUBMISSION_AUTHORIZED"),
("Stage 71 readiness intact",bool(s71) and norm(s71.get("readiness_outcome")).upper()=="READY_FOR_SUBMISSION_AUTHORIZATION"),
("Application chain consistent",bool(s72) and bool(s71) and app_ref==norm(s72.get("application_reference"))==norm(s71.get("application_reference"))),
("Official portal URL",official_url(portal_url)),
("Not submitted",not bool(s73.get("external_submission_performed"))),
("No receipt",not bool(s73.get("external_receipt_obtained"))),
]
base_ready=all(v for _,v in checks)

st.divider(); st.subheader("Stage 73 → Stage 74 integrity chain")
a,b,c,d=st.columns(4)
a.metric("Stage 73",norm(s73.get("completeness_outcome"))); b.metric("Application",app_ref); c.metric("Deadline",str(deadline)[:10]); d.metric("Base integrity","VERIFIED" if base_ready else "FAILED")
with st.expander("Hard-gate checks"):
    st.dataframe([{"Check":n,"PASS":v} for n,v in checks],use_container_width=True,hide_index=True)
if base_ready: st.success("Stage 74 base integrity gate: READY")
else: st.error("Stage 74 base integrity gate: BLOCKED")

existing=rows("stage74_final_pre_submission_integrity_runs",{"user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id,"stage73_run_id":s73_id},"created_at",1)
existing=existing[0] if existing else None

if not existing:
    st.divider(); st.subheader("Current portal/package revalidation")
    st.warning("Dacă există încă valori de test/provizorii, NU bifa Production data final. Stage 74 trebuie să rămână BLOCKED.")

    current_url=st.text_input("Current official portal URL",value=portal_url)
    current_ref=st.text_input("Draft reference currently visible in portal",value=app_ref)

    clean=st.checkbox("Current Validation Summary shows ZERO blocking errors.")
    parta=st.checkbox("Part A contains final production data.")
    partb=st.checkbox("Part B/package is attached, current and final.")
    wp=st.checkbox("Work packages/tasks/deliverables/milestones are final and consistent.")
    budget=st.checkbox("Budget and person-months are final, justified and NOT test/provisional values.")
    participants=st.checkbox("Participants, roles and organisation/contact data are final and correct.")
    ethics=st.checkbox("Ethics and Security are final and accurate.")
    annexes=st.checkbox("All mandatory opportunity-specific annexes/components are final and current.")
    no_test=st.checkbox("NO test, placeholder, dummy or provisional data remains anywhere in the submission package.")
    editable=st.checkbox("Proposal is still editable and has NOT been finally submitted.")
    no_receipt=st.checkbox("No final submission receipt has been issued.")
    understand=st.checkbox("I understand Stage 74 only records integrity confirmation and does NOT press Submit.")
    phrase=st.text_input("Confirmation phrase",placeholder="CONFIRM STAGE 74 FINAL INTEGRITY")
    note=st.text_area("Optional integrity note")

    production=all([parta,partb,wp,budget,participants,ethics,annexes,no_test])
    ready=base_ready and official_url(current_url) and norm(current_ref)==app_ref and clean and production and editable and no_receipt and understand and norm(phrase)=="CONFIRM STAGE 74 FINAL INTEGRITY"

    if not production: st.error("Production-data gate: BLOCKED — test/provisional data must be replaced before confirmation.")
    else: st.success("Production-data gate: READY")

    integrity_payload={
        "integrity_version":"stage74-v1.0","user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id,
        "stage73_run_id":s73_id,"stage72_run_id":s72_id,"stage71_run_id":s71_id,"application_reference":app_ref,
        "current_portal_url":norm(current_url),"portal_validation_clean":clean,"part_a_final":parta,"part_b_final":partb,
        "work_packages_final":wp,"budget_final":budget,"participants_final":participants,"ethics_security_final":ethics,
        "annexes_final":annexes,"no_test_or_provisional_data":no_test,"proposal_editable":editable,
        "no_final_receipt":no_receipt,"note":norm(note) or None,"external_submission_performed":False,"external_receipt_obtained":False
    }
    integrity_sha=sha(integrity_payload)
    run_basis={"stage":74,"contract":"stage74-v1.0-final-pre-submission-integrity","stage73_run_id":s73_id,
               "stage73_package_manifest_sha256":manifest_sha,"stage73_completeness_evidence_sha256":evidence_sha,
               "final_integrity_evidence_sha256":integrity_sha}
    fingerprint=sha(run_basis)

    if st.button("🛡️ Confirm & persist Stage 74 final pre-submission integrity",type="primary",use_container_width=True,disabled=not ready):
        out={
            "user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id,"stage73_run_id":s73_id,
            "stage72_run_id":s72_id or None,"stage71_run_id":s71_id or None,"stage":74,"integrity_version":"stage74-v1.0",
            "opportunity_identity":identity,"official_deadline":str(deadline)[:10] if deadline else None,
            "application_reference":app_ref,"current_portal_url":norm(current_url),"run_status":"COMPLETED",
            "integrity_outcome":"FINAL_PRE_SUBMISSION_INTEGRITY_CONFIRMED","portal_validation_clean":True,
            "production_data_final":True,"no_test_or_provisional_data":True,"proposal_editable":True,
            "external_submission_performed":False,"external_receipt_obtained":False,
            "stage73_package_manifest_sha256":manifest_sha,"stage73_completeness_evidence_sha256":evidence_sha,
            "final_integrity_evidence_sha256":integrity_sha,"run_fingerprint":fingerprint,
            "integrity_payload":integrity_payload,"run_payload":run_basis,"confirmed_at":now(),"completed_at":now(),"created_at":now(),"updated_at":now()
        }
        try:
            # Stage 74 v1.1 persistence:
            # Use direct table insert, matching the working Stage 73 fallback pattern.
            # This avoids AUTH_REQUIRED from an RPC that depends on auth.uid()
            # when the app is using its own persisted user_id/session model.
            existing_same = (
                db()
                .table("stage74_final_pre_submission_integrity_runs")
                .select("*")
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .eq("opportunity_lock_id", lock_id)
                .eq("stage73_run_id", s73_id)
                .eq("run_fingerprint", fingerprint)
                .limit(1)
                .execute()
            ).data or []

            if not existing_same:
                db().table("stage74_final_pre_submission_integrity_runs").insert(out).execute()

            st.success("Stage 74 persisted — FINAL_PRE_SUBMISSION_INTEGRITY_CONFIRMED.")
            st.rerun()
        except Exception as e:
            st.error(
                "Stage 74 direct persistence failed. "
                f"{type(e).__name__}: {str(e)[:1500]}"
            )

existing=rows("stage74_final_pre_submission_integrity_runs",{"user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id,"stage73_run_id":s73_id},"created_at",1)
if existing:
    r=existing[0]; st.divider(); st.subheader("Stage 74 outcome")
    st.success(f"Run ID: {r.get('id')} — Outcome: {r.get('integrity_outcome')}")
    x1,x2,x3,x4=st.columns(4)
    x1.metric("Outcome",r.get("integrity_outcome")); x2.metric("Production final?","YES" if r.get("production_data_final") else "NO")
    x3.metric("Submitted?","YES" if r.get("external_submission_performed") else "NO"); x4.metric("Receipt?","YES" if r.get("external_receipt_obtained") else "NO")
    st.write(f"**Final integrity SHA256:** `{r.get('final_integrity_evidence_sha256')}`")
    st.write(f"**Run fingerprint:** `{r.get('run_fingerprint')}`")

st.caption("Invariantă Stage 74: integrity confirmation is NOT evidence of submission. Stage 74 cannot execute Submit.")
