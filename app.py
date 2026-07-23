import json, os
import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="GrantAI Europe", page_icon="🇪🇺", layout="wide")

PROFILE = {
    "organisation": {
        "legal_name": "II Ciobotaru Viorel Razvan Ionut",
        "country": "Romania",
        "type": "SME / întreprindere individuală",
        "capabilities": ["agriculture", "smart greenhouse", "renewable energy", "battery storage", "AI automation"],
    },
    "project": {
        "name": "GreenRise",
        "summary": "Seră inteligentă cu energie regenerabilă, baterii și automatizare AI.",
        "keywords": ["agriculture", "greenhouse", "energy", "battery", "AI", "rural", "agrifood"],
    },
}

def secret(name, default=""):
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)

def run_ai(task, opportunity):
    key = secret("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Lipsește OPENAI_API_KEY în Streamlit Secrets.")
    model = secret("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=key)
    response = client.responses.create(
        model=model,
        instructions=(
            "Ești consultant senior pentru finanțări UE. Scrie în română. "
            "Nu inventa eligibilitate, parteneri, bugete, certificări sau rezultate. "
            "Pentru informații lipsă scrie [DE COMPLETAT]. "
            "Separă faptele confirmate de presupuneri și riscuri."
        ),
        input=f"""
SARCINĂ:
{task}

ORGANIZAȚIE:
{json.dumps(PROFILE["organisation"], ensure_ascii=False, indent=2)}

PROIECT:
{json.dumps(PROFILE["project"], ensure_ascii=False, indent=2)}

APEL:
{json.dumps(opportunity, ensure_ascii=False, indent=2)}
""",
    )
    return response.output_text

st.title("🇪🇺 GrantAI Europe — Etapa 3")
st.caption("Analiză și redactare cu AI pentru apelurile europene")

tab1, tab2, tab3 = st.tabs(["Profil", "Analiză AI", "Generator AI"])

with tab1:
    st.subheader("Organizație")
    st.json(PROFILE["organisation"])
    st.subheader("Proiect")
    st.json(PROFILE["project"])
    if secret("OPENAI_API_KEY"):
        st.success("OpenAI API este configurat.")
    else:
        st.warning("Trebuie configurată cheia OpenAI în Streamlit Secrets.")

with tab2:
    st.subheader("Analiză AI a unui apel")
    reference = st.text_input("Referință apel", "HORIZON-CL6-...")
    title = st.text_input("Titlu apel")
    programme = st.text_input("Program", "Horizon Europe")
    deadline = st.text_input("Deadline")
    description = st.text_area("Descrierea apelului", height=220)

    opportunity = {
        "reference": reference,
        "title": title,
        "programme": programme,
        "deadline": deadline,
        "description": description,
    }

    if st.button("Rulează analiza AI", type="primary"):
        task = """
Realizează:
1. verdict GO / CONDITIONAL GO / NO-GO;
2. potrivirea proiectului cu apelul;
3. întrebările de eligibilitate;
4. punctele forte;
5. lacunele și riscurile;
6. tipurile de parteneri necesari;
7. documentele și dovezile lipsă;
8. zece acțiuni următoare;
9. scor de pregătire 0-100, explicat.
"""
        with st.spinner("AI analizează apelul..."):
            try:
                st.session_state["analysis"] = run_ai(task, opportunity)
            except Exception as exc:
                st.error(str(exc))

    if st.session_state.get("analysis"):
        st.markdown(st.session_state["analysis"])
        st.download_button(
            "Descarcă analiza",
            st.session_state["analysis"],
            file_name="analiza_ai.md",
            mime="text/markdown",
        )

with tab3:
    st.subheader("Generator AI")
    call_reference = st.text_input("Referință", key="g_ref")
    call_title = st.text_input("Titlu", key="g_title")
    call_description = st.text_area("Descriere apel", height=180, key="g_desc")
    document_type = st.selectbox(
        "Document",
        [
            "Concept de proiect",
            "Excellence",
            "Impact",
            "Implementation",
            "Work packages și deliverables",
            "Plan de impact și KPI",
            "Registru de riscuri",
        ],
    )

    opportunity2 = {
        "reference": call_reference,
        "title": call_title,
        "description": call_description,
    }

    if st.button("Generează documentul", type="primary"):
        task = f"""
Generează: {document_type}.
Textul trebuie să fie un prim draft profesional.
Folosește tabele Markdown unde sunt utile.
Mapează conținutul la cerințele apelului.
Marchează orice informație nesusținută cu [DE COMPLETAT].
Încheie cu un checklist de validare factuală.
"""
        with st.spinner("AI redactează..."):
            try:
                st.session_state["draft"] = run_ai(task, opportunity2)
            except Exception as exc:
                st.error(str(exc))

    if st.session_state.get("draft"):
        st.markdown(st.session_state["draft"])
        st.download_button(
            "Descarcă draftul",
            st.session_state["draft"],
            file_name="grantai_draft.md",
            mime="text/markdown",
        )

st.divider()
st.caption("AI pregătește drafturi. Eligibilitatea, bugetul și depunerea finală necesită verificare umană.")
