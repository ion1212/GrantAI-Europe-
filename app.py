import streamlit as st

st.set_page_config(
    page_title="GrantAI Europe",
    page_icon="🇪🇺",
    layout="wide"
)

st.title("🇪🇺 GrantAI Europe")
st.caption("Agent AI pentru identificarea și pregătirea proiectelor europene")

profile = {
    "organisation": {
        "legal_name": "II Ciobotaru Viorel Razvan Ionut",
        "country": "Romania",
        "organisation_type": "SME / întreprindere individuală",
        "capabilities": [
            "agriculture",
            "smart greenhouse",
            "renewable energy",
            "battery storage",
            "AI automation"
        ]
    },
    "project": {
        "name": "GreenRise",
        "summary": "Seră inteligentă cu energie regenerabilă, baterii și automatizare AI.",
        "keywords": [
            "agriculture",
            "greenhouse",
            "energy",
            "battery",
            "AI"
        ]
    }
}

tab1, tab2, tab3 = st.tabs([
    "Profil",
    "Apeluri",
    "Generator"
])

with tab1:
    st.subheader("Organizație")
    st.json(profile["organisation"])

    st.subheader("Proiect principal")
    st.json(profile["project"])

with tab2:
    st.subheader("Căutare apeluri europene")
    keyword = st.text_input(
        "Cuvinte-cheie",
        "agriculture energy AI"
    )

    if st.button("Caută apeluri"):
        st.success(
            f"Căutarea pentru „{keyword}” este pregătită."
        )
        st.info(
            "În etapa următoare conectăm aplicația la Funding & Tenders Portal."
        )

with tab3:
    st.subheader("Generator proiect")

    if st.button("Generează structură proiect"):
        st.markdown("""
### Excellence
- Obiective
- Ambiție
- Metodologie
- Stadiul actual al tehnologiei

### Impact
- Rezultate estimate
- Indicatori de performanță
- Exploatare
- Diseminare

### Implementation
- Work packages
- Milestones
- Deliverables
- Riscuri
- Buget
- Parteneri
""")

st.divider()
st.caption(
    "Declarațiile juridice, datele financiare și depunerea finală trebuie aprobate de o persoană autorizată."
)
