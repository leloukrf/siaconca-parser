import streamlit as st
import pdfplumber
import pandas as pd
from google import genai
from groq import Groq
import json
import io
import time
import re

# --- CONFIGURACIÓN ---
GROQ_API_KEY = "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"
GEMINI_API_KEY = "AIzaSyDTsGFLY9imY0AzRWBBSM5Y-uqS6u6aZXE"

client_groq = Groq(api_key=GROQ_API_KEY)
client_gemini = genai.Client(api_key=GEMINI_API_KEY)

st.set_page_config(page_title="Siaconca: Extractor Profesional", layout="wide")

st.title("📄 Siaconca: Extractor v5.0 (Módulo de Costos y Logística)")

# Creamos las dos pestañas solicitadas
tab1, tab2 = st.tabs(["📊 Extractor Simple (Solo Factura)", "📦 Consolidado Logístico (Factura + Packing List)"])

# =========================================================================
# PESTAÑA 1: EXTRACTOR SIMPLE (Mantiene la lógica original intacta)
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
            with st.spinner("Leyendo y extrayendo datos con IA..."):
                try:
                    texto_extraido = ""
                    with pdfplumber.open(archivo_pdf) as pdf:
                        for page in pdf.pages:
                            texto_extraido += page.extract_text(layout=False) + "\n"

                    prompt = f"""Analiza este texto de una factura o presupuesto y extrae los datos en JSON.
                    IMPORTANTE: Busca el número de identificación del documento (Invoice Number, Presupuesto Nro, Control, etc).
                    
                    Schema JSON esperado:
                    {{
                      "proveedor": {{"nombre": "", "rif": ""}},
                      "numero_documento": "AQUÍ EL NÚMERO DE FACTURA O PEDIDO",
                      "ajuste": {{"flete": 0.00, "descuento": 0.00, "recargo": 0.00}},
                      "totales": {{"total_neto_final": 0.00}},
                      "productos": [{{"codigo": "", "descripcion": "", "cantidad": 0, "costo_unitario": 0.00}}]
                    }}
                    
                    Texto extraído:
                    {texto_extraido[:12000]}"""

                    try:
                        res = client_groq.chat.completions.create(
                            model="llama-3.3-70b-versatile",
                            messages=[{"role": "system", "content": "Return ONLY JSON."}, {"role": "user", "content": prompt}],
                            temperature=0.1, response_format={"type": "json_object"}
                        )
                        response_text = res.choices[0].message.content
                    except:
                        res = client_gemini.models.generate_content(model='gemini-1.5-flash', contents=prompt)
                        response_text = res.text

                    datos = json.loads(response_text)
                    if isinstance(datos, list): datos = datos[0]
                    
                    num_doc = str(datos.get("numero_documento", "S_N")).strip()
                    if len(num_doc) > 20: 
                        temp_match = re.search(r'([A-Z0-9-]{4,})', num_doc)
                        if temp_match: num_doc = temp_match.group(1)

                    items = datos.get("productos", [])
                    ajustes = datos.get("ajuste", {})
                    flete = float(ajustes.get("flete", 0.0) or 0.0)
                    descuento = float(ajustes.get("descuento", 0.0) or 0.0)
                    recargo = float(ajustes.get("recargo", 0.0) or 0.0)

                    if items:
                        df_temp = pd.DataFrame(items)
                        if flete > 0 or descuento > 0 or recargo > 0:
                            total_neto_real = float(datos.get("totales", {}).get("total_neto_final", 0.0))
                            suma_bruta = (df_temp['cantidad'] * df_temp['costo_unitario']).sum()
                            factor = total_neto_real / suma_bruta if suma_bruta > 0 else 1.0
                        else:
                            factor = 1.0
                            
                        df_temp['costo_final'] = (df_temp['costo_unitario'] * factor).round(3)
                        datos['productos'] = df_temp.to_dict('records')

                    st.session_state.num_doc = num_doc
                    st.session_state.datos = datos
                    st.session_state.datos_listos = True
                    st.session_state.tiempo = time.time() - inicio
                except Exception as e:
                    st.error(f"Error crítico: {e}"); st.stop()

        if st.session_state.get("datos_listos"):
            st.success(f"✅ Procesado en {st.session_state.tiempo:.2f}s")
            datos = st.session_state.datos
            st.info(f"📄 **Documento:** {st.session_state.num_doc} | **Proveedor:** {datos['proveedor']['nombre']}")
            
            col1, col2, col3 = st.columns(3)
            with col1: st.metric("Flete", f"$ {datos.get('ajuste', {}).get('flete', 0.0):,.2f}")
            with col2: st.metric("Descuento", f"$ {datos.get('ajuste', {}).get('descuento', 0.0):,.2f}")
            with col3: st.metric("TOTAL NETO PDF", f"$ {datos['totales']['total_neto_final']:,.2f}")

            df_display = pd.DataFrame(datos['productos'])[["codigo", "descripcion", "cantidad", "costo_unitario", "costo_final"]]
            df_display.columns = ["CODIGO", "DESCRIPCION", "CANTIDAD", "COSTO LISTA", "COSTO FINAL"]
            
            st.dataframe(df_display.style.format({"COSTO LISTA": "{:,.3f}", "COSTO FINAL": "{:,.3f}", "CANTIDAD": "{:,.0f}"}), use_container_width=True)

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_display.to_excel(writer, sheet_name='Carga', index=False, startrow=8)
                wb, ws = writer.book, writer.sheets['Carga']
                fmt_verde = wb.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1})
                fmt_normal = wb.add_format({'border': 1})
                fmt_num = wb.add_format({'border': 1, 'num_format': '#,##0.000'})
                fmt_header = wb.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1, 'align': 'center'})

                ws.write(1, 0, "Nombre / Razón Social:", fmt_verde); ws.write(1, 1, datos['proveedor']['nombre'], fmt_normal)
                ws.write(2, 0, "RIF / Identificación:", fmt_verde); ws.write(2, 1, datos['proveedor']['rif'], fmt_normal)
                ws.write(5, 0, "Número de Factura/Pedido:", fmt_verde); ws.write(5, 1, st.session_state.num_doc, fmt_normal)
                ws.write(6, 0, "Flete Extra:", fmt_verde); ws.write(6, 1, datos.get('ajuste', {}).get('flete', 0.0), fmt_num)
                ws.write(6, 2, "Descuento:", fmt_verde); ws.write(6, 3, datos.get('ajuste', {}).get('descuento', 0.0), fmt_num)

                ws.set_column('A:A', 30); ws.set_column('B:B', 50); ws.set_column('C:E', 15)
                for col_num, value in enumerate(df_display.columns.values): ws.write(8, col_num, value, fmt_header)
                for r_idx, row in enumerate(df_display.values):
                    ws.write(9 + r_idx, 0, row[0], fmt_normal); ws.write(9 + r_idx, 1, row[1], fmt_normal)
                    ws.write(9 + r_idx, 2, row[2], fmt_normal); ws.write(9 + r_idx, 3, row[3], fmt_num); ws.write(9 + r_idx, 4, row[4], fmt_num)

            nombre_archivo = f"PEDIDO_{st.session_state.num_doc}.xlsx"
            st.download_button(f"📥 Descargar {nombre_archivo}", buffer.getvalue(), nombre_archivo, key="btn_simple")


# =========================================================================
# PESTAÑA 2: CONSOLIDADO LOGÍSTICO (Usa IA por separado + Match exacto en Python)
# =========================================================================
with tab2:
    st.header("Consolidación por Código de Producto")
    st.write("Sube la Factura y el Packing List de Dahua para fusionar los costos con sus pesos y volúmenes.")

    col_files = st.columns(2)
    with col_files[0]:
        pdf_inv = st.file_uploader("1. Sube la FACTURA (Invoice)", type=["pdf"], key="invoice_union")
    with col_files[1]:
        pdf_pl = st.file_uploader("2. Sube el PACKING LIST", type=["pdf"], key="packing_union")

    if pdf_inv and pdf_pl:
        if st.button("🚀 Cruzar y Consolidar Datos", use_container_width=True):
            with st.spinner("Procesando documentos de forma independiente..."):
                try:
                    # 1. Extraer texto de ambos archivos
                    texto_inv = ""
                    with pdfplumber.open(pdf_inv) as pdf:
                        for page in pdf.pages: texto_inv += page.extract_text(layout=False) + "\n"

                    texto_pl = ""
                    with pdfplumber.open(pdf_pl) as pdf:
                        for page in pdf.pages: texto_pl += page.extract_text(layout=False) + "\n"

                    # --- PASO 1: EXTRAER SOLO LA FACTURA ---
                    prompt_inv = f"""Analiza este texto de una FACTURA y extrae los datos en JSON.
                    Schema JSON esperado:
                    {{
                      "numero_documento": "NRO_FACTURA",
                      "ajuste": {{"flete": 0.00, "descuento": 0.00, "recargo": 0.00}},
                      "totales": {{"total_neto_final": 0.00}},
                      "productos": [{{"codigo": "", "descripcion": "", "cantidad": 0, "costo_unitario": 0.00}}]
                    }}
                    Texto extraído:
                    {texto_inv}"""

                    # --- PASO 2: EXTRAER SOLO EL PACKING LIST ---
                    prompt_pl = f"""Analiza este texto de un PACKING LIST y extrae los datos de empaque en JSON.
                    Schema JSON esperado:
                    {{
                      "productos": [{{"codigo": "", "peso_bruto_kg": 0.00, "peso_neto_kg": 0.00, "volumen_cbm": 0.00}}]
                    }}
                    Texto extraído:
                    {texto_pl}"""

                    # Ejecutor modular usando Gemini 2.5 Flash para evitar cualquier caída por tamaño
                    def preguntar_ia(prompt_texto):
                        try:
                            # Intentamos con la sintaxis correcta del nuevo SDK usando Gemini 2.5 Flash
                            res = client_gemini.models.generate_content(
                                model='gemini-2.5-flash',
                                contents=prompt_texto,
                                config={"response_mime_type": "application/json"} # Forzar salida estructurada limpia
                            )
                            return json.loads(res.text)
                        except Exception as e_gemini:
                            # Fallback secundario a Groq si es requerido
                            try:
                                res = client_groq.chat.completions.create(
                                    model="llama-3.3-70b-versatile",
                                    messages=[{"role": "user", "content": prompt_texto}],
                                    temperature=0.1, response_format={"type": "json_object"}
                                )
                                return json.loads(res.choices[0].message.content)
                            except Exception as e_groq:
                                st.error(f"Error crítico en la IA: Gemini ({e_gemini}) | Groq ({e_groq})")
                                st.stop()

                    st.text("⏳ Extrayendo ítems y costos de la Factura...")
                    json_factura = preguntar_ia(prompt_inv)
                    
                    st.text("⏳ Extrayendo pesos y medidas del Packing List...")
                    json_packing = preguntar_ia(prompt_pl)

                    # --- PASO 3: PROCESAMIENTO EN PANDAS (Fusión libre de errores de contexto) ---
                    df_factura = pd.DataFrame(json_factura.get("productos", []))
                    df_packing = pd.DataFrame(json_packing.get("productos", []))

                    if df_factura.empty or df_packing.empty:
                        st.error("No se pudieron extraer listas válidas de productos. Asegúrate de que los archivos correspondan.")
                    else:
                        # Limpieza estricta de códigos
                        df_factura['codigo'] = df_factura['codigo'].astype(str).str.strip()
                        df_packing['codigo'] = df_packing['codigo'].astype(str).str.strip()

                        # Eliminar duplicados en la tabla de packing (por si la IA repitió filas de paletas)
                        df_packing = df_packing.drop_duplicates(subset=['codigo'])

                        # Lógica de Prorrateo de Costos en la Factura
                        ajustes = json_factura.get("ajuste", {})
                        flete = float(ajustes.get("flete", 0.0) or 0.0)
                        descuento = float(ajustes.get("descuento", 0.0) or 0.0)
                        recargo = float(ajustes.get("recargo", 0.0) or 0.0)

                        if flete > 0 or descuento > 0 or recargo > 0:
                            total_neto_real = float(json_factura.get("totales", {}).get("total_neto_final", 0.0))
                            suma_bruta = (df_factura['cantidad'] * df_factura['costo_unitario']).sum()
                            factor = total_neto_real / suma_bruta if suma_bruta > 0 else 1.0
                        else:
                            factor = 1.0
                        
                        df_factura['costo_final'] = (df_factura['costo_unitario'] * factor).round(3)

                        # --- EL MATCH PERFECTO EN PYTHON ---
                        df_consolidado = pd.merge(df_factura, df_packing, on='codigo', how='left')
                        
                        # Completar valores nulos de logística de forma segura
                        for col in ['peso_bruto_kg', 'peso_neto_kg', 'volumen_cbm']:
                            if col in df_consolidado.columns:
                                df_consolidado[col] = pd.to_numeric(df_consolidado[col], errors='coerce').fillna(0.0)
                            else:
                                df_consolidado[col] = 0.0

                        # Reestructurar nombres para visualización
                        df_final_display = df_consolidado[["codigo", "descripcion", "cantidad", "costo_unitario", "costo_final", "peso_bruto_kg", "peso_neto_kg", "volumen_cbm"]]
                        df_final_display.columns = ["CODIGO", "DESCRIPCION", "CANTIDAD", "COSTO LISTA", "COSTO FINAL", "PESO BRUTO (KG)", "PESO NETO (KG)", "VOLUMEN (CBM)"]

                        # --- RENDERIZAR RESULTADOS ---
                        st.success("¡Datos cruzados matemáticamente en Python sin errores de JSON!")
                        
                        num_doc_union = json_factura.get("numero_documento", "S_N")
                        st.info(f"📄 **Nro. Invoice Detectado:** {num_doc_union}")

                        st.dataframe(
                            df_final_display.style.format({
                                "COSTO LISTA": "{:,.3f}", "COSTO FINAL": "{:,.3f}", "CANTIDAD": "{:,.0f}",
                                "PESO BRUTO (KG)": "{:,.2f}", "PESO NETO (KG)": "{:,.2f}", "VOLUMEN (CBM)": "{:,.4f}"
                            }).set_table_styles([{'selector': 'th', 'props': [('background-color', '#CFE2F3'), ('color', 'black'), ('font-weight', 'bold')]}])
                            , use_container_width=True
                        )

                        # --- GENERACIÓN DEL EXCEL ---
                        buffer_logistico = io.BytesIO()
                        with pd.ExcelWriter(buffer_logistico, engine='xlsxwriter') as writer:
                            df_final_display.to_excel(writer, sheet_name='Consolidado Logistico', index=False, startrow=4)
                            wb, ws = writer.book, writer.sheets['Consolidado Logistico']
                            
                            fmt_azul_header = wb.add_format({'bold': True, 'bg_color': '#CFE2F3', 'border': 1, 'align': 'center'})
                            fmt_normal = wb.add_format({'border': 1})
                            fmt_num_tres = wb.add_format({'border': 1, 'num_format': '#,##0.000'})
                            fmt_num_dos = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
                            fmt_num_cuatro = wb.add_format({'border': 1, 'num_format': '#,##0.0000'})
                            
                            ws.write(0, 0, "CONSOLIDADO DE COSTOS, PESOS Y MEDIDAS", wb.add_format({'bold': True, 'font_size': 14}))
                            ws.write(1, 0, f"Invoice Nro: {num_doc_union}", fmt_normal)

                            ws.set_column('A:A', 25)
                            ws.set_column('B:B', 45)
                            ws.set_column('C:E', 15)
                            ws.set_column('F:H', 18)
                            
                            for col_num, value in enumerate(df_final_display.columns.values):
                                ws.write(4, col_num, value, fmt_azul_header)

                            for r_idx, row in enumerate(df_final_display.values):
                                ws.write(5 + r_idx, 0, row[0], fmt_normal)
                                ws.write(5 + r_idx, 1, row[1], fmt_normal)
                                ws.write(5 + r_idx, 2, row[2], fmt_normal)
                                ws.write(5 + r_idx, 3, row[3], fmt_num_tres)
                                ws.write(5 + r_idx, 4, row[4], fmt_num_tres)
                                ws.write(5 + r_idx, 5, row[5], fmt_num_dos)
                                ws.write(5 + r_idx, 6, row[6], fmt_num_dos)
                                ws.write(5 + r_idx, 7, row[7], fmt_num_cuatro)

                        nombre_archivo_log = f"CONSOLIDADO_LOGISTICO_{num_doc_union}.xlsx"
                        st.download_button(f"📥 Descargar {nombre_archivo_log}", buffer_logistico.getvalue(), nombre_archivo_log, key="btn_union")
                
                except Exception as e:
                    st.error(f"Error crítico en el proceso de consolidación: {e}")