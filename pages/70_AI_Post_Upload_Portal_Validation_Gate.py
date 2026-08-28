import os, json, hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse
import streamlit as st
from supabase import create_client

# STAGE 70 v1.0 — AI Post-Upload Portal Validation Gate
# Consumes ONLY Stage 69 PORTAL_UPLOAD_CONFIRMED.
# Records explicit human-observed post-upload validation evidence.
# It never logs in, uploads, submits, signs, or collects credentials/secrets.

st.set_page_config(page_title="Stage 70 v1.0 — Post-Upload Portal Validation", page_icon="🔎", layout="wide")
st.title("🔎 Etapa 70 v1.0 — AI Post-Upload Portal Validation Gate")
st.caption("Validează starea draftului după upload și înainte de orice autorizare de Submit. Stage 70 nu execută Submit.")

def secret(name, default=""):
    try: return str(st.secrets.get(name, default))
    except Exception: return os.getenv(name, default)

@st.cache_resource
def get_supabase():
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"))

def norm(v): return str(v or "").strip()
def now_iso(): return datetime.now(timezone.utc).isoformat()
def as_dict(v):
    if isinstance(v, dict): return v
    if isinstance(v, str) and v.strip():
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception: return {}
    return {}
def stable_sha256(v: Any):
    return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def rows(table, filters=None, order="created_at", limit=1000):
    q=supabase.table(table).select("*")
    for k,v in (filters or {}).items():
        if v not in (None,""): q=q.eq(k,v)
    if order: q=q.order(order,desc=True)
    if limit: q=q.limit(limit)
    return q.execute().data or []
def restore_auth(sb):
    s=st.session_state.get("auth_session")
    if not s:return
    a=s.get("access_token") if isinstance(s,dict) else getattr(s,"access_token",None)
    r=s.get("refresh_token") if isinstance(s,dict) else getattr(s,"refresh_token",None)
    if a and r:
        try: sb.auth.set_session(a,r)
        except Exception: pass
def uid(sb):
    for k in ("auth_user","user"):
        u=st.session_state.get(k)
        if isinstance(u,dict) and u.get("id"): return str(u["id"])
        if getattr(u,"id",None): return str(u.id)
    for k in ("user_id","auth_user_id"):
        if st.session_state.get(k): return str(st.session_state[k])
    try:
        u=sb.auth.get_user().user
        return str(u.id) if u and getattr(u,"id",None) else None
    except Exception:return None
def future_deadline(v):
    try:return date.fromisoformat(str(v)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:return False
def official_url(v):
    try:
        p=urlparse(norm(v)); h=(p.hostname or "").lower().strip(".")
        return p.scheme.lower()=="https" and (h=="europa.eu" or h.endswith(".europa.eu"))
    except Exception:return False
def label(p): return f"{p.get('name') or 'Project'} — {str(p.get('id') or '')[:8]}"

try: supabase=get_supabase()
except Exception as e: st.error(f"Supabase initialization failed: {type(e).__name__}: {e}"); st.stop()
restore_auth(supabase)
user_id=uid(supabase)
if not user_id: st.error("Stage 70 BLOCKED: utilizator neidentificat."); st.stop()
projects=rows("projects",{"user_id":user_id},"updated_at",200)
if not projects: st.warning("Nu există proiecte."); st.stop()
pm={label(p):p for p in projects}; project=pm[st.selectbox("Project",list(pm.keys()),key="stage70_project")]; project_id=str(project["id"])
locks=rows("selected_opportunity_locks",{"user_id":user_id,"project_id":project_id,"lock_status":"ACTIVE"},"created_at",10)
if not locks: st.error("Stage 70 BLOCKED: nu există opportunity lock ACTIVE."); st.stop()
lock=locks[0]; lock_id=str(lock["id"]); identity=norm(lock.get("opportunity_identity")); deadline=lock.get("official_deadline")
st.write(f"**Locked opportunity:** {identity or '—'}"); st.write(f"**Deadline:** {str(deadline or '—')[:10]}")

s69s=rows("stage69_portal_upload_confirmation_runs",{"user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id},"created_at",100)
s69=next((r for r in s69s if norm(r.get("run_status")).upper()=="COMPLETED" and norm(r.get("upload_outcome")).upper()=="PORTAL_UPLOAD_CONFIRMED" and bool(r.get("portal_upload_performed")) and not bool(r.get("external_submission_performed")) and not bool(r.get("external_receipt_obtained"))),None)
if not s69: st.error("Stage 70 BLOCKED: lipsește Stage 69 PORTAL_UPLOAD_CONFIRMED."); st.stop()

s69id=str(s69["id"]); appref=norm(s69.get("application_reference")); draft_title=norm(s69.get("draft_title")); portal_url=norm(s69.get("current_portal_url")); file_count=int(s69.get("file_count") or 0)
stage68_hash=norm(s69.get("stage68_file_manifest_sha256")); upload_hash=norm(s69.get("upload_evidence_sha256")); s69_fp=norm(s69.get("run_fingerprint"))
up_evidence=as_dict(s69.get("upload_evidence")); up_confirm=as_dict(s69.get("upload_confirmation_payload")); s69_run=as_dict(s69.get("run_payload"))
re_upload=stable_sha256(up_evidence) if up_evidence else ""; re_confirm=stable_sha256(up_confirm) if up_confirm else ""; re_run=stable_sha256(s69_run) if s69_run else ""
expected_names=up_evidence.get("uploaded_filenames") or up_confirm.get("uploaded_filenames") or []
if isinstance(expected_names,str): expected_names=[x.strip() for x in expected_names.splitlines() if x.strip()]
if not isinstance(expected_names,list): expected_names=[]
expected_names=[norm(x) for x in expected_names if norm(x)]

checks=[]
def ck(n,p,d): checks.append({"PASS":bool(p),"Check":n,"Detail":d})
ck("ACTIVE lock",norm(lock.get("lock_status")).upper()=="ACTIVE",norm(lock.get("lock_status")))
ck("Workflow allowed",bool(lock.get("workflow_allowed")),f"workflow_allowed={bool(lock.get('workflow_allowed'))}")
ck("Deadline valid",future_deadline(deadline),str(deadline or '')[:10])
ck("Stage 69 PORTAL_UPLOAD_CONFIRMED",norm(s69.get("upload_outcome")).upper()=="PORTAL_UPLOAD_CONFIRMED",norm(s69.get("upload_outcome")))
ck("Portal upload performed",bool(s69.get("portal_upload_performed")),str(bool(s69.get("portal_upload_performed"))))
ck("No submission",not bool(s69.get("external_submission_performed")),str(bool(s69.get("external_submission_performed"))))
ck("No receipt",not bool(s69.get("external_receipt_obtained")),str(bool(s69.get("external_receipt_obtained"))))
ck("Official portal URL",official_url(portal_url),portal_url)
ck("Application reference present",len(appref)>=3,appref)
ck("Stage 68 manifest SHA256",len(stage68_hash)==64,stage68_hash[:16]+"...")
ck("Upload evidence SHA256 stable",len(upload_hash)==64 and upload_hash==re_upload,f"stored={upload_hash[:16]}..., recomputed={re_upload[:16]}...")
ck("Stage 69 run fingerprint stable",len(s69_fp)==64 and s69_fp==re_run,f"stored={s69_fp[:16]}..., recomputed={re_run[:16]}...")
gate="READY" if all(x["PASS"] for x in checks) else "BLOCKED"

existing=rows("stage70_post_upload_portal_validation_runs",{"user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id,"stage69_run_id":s69id},"created_at",1)
existing=existing[0] if existing else None
st.divider(); st.subheader("Stage 70 upstream hard-gate checks"); st.dataframe(checks,use_container_width=True,hide_index=True)
if gate=="READY": st.success("Stage 70 base gate: READY")
else: st.error("Stage 70 BLOCKED: "+"; ".join(x["Check"] for x in checks if not x["PASS"])); st.stop()

if existing:
    st.subheader("Stage 70 outcome")
    st.success(f"Stage 70 este deja persistată. Run ID: {existing['id']} — Outcome: {existing.get('validation_outcome')}")
    a,b,c,d=st.columns(4); a.metric("Outcome",existing.get("validation_outcome")); b.metric("Portal upload?","YES" if existing.get("portal_upload_verified") else "NO"); c.metric("Submitted?","YES" if existing.get("external_submission_performed") else "NO"); d.metric("Blocking errors?","YES" if existing.get("blocking_validation_errors") else "NO")
    st.write(f"**Application reference:** `{existing.get('application_reference')}`")
    st.write(f"**Post-upload evidence SHA256:** `{existing.get('post_upload_evidence_sha256')}`")
    st.success("Stage 70 PORTAL_POST_UPLOAD_VALIDATED. A future Stage 71 may consume this validation. No submission occurred at Stage 70.")
    st.stop()

st.divider(); st.subheader("Observed official portal state after upload")
st.info("Verifică manual draftul în Funding & Tenders. Stage 70 înregistrează doar ceea ce confirmi că vezi; nu accesează sesiunea și nu apasă Submit.")
st.text_input("Current official portal URL",value=portal_url,disabled=True)
st.text_input("Application / draft reference",value=appref,disabled=True)
if expected_names:
    st.write("**Stage 69 uploaded filename(s):**")
    for n in expected_names: st.code(n)
else: st.write(f"**Stage 69 file count:** {file_count}")

portal_names=st.text_area("Filename(s) currently visible in portal",value="\n".join(expected_names),key="stage70_names")
observed=[x.strip() for x in portal_names.splitlines() if x.strip()]
names_match=(not expected_names and len(observed)==file_count and file_count>0) or (sorted(observed)==sorted(expected_names) and len(observed)>0)
if names_match: st.success("Visible uploaded filename(s) match Stage 69 evidence.")
else: st.warning("Visible filename set does not match Stage 69 evidence.")

c1=st.checkbox("I confirm the uploaded file set is still attached to this exact draft.",key="s70_c1")
c2=st.checkbox("I confirm the portal shows no blocking validation error for the uploaded Part B/package.",key="s70_c2")
c3=st.checkbox("I confirm the proposal is still editable and has NOT been finally submitted.",key="s70_c3")
c4=st.checkbox("I confirm no final submission receipt has been issued.",key="s70_c4")
phrase=st.text_input("Confirmation phrase",placeholder="Type exactly: CONFIRM STAGE 70 POST UPLOAD VALIDATION",key="s70_phrase")
note=st.text_area("Optional validation note",placeholder="Optional non-sensitive note about the observed portal validation state.",key="s70_note")
ready=names_match and c1 and c2 and c3 and c4 and phrase=="CONFIRM STAGE 70 POST UPLOAD VALIDATION"

if st.button("🔎 Confirm & persist Stage 70 post-upload validation",disabled=not ready,use_container_width=True):
    evidence={"application_reference":appref,"portal_url":portal_url,"visible_filenames":observed,"filenames_match":names_match,"portal_upload_verified":True,"blocking_validation_errors":False,"proposal_editable":True,"external_submission_performed":False,"external_receipt_obtained":False,"note":norm(note),"observed_at":now_iso()}
    evhash=stable_sha256(evidence)
    validation={"stage69_run_id":s69id,"stage69_upload_evidence_sha256":upload_hash,"stage68_file_manifest_sha256":stage68_hash,"application_reference":appref,"post_upload_evidence_sha256":evhash,"validation_version":"stage70-v1.0"}
    valhash=stable_sha256(validation)
    run_payload={"stage":70,"validation_outcome":"PORTAL_POST_UPLOAD_VALIDATED","validation_fingerprint":valhash,"post_upload_evidence_sha256":evhash,"stage69_run_id":s69id,"application_reference":appref}
    runfp=stable_sha256(run_payload)
    payload={"user_id":user_id,"project_id":project_id,"opportunity_lock_id":lock_id,"stage69_run_id":s69id,"stage68_run_id":str(s69.get('stage68_run_id') or ''),"stage67_run_id":str(s69.get('stage67_run_id') or ''),"stage66_run_id":str(s69.get('stage66_run_id') or ''),"stage60_run_id":str(s69.get('stage60_run_id') or ''),"opportunity_identity":identity,"official_deadline":str(deadline or '')[:10],"application_reference":appref,"draft_title":draft_title,"current_portal_url":portal_url,"validation_outcome":"PORTAL_POST_UPLOAD_VALIDATED","file_count":len(observed),"portal_upload_verified":True,"blocking_validation_errors":False,"proposal_editable":True,"external_submission_performed":False,"external_receipt_obtained":False,"stage68_file_manifest_sha256":stage68_hash,"stage69_upload_evidence_sha256":upload_hash,"post_upload_evidence_sha256":evhash,"validation_fingerprint":valhash,"run_fingerprint":runfp,"post_upload_evidence":evidence,"validation_payload":validation,"run_payload":run_payload,"confirmed_at":now_iso(),"completed_at":now_iso()}
    try:
        res=supabase.rpc("persist_stage70_post_upload_portal_validation",{"p_payload":payload}).execute()
        st.success("Stage 70 PORTAL_POST_UPLOAD_VALIDATED a fost persistată.")
        st.rerun()
    except Exception as e: st.error(f"Stage 70 persistence failed: {type(e).__name__}: {e}")
