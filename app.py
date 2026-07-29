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

st.title("📄 Siaconca: Extractor v8.4 (Ultra Precisión y Paginación)")

# --- CARGA AUTOMÁTICA Y SEGURA DE API KEYS ---
groq_api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", "")
gemini_api_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")
openrouter_api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY", "")

# --- SISTEMA DE RESETEO PROFUNDO ---
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = str(int(time.time()))

st.sidebar.header("🛡️ Estado y Controles")
if st.sidebar.button("🧹 Limpiar Todo (Reset)", use_container_width=True, type="primary"):
    st.session_state.clear()
    st.session_state.uploader_key = str(int(time.time()))
    st.rerun()

st.sidebar.divider()

if groq_api_key or gemini_api_key or openrouter_api_key:
    st.sidebar.success("● Conectado a la Red de IA")
    if groq_api_key: st.sidebar.caption("1️⃣ Motor: Groq (Cascada)")
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
    valor_str = str(valor).strip().replace(' ', '')
    
    if re.search(r',\d{2,3}$', valor_str) or ('.' in valor_str and ',' in valor_str and valor_str.rfind(',') > valor_str.rfind('.')):
        limpio = valor_str.replace('.', '').replace(',', '.')
    else:
        limpio = valor_str.replace(',', '')
        
    try:
        return float(limpio)
    except ValueError:
        return 0.0

def normalizar_codigo_cruce(codigo):
    c = str(codigo).upper()
    c = re.sub(r'[^A-Z0-9]', '', c)
    c = c.replace('I', '1').replace('O', '0')
    return c

# --- RED DE REDUNDANCIA MULTI-MODELO ---
def preguntar_ia(prompt_texto):
    error_log = []

    # INTENTO 1: GROQ
    if groq_api_key:
        client_groq = Groq(api_key=groq_api_key)
        modelos_groq = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
        
        for modelo in modelos_groq:
            try:
                res = client_groq.chat.completions.create(
                    model=modelo,
                    messages=[
                        {"role": "system", "content": "Return ONLY JSON. Extract absolutely all items, do not truncate."}, 
                        {"role": "user", "content": prompt_texto}
                    ],
                    temperature=0.0, 
                    max_tokens=4000,
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
                contents="Return ONLY JSON. Extract absolutely all items.\n\n" + prompt_texto,
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
                "temperature": 0.0,
                "max_tokens": 4000
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

    st.error("❌ Falla masiva: Ningún motor de IA pudo responder.")
    st.code("\n".join(error_log))
    st.stop()


# =========================================================================
# INTERFAZ
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
            with st.spinner("Extrayendo página por página..."):
                try:
                    paginas_texto = []
                    with pdfplumber.open(archivo_pdf) as pdf:
                        for page in pdf.pages:
                            text = optimizar_tokens(page.extract_text(layout=False))
                            if text: paginas_texto.append(text + "\n")

                    texto_global = paginas_texto[0]
                    if len(paginas_texto) > 1:
                        texto_global += "\n---\n" + paginas_texto[-1]

                    prompt_global = f"""Analiza este texto de una factura y extrae EXCLUSIVAMENTE los datos globales.
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
                    # CHUNKING REPARADO: 1 Página a la vez
                    chunks = [paginas_texto[i:i+1] for i in range(0, len(paginas_texto), 1)]
                    progreso_simple = st.progress(0)
                    status_simple = st.empty()

                    for idx, chunk in enumerate(chunks):
                        status_simple.text(f"⏳ Procesando Página {idx+1}/{len(chunks)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        
                        prompt_productos = f"""Analiza este segmento de factura y extrae TODOS los productos.
                        REGLAS ESTRICTAS:
                        1. NO TE SALTES NADA: Extrae todas las filas que correspondan a productos.
                        2. IGNORAR ENCABEZADOS: Textos que no tienen precio ni cantidad NO SON PRODUCTOS.
                        3. CÓDIGO DIVIDIDO: Si un 'codigo' (Part Number) está dividido en dos líneas por un salto de línea (ej. termina en guion y sigue abajo), únelo obligatoriamente en un solo texto continuo.
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
                        df_temp['descripcion'] = df_temp['codigo'] 
                        df_temp['cantidad'] = pd.to_numeric(df_temp['cantidad'], errors='coerce').fillna(0)
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

                    st.session_state.tab1_datos = datos
                    st.session_state.tab1_num_doc = num_doc

                except Exception as e:
                    st.error(f"Error crítico en Extracción: {e}")

    if "tab1_datos" in st.session_state:
        datos = st.session_state.tab1_datos
        num_doc = st.session_state.tab1_num_doc
        
        st.success("✅ Procesado por completo.")
        
        df_display = pd.DataFrame(datos['productos'])[["codigo", "descripcion", "cantidad", "costo_unitario", "costo_final", "subtotal"]]
        df_display.columns = ["CODIGO", "DESCRIPCION", "CANTIDAD", "COSTO LISTA", "COSTO FINAL", "SUBTOTAL"]
        st.dataframe(df_display, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_display.to_excel(writer, sheet_name='Carga', index=False)
        st.download_button(f"📥 Descargar PEDIDO_{num_doc}.xlsx", buffer.getvalue(), f"PEDIDO_{num_doc}.xlsx", key="btn_simple_dl")

# =========================================================================
# PESTAÑA 2: CARGA MASIVA (Factura + Packing List)
# =========================================================================
with tab2:
    st.header("Generador de Plantillas Limpias para Sistema Externo")
    col_files_m = st.columns(2)
    with col_files_m[0]:
        pdf_inv_m = st.file_uploader("1. Sube la FACTURA (Invoice)", type=["pdf"], key=f"inv_masivo_{st.session_state.uploader_key}")
    with col_files_m[1]:
        pdf_pl_m = st.file_uploader("2. Sube el PACKING LIST", type=["pdf"], key=f"pl_masivo_{st.session_state.uploader_key}")

    if pdf_inv_m and pdf_pl_m:
        if st.button("🚀 Procesar y Generar Archivos Masivos", type="primary", use_container_width=True):
            with st.spinner("Ejecutando extracción profunda..."):
                try:
                    paginas_inv_m = []
                    with pdfplumber.open(pdf_inv_m) as pdf:
                        for page in pdf.pages:
                            text = optimizar_tokens(page.extract_text(layout=False))
                            if text: paginas_inv_m.append(text + "\n")
                    
                    texto_global_inv_m = paginas_inv_m[0]
                    if len(paginas_inv_m) > 1: texto_global_inv_m += "\n---\n" + paginas_inv_m[-1]

                    json_factura_m = preguntar_ia(f"""Extrae datos globales en JSON.
                    {{ "proveedor": {{"nombre": "", "rif": ""}}, "numero_documento": "NRO", "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}}, "totales": {{"total_neto_final": "0.00"}} }}
                    Texto: {texto_global_inv_m}""")

                    productos_factura_m = []
                    # CHUNKING REPARADO: 1 Página a la vez
                    chunks_inv_m = [paginas_inv_m[i:i+1] for i in range(0, len(paginas_inv_m), 1)]
                    progreso_inv_m = st.progress(0)
                    status_inv_m = st.empty()

                    for idx, chunk in enumerate(chunks_inv_m):
                        status_inv_m.text(f"⏳ Extrayendo Factura: Página {idx+1}/{len(chunks_inv_m)}...")
                        
                        prompt_prod_inv_m = f"""Analiza esta página y extrae TODOS los productos.
                        REGLAS OBLIGATORIAS:
                        1. NO TE SALTES NADA: Debes incluir cada producto listado.
                        2. CÓDIGO DIVIDIDO: Si un Part Number está partido en dos líneas por un salto de línea (ej. termina en guion y sigue abajo), ÚNELO en un solo texto continuo.
                        3. IGNORAR ENCABEZADOS DE CATEGORÍA.
                        Schema JSON:
                        {{ "productos": [{{"codigo": "PART NUMBER", "cantidad": 0, "costo_unitario": "0.00"}}] }}
                        Texto:
                        {"\n--- NUEVA PÁGINA ---\n".join(chunk)}"""
                        
                        res_chunk = preguntar_ia(prompt_prod_inv_m)
                        productos_factura_m.extend(res_chunk.get("productos", []))
                        progreso_inv_m.progress((idx + 1) / len(chunks_inv_m))

                    status_inv_m.empty(); progreso_inv_m.empty()

                    paginas_pl_m = []
                    with pdfplumber.open(pdf_pl_m) as pdf:
                        for page in pdf.pages:
                            text = optimizar_tokens(page.extract_text(layout=False))
                            if text: paginas_pl_m.append(text + "\n")

                    productos_packing_m = []
                    # CHUNKING REPARADO: 1 Página a la vez
                    chunks_pl_m = [paginas_pl_m[i:i+1] for i in range(0, len(paginas_pl_m), 1)]
                    progreso_pl_m = st.progress(0)
                    status_pl_m = st.empty()

                    for idx, chunk in enumerate(chunks_pl_m):
                        status_pl_m.text(f"⏳ Extrayendo Packing List: Página {idx+1}/{len(chunks_pl_m)}...")
                        
                        prompt_prod_pl_m = f"""Analiza esta página del PACKING LIST.
                        REGLAS OBLIGATORIAS:
                        1. EXTRAE TODAS LAS FILAS: No omitas productos como DH-PFM.
                        2. CÓDIGO DIVIDIDO: Si un Part Number está partido en dos líneas, únelo.
                        3. MAPEADO EXACTO: El Peso Bruto (Gr.wt) asígnalo a la llave 'kgs'. El Volumen (Measurement o CBMS) asígnalo a 'cbms'.
                        Schema JSON:
                        {{ "productos": [{{"codigo": "PART NUMBER", "kgs": "0.00", "cbms": "0.00"}}] }}
                        Texto:
                        {"\n--- NUEVA PÁGINA ---\n".join(chunk)}"""
                        
                        res_chunk = preguntar_ia(prompt_prod_pl_m)
                        productos_packing_m.extend(res_chunk.get("productos", []))
                        progreso_pl_m.progress((idx + 1) / len(chunks_pl_m))

                    status_pl_m.empty(); progreso_pl_m.empty()

                    df_factura_m = pd.DataFrame(productos_factura_m)
                    df_packing_m = pd.DataFrame(productos_packing_m)

                    if df_factura_m.empty or df_packing_m.empty:
                        st.error("Error al consolidar. Verifica tus PDFs.")
                    else:
                        df_factura_m['codigo'] = df_factura_m['codigo'].astype(str).str.strip().str.upper()
                        df_factura_m['descripcion'] = df_factura_m['codigo'] 
                        df_factura_m['cantidad'] = pd.to_numeric(df_factura_m['cantidad'], errors='coerce').fillna(0)
                        df_factura_m = df_factura_m[df_factura_m['cantidad'] > 0].copy()
                        df_factura_m['costo_unitario'] = df_factura_m['costo_unitario'].apply(parse_monto_seguro)

                        df_packing_m['codigo'] = df_packing_m['codigo'].astype(str).str.strip().str.upper()
                        for col in ['kgs', 'cbms']:
                            if col in df_packing_m.columns:
                                df_packing_m[col] = df_packing_m[col].apply(parse_monto_seguro)
                            else:
                                df_packing_m[col] = 0.0
                        
                        df_packing_m = df_packing_m[(df_packing_m['kgs'] > 0) | (df_packing_m['cbms'] > 0)]
                        
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
                            'kgs': 'sum',
                            'cbms': 'sum'
                        })

                        df_consolidado_m = pd.merge(df_factura_m, df_packing_grouped_m, on='codigo_clean', how='left')
                        df_consolidado_m['total_cantidad_codigo'] = df_consolidado_m.groupby('codigo_clean')['cantidad'].transform('sum')
                        
                        for col in ['kgs', 'cbms']:
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
                        
                        df_pl_final_out = df_consolidado_m[['codigo', 'descripcion', 'cantidad', 'cbms', 'kgs']].copy()
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

        st.success("¡Plantillas construidas exitosamente!")
        st.dataframe(df_pl_final_out, use_container_width=True)

        buf_c = io.BytesIO(); buf_p = io.BytesIO()
        with pd.ExcelWriter(buf_c, engine='xlsxwriter') as w1: df_costo_final_out.to_excel(w1, index=False)
        with pd.ExcelWriter(buf_p, engine='xlsxwriter') as w2: df_pl_final_out.to_excel(w2, index=False)

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1: st.download_button("📥 Descargar cargaMasivaCosto.xlsx", buf_c.getvalue(), f"cargaMasivaCosto_{num_doc_m}.xlsx")
        with col_dl2: st.download_button("📥 Descargar cargaMasivaPackageList.xlsx", buf_p.getvalue(), f"cargaMasivaPackageList_{num_doc_m}.xlsx")