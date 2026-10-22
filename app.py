import streamlit as st
import pandas as pd

st.set_page_config(page_title="Detector de Pedidos Duplicados", layout="wide")

# 💧 Inyección de CSS para la marca de agua de fondo ("FACTU ENCAR")
st.markdown(
    """
    <style>
    .stApp::before {
        content: "FACTU ENCAR";
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        font-size: 8vw;
        font-weight: 800;
        color: rgba(255, 255, 255, 0.03);
        z-index: 0;
        pointer-events: none;
        white-space: nowrap;
        user-select: none;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --- LOGOTIPO EN LA BARRA LATERAL ---
try:
    st.sidebar.image("logo_cervepar.png", use_container_width=True)
except Exception:
    st.sidebar.markdown("### 🍺 Cervepar")

st.sidebar.markdown("---")

st.title("📦 Detección de Posibles Pedidos Duplicados")
st.write("Sube tu archivo .csv limpio")


# 1. Cargar archivo con caché para máxima velocidad
@st.cache_data
def cargar_y_limpiar_datos(uploaded_file):
    try:
        df = pd.read_csv(uploaded_file, sep=';', encoding='latin1')
    except Exception:
        df = pd.read_csv(uploaded_file, sep=',', encoding='utf-8')

    df.columns = df.columns.str.strip()

    # Filtrar tipos de movimiento válidos
    movimientos_validos = ['FACTURA CONTADO', 'FACTURA CREDITO']
    df_filtrado = df[df['Movimiento'].isin(movimientos_validos)].copy()

    # Filtrar por la columna de estado (Sts) si existe en el archivo
    col_sts = next((c for c in df_filtrado.columns if c.lower() == 'sts'), None)
    if col_sts:
        estados_validos = ['RET', 'PRC', 'LIB']
        df_filtrado['Sts_Clean'] = df_filtrado[col_sts].astype(str).str.strip().str.upper()
        df_filtrado = df_filtrado[df_filtrado['Sts_Clean'].isin(estados_validos)].copy()

    # Convertir fechas
    df_filtrado['Fecha_Entrega'] = pd.to_datetime(df_filtrado['Entrega'], format='%d/%m/%Y', errors='coerce')

    # Convertir importe a numérico para poder comparar montos
    df_filtrado['Importe_Num'] = (
        df_filtrado['Importe Total']
        .astype(str)
        .str.replace('.', '', regex=False)
        .str.replace(',', '.', regex=False)
        .astype(float)
    )

    return df_filtrado


# Controles en la barra lateral (Días de tolerancia predeterminado en 3)
uploaded_file = st.sidebar.file_uploader("Cargar archivo CSV", type=["csv"])
dias_tolerancia = st.sidebar.slider("Diferencia máxima de días entre entregas", 0, 30, 3)
margen_parecido = st.sidebar.slider("Margen de diferencia para montos 'parecidos' (%)", 1, 25, 10)

if uploaded_file is not None:
    df_filtrado = cargar_y_limpiar_datos(uploaded_file)

    # 2. Lógica de Detección de Posibles Duplicados
    df_filtrado = df_filtrado.sort_values(by=['Client', 'C.Prd', 'Fecha_Entrega'])
    df_filtrado['Dias_Diferencia'] = df_filtrado['Fecha_Entrega'].groupby(
        [df_filtrado['Client'], df_filtrado['C.Prd']]).diff().dt.days

    es_duplicado = (
            df_filtrado.duplicated(subset=['Client', 'C.Prd', 'Cant'], keep=False) &
            ((df_filtrado['Dias_Diferencia'].isna()) | (df_filtrado['Dias_Diferencia'] <= dias_tolerancia))
    )

    df_dup = df_filtrado[es_duplicado].copy()

    # 3. Transformación lado a lado
    if not df_dup.empty:
        df_dup = df_dup.sort_values(by=['Client', 'C.Prd', 'Cant', 'Fecha_Entrega'], ascending=[True, True, True, True])
        df_dup['Secuencia'] = df_dup.groupby(['Client', 'C.Prd', 'Cant']).cumcount()

        col_trr = next((c for c in df_dup.columns if c.lower() in ['trr', 'territorio']), None)
        trr_col_name = col_trr if col_trr else 'Trr'
        if not col_trr:
            df_dup['Trr'] = 'N/A'

        df_p1_final = df_dup[df_dup['Secuencia'] == 0][
            [trr_col_name, 'Client', 'C.Prd', 'Cant', 'Entrega', 'Movimiento', 'Importe_Num']].copy()
        df_p1_final.columns = ['Trr', 'Client', 'C.Prd', 'Cant', 'Primera Entrega', 'Primera Entrega (Mov.)', 'Monto_1']

        df_p2_final = df_dup[df_dup['Secuencia'] == 1][
            [trr_col_name, 'Client', 'C.Prd', 'Cant', 'Entrega', 'Movimiento', 'Importe_Num']].copy()
        df_p2_final.columns = ['Trr', 'Client', 'C.Prd', 'Cant', 'Segunda Entrega', 'Segunda Entrega (Mov.)', 'Monto_2']

        df_comparacion = pd.merge(
            df_p1_final,
            df_p2_final,
            on=['Trr', 'Client', 'C.Prd', 'Cant']
        ).drop_duplicates().reset_index(drop=True)

        # Recuperar la Razón Social desde el dataframe original
        razon_social_map = df_filtrado.set_index('Client')['Razon social'].to_dict()
        df_comparacion['Razon social'] = df_comparacion['Client'].map(razon_social_map)

        # 4. ORDENAR: Exactos primero (diferencia == 0), luego los parecidos
        df_comparacion['Diff_Monto'] = abs(df_comparacion['Monto_1'] - df_comparacion['Monto_2'])
        df_comparacion['Es_Exacto'] = df_comparacion['Diff_Monto'] < 1e-2

        df_comparacion = df_comparacion.sort_values(by=['Es_Exacto', 'Diff_Monto'],
                                                    ascending=[False, True]).reset_index(drop=True)


        # Formatear los montos con Guaraníes (₲) y separador de miles por puntos
        def formatear_guaranies(val):
            if pd.isna(val):
                return ""
            return f"₲ {round(val):,.0f}".replace(",", ".")


        df_comparacion['Primera Entrega (Monto)'] = df_comparacion['Monto_1'].apply(formatear_guaranies)
        df_comparacion['Segunda Entrega (Monto)'] = df_comparacion['Monto_2'].apply(formatear_guaranies)

    else:
        df_comparacion = pd.DataFrame()

    # 5. Métricas y Visualización
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Registros Validados", len(df_filtrado))
    col2.metric("Pares Duplicados Detectados", len(df_comparacion))
    col3.metric("Clientes Involucrados", df_comparacion['Client'].nunique() if not df_comparacion.empty else 0)

    st.subheader("📋 Comparativa Lado a Lado")

    if not df_comparacion.empty:
        columnas_finales = [
            'Trr', 'Client', 'Razon social',
            'Primera Entrega', 'Primera Entrega (Mov.)', 'Primera Entrega (Monto)',
            'Segunda Entrega', 'Segunda Entrega (Mov.)', 'Segunda Entrega (Monto)'
        ]

        df_view = df_comparacion[columnas_finales].copy()

        m1_series = df_comparacion['Monto_1']
        m2_series = df_comparacion['Monto_2']


        # Función de estilo basada en el índice
        def color_montos_clean(row):
            i = row.name
            m1 = m1_series.iloc[i]
            m2 = m2_series.iloc[i]

            estilos = [''] * len(row)
            if pd.isna(m1) or pd.isna(m2):
                return estilos

            idx_m1 = row.index.get_loc('Primera Entrega (Monto)')
            idx_m2 = row.index.get_loc('Segunda Entrega (Monto)')

            diff_pct = abs(m1 - m2) / max(m1, 1) * 100

            if abs(m1 - m2) < 1e-2:
                estilos[idx_m1] = 'border: 2px solid #ff4b4b; background-color: rgba(255, 75, 75, 0.1);'
                estilos[idx_m2] = 'border: 2px solid #ff4b4b; background-color: rgba(255, 75, 75, 0.1);'
            elif diff_pct <= margen_parecido:
                estilos[idx_m1] = 'border: 2px solid #ffa500; background-color: rgba(255, 165, 0, 0.1);'
                estilos[idx_m2] = 'border: 2px solid #ffa500; background-color: rgba(255, 165, 0, 0.1);'

            return estilos


        df_estilizado = df_view.style.apply(color_montos_clean, axis=1)

        st.dataframe(df_estilizado, use_container_width=True)

        # Leyenda visual
        st.markdown("""
        * **🔴 Borde Rojo:** Montos exactamente iguales (ubicados en la parte superior).
        * **🟠 Borde Naranja:** Montos parecidos dentro del margen configurado (ubicados debajo de los exactos).
        """)

        # Botón de exportación
        csv_download = df_view.to_csv(index=False, sep=';').encode('utf-8')
        st.download_button(
            label="📥 Descargar Reporte con Territorio (CSV)",
            data=csv_download,
            file_name="comparativa_pedidos_trr.csv",
            mime="text/csv"
        )
    else:
        st.success("¡No se encontraron posibles pedidos duplicados con los parámetros actuales!")

else:
    st.info("Por favor, sube el archivo CSV en la barra lateral izquierda para comenzar.")