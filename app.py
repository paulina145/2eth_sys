import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# ==========================================
# 1. CONFIGURACIÓN INICIAL
# ==========================================
st.set_page_config(page_title="BioSTEAM Explorer", layout="wide")

# Forzamos el borrado de cualquier configuración previa de BioSTEAM al iniciar
if 'first_run' not in st.session_state:
    bst.main_flowsheet.clear()
    st.session_state['first_run'] = True

# ==========================================
# 2. FUNCIÓN DE SIMULACIÓN
# ==========================================
def run_simulation(flow_ethanol, temp_input, p_flash):
    # Limpiar flowsheet global para evitar IDs duplicados en cada slider-change
    bst.main_flowsheet.clear() 
    
    # Configuración de componentes
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Definición de Corrientes
    mosto = bst.Stream("MOSTO", 
                       Water=1000 - flow_ethanol, 
                       Ethanol=flow_ethanol, 
                       units="kg/hr", 
                       T=temp_input + 273.15, 
                       P=101325)
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15, P=300000)

    # Unidades de Proceso
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    W210 = bst.HXprocess("W210", 
                         ins=(P100-0, vinazas_retorno), 
                         outs=("Mosto_Pre", "Drenaje"), 
                         phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15

    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=92+273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=p_flash * 101325)
    
    # Manejo de Energía: Usamos Q=0 (Adiabático)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_caliente", "Vinazas"), P=p_flash * 101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Crear sistema y simular
    sys = bst.System("eth_sys", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    
    return sys

# ==========================================
# 3. INTERFAZ DE USUARIO
# ==========================================
st.title("🧪 BioSTEAM: Simulación de Separación")
st.sidebar.header("🎛️ Control de Variables")

f_eth = st.sidebar.slider("Flujo Etanol (kg/h)", 50, 250, 100)
t_in = st.sidebar.slider("Temp. Alimento (°C)", 15, 45, 25)
p_v1 = st.sidebar.slider("Presión Flash (atm)", 0.5, 1.5, 1.0)

if st.button("▶️ Ejecutar Simulación"):
    try:
        with st.spinner('Resolviendo balances...'):
            sys = run_simulation(f_eth, t_in, p_v1)
            
            # --- Métricas ---
            prod = bst.main_flowsheet.stream.Producto_Final
            c1, c2, c3 = st.columns(3)
            c1.metric("Masa Producto", f"{prod.F_mass:.1f} kg/h")
            c2.metric("Pureza Etanol", f"{(prod.imass['Ethanol']/prod.F_mass)*100:.1f}%")
            c3.metric("Energía Bomba P100", f"{sys.units[0].power_utility.rate:.2f} kW")

            # --- Diagrama ---
            st.subheader("🖼️ Diagrama de Proceso")
            # BioSTEAM genera un objeto Graphviz que Streamlit renderiza nativamente
            st.graphviz_chart(sys.diagram('dot'))

            # --- Tablas de Resultados ---
            col_tab1, col_tab2 = st.columns(2)
            
            with col_tab1:
                st.write("**Balance de Materia**")
                m_data = [{"ID": s.ID, "Flow": round(s.F_mass, 1), "EtOH%": f"{s.imass['Ethanol']/s.F_mass:.1%}"} 
                          for s in sys.streams if s.F_mass > 0]
                st.table(pd.DataFrame(m_data))

            with col_tab2:
                st.write("**Balance de Energía (Equipos)**")
                e_data = []
                for u in sys.units:
                    # Método seguro para extraer duty evitando errores de atributo
                    duty = u.design_results.get('Heat duty', 0) / 3600 if hasattr(u, 'design_results') else 0
                    if abs(duty) > 0.01:
                        e_data.append({"Equipo": u.ID, "Duty [kW]": round(duty, 2)})
                st.dataframe(pd.DataFrame(e_data))

            # --- IA INTEGRATION ---
            st.divider()
            st.subheader("🤖 Consultoría IA (Gemini)")
            
            if "GEMINI_API_KEY" in st.secrets:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                model = genai.GenerativeModel('gemini-pro')
                
                prompt = f"""
                Analiza como ingeniero químico estos resultados:
                - Flujo Etanol Alimento: {f_eth} kg/h
                - Pureza Final: {(prod.imass['Ethanol']/prod.F_mass)*100:.1f}%
                - Presión Flash: {p_v1} atm
                ¿Es coherente el resultado? Da un consejo para mejorar la pureza.
                """
                response = model.generate_content(prompt)
                st.info(response.text)
            else:
                st.warning("⚠️ Clave API no encontrada en Secrets.")

    except Exception as e:
        st.error(f"Error técnico: {e}")

else:
    st.markdown("> Ajusta los valores en el panel lateral y presiona el botón para iniciar.")
