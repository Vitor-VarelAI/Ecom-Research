"""
Ecom-Research - Main Streamlit Application
"""
import streamlit as st
from components.sidebar import render_sidebar

def main():
    # Set page configuration
    st.set_page_config(
        page_title="Ecom-Research - Pesquisa Estratégica para E-commerce",
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Custom CSS
    st.markdown("""
        <style>
        .main-header {
            font-size: 2.5rem;
            margin-bottom: 1rem;
        }
        .sub-header {
            font-size: 1.5rem;
            margin-bottom: 2rem;
            color: #4d4d4d;
        }
        </style>
        """, unsafe_allow_html=True)

    # Sidebar
    render_sidebar()

    # Main content
    st.markdown('<h1 class="main-header">Ecom-Research</h1>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Pesquisa Estratégica para E-commerce</p>', unsafe_allow_html=True)
    
    # Keyword search section
    st.subheader("🔍 Pesquisa por palavra-chave")
    keyword = st.text_input("Digite uma palavra-chave de produto ou nicho", placeholder="Ex: mochila personalizada")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Pesquisar", use_container_width=True):
            if keyword:
                st.session_state.search_keyword = keyword
                st.info(f"Pesquisando por: {keyword}")
                # Here we'll call the search functionality once implemented
            else:
                st.error("Por favor, digite uma palavra-chave")
    
    with col2:
        st.button("Limpar", use_container_width=True)
    
    # Results area (placeholder)
    if st.session_state.get('search_keyword'):
        st.subheader(f"Resultados para '{st.session_state.search_keyword}'")
        
        # Tabs for different result types
        tab1, tab2, tab3, tab4 = st.tabs(["Google Trends", "Volume & CPC", "Ads Sociais", "Feedback"])
        
        with tab1:
            st.write("Dados do Google Trends serão exibidos aqui")
            st.line_chart({"data": [10, 20, 15, 30, 25, 38, 40]})
            
        with tab2:
            st.write("Dados de volume e CPC serão exibidos aqui")
            st.bar_chart({"Volume": [100, 200, 300, 400], "CPC": [2, 1.5, 3, 4]})
            
        with tab3:
            st.write("Anúncios sociais virais serão exibidos aqui")
            
        with tab4:
            st.write("Análise de feedback será exibida aqui")
        
        # Export option
        st.download_button(
            label="Exportar como CSV",
            data="placeholder,data",
            file_name=f"{st.session_state.search_keyword}_results.csv",
            mime="text/csv",
            disabled=True
        )

if __name__ == "__main__":
    # Initialize session state if not exists
    if 'search_keyword' not in st.session_state:
        st.session_state.search_keyword = None
        
    main()
