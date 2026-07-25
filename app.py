import streamlit as st
import pdfplumber
import pandas as pd
from groq import Groq
import json
import io
import time
import re
import os  # Requerido para leer las variables de entorno de Render

st.set_page_config(page_title="Siaconca: Extractor Profesional", layout="wide")

st.title("📄 Siaconca: Extractor v6.7 (Parseo Seguro - Groq Only)")

# --- CARGA AUTOMÁTICA Y SEGURA DE API KEYS ---
groq_api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", "")

# Panel informativo en la barra lateral para validar el estado de conexión
st.sidebar.header("🛡️ Estado del Servidor Cloud")
if groq_api_key:
    st.sidebar.success("● Conectado a los servicios de IA")
    st.sidebar.caption("✓ Motor Único: Groq Activo")
else:
    st.sidebar.error("❌ Falta configuración de llaves")
    st.sidebar.markdown(
        "Por favor, añade `GROQ_API_KEY` en la pestaña **Environment** de tu panel de Render."
    )

# --- FUNCIÓN DE PARSEO SEGURO ---
def parse_monto_seguro(valor):
    if pd.isna(valor) or valor == '' or valor is None: return 0.0
    valor_str = str(valor).strip()
    
    # Si detecta formato latino (ej: 7,590 o 1.827,02)
    if re.search(r',\d{2,3}$', valor_str) or ('.' in valor_str and ',' in valor_str and valor_str.rfind(',') > valor_str.rfind('.')):
        limpio = valor_str.replace('.', '').replace(',', '.')
    else:
        # Formato DAHUA estándar (ej: 134.70 o 5,000.00)
        limpio = valor_str.replace(',', '')
        
    try:
        return float(limpio)
    except ValueError:
        return 0.0

# Creamos las tres pestañas operativas
tab1, tab2, tab3 = st.tabs([
    "📊 Extractor Simple (Solo Factura)", 
    "📦 Consolidado Logístico (Factura + Packing List)", 
    "📥 Carga Masiva (Dos Archivos Separados)"
])

# --- FUNCIÓN GLOBAL DE CONEXIÓN CON IA ---
def preguntar_ia(prompt_texto):
    if not groq_api_key:
        st.error("⚠️ Error de entorno: No se detectó la API key de Groq en el servidor de Render. Configúrala en la sección Environment.")
        st.stop()

    try:
        client_groq = Groq(api_key=groq_api_key)
        res = client_groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "Return ONLY JSON."}, {"role": "user", "content": prompt_texto}],
            temperature=0.1, response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e_groq:
        st.error(f"Error crítico en Groq: {e_groq}")
        st.stop()

# =========================================================================
# PESTAÑA 1: EXTRACTOR SIMPLE
# =========================================================================
with tab1:
    st.header("Procesar Factura Individual")
    archivo_pdf = st.file_uploader("Sube el PDF de la factura", type=["pdf"], key="pdf_simple")

    if archivo_pdf and ("archivo_nombre" not in st.session_state or st.session_state.archivo_nombre != archivo_pdf.name):
        st.session_state.datos_listos = False
        st.session_state.archivo_nombre = archivo_pdf.name

    if archivo_pdf:
        if not st.session_state.get("datos_listos"):
            inicio = time.time()
            with st.spinner("Analizando estructura de la factura..."):
                try:
                    paginas_texto = []
                    with pdfplumber.open(archivo_pdf) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text(layout=False)
                            if text: paginas_texto.append(text + "\n")

                    texto_completo = "\n".join(paginas_texto)

                    prompt_global = f"""Analiza este texto de una factura y extrae EXCLUSIVAMENTE los datos globales de encabezado, ajustes y totales en formato JSON. No extraigas la lista de productos aquí.
                    Schema JSON esperado:
                    {{
                      "proveedor": {{"nombre": "", "rif": ""}},
                      "numero_documento": "NÚMERO DE FACTURA O PEDIDO",
                      "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}},
                      "totales": {{"total_neto_final": "0.00"}}
                    }}
                    Texto extraído:
                    {texto_completo}"""

                    datos = preguntar_ia(prompt_global)
                    if isinstance(datos, list): datos = datos[0]

                    productos_totales = []
                    chunks = [paginas_texto[i:i+4] for i in range(0, len(paginas_texto), 4)]
                    
                    progreso_simple = st.progress(0)
                    status_simple = st.empty()

                    for idx, chunk in enumerate(chunks):
                        p_inicio = idx * 4 + 1
                        p_fin = min((idx + 1) * 4, len(paginas_texto))
                        status_simple.text(f"⏳ Extrayendo productos: Bloque {idx+1}/{len(chunks)} (Páginas {p_inicio} a {p_fin})...")
                        
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        prompt_productos = f"""Analiza este segmento de texto de una factura y extrae TODOS los productos listados en él en formato JSON.
                        
                        ⚠️ INSTRUCCIÓN CRÍTICA DE INTEGRIDAD: Extrae CADA FILA O LÍNEA de la tabla exactamente como aparece de forma literal. SI UN MISMO CÓDIGO DE PRODUCTO SE REPETITE EN LÍNEAS DIFERENTES O EN PÁGINAS DISTINTAS (incluso con costos diferentes o el mismo costo), DEBES CREAR UN ELEMENTO SEPARADO EN EL ARRAY PARA CADA FILA. Está estrictamente PROHIBIDO agrupar, consolidar, sumar cantidades o eliminar duplicados de líneas.
                        
                        El 'codigo' debe ser el modelo o part number literal y exacto del producto.
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "", "descripcion": "", "cantidad": 0, "costo_unitario": "0.00"}}]
                        }}
                        Texto del segmento:
                        {texto_chunk}"""

                        res_chunk = preguntar_ia(prompt_productos)
                        productos_totales.extend(res_chunk.get("productos", []))
                        progreso_simple.progress((idx + 1) / len(chunks))

                    status_simple.empty()
                    progreso_simple.empty()

                    datos["productos"] = productos_totales

                    num_doc = str(datos.get("numero_documento", "S_N")).strip()
                    if len(num_doc) > 20: 
                        temp_match = re.search(r'([A-Z0-9-]{4,})', num_doc)
                        if temp_match: num_doc = temp_match.group(1)

                    items = datos.get("productos", [])
                    ajustes = datos.get("ajuste", {})
                    flete = parse_monto_seguro(ajustes.get("flete", "0.0"))
                    descuento = parse_monto_seguro(ajustes.get("descuento", "0.0"))
                    recargo = parse_monto_seguro(ajustes.get("recargo", "0.0"))
                    
                    datos["ajuste"]["flete"] = flete
                    datos["ajuste"]["descuento"] = descuento
                    datos["totales"]["total_neto_final"] = parse_monto_seguro(datos.get("totales", {}).get("total_neto_final", "0.0"))

                    if items:
                        df_temp = pd.DataFrame(items)
                        df_temp['codigo'] = df_temp['codigo'].astype(str).str.strip().str.upper().str.replace(r'\s+', '', regex=True)
                        df_temp['cantidad'] = pd.to_numeric(df_temp['cantidad'], errors='coerce').fillna(0)
                        
                        # PARSEO SEGURO DE COSTO
                        df_temp['costo_unitario'] = df_temp['costo_unitario'].apply(parse_monto_seguro)
                        
                        if flete > 0 or descuento > 0 or recargo > 0:
                            total_neto_real = datos["totales"]["total_neto_final"]
                            suma_bruta = (df_temp['cantidad'] * df_temp['costo_unitario']).sum()
                            factor = total_neto_real / suma_bruta if suma_bruta > 0 else 1.0
                        else:
                            factor = 1.0
                            
                        df_temp['costo_final'] = (df_temp['costo_unitario'] * factor).round(3)
                        df_temp['subtotal'] = (df_temp['cantidad'] * df_temp['costo_final']).round(3)
                        
                        datos['productos'] = df_temp.to_dict('records')

                    st.session_state.num_doc = num_doc
                    st.session_state.datos = datos
                    st.session_state.datos_listos = True
                    st.session_state.tiempo = time.time() - inicio
                except Exception as e:
                    st.error(f"Error crítico en Pestaña 1: {e}"); st.stop()

        if st.session_state.get("datos_listos"):
            st.success(f"✅ Procesado por completo en {st.session_state.tiempo:.2f}s")
            datos = st.session_state.datos
            st.info(f"📄 **Documento:** {st.session_state.num_doc} | **Proveedor:** {datos['proveedor']['nombre']}")
            
            col1, col2, col3 = st.columns(3)
            with col1: st.metric("Flete", f"$ {datos.get('ajuste', {}).get('flete', 0.0):,.2f}")
            with col2: st.metric("Descuento", f"$ {datos.get('ajuste', {}).get('descuento', 0.0):,.2f}")
            with col3: st.metric("TOTAL NETO PDF", f"$ {datos['totales']['total_neto_final']:,.2f}")

            df_display = pd.DataFrame(datos['productos'])[["codigo", "descripcion", "cantidad", "costo_unitario", "costo_final", "subtotal"]]
            df_display.columns = ["CODIGO", "DESCRIPCION", "CANTIDAD", "COSTO LISTA", "COSTO FINAL", "SUBTOTAL"]
            
            st.dataframe(
                df_display.style.format({"COSTO LISTA": "{:,.3f}", "COSTO FINAL": "{:,.3f}", "SUBTOTAL": "{:,.3f}", "CANTIDAD": "{:,.0f}"})
                .set_table_styles([{'selector': 'th', 'props': [('background-color', '#D9EAD3'), ('color', 'black'), ('font-weight', 'bold')]}])
                , use_container_width=True
            )

            total_calculado_simple = df_display["SUBTOTAL"].sum()
            col_tot_simple = st.columns([3, 1])
            with col_tot_simple[1]:
                st.metric(label="💰 Suma Total Subtotales", value=f"$ {total_calculado_simple:,.2f}")

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_display.to_excel(writer, sheet_name='Carga', index=False, startrow=8)
                wb, ws = writer.book, writer.sheets['Carga']
                fmt_verde = wb.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1})
                fmt_normal = wb.add_format({'border': 1})
                fmt_num = wb.add_format({'border': 1, 'num_format': '#,##0.000'})
                fmt_header = wb.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1, 'align': 'center'})
                fmt_total_final = wb.add_format({'bold': True, 'bg_color': '#FFF2CC', 'border': 2, 'num_format': '#,##0.00'})

                ws.write(1, 0, "Nombre / Razón Social:", fmt_verde); ws.write(1, 1, datos['proveedor']['nombre'], fmt_normal)
                ws.write(2, 0, "RIF / Identificación:", fmt_verde); ws.write(2, 1, datos['proveedor']['rif'], fmt_normal)
                ws.write(5, 0, "Número de Factura/Pedido:", fmt_verde); ws.write(5, 1, st.session_state.num_doc, fmt_normal)
                ws.write(6, 0, "Flete Extra:", fmt_verde); ws.write(6, 1, datos.get('ajuste', {}).get('flete', 0.0), fmt_num)
                ws.write(6, 2, "Descuento:", fmt_verde); ws.write(6, 3, datos.get('ajuste', {}).get('descuento', 0.0), fmt_num)

                ws.set_column('A:A', 30); ws.set_column('B:B', 50); ws.set_column('C:F', 15)
                for col_num, value in enumerate(df_display.columns.values): ws.write(8, col_num, value, fmt_header)
                
                for r_idx, row in enumerate(df_display.values):
                    ws.write(9 + r_idx, 0, row[0], fmt_normal)
                    ws.write(9 + r_idx, 1, row[1], fmt_normal)
                    ws.write(9 + r_idx, 2, row[2], fmt_normal)
                    ws.write(9 + r_idx, 3, row[3], fmt_num)
                    ws.write(9 + r_idx, 4, row[4], fmt_num)
                    ws.write(9 + r_idx, 5, row[5], fmt_num)

                fila_total = 9 + len(df_display)
                ws.write(fila_total, 4, "TOTAL CALCULADO:", fmt_verde)
                ws.write_formula(fila_total, 5, f"=SUM(F10:F{fila_total})", fmt_total_final)

            nombre_archivo = f"PEDIDO_{st.session_state.num_doc}.xlsx"
            st.download_button(f"📥 Descargar {nombre_archivo}", buffer.getvalue(), nombre_archivo, key="btn_simple")

# =========================================================================
# PESTAÑA 2: CONSOLIDADO LOGÍSTICO (v6.5 - PRESERVACIÓN ESTRICTA DE FILAS)
# =========================================================================
with tab2:
    st.header("Consolidación por Línea de Factura (Prorrateo Seguro)")
    st.write("Sube la Factura y el Packing List. Esta versión mantiene cada fila de la factura intacta y distribuye el peso de forma proporcional.")

    col_files = st.columns(2)
    with col_files[0]:
        pdf_inv = st.file_uploader("1. Sube la FACTURA (Invoice)", type=["pdf"], key="invoice_union")
    with col_files[1]:
        pdf_pl = st.file_uploader("2. Sube el PACKING LIST", type=["pdf"], key="packing_union")

    if pdf_inv and pdf_pl:
        if st.button("🚀 Cruzar y Consolidar Datos", use_container_width=True):
            with st.spinner("Analizando documentos de forma independiente..."):
                try:
                    paginas_inv = []
                    with pdfplumber.open(pdf_inv) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text(layout=False)
                            if text: paginas_inv.append(text + "\n")
                    
                    texto_completo_inv = "\n".join(paginas_inv)

                    prompt_global_inv = f"""Analiza este texto de una FACTURA y extrae EXCLUSIVAMENTE los datos globales en JSON. No traigas productos aquí.
                    Schema JSON esperado:
                    {{
                      "numero_documento": "NRO_FACTURA",
                      "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}},
                      "totales": {{"total_neto_final": "0.00"}}
                    }}
                    Texto:
                    {texto_completo_inv}"""
                    json_factura = preguntar_ia(prompt_global_inv)

                    productos_factura = []
                    chunks_inv = [paginas_inv[i:i+4] for i in range(0, len(paginas_inv), 4)]
                    progreso_inv = st.progress(0)
                    status_inv = st.empty()

                    for idx, chunk in enumerate(chunks_inv):
                        status_inv.text(f"⏳ Extrayendo ítems Invoice: Bloque {idx+1}/{len(chunks_inv)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        prompt_prod_inv = f"""Analiza este segmento de FACTURA y extrae TODOS los productos en JSON.
                        
                        ⚠️ INSTRUCCIÓN CRÍTICA DE INTEGRIDAD: Extrae CADA FILA O LÍNEA de la tabla exactamente como aparece de forma literal. SI UN MISMO CÓDIGO DE PRODUCTO SE REPETITE EN LÍNEAS DIFERENTES O EN PÁGINAS DISTINTAS (incluso con costos diferentes o el mismo costo), DEBES CREAR UN ELEMENTO SEPARADO EN EL ARRAY PARA CADA FILA. Está estrictamente PROHIBIDO agrupar, consolidar, sumar cantidades o eliminar duplicados de líneas.
                        
                        El 'codigo' debe ser el part number o modelo literal.
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "", "descripcion": "", "cantidad": 0, "costo_unitario": "0.00"}}]
                        }}
                        Texto:
                        {texto_chunk}"""
                        res_chunk = preguntar_ia(prompt_prod_inv)
                        productos_factura.extend(res_chunk.get("productos", []))
                        progreso_inv.progress((idx + 1) / len(chunks_inv))

                    status_inv.empty()
                    progreso_inv.empty()

                    paginas_pl = []
                    with pdfplumber.open(pdf_pl) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text(layout=False)
                            if text: paginas_pl.append(text + "\n")

                    productos_packing = []
                    chunks_pl = [paginas_pl[i:i+4] for i in range(0, len(paginas_pl), 4)]
                    progreso_pl = st.progress(0)
                    status_pl = st.empty()

                    for idx, chunk in enumerate(chunks_pl):
                        status_pl.text(f"⏳ Extrayendo empaques Packing List: Bloque {idx+1}/{len(chunks_pl)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        prompt_prod_pl = f"""Analiza este segmento de PACKING LIST y extrae TODOS los datos logísticos en JSON.
                        El 'codigo' debe ser exactamente el modelo o part number de fábrica.
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "", "peso_bruto_kg": "0.00", "peso_neto_kg": "0.00", "volumen_cbm": "0.00"}}]
                        }}
                        Texto:
                        {texto_chunk}"""
                        res_chunk = preguntar_ia(prompt_prod_pl)
                        productos_packing.extend(res_chunk.get("productos", []))
                        progreso_pl.progress((idx + 1) / len(chunks_pl))

                    status_pl.empty()
                    progreso_pl.empty()

                    df_factura = pd.DataFrame(productos_factura)
                    df_packing = pd.DataFrame(productos_packing)

                    if df_factura.empty or df_packing.empty:
                        st.error("No se pudieron consolidar las listas. Verifica la estructura de tus archivos PDF.")
                    else:
                        # 1. Limpieza rigurosa de códigos en ambas listas
                        df_factura['codigo'] = df_factura['codigo'].astype(str).str.strip().str.upper().str.replace(r'\s+', '', regex=True)
                        df_packing['codigo'] = df_packing['codigo'].astype(str).str.strip().str.upper().str.replace(r'\s+', '', regex=True)

                        df_factura['cantidad'] = pd.to_numeric(df_factura['cantidad'], errors='coerce').fillna(0)
                        
                        # PARSEO SEGURO AQUÍ
                        df_factura['costo_unitario'] = df_factura['costo_unitario'].apply(parse_monto_seguro)

                        # 2. Aplicación del factor de flete/ajustes
                        ajustes = json_factura.get("ajuste", {})
                        flete = parse_monto_seguro(ajustes.get("flete", "0.0"))
                        descuento = parse_monto_seguro(ajustes.get("descuento", "0.0"))
                        recargo = parse_monto_seguro(ajustes.get("recargo", "0.0"))

                        if flete > 0 or descuento > 0 or recargo > 0:
                            total_neto_real = parse_monto_seguro(json_factura.get("totales", {}).get("total_neto_final", "0.0"))
                            suma_bruta = (df_factura['cantidad'] * df_factura['costo_unitario']).sum()
                            factor = total_neto_real / suma_bruta if suma_bruta > 0 else 1.0
                        else:
                            factor = 1.0
                        
                        df_factura['costo_final'] = (df_factura['costo_unitario'] * factor).round(3)
                        df_factura['subtotal'] = (df_factura['cantidad'] * df_factura['costo_final']).round(3)

                        # 3. Procesar Packing List agrupado por código único
                        for col in ['peso_bruto_kg', 'peso_neto_kg', 'volumen_cbm']:
                            if col in df_packing.columns:
                                df_packing[col] = df_packing[col].apply(parse_monto_seguro)
                            else:
                                df_packing[col] = 0.0
                        
                        df_packing_grouped = df_packing.groupby('codigo', as_index=False).agg({
                            'peso_bruto_kg': 'sum',
                            'peso_neto_kg': 'sum',
                            'volumen_cbm': 'sum'
                        })

                        # 4. Cruzar la FACTURA ÍNTEGRA (sin agrupar) con los totales de packing
                        df_consolidado = pd.merge(df_factura, df_packing_grouped, on='codigo', how='left')
                        
                        # 5. PRORRATEO ESTRICTO POR LÍNEA INDEPENDIENTE
                        df_consolidado['total_cantidad_codigo'] = df_consolidado.groupby('codigo')['cantidad'].transform('sum')
                        
                        for col in ['peso_bruto_kg', 'peso_neto_kg', 'volumen_cbm']:
                            df_consolidado[col] = df_consolidado[col].fillna(0.0)
                            df_consolidado[col] = df_consolidado.apply(
                                lambda r: (r[col] * (r['cantidad'] / r['total_cantidad_codigo'])) if r['total_cantidad_codigo'] > 0 else 0.0,
                                axis=1
                            ).round(4)

                        # Limpiamos la columna auxiliar de cálculo
                        df_consolidado = df_consolidado.drop(columns=['total_cantidad_codigo'])

                        # 6. Estructura final para visualización
                        df_final_display = df_consolidado[[
                            "codigo", "descripcion", "cantidad", "costo_unitario", "costo_final", "subtotal", 
                            "peso_bruto_kg", "peso_neto_kg", "volumen_cbm"
                        ]]
                        df_final_display.columns = [
                            "CODIGO", "DESCRIPCION", "CANTIDAD", "COSTO LISTA", "COSTO FINAL", "SUBTOTAL", 
                            "PESO BRUTO (KG)", "PESO NETO (KG)", "VOLUMEN (CBM)"
                        ]

                        st.success("¡Datos consolidados secuencialmente con éxito!")
                        num_doc_union = json_factura.get("numero_documento", "S_N")
                        st.info(f"📄 **Nro. Invoice:** {num_doc_union}")

                        st.dataframe(
                            df_final_display.style.format({
                                "COSTO LISTA": "{:,.3f}", "COSTO FINAL": "{:,.3f}", "SUBTOTAL": "{:,.3f}", "CANTIDAD": "{:,.0f}",
                                "PESO BRUTO (KG)": "{:,.2f}", "PESO NETO (KG)": "{:,.2f}", "VOLUMEN (CBM)": "{:,.4f}"
                            }).set_table_styles([{'selector': 'th', 'props': [('background-color', '#CFE2F3'), ('color', 'black'), ('font-weight', 'bold')]}])
                            , use_container_width=True
                        )

                        t_subtotal_log = df_final_display["SUBTOTAL"].sum()
                        t_bruto_log = df_final_display["PESO BRUTO (KG)"].sum()
                        t_neto_log = df_final_display["PESO NETO (KG)"].sum()
                        t_volumen_log = df_final_display["VOLUMEN (CBM)"].sum()

                        c_tot1, c_tot2, c_tot3, c_tot4 = st.columns(4)
                        with c_tot1: st.metric("💰 Total Subtotales", f"$ {t_subtotal_log:,.2f}")
                        with c_tot2: st.metric("⚖️ Total Peso Bruto", f"{t_bruto_log:,.2f} KG")
                        with c_tot3: st.metric("⚖️ Total Peso Neto", f"{t_neto_log:,.2f} KG")
                        with c_tot4: st.metric("📦 Total Volumen", f"{t_volumen_log:,.4f} CBM")

                        buffer_logistico = io.BytesIO()
                        with pd.ExcelWriter(buffer_logistico, engine='xlsxwriter') as writer:
                            df_final_display.to_excel(writer, sheet_name='Consolidado Logistico', index=False, startrow=4)
                            wb, ws = writer.book, writer.sheets['Consolidado Logistico']
                            
                            fmt_azul_header = wb.add_format({'bold': True, 'bg_color': '#CFE2F3', 'border': 1, 'align': 'center'})
                            fmt_normal = wb.add_format({'border': 1})
                            fmt_num_tres = wb.add_format({'border': 1, 'num_format': '#,##0.000'})
                            fmt_num_dos = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
                            fmt_num_cuatro = wb.add_format({'border': 1, 'num_format': '#,##0.0000'})
                            
                            ws.write(0, 0, "CONSOLIDADO DE COSTOS, PESOS Y MEDIDAS (LINEAL)", wb.add_format({'bold': True, 'font_size': 14}))
                            ws.write(1, 0, f"Invoice Nro: {num_doc_union}", fmt_normal)

                            ws.set_column('A:A', 25)
                            ws.set_column('B:B', 45)
                            ws.set_column('C:F', 15) 
                            ws.set_column('G:I', 18) 
                            
                            for col_num, value in enumerate(df_final_display.columns.values):
                                ws.write(4, col_num, value, fmt_azul_header)

                            for r_idx, row in enumerate(df_final_display.values):
                                ws.write(5 + r_idx, 0, row[0], fmt_normal)
                                ws.write(5 + r_idx, 1, row[1], fmt_normal)
                                ws.write(5 + r_idx, 2, row[2], fmt_normal)
                                ws.write(5 + r_idx, 3, row[3], fmt_num_tres)
                                ws.write(5 + r_idx, 4, row[4], fmt_num_tres)
                                ws.write(5 + r_idx, 5, row[5], fmt_num_tres)   
                                ws.write(5 + r_idx, 6, row[6], fmt_num_dos)    
                                ws.write(5 + r_idx, 7, row[7], fmt_num_dos)    
                                ws.write(5 + r_idx, 8, row[8], fmt_num_cuatro) 

                            fila_total_log = 5 + len(df_final_display)
                            fmt_tot_lbl = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#F2F2F2', 'align': 'right'})
                            fmt_tot_num2 = wb.add_format({'bold': True, 'border': 2, 'num_format': '#,##0.00', 'bg_color': '#FFF2CC'})
                            fmt_tot_num4 = wb.add_format({'bold': True, 'border': 2, 'num_format': '#,##0.0000', 'bg_color': '#FFF2CC'})

                            ws.write(fila_total_log, 4, "TOTALES:", fmt_tot_lbl)
                            ws.write_formula(fila_total_log, 5, f"=SUM(F6:F{fila_total_log})", fmt_tot_num2)
                            ws.write_formula(fila_total_log, 6, f"=SUM(G6:G{fila_total_log})", fmt_tot_num2)
                            ws.write_formula(fila_total_log, 7, f"=SUM(H6:H{fila_total_log})", fmt_tot_num2)
                            ws.write_formula(fila_total_log, 8, f"=SUM(I6:I{fila_total_log})", fmt_tot_num4)

                        nombre_archivo_log = f"CONSOLIDADO_LOGISTICO_{num_doc_union}.xlsx"
                        st.download_button(f"📥 Descargar {nombre_archivo_log}", buffer_logistico.getvalue(), nombre_archivo_log, key="btn_union")
                
                except Exception as e:
                    st.error(f"Error crítico en el proceso de consolidación: {e}")

# =========================================================================
# PESTAÑA 3: CARGA MASIVA (DOS ARCHIVOS PLANOS SEPARADOS)
# =========================================================================
with tab3:
    st.header("Generador de Plantillas Limpias para Sistema Externo")
    st.write("Sube la Factura y el Packing List. Esta pestaña procesa las líneas y exporta automáticamente los dos archivos individuales con las columnas exactas requeridas.")

    col_files_m = st.columns(2)
    with col_files_m[0]:
        pdf_inv_m = st.file_uploader("1. Sube la FACTURA (Invoice)", type=["pdf"], key="invoice_masivo")
    with col_files_m[1]:
        pdf_pl_m = st.file_uploader("2. Sube el PACKING LIST", type=["pdf"], key="packing_masivo")

    if pdf_inv_m and pdf_pl_m:
        if st.button("🚀 Procesar y Separar en 2 Archivos Masivos", use_container_width=True):
            with st.spinner("Ejecutando extracción lineal y estructurando layouts masivos..."):
                try:
                    paginas_inv_m = []
                    with pdfplumber.open(pdf_inv_m) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text(layout=False)
                            if text: paginas_inv_m.append(text + "\n")
                    
                    texto_completo_inv_m = "\n".join(paginas_inv_m)

                    prompt_global_inv_m = f"""Analiza este texto de una FACTURA y extrae EXCLUSIVAMENTE los datos globales de encabezado (proveedor con su RIF), ajustes y totales en formato JSON. No traigas productos aquí.
                    Schema JSON esperado:
                    {{
                      "proveedor": {{"nombre": "", "rif": ""}},
                      "numero_documento": "NRO_FACTURA",
                      "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}},
                      "totales": {{"total_neto_final": "0.00"}}
                    }}
                    Texto:
                    {texto_completo_inv_m}"""
                    json_factura_m = preguntar_ia(prompt_global_inv_m)

                    productos_factura_m = []
                    chunks_inv_m = [paginas_inv_m[i:i+4] for i in range(0, len(paginas_inv_m), 4)]
                    progreso_inv_m = st.progress(0)
                    status_inv_m = st.empty()

                    for idx, chunk in enumerate(chunks_inv_m):
                        status_inv_m.text(f"⏳ Extrayendo ítems Invoice: Bloque {idx+1}/{len(chunks_inv_m)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        prompt_prod_inv_m = f"""Analiza este segmento de FACTURA y extrae TODOS los productos en JSON.
                        
                        ⚠️ INSTRUCCIÓN CRÍTICA DE INTEGRIDAD: Extrae CADA FILA O LÍNEA de la tabla exactamente como aparece de forma literal. SI UN MISMO CÓDIGO DE PRODUCTO SE REPETITE EN LÍNEAS DIFERENTES O EN PÁGINAS DISTINTAS (incluso con costos diferentes o el mismo costo), DEBES CREAR UN ELEMENTO SEPARADO EN EL ARRAY PARA CADA FILA. Está estrictamente PROHIBIDO agrupar, consolidar, sumar cantidades o eliminar duplicados de líneas.
                        
                        El 'codigo' debe ser el part number o modelo literal.
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "", "descripcion": "", "cantidad": 0, "costo_unitario": "0.00"}}]
                        }}
                        Texto:
                        {texto_chunk}"""
                        res_chunk = preguntar_ia(prompt_prod_inv_m)
                        productos_factura_m.extend(res_chunk.get("productos", []))
                        progreso_inv_m.progress((idx + 1) / len(chunks_inv_m))

                    status_inv_m.empty()
                    progreso_inv_m.empty()

                    paginas_pl_m = []
                    with pdfplumber.open(pdf_pl_m) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text(layout=False)
                            if text: paginas_pl_m.append(text + "\n")

                    productos_packing_m = []
                    chunks_pl_m = [paginas_pl_m[i:i+4] for i in range(0, len(paginas_pl_m), 4)]
                    progreso_pl_m = st.progress(0)
                    status_pl_m = st.empty()

                    for idx, chunk in enumerate(chunks_pl_m):
                        status_pl_m.text(f"⏳ Extrayendo empaques Packing List: Bloque {idx+1}/{len(chunks_pl_m)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        prompt_prod_pl_m = f"""Analiza este segmento de PACKING LIST y extrae TODOS los datos logísticos en JSON.
                        El 'codigo' debe ser exactamente el modelo o part number de fábrica.
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "", "peso_bruto_kg": "0.00", "peso_neto_kg": "0.00", "volumen_cbm": "0.00"}}]
                        }}
                        Texto:
                        {texto_chunk}"""
                        res_chunk = preguntar_ia(prompt_prod_pl_m)
                        productos_packing_m.extend(res_chunk.get("productos", []))
                        progreso_pl_m.progress((idx + 1) / len(chunks_pl_m))

                    status_pl_m.empty()
                    progreso_pl_m.empty()

                    df_factura_m = pd.DataFrame(productos_factura_m)
                    df_packing_m = pd.DataFrame(productos_packing_m)

                    if df_factura_m.empty or df_packing_m.empty:
                        st.error("No se pudieron consolidar las fuentes de datos. Verifica tus PDFs.")
                    else:
                        # 1. Limpieza estricta de códigos
                        df_factura_m['codigo'] = df_factura_m['codigo'].astype(str).str.strip().str.upper().str.replace(r'\s+', '', regex=True)
                        df_packing_m['codigo'] = df_packing_m['codigo'].astype(str).str.strip().str.upper().str.replace(r'\s+', '', regex=True)

                        df_factura_m['cantidad'] = pd.to_numeric(df_factura_m['cantidad'], errors='coerce').fillna(0)
                        
                        # PARSEO SEGURO AQUÍ
                        df_factura_m['costo_unitario'] = df_factura_m['costo_unitario'].apply(parse_monto_seguro)

                        # 2. Aplicación proporcional de costos (Flete/Recargos)
                        ajustes_m = json_factura_m.get("ajuste", {})
                        flete_m = parse_monto_seguro(ajustes_m.get("flete", "0.0"))
                        descuento_m = parse_monto_seguro(ajustes_m.get("descuento", "0.0"))
                        recargo_m = parse_monto_seguro(ajustes_m.get("recargo", "0.0"))

                        if flete_m > 0 or descuento_m > 0 or recargo_m > 0:
                            total_neto_real_m = parse_monto_seguro(json_factura_m.get("totales", {}).get("total_neto_final", "0.0"))
                            suma_bruta_m = (df_factura_m['cantidad'] * df_factura_m['costo_unitario']).sum()
                            factor_m = total_neto_real_m / suma_bruta_m if suma_bruta_m > 0 else 1.0
                        else:
                            factor_m = 1.0
                        
                        df_factura_m['costo_final'] = (df_factura_m['costo_unitario'] * factor_m).round(3)

                        # 3. Agrupación del Packing List (Parseo Seguro)
                        for col in ['peso_bruto_kg', 'peso_neto_kg', 'volumen_cbm']:
                            if col in df_packing_m.columns:
                                df_packing_m[col] = df_packing_m[col].apply(parse_monto_seguro)
                            else:
                                df_packing_m[col] = 0.0
                        
                        df_packing_grouped_m = df_packing_m.groupby('codigo', as_index=False).agg({
                            'peso_bruto_kg': 'sum',
                            'peso_neto_kg': 'sum',
                            'volumen_cbm': 'sum'
                        })

                        # 4. Cruce secuencial sobre la factura intacta (Línea por Línea)
                        df_consolidado_m = pd.merge(df_factura_m, df_packing_grouped_m, on='codigo', how='left')
                        
                        # 5. Prorrateo logístico estricto individual por renglón
                        df_consolidado_m['total_cantidad_codigo'] = df_consolidado_m.groupby('codigo')['cantidad'].transform('sum')
                        
                        for col in ['peso_bruto_kg', 'peso_neto_kg', 'volumen_cbm']:
                            df_consolidado_m[col] = df_consolidado_m[col].fillna(0.0)
                            df_consolidado_m[col] = df_consolidado_m.apply(
                                lambda r: (r[col] * (r['cantidad'] / r['total_cantidad_codigo'])) if r['total_cantidad_codigo'] > 0 else 0.0,
                                axis=1
                            ).round(4)

                        df_consolidado_m = df_consolidado_m.drop(columns=['total_cantidad_codigo'])

                        # =========================================================================
                        # RE-ESTRUCTURACIÓN EXACTA SEGÚN FORMATOS EXIGIDOS POR EL SISTEMA EXTERNO
                        # =========================================================================
                        
                        # Formato 1: cargaMasivaCosto.xlsx -> Columnas: 'codigo', 'costo'
                        df_costo_final_out = df_consolidado_m[['codigo', 'costo_final']].copy()
                        df_costo_final_out.columns = ['codigo', 'costo']

                        # Formato 2: cargaMasivaPackageList.xlsx -> Columnas: 'rif', 'razonSocial', 'codigo', 'descripcion', 'cantidad', 'cbms', 'kgs'
                        rif_proveedor = str(json_factura_m.get("proveedor", {}).get("rif", "S_R")).strip()
                        razon_social_proveedor = str(json_factura_m.get("proveedor", {}).get("nombre", "S_R")).strip()
                        
                        # Nos traemos también 'descripcion' que ya existía en df_consolidado_m
                        df_pl_final_out = df_consolidado_m[['codigo', 'descripcion', 'cantidad', 'volumen_cbm', 'peso_bruto_kg']].copy()
                        
                        # Insertamos ordenadamente las nuevas columnas al inicio
                        df_pl_final_out.insert(0, 'razonSocial', razon_social_proveedor)
                        df_pl_final_out.insert(0, 'rif', rif_proveedor)
                        
                        # Renombramos explícitamente para que coincida al 100% con tu plantilla nueva
                        df_pl_final_out.columns = ['rif', 'razonSocial', 'codigo', 'descripcion', 'cantidad', 'cbms', 'kgs']

                        st.success("¡Plantillas para carga masiva construidas con éxito!")
                        
                        # Muestra previsualizaciones para control visual rápido
                        st.subheader("📋 Vista de Carga Masiva: Costos")
                        st.dataframe(df_costo_final_out, use_container_width=True)
                        
                        st.subheader("📋 Vista de Carga Masiva: Package List")
                        st.dataframe(df_pl_final_out, use_container_width=True)

                        # Generar los archivos binarios puros (sin filas vacías ni estilos en la cabecera)
                        buffer_masivo_costo = io.BytesIO()
                        with pd.ExcelWriter(buffer_masivo_costo, engine='xlsxwriter') as writer_costo:
                            df_costo_final_out.to_excel(writer_costo, index=False, sheet_name='Sheet1')
                        
                        buffer_masivo_pl = io.BytesIO()
                        with pd.ExcelWriter(buffer_masivo_pl, engine='xlsxwriter') as writer_pl:
                            df_pl_final_out.to_excel(writer_pl, index=False, sheet_name='Sheet1')

                        num_doc_m = str(json_factura_m.get("numero_documento", "S_N")).strip()
                        
                        st.markdown("### 📥 Descargar Archivos Listos")
                        col_dl1, col_dl2 = st.columns(2)
                        with col_dl1:
                            st.download_button(
                                label="📥 Descargar cargaMasivaCosto.xlsx",
                                data=buffer_masivo_costo.getvalue(),
                                file_name=f"cargaMasivaCosto_{num_doc_m}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="btn_dl_costo_m"
                            )
                        with col_dl2:
                            st.download_button(
                                label="📥 Descargar cargaMasivaPackageList.xlsx",
                                data=buffer_masivo_pl.getvalue(),
                                file_name=f"cargaMasivaPackageList_{num_doc_m}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="btn_dl_pl_m"
                            )
                except Exception as e:
                    st.error(f"Error crítico construyendo los layouts masivos: {e}")