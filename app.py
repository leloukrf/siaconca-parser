import streamlit as st
import pdfplumber
import pandas as pd
from groq import Groq
from google import genai
import json
import io
import time
import re
import os
import requests 

st.set_page_config(page_title="Siaconca: Extractor Profesional", layout="wide")

st.title("📄 Siaconca: Extractor v8.2 (Filtro Estricto + Cruce Blindado)")

# --- CARGA AUTOMÁTICA Y SEGURA DE API KEYS ---
groq_api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", "")
gemini_api_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")
openrouter_api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY", "")

# --- SISTEMA DE RESETEO PROFUNDO (Destruye los archivos cacheados) ---
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = str(int(time.time()))

st.sidebar.header("🛡️ Estado y Controles")
if st.sidebar.button("🧹 Limpiar Todo (Reset)", use_container_width=True, type="primary"):
    # Limpiamos todo el estado menos la llave del uploader, a esa le generamos un ID nuevo
    st.session_state.clear()
    st.session_state.uploader_key = str(int(time.time()))
    st.rerun()

st.sidebar.divider()

if groq_api_key or gemini_api_key or openrouter_api_key:
    st.sidebar.success("● Conectado a la Red de IA")
    if groq_api_key: st.sidebar.caption("1️⃣ Motor: Groq (Cascada de 3 Modelos)")
    if gemini_api_key: st.sidebar.caption("2️⃣ Motor: Gemini 1.5")
    if openrouter_api_key: st.sidebar.caption("3️⃣ Motor: OpenRouter")
else:
    st.sidebar.error("❌ Servidor desconectado. Faltan llaves.")

# --- FUNCIONES DE OPTIMIZACIÓN Y PARSEO ---
def optimizar_tokens(texto):
    if not texto: return ""
    return re.sub(r'[ \t]+', ' ', texto).strip()

def parse_monto_seguro(valor):
    if pd.isna(valor) or valor == '' or valor is None: return 0.0
    if isinstance(valor, (int, float)): return float(valor)
    valor_str = str(valor).strip()
    
    if re.search(r',\d{2,3}$', valor_str) or ('.' in valor_str and ',' in valor_str and valor_str.rfind(',') > valor_str.rfind('.')):
        limpio = valor_str.replace('.', '').replace(',', '.')
    else:
        limpio = valor_str.replace(',', '')
        
    try:
        return float(limpio)
    except ValueError:
        return 0.0

# Genera una llave de cruce a prueba de balas (sin espacios, guiones, ni minúsculas)
def normalizar_codigo_cruce(codigo):
    return re.sub(r'[^A-Z0-9]', '', str(codigo).upper())

# --- RED DE REDUNDANCIA MULTI-MODELO ---
def preguntar_ia(prompt_texto):
    error_log = []

    # INTENTO 1: GROQ (CON CASCADA INTERNA)
    if groq_api_key:
        client_groq = Groq(api_key=groq_api_key)
        modelos_groq = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
        
        for modelo in modelos_groq:
            try:
                res = client_groq.chat.completions.create(
                    model=modelo,
                    messages=[{"role": "system", "content": "Return ONLY JSON."}, {"role": "user", "content": prompt_texto}],
                    temperature=0.0, 
                    response_format={"type": "json_object"}
                )
                return json.loads(res.choices[0].message.content)
            except Exception as e:
                err_str = str(e).lower()
                if 'rate limit' in err_str or '429' in err_str or 'tokens' in err_str:
                    error_log.append(f"Groq ({modelo}) sin tokens. Saltando...")
                    continue
                else:
                    error_log.append(f"Groq ({modelo}) falló: {e}")
                    break 
    else:
        error_log.append("Groq: Key no configurada.")

    # INTENTO 2: GEMINI
    if gemini_api_key:
        try:
            time.sleep(4.2)
            client_genai = genai.Client(api_key=gemini_api_key)
            res = client_genai.models.generate_content(
                model='gemini-1.5-flash', 
                contents="Return ONLY JSON.\n\n" + prompt_texto,
                config={"response_mime_type": "application/json", "temperature": 0.0} 
            )
            return json.loads(res.text)
        except Exception as e:
            error_log.append(f"Gemini falló: {e}")
    else:
        error_log.append("Gemini: Key no configurada.")

    # INTENTO 3: OPENROUTER
    if openrouter_api_key:
        try:
            headers_or = {"Authorization": f"Bearer {openrouter_api_key}", "Content-Type": "application/json"}
            data_or = {
                "model": "openrouter/free", 
                "messages": [{"role": "system", "content": "Return ONLY JSON."}, {"role": "user", "content": prompt_texto}],
                "temperature": 0.0
            }
            response_or = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers_or, json=data_or)
            
            if response_or.ok:
                content_or = response_or.json()["choices"][0]["message"]["content"]
                content_or = re.sub(r'^```json\n|\n```$', '', content_or.strip())
                return json.loads(content_or)
            else:
                error_log.append(f"OpenRouter Error HTTP: {response_or.text}")
        except Exception as e:
            error_log.append(f"OpenRouter falló: {e}")

    # SI TODOS FALLAN
    st.error("❌ Falla masiva: Ningún motor de IA pudo responder.")
    st.code("\n".join(error_log))
    st.stop()


# =========================================================================
# INTERFAZ - SOLO 2 PESTAÑAS
# =========================================================================
tab1, tab2 = st.tabs([
    "📊 Extractor Simple (Solo Factura)", 
    "📥 Carga Masiva (Factura + Packing List)"
])

# =========================================================================
# PESTAÑA 1: EXTRACTOR SIMPLE
# =========================================================================
with tab1:
    st.header("Procesar Factura Individual")
    archivo_pdf = st.file_uploader("Sube el PDF de la factura", type=["pdf"], key=f"pdf_simple_{st.session_state.uploader_key}")

    if archivo_pdf:
        if st.button("🚀 Procesar Factura", type="primary", use_container_width=True):
            with st.spinner("Extrayendo y filtrando datos (Ignorando separadores)..."):
                try:
                    paginas_texto = []
                    with pdfplumber.open(archivo_pdf) as pdf:
                        for page in pdf.pages:
                            text = optimizar_tokens(page.extract_text(layout=False))
                            if text: paginas_texto.append(text + "\n")

                    texto_global = paginas_texto[0]
                    if len(paginas_texto) > 1:
                        texto_global += "\n---\n" + paginas_texto[-1]

                    prompt_global = f"""Analiza este texto de una factura y extrae EXCLUSIVAMENTE los datos globales de encabezado, ajustes y totales en JSON. No extraigas la lista de productos aquí.
                    Schema JSON esperado:
                    {{
                      "proveedor": {{"nombre": "", "rif": ""}},
                      "numero_documento": "NÚMERO DE FACTURA O PEDIDO",
                      "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}},
                      "totales": {{"total_neto_final": "0.00"}}
                    }}
                    Texto extraído:
                    {texto_global}"""
                    datos = preguntar_ia(prompt_global)

                    productos_totales = []
                    chunks = [paginas_texto[i:i+4] for i in range(0, len(paginas_texto), 4)]
                    progreso_simple = st.progress(0)
                    status_simple = st.empty()

                    for idx, chunk in enumerate(chunks):
                        status_simple.text(f"⏳ Extrayendo productos reales: Bloque {idx+1}/{len(chunks)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        
                        prompt_productos = f"""Analiza este segmento de factura y extrae TODOS los productos en JSON bajo estas reglas estrictas:
                        1. IGNORAR ENCABEZADOS DE CATEGORÍA: Textos como "CRIMPING TOOL", "BARRIER POLE", "CCTV CAMERA", "COMPUTER MONITOR" que NO tienen precio ni cantidad en su línea NO SON PRODUCTOS. Omítelos por completo.
                        2. CÓDIGO: El 'codigo' debe ser exclusivamente el Part Number alfanumérico (ej. 'DH-PFM914').
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "PART NUMBER", "cantidad": 0, "costo_unitario": "0.00"}}]
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
                    
                    datos['ajuste']['flete'] = flete
                    datos['ajuste']['descuento'] = descuento
                    datos['totales']['total_neto_final'] = parse_monto_seguro(datos.get("totales", {}).get("total_neto_final", "0.0"))

                    if items:
                        df_temp = pd.DataFrame(items)
                        df_temp['codigo'] = df_temp['codigo'].astype(str).str.strip().str.upper()
                        
                        # FORZAMOS LA DESCRIPCIÓN POR CÓDIGO (Overrides AI hallucinations)
                        df_temp['descripcion'] = df_temp['codigo']
                        
                        df_temp['cantidad'] = pd.to_numeric(df_temp['cantidad'], errors='coerce').fillna(0)
                        
                        # Limpiamos items basura que se hayan colado con cantidad 0
                        df_temp = df_temp[df_temp['cantidad'] > 0].copy()
                        
                        df_temp['costo_unitario'] = df_temp['costo_unitario'].apply(parse_monto_seguro)
                        
                        if flete > 0 or descuento > 0 or recargo > 0:
                            total_neto_real = datos['totales']['total_neto_final']
                            suma_bruta = (df_temp['cantidad'] * df_temp['costo_unitario']).sum()
                            factor = total_neto_real / suma_bruta if suma_bruta > 0 else 1.0
                        else:
                            factor = 1.0
                            
                        df_temp['costo_final'] = (df_temp['costo_unitario'] * factor).round(3)
                        df_temp['subtotal'] = (df_temp['cantidad'] * df_temp['costo_final']).round(3)
                        
                        datos['productos'] = df_temp.to_dict('records')

                    # Guardar en memoria RAM persistente
                    st.session_state.tab1_datos = datos
                    st.session_state.tab1_num_doc = num_doc

                except Exception as e:
                    st.error(f"Error crítico en Extracción: {e}")

    # Renderizado Persistente
    if "tab1_datos" in st.session_state:
        datos = st.session_state.tab1_datos
        num_doc = st.session_state.tab1_num_doc
        
        st.success("✅ Procesado por completo y sin encabezados basura.")
        st.info(f"📄 **Documento:** {num_doc} | **Proveedor:** {datos['proveedor']['nombre']}")
        
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
            ws.write(5, 0, "Número de Factura/Pedido:", fmt_verde); ws.write(5, 1, num_doc, fmt_normal)
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

        nombre_archivo = f"PEDIDO_{num_doc}.xlsx"
        st.download_button(f"📥 Descargar {nombre_archivo}", buffer.getvalue(), nombre_archivo, key="btn_simple_dl")

# =========================================================================
# PESTAÑA 2: CARGA MASIVA (Factura + Packing List)
# =========================================================================
with tab2:
    st.header("Generador de Plantillas Limpias para Sistema Externo")
    st.write("Sube la Factura y el Packing List. Esta pestaña procesa las líneas y exporta automáticamente los dos archivos individuales.")

    col_files_m = st.columns(2)
    with col_files_m[0]:
        pdf_inv_m = st.file_uploader("1. Sube la FACTURA (Invoice)", type=["pdf"], key=f"inv_masivo_{st.session_state.uploader_key}")
    with col_files_m[1]:
        pdf_pl_m = st.file_uploader("2. Sube el PACKING LIST", type=["pdf"], key=f"pl_masivo_{st.session_state.uploader_key}")

    if pdf_inv_m and pdf_pl_m:
        if st.button("🚀 Procesar y Generar Archivos Masivos", type="primary", use_container_width=True):
            with st.spinner("Ejecutando extracción lineal inteligente..."):
                try:
                    paginas_inv_m = []
                    with pdfplumber.open(pdf_inv_m) as pdf:
                        for page in pdf.pages:
                            text = optimizar_tokens(page.extract_text(layout=False))
                            if text: paginas_inv_m.append(text + "\n")
                    
                    texto_global_inv_m = paginas_inv_m[0]
                    if len(paginas_inv_m) > 1:
                        texto_global_inv_m += "\n---\n" + paginas_inv_m[-1]

                    prompt_global_inv_m = f"""Analiza este texto de una FACTURA y extrae EXCLUSIVAMENTE los datos globales.
                    Schema JSON esperado:
                    {{
                      "proveedor": {{"nombre": "", "rif": ""}},
                      "numero_documento": "NRO_FACTURA",
                      "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}},
                      "totales": {{"total_neto_final": "0.00"}}
                    }}
                    Texto:
                    {texto_global_inv_m}"""
                    json_factura_m = preguntar_ia(prompt_global_inv_m)

                    productos_factura_m = []
                    chunks_inv_m = [paginas_inv_m[i:i+4] for i in range(0, len(paginas_inv_m), 4)]
                    progreso_inv_m = st.progress(0)
                    status_inv_m = st.empty()

                    for idx, chunk in enumerate(chunks_inv_m):
                        status_inv_m.text(f"⏳ Extrayendo ítems reales de Invoice: Bloque {idx+1}/{len(chunks_inv_m)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        
                        prompt_prod_inv_m = f"""Analiza este segmento de FACTURA y extrae TODOS los productos bajo estas reglas:
                        1. IGNORAR ENCABEZADOS: Textos como "CRIMPING TOOL" o "CCTV CAMERA" sin cantidad ni precio NO SON PRODUCTOS. Omítelos totalmente.
                        2. CÓDIGO: El 'codigo' debe ser exclusivamente el modelo alfanumérico (ej. 'DH-PFM914').
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "PART NUMBER", "cantidad": 0, "costo_unitario": "0.00"}}]
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
                            text = optimizar_tokens(page.extract_text(layout=False))
                            if text: paginas_pl_m.append(text + "\n")

                    productos_packing_m = []
                    chunks_pl_m = [paginas_pl_m[i:i+4] for i in range(0, len(paginas_pl_m), 4)]
                    progreso_pl_m = st.progress(0)
                    status_pl_m = st.empty()

                    for idx, chunk in enumerate(chunks_pl_m):
                        status_pl_m.text(f"⏳ Extrayendo logística de Packing List: Bloque {idx+1}/{len(chunks_pl_m)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        
                        prompt_prod_pl_m = f"""Analiza este PACKING LIST y extrae datos logísticos reales.
                        1. IGNORAR ENCABEZADOS: Textos como "CRIMPING TOOL" sin peso ni volumen NO SON PRODUCTOS. Omítelos.
                        2. CÓDIGO: Extraer exclusivamente el Part Number alfanumérico.
                        Schema JSON esperado:
                        {{
                          "productos": [{{"codigo": "PART NUMBER", "peso_bruto_kg": "0.00", "volumen_cbm": "0.00"}}]
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
                        # Limpieza inicial de la factura
                        df_factura_m['codigo'] = df_factura_m['codigo'].astype(str).str.strip().str.upper()
                        
                        # FORZAMOS LA DESCRIPCIÓN POR CÓDIGO DE FORMA PROGRAMÁTICA
                        df_factura_m['descripcion'] = df_factura_m['codigo']
                        
                        df_factura_m['cantidad'] = pd.to_numeric(df_factura_m['cantidad'], errors='coerce').fillna(0)
                        df_factura_m = df_factura_m[df_factura_m['cantidad'] > 0].copy()
                        df_factura_m['costo_unitario'] = df_factura_m['costo_unitario'].apply(parse_monto_seguro)

                        # Limpieza del Packing List
                        df_packing_m['codigo'] = df_packing_m['codigo'].astype(str).str.strip().str.upper()
                        
                        for col in ['peso_bruto_kg', 'volumen_cbm']:
                            if col in df_packing_m.columns:
                                df_packing_m[col] = df_packing_m[col].apply(parse_monto_seguro)
                            else:
                                df_packing_m[col] = 0.0
                        
                        df_packing_m = df_packing_m[(df_packing_m['peso_bruto_kg'] > 0) | (df_packing_m['volumen_cbm'] > 0)]
                        
                        # CREAMOS LA LLAVE DE CRUCE BLINDADA (Ignora diferencias de OCR como espacios o guiones)
                        df_factura_m['codigo_clean'] = df_factura_m['codigo'].apply(normalizar_codigo_cruce)
                        df_packing_m['codigo_clean'] = df_packing_m['codigo'].apply(normalizar_codigo_cruce)

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

                        df_packing_grouped_m = df_packing_m.groupby('codigo_clean', as_index=False).agg({
                            'peso_bruto_kg': 'sum',
                            'volumen_cbm': 'sum'
                        })

                        # CRUCE BLINDADO usando la llave limpia
                        df_consolidado_m = pd.merge(df_factura_m, df_packing_grouped_m, on='codigo_clean', how='left')
                        df_consolidado_m['total_cantidad_codigo'] = df_consolidado_m.groupby('codigo_clean')['cantidad'].transform('sum')
                        
                        for col in ['peso_bruto_kg', 'volumen_cbm']:
                            df_consolidado_m[col] = df_consolidado_m[col].fillna(0.0)
                            df_consolidado_m[col] = df_consolidado_m.apply(
                                lambda r: (r[col] * (r['cantidad'] / r['total_cantidad_codigo'])) if r['total_cantidad_codigo'] > 0 else 0.0,
                                axis=1
                            ).round(4)

                        df_consolidado_m = df_consolidado_m.drop(columns=['total_cantidad_codigo', 'codigo_clean'])
                        
                        df_costo_final_out = df_consolidado_m[['codigo', 'costo_final']].copy()
                        df_costo_final_out.columns = ['codigo', 'costo']

                        rif_proveedor = str(json_factura_m.get("proveedor", {}).get("rif", "S_R")).strip()
                        razon_social_proveedor = str(json_factura_m.get("proveedor", {}).get("nombre", "S_R")).strip()
                        
                        # Generación Final del Excel de Package List
                        df_pl_final_out = df_consolidado_m[['codigo', 'descripcion', 'cantidad', 'volumen_cbm', 'peso_bruto_kg']].copy()
                        df_pl_final_out.insert(0, 'razonSocial', razon_social_proveedor)
                        df_pl_final_out.insert(0, 'rif', rif_proveedor)
                        df_pl_final_out.columns = ['rif', 'razonSocial', 'codigo', 'descripcion', 'cantidad', 'cbms', 'kgs']

                        st.session_state.tab2_df_costo = df_costo_final_out
                        st.session_state.tab2_df_pl = df_pl_final_out
                        st.session_state.tab2_num_doc = str(json_factura_m.get("numero_documento", "S_N")).strip()

                except Exception as e:
                    st.error(f"Error crítico en Extracción Masiva: {e}")

    if "tab2_df_costo" in st.session_state:
        df_costo_final_out = st.session_state.tab2_df_costo
        df_pl_final_out = st.session_state.tab2_df_pl
        num_doc_m = st.session_state.tab2_num_doc

        st.success("¡Plantillas para carga masiva construidas y filtradas con éxito!")
        
        st.subheader("📋 Vista de Carga Masiva: Costos")
        st.dataframe(df_costo_final_out, use_container_width=True)
        
        st.subheader("📋 Vista de Carga Masiva: Package List")
        st.dataframe(df_pl_final_out, use_container_width=True)

        buffer_masivo_costo = io.BytesIO()
        with pd.ExcelWriter(buffer_masivo_costo, engine='xlsxwriter') as writer_costo:
            df_costo_final_out.to_excel(writer_costo, index=False, sheet_name='Sheet1')
        
        buffer_masivo_pl = io.BytesIO()
        with pd.ExcelWriter(buffer_masivo_pl, engine='xlsxwriter') as writer_pl:
            df_pl_final_out.to_excel(writer_pl, index=False, sheet_name='Sheet1')

        st.markdown("### 📥 Descargar Archivos Listos")
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                label="📥 Descargar cargaMasivaCosto.xlsx",
                data=buffer_masivo_costo.getvalue(),
                file_name=f"cargaMasivaCosto_{num_doc_m}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="btn_dl_costo_m_persistente"
            )
        with col_dl2:
            st.download_button(
                label="📥 Descargar cargaMasivaPackageList.xlsx",
                data=buffer_masivo_pl.getvalue(),
                file_name=f"cargaMasivaPackageList_{num_doc_m}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="btn_dl_pl_m_persistente"
            )