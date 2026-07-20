import json
from pathlib import Path
import streamlit as st

BASE = Path(__file__).parent
PROFILE = BASE / "data" / "profile.json"

st.set_page_config(page_title="GrantAI Europe", page_icon="🇪🇺", layout="wide")
st.title("🇪🇺 GrantAI Europe")
st.caption("Agent AI pentru identificarea și pregătirea proiectelor europene")

profile = json.loads(PROFILE.read_text(encoding="utf-8"))

tab1, tab2, tab3 = st.tabs(["Profil", "Apeluri", "Generator"])

with tab1:
    st.subheader("Organizație")
    st.write(profile["organisation"])
    st.subheader("Proiect principal")
    st.write(profile["projects"][0])

with tab2:
    st.info("Modulul de căutare automată a apelurilor va fi conectat în etapa următoare.")
    keyword = st.text_input("Cuvinte-cheie", "agriculture energy AI")
    if st.button("Caută apeluri"):
        st.success(f"Căutarea pentru «{keyword}» este pregătită pentru integrarea API.")

with tab3:
    st.info("Generatorul AI va folosi cheia OpenAI configurată în cloud.")
    if st.button("Generează structură proiect"):
        st.markdown("""
### Structură inițială
1. Excellence
2. Impact
3. Implementation
4. Work packages
5. Buget
6. Riscuri
7. Parteneri
8. Checklist de conformitate
""")

st.divider()
st.caption("Declarațiile juridice, datele financiare și depunerea finală trebuie aprobate de o persoană autorizată.")
