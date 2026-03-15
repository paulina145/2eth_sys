import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILO
# ==========================================
st.set_page_config(page_title="BioSTEAM Web Simulator", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_style_all=True)

# ==========================================
# 2. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# ==========================================
def run_simulation(flow_ethanol, temp_input, p_flash):
    # Limpieza del flowsheet para permitir re-ejecución sin errores de ID
    bst.main_flowsheet.clear() 
    
    # Configuración Termodinámica
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
    
    # Manejo de error de Duty: Q=0 para adiabatic flash
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_caliente", "Vinazas"), P=p_flash * 101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Creación y Ejecución del Sistema
    sys = bst.System("eth_sys", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    
    return sys

# ==========================================
# 3. INTERFAZ DE USUARIO (SIDEBAR)
# ==========================================
st.sidebar.header("🛠️ Parámetros de Proceso")
f_eth = st.sidebar.slider("Flujo Etanol en Alimento (kg/h)", 10, 300, 100)
t_in = st.sidebar.slider("Temperatura Alimento (°C)", 10, 50, 25)
p_v1 = st.sidebar.number_input("Presión de Flash (atm)", 0.1, 2.0, 1.0)

# ==========================================
# 4. EJECUCIÓN Y VISUALIZACIÓN
# ==========================================
st.title("⚗️ Planta de Separación de Etanol")
st.info("Simulación termodinámica profesional potenciada por BioSTEAM y Gemini IA.")

if st.button("🚀 Ejecutar Simulación"):
    try:
        with st.spinner('Calculando balances de materia y energía...'):
            sys = run_simulation(f_eth, t_in, p_v1)
            
            # --- Métricas Principales ---
            prod = bst.main_flowsheet.stream.Producto_Final
            col1, col2, col3 = st.columns(3)
            col1.metric("Flujo Producto", f"{prod.F_mass:.2f} kg/h")
            col2.metric("Pureza Etanol", f"{prod.imass['Ethanol']/prod.F_mass:.1%}")
            col3.metric("Temp. Salida", f"{prod.T - 273.15:.1f} °C")

            # --- Diagrama de Flujo (PFD) ---
            st.subheader("📊 Diagrama de Flujo del Proceso (PFD)")
            st.graphviz_chart(sys.diagram('dot'))

            # --- Reportes ---
            st.subheader("📋 Resultados Detallados")
            tab1, tab2 = st.tabs(["Balance de Materia", "Consumo Energético"])
            
            with tab1:
                data_m = []
                for s in sys.streams:
                    if s.F_mass > 0:
                        data_m.append({
                            "Corriente": s.ID,
                            "F [kg/h]": round(s.F_mass, 2),
                            "T [°C]": round(s.T - 273.15, 2),
                            "% Etanol": f"{s.imass['Ethanol']/s.F_mass:.1%}"
                        })
                df_m = pd.DataFrame(data_m)
                st.table(df_m)

            with tab2:
                data_e = []
                for u in sys.units:
                    # Acceso seguro a energía térmica
                    duty = u.design_results.get('Heat duty', 0) / 3600 if hasattr(u, 'design_results') else 0
                    if abs(duty) > 0.01:
                        data_e.append({"Equipo": u.ID, "Carga Térmica [kW]": round(duty, 2)})
                st.dataframe(pd.DataFrame(data_e))

            # --- Integración con Gemini IA ---
            st.divider()
            st.subheader("🤖 Análisis del Tutor de Ingeniería (IA)")
            
            if "GEMINI_API_KEY" in st.secrets:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                model = genai.GenerativeModel('gemini-2.5-pro')
                
                contexto = f"""
                Resultados de simulación:
                - Flujo alimento: {f_eth} kg/h
                - Pureza obtenida: {prod.imass['Ethanol']/prod.F_mass:.1%}
                - Presión Flash: {p_v1} atm
                Analiza si estos parámetros son óptimos para una separación flash y da una recomendación técnica breve.
                """
                
                respuesta = model.generate_content(contexto)
                st.write(respuesta.text)
            else:
                st.warning("Configura GEMINI_API_KEY en los Secrets de Streamlit para activar el análisis por IA.")

    except Exception as e:
        st.error(f"Error en la simulación: {e}")

else:
    st.write("Configura los parámetros en la izquierda y presiona 'Ejecutar'.")
