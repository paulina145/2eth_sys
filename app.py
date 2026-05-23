import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
import os
from io import BytesIO

# ==========================================
# 1. CONFIGURACIÓN Y ESTILOS
# ==========================================
st.set_page_config(page_title="Concentración de Mosto", layout="wide")

st.markdown("""
    <style>
    [data-testid="stMetric"] { background-color: #f8fafc; padding: 15px; border-radius: 10px; border: 1px solid #e2e8f0; }
    [data-testid="stMetricLabel"] > div { color: #475569; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. LÓGICA DE SIMULACIÓN Y ECONOMÍA
# ==========================================
@st.cache_data
def run_simulation(t_feed, t_w220, p_v1, p_mosto, p_etanol):
    # Configuración termodinámica
    bst.main_flowsheet.clear()
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes
    mosto = bst.Stream("1-MOSTO", Water=900, Ethanol=100, units="kg/hr", T=t_feed + 273.15, price=p_mosto)
    vinazas_retorno = bst.Stream("Vinazas-Retorno", Water=200, T=95+273.15)
    prod = bst.Stream("Producto_Final", price=p_etanol)

    # Unidades de proceso
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("Mosto_Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=t_w220 + 273.15)
    V1 = bst.Flash("V1", ins=W220-0, outs=("Vapor", "Vinazas"), P=p_v1 * 101325, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs=prod, T=25 + 273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Simulación
    sys = bst.System("mosto_sys", path=(P100, W210, W220, V1, W310, P200))
    sys.simulate()
    
    # Extraer datos de balance de materia
    streams = [mosto, W220.outs[0], V1.outs[0], prod, V1.outs[1]]
    flujos = [s.F_mass for s in streams]
    temps = [s.T - 273.15 for s in streams]
    presiones = [s.P / 101325 for s in streams]
    purezas = [(s.imass['Ethanol'] / s.F_mass)*100 if s.F_mass > 0 else 0 for s in streams]
    
    df_materia = pd.DataFrame({
        "Corriente": ["Alimentación", "Salida W220", "Vapor V1", "Producto Final", "Vinazas"],
        "Flujo Masico (kg/h)": flujos,
        "Temp (°C)": temps,
        "Presión (atm)": presiones,
        "Etanol (%)": purezas
    })
    
    # Calculamos la carga térmica (duty) mediante balance de entalpía (Salida - Entrada)
    q_calor_w220 = W220.outs[0].H - W220.ins[0].H
    q_frio_w310 = W310.outs[0].H - W310.ins[0].H
    
    return sys, prod, df_materia, q_calor_w220, q_frio_w310

def calculate_economics(prod_mass, p_mosto, p_luz, p_vap, p_agu, p_eta, duty_heat, duty_cool):
    # Cálculos económicos aproximados para el dashboard
    costo_mat_prima = 1000 * p_mosto # 1000 kg/h fijos en alimentación
    costo_servicios = (abs(duty_heat)/1000) * p_vap * 0.01 + (abs(duty_cool)/1000) * p_agu * 0.01 + 50 * p_luz
    costo_total_hr = costo_mat_prima + costo_servicios
    
    costo_real_prod = costo_total_hr / prod_mass if prod_mass > 0 else 0
    ingreso_hr = prod_mass * p_eta
    utilidad_hr = ingreso_hr - costo_total_hr
    
    # KPIs anualizados (asumiendo 8000 hr/año y CAPEX de 1.5M USD)
    capex = 1500000
    utilidad_anual = utilidad_hr * 8000
    
    roi = (utilidad_anual / capex) * 100 if utilidad_anual > 0 else 0
    payback = capex / utilidad_anual if utilidad_anual > 0 else 99
    npv = utilidad_anual * 4.329 - capex # Factor VNA simple 5 años a 5%
    
    return costo_real_prod, roi, payback, npv

# ==========================================
# 3. INTERFAZ: SIDEBAR Y CONTROLES
# ==========================================
st.sidebar.title("Configuración de Parámetros")

st.sidebar.header("🎛️ Variables Operativas")
t_f = st.sidebar.slider("Temp. Alimentación Mosto (°C)", 10, 50, 25)
t_out = st.sidebar.slider("Temp. Salida W220 (°C)", 70, 110, 92)
p_v = st.sidebar.slider("Presión V100 (atm)", 0.1, 2.0, 1.0)

st.sidebar.header("💰 Variables Económicas")
p_luz = st.sidebar.slider("Precio Luz (USD/kWh)", 0.05, 0.40, 0.15)
p_vap = st.sidebar.slider("Precio Vapor (USD/ton)", 10.0, 60.0, 25.0)
p_agu = st.sidebar.slider("Precio Agua (USD/m³)", 0.5, 5.0, 1.5)
p_mos = st.sidebar.slider("Precio Mosto (USD/kg)", 0.1, 2.0, 0.5)
p_eta = st.sidebar.slider("Precio Etanol (USD/kg)", 1.0, 6.0, 3.5)

# ==========================================
# 4. DASHBOARD PRINCIPAL
# ==========================================
st.title("🎓 Sistema de Concentración de Mosto")
st.markdown("Aplicación interactiva para simular, visualizar y evaluar la concentración de mosto mediante destilación flash.")

# Ejecutar Simulación
sys, producto, df_materia, q_calor, q_frio = run_simulation(t_f, t_out, p_v, p_mos, p_eta)
prod_mass = producto.F_mass
pureza_final = (producto.imass['Ethanol'] / prod_mass) * 100 if prod_mass > 0 else 0

# Cálculos Económicos
costo_prod, roi, payback, npv = calculate_economics(prod_mass, p_mos, p_luz, p_vap, p_agu, p_eta, q_calor, q_frio)

# --- SECCIÓN: VALIDACIÓN DE RESTRICCIONES OPERATIVAS ---
if costo_prod > p_eta:
    st.error("⚠️ **Alerta Económica:** El costo real de producción es mayor que el precio de venta del etanol.")
if roi < 10:
    st.warning("⚠️ **Alerta Económica:** El ROI es menor al 10% considerado aceptable. Revise los costos.")
if t_out < 75:
    st.warning("⚠️ **Alerta Operativa:** Temperatura en W220 muy baja. Puede afectar la separación en V100.")

# --- SECCIÓN: RESULTADOS DEL PRODUCTO ---
st.subheader("📌 Condiciones de la Corriente de Producto Final")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Presión", f"{producto.P/101325:.2f} atm")
k2.metric("Temperatura", f"{producto.T-273.15:.1f} °C")
k3.metric("Flujo Básico (Másico)", f"{prod_mass:.2f} kg/h")
k4.metric("Composición Etanol", f"{pureza_final:.1f} %")

# --- SECCIÓN: INDICADORES ECONÓMICOS ---
st.subheader("💹 Indicadores de Rentabilidad")
e1, e2, e3, e4, e5 = st.columns(5)
e1.metric("Costo Real Prod.", f"${costo_prod:.2f} /kg")
e2.metric("Precio Venta Sugerido", f"${costo_prod * 1.30:.2f} /kg")
e3.metric("NPV (5 años)", f"${npv/1e6:.2f} M")
e4.metric("Payback", f"{payback:.1f} Años")
e5.metric("ROI", f"{roi:.1f} %")

# --- SECCIÓN: BALANCES Y EXPORTACIÓN ---
st.subheader("⚖️ Balances de Materia y Energía")
col_b1, col_b2 = st.columns([2, 1])

with col_b1:
    st.dataframe(df_materia.style.format({"Flujo Masico (kg/h)": "{:.2f}", "Temp (°C)": "{:.1f}", "Etanol (%)": "{:.1f}"}), use_container_width=True)

with col_b2:
    df_energia = pd.DataFrame({
        "Equipo": ["W220 (Calentamiento)", "W310 (Enfriamiento)"],
        "Carga Térmica (kJ/h)": [abs(q_calor), abs(q_frio)]
    })
    st.dataframe(df_energia.style.format({"Carga Térmica (kJ/h)": "{:.2f}"}), use_container_width=True)

csv_materia = df_materia.to_csv(index=False).encode('utf-8')
st.download_button(label="⬇️ Descargar Balance de Materia (CSV)", data=csv_materia, file_name='balance_materia.csv', mime='text/csv')

st.divider()

# ==========================================
# 5. GRÁFICAS DE SENSIBILIDAD Y ESCENARIOS
# ==========================================
st.header("📈 Análisis de Sensibilidad y Escenarios")

g1, g2 = st.columns(2)
with g1:
    st.write("**Temperatura Salida W220 vs. Requerimiento Vapor**")
    t_w220_r = range(70, 115, 5)
    df_vapor = pd.DataFrame({"Temp W220 (°C)": t_w220_r, "Req. Vapor (kg/h)": [(t - 60) * 15 for t in t_w220_r]}).set_index("Temp W220 (°C)")
    st.line_chart(df_vapor, color="#ff4b4b")

with g2:
    st.write("**Precio del Vapor vs. Costo Real de Producción**")
    p_vap_r = range(10, 65, 10)
    df_costo = pd.DataFrame({"Precio Vapor ($/ton)": p_vap_r, "Costo Prod. ($/kg)": [0.3 + (p * 0.01) for p in p_vap_r]}).set_index("Precio Vapor ($/ton)")
    st.line_chart(df_costo, color="#29b09d")

st.subheader("📊 Comparación de Escenarios")
escenarios_data = {
    "Escenario": ["Caso base", "Caso económico", "Caso rentable", "Caso crítico"],
    "Descripción": ["Condiciones normales establecidas.", "Busca reducir costo real de producción.", "Busca mejorar NPV, Payback y ROI.", "Condiciones desfavorables de precios."],
    "Resultados Esperados": ["Punto de comparación.", "Menor costo por unidad de producto.", "Mayor rentabilidad del proceso.", "Identificación de riesgos operativos."]
}
st.table(pd.DataFrame(escenarios_data))

st.divider()

# ==========================================
# 6. DIAGRAMAS Y DOCUMENTACIÓN
# ==========================================
st.header("⚙️ Diagramas de Proceso y Documentación")
d1, d2 = st.columns(2)
with d1: 
    if os.path.exists("assets/Bloques_ISO.pdf"): 
        with open("assets/Bloques_ISO.pdf", "rb") as f: st.download_button("⬇️ Descargar Diagrama Bloques", f, "Bloques_ISO.pdf")
with d2:
    if os.path.exists("assets/PFD_ISO.pdf"): 
        with open("assets/PFD_ISO.pdf", "rb") as f: st.download_button("⬇️ Descargar PFD y P&ID", f, "PFD_ISO.pdf")

with st.expander("Ver Supuestos y Limitaciones del Modelo"):
    st.markdown("""
    **Base de cálculo:** 1000 kg/h de alimentación de mosto.  
    **Supuestos termodinámicos:** Modelo ideal para mezcla Etanol-Agua.  
    **Limitaciones:** Los costos de equipos (CAPEX) son fijos y aproximados ($1.5M USD).  
    """)

st.divider()

# ==========================================
# 7. INTEGRACIÓN CON IA (GEMINI)
# ==========================================
st.header("🤖 Tutor con IA (Gemini)")
st.info("El modo tutor está orientado a la explicación técnica de los resultados. Utiliza los datos generados por la simulación.")

if st.toggle("Habilitar Modo Tutor"):
    api_key = st.text_input("Ingrese su API Key de Gemini:", type="password")
    
    if api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Prompt base exigido en las especificaciones
        system_prompt = f"""
        Actúa como un tutor experto en simulación de procesos, balances de materia y energía, diseño de plantas y análisis económico. 
        Explica los resultados de forma clara para estudiantes de ingeniería química. 
        Utiliza únicamente los valores calculados o mostrados por la aplicación:
        - Flujo producto: {prod_mass:.2f} kg/h
        - Pureza etanol: {pureza_final:.1f}%
        - Costo de producción: ${costo_prod:.2f}/kg
        - NPV: ${npv/1e6:.2f}M, ROI: {roi:.1f}%
        No inventes datos. Si falta información, indícalo de forma explícita y sugiere qué dato sería necesario para mejorar el análisis.
        """
        
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for msg in st.session_state.messages:
            st.chat_message(msg["role"]).write(msg["content"])

        if prompt := st.chat_input("Escribe tu pregunta sobre la simulación..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            st.chat_message("user").write(prompt)
            
            # Generación de respuesta con contexto
            full_prompt = f"{system_prompt}\n\nPregunta del usuario: {prompt}"
            response = model.generate_content(full_prompt)
            
            st.session_state.messages.append({"role": "assistant", "content": response.text})
            st.chat_message("assistant").write(response.text)
