"""
Sidebar component for the Ecom-Research application
"""
import streamlit as st

def render_sidebar():
    """
    Renders the sidebar for the Ecom-Research application
    """
    with st.sidebar:
        st.title("Ecom-Research")
        st.caption("Pesquisa Estratégica para E-commerce")
        
        st.markdown("---")
        
        # Filter options
        st.subheader("Filtros")
        
        st.selectbox(
            "Idioma",
            options=["Português", "English"],
            index=0
        )
        
        st.multiselect(
            "Países",
            options=["Brasil", "Portugal", "EUA", "Espanha", "Global"],
            default=["Brasil", "Portugal"]
        )
        
        st.markdown("---")
        
        # Data sources
        st.subheader("Fontes de Dados")
        
        st.checkbox("Google Trends", value=True)
        st.checkbox("Google Ads Planner", value=True)
        st.checkbox("TikTok Ads", value=True)
        st.checkbox("Meta Ads", value=True)
        st.checkbox("Reddit", value=False)
        st.checkbox("YouTube", value=False)
        
        st.markdown("---")
        
        # About section
        st.info(
            """
            🔎 **Ecom-Research** é uma aplicação de pesquisa estratégica para e-commerce, 
            combinando scraping, análise de tendências e IA.
            """
        )
        
        st.markdown("v0.1 - MVP")
