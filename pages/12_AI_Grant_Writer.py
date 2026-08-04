import os, json
from datetime import datetime
import streamlit as st
from supabase import create_client
from openai import OpenAI

st.set_page_config(page_title="Etapa 12 — AI Grant Writer",page_icon="✍️",layout="wide")
SECTIONS={"excellence":"Excellence","impact":"Impact","implementation":"Implementation","budget":"Budget Justification","ethics":"Ethics","risks":"Risk Management","dissemination":"Dissemination, Exploitation & Communication"}

def sec(n,d=None):
    try:return st.secrets.get(n,d)
    except:return os.getenv(n,d)

@st.cache_resource
def db():
    return create_client(sec("SUPABASE_URL"),sec("SUPABASE_KEY") or sec("SUPABASE_ANON_KEY"))

def data(r): return r.data if isinstance(getattr(r,"data",None),list) else []

def user_id(sb):
    for k in ("user","auth_user","current_user"):
        u=st.session_state.get(k)
        if isinstance(u,dict) and u.get("id"): return str(u["id"])
        if getattr(u,"id",None): return str(u.id)
    try:return str(sb.auth.get_user().user.id)
    except:return None

def opp_id(o):
    for k in ("opportunity_identity","identity","call_id","identifier","code","id"):
        if o.get(k) not in (None,""): return str(o[k])
    return str(o.get("title") or o.get("name") or "opportunity")[:240]

def project_name(p):
    n=p.get("name") or p.get("project_name") or p.get("title") or "Project"
    return f"{n} — {str(p.get('id',''))[:8]}"

def opp_name(o):
    n=o.get("title") or o.get("name") or o.get("topic") or "Funding opportunity"
    return (f"{o.get('match_score')}% · " if o.get("match_score") is not None else "")+str(n)

def opportunities(sb,uid,pid):
    for t in ("selected_opportunities","opportunities","funding_opportunities","grant_matches"):
        try:
            try:r=sb.table(t).select("*").eq("user_id",uid).eq("project_id",pid).execute()
            except:r=sb.table(t).select("*").execute()
            if data(r): return t,data(r)
        except:pass
    return None,[]

def document(sb,uid,pid,oid,title):
    r=sb.table("grant_writer_documents").select("*").eq("user_id",uid).eq("project_id",pid).eq("opportunity_identity",oid).limit(1).execute()
    if data(r):return data(r)[0]
    r=sb.table("grant_writer_documents").insert({"user_id":uid,"project_id":pid,"opportunity_identity":oid,"document_title":title}).execute()
    return data(r)[0]

def section(sb,did,key):
    r=sb.table("grant_writer_sections").select("*").eq("document_id",did).eq("section_key",key).limit(1).execute()
    return data(r)[0] if data(r) else None

def save(sb,uid,doc,pid,oid,key,title,content,note):
    old=section(sb,doc["id"],key); ver=int((old or {}).get("version_no") or 0)+1
    if old and old.get("content"):
        sb.table("grant_writer_versions").insert({"user_id":uid,"document_id":doc["id"],"section_key":key,"version_no":int(old.get("version_no") or 1),"content":old["content"],"change_note":note}).execute()
    payload={"user_id":uid,"document_id":doc["id"],"project_id":pid,"opportunity_identity":oid,"section_key":key,"section_title":title,"content":content,"version_no":ver,"updated_at":datetime.utcnow().isoformat()}
    if old:sb.table("grant_writer_sections").update(payload).eq("id",old["id"]).execute()
    else:sb.table("grant_writer_sections").insert(payload).execute()

def generate(client,model,p,o,title,instructions,current):
    system="You are an expert EU grant proposal writer. Never invent eligibility, call rules, partners, budgets, TRLs or results. Mark missing evidence [TO CONFIRM]. Write evaluator-oriented proposal text."
    prompt=f"PROJECT:\n{json.dumps(p,default=str)[:10000]}\n\nOPPORTUNITY:\n{json.dumps(o,default=str)[:10000]}\n\nSECTION: {title}\nCURRENT TEXT:\n{current[:10000]}\nINSTRUCTIONS:\n{instructions}\n\nReturn only the improved proposal section."
    r=client.chat.completions.create(model=model,messages=[{"role":"system","content":system},{"role":"user","content":prompt}],temperature=.25)
    return r.choices[0].message.content.strip()

st.title("✍️ Etapa 12 — AI Grant Writer")
st.caption("Generator pe secțiuni, editor, salvare și versionare în Supabase")
try: sb=db()
except Exception as e: st.error(f"Supabase nu este configurat: {e}"); st.stop()
uid=user_id(sb)
if not uid: st.error("Intră în cont din pagina principală și revino."); st.stop()
try: projects=data(sb.table("projects").select("*").eq("user_id",uid).execute())
except Exception as e: st.error(f"Nu pot încărca proiectele: {e}");st.stop()
if not projects:st.warning("Nu există proiecte.");st.stop()
p=st.selectbox("Project",projects,format_func=project_name);pid=str(p["id"])
source,opps=opportunities(sb,uid,pid)
if not opps:st.warning("Nu există oportunități salvate pentru proiect.");st.stop()
o=st.selectbox("Oportunitate",opps,format_func=opp_name);oid=opp_id(o)
try:doc=document(sb,uid,pid,oid,str(o.get("title") or o.get("name") or "Grant Proposal"))
except Exception as e:st.error("Rulează mai întâi stage12_schema.sql în Supabase.");st.exception(e);st.stop()
with st.expander("Datele apelului selectat"):st.json(o)
client=OpenAI(api_key=sec("OPENAI_API_KEY"));model=sec("OPENAI_MODEL","gpt-4.1-mini")
tabs=st.tabs(list(SECTIONS.values())+["Document complet","Versiuni"])
for (key,title),tab in zip(SECTIONS.items(),tabs):
    with tab:
        old=section(sb,doc["id"],key) or {}; sk=f"w_{doc['id']}_{key}"
        if sk not in st.session_state:st.session_state[sk]=old.get("content","")
        ins=st.text_area("Instrucțiuni suplimentare",key=f"i_{doc['id']}_{key}",placeholder="Ex.: impact rural, KPI măsurabili, replicare europeană.")
        c1,c2=st.columns(2)
        if c1.button(f"Generează {title}",key=f"g_{key}",use_container_width=True):
            with st.spinner("AI redactează..."):
                try:
                    txt=generate(client,model,p,o,title,ins,st.session_state[sk]);st.session_state[sk]=txt
                    save(sb,uid,doc,pid,oid,key,title,txt,"AI generation");st.success("Generat și salvat.");st.rerun()
                except Exception as e:st.error(f"Generarea a eșuat: {e}")
        if c2.button("Salvează manual",key=f"s_{key}",use_container_width=True):
            try:save(sb,uid,doc,pid,oid,key,title,st.session_state[sk],"Manual edit");st.success("Salvat.")
            except Exception as e:st.error(f"Salvarea a eșuat: {e}")
        st.text_area("Editor",key=sk,height=500)
with tabs[-2]:
    parts=[]
    for k,t in SECTIONS.items():
        s=section(sb,doc["id"],k)
        if s and s.get("content"):parts.append(f"# {t}\n\n{s['content']}")
    full="\n\n---\n\n".join(parts)
    st.text_area("Propunere consolidată",value=full,height=650)
    st.download_button("Descarcă Markdown",full.encode(),"grant_proposal.md","text/markdown",use_container_width=True)
with tabs[-1]:
    try:vs=data(sb.table("grant_writer_versions").select("*").eq("document_id",doc["id"]).order("created_at",desc=True).limit(100).execute())
    except:vs=[]
    if not vs:st.info("Nu există încă versiuni istorice.")
    for v in vs:
        with st.expander(f"{v.get('section_key')} · v{v.get('version_no')} · {v.get('created_at','')}"):
            st.caption(v.get("change_note") or "");st.write(v.get("content") or "")
st.divider()
st.caption("Drafturile AI trebuie validate față de documentația oficială a apelului.")
