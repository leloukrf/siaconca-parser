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

st.title("📄 Siaconca: Extractor v8.5 (Anti-Alucinaciones de Columnas)")

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
    # OJO: antes esto colapsaba espacios múltiples a uno solo con r'[ \t]+' -> ' ',
    # lo cual destruye la alineación de columnas que produce layout=True (esa
    # alineación se representa justamente con varios espacios seguidos). Ahora
    # solo quitamos espacios/tabs SOBRANTES al final de cada línea y limitamos
    # líneas en blanco repetidas, sin tocar los espacios internos de cada línea.
    lineas = [linea.rstrip() for linea in texto.split("\n")]
    texto_limpio = "\n".join(lineas)
    texto_limpio = re.sub(r'\n{3,}', '\n\n', texto_limpio)
    return texto_limpio.strip()

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
    # NOTA (jul-2026): llama-3.3-70b-versatile y llama-3.1-8b-instant fueron
    # descontinuados por Groq (apagado definitivo 16-ago-2026). mixtral-8x7b-32768
    # ya no existe desde 2025. Se reemplazan por los modelos vigentes recomendados
    # por Groq. Revisa console.groq.com/docs/models si esto vuelve a fallar.
    if groq_api_key:
        client_groq = Groq(api_key=groq_api_key)
        modelos_groq = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"]
        # gpt-oss acepta 'low'/'medium'/'high'; qwen3.6 acepta 'none'/'default'.
        # Usamos el mínimo esfuerzo de razonamiento posible en cada caso: esta
        # tarea es extracción de datos, no necesita razonamiento profundo, y así
        # dejamos más presupuesto de tokens para el JSON de salida.
        reasoning_por_modelo = {
            "openai/gpt-oss-120b": "low",
            "openai/gpt-oss-20b": "low",
            "qwen/qwen3.6-27b": "none",
        }

        for modelo in modelos_groq:
            # Hasta 3 intentos por modelo: si es rate limit, esperamos y reintentamos
            # ese MISMO modelo antes de darlo por perdido. Antes el código pasaba
            # directo al siguiente motor (Gemini) ante cualquier 429, agotando la
            # cascada de Groq sin necesidad.
            for intento in range(3):
                try:
                    res = client_groq.chat.completions.create(
                        model=modelo,
                        messages=[
                            {"role": "system", "content": "Return ONLY JSON. Extract absolutely all items, do not truncate."},
                            {"role": "user", "content": prompt_texto}
                        ],
                        temperature=0.0,
                        # openai/gpt-oss-* y qwen3.6 son modelos "razonadores": consumen tokens
                        # pensando antes de escribir la respuesta. Con max_tokens bajo, el
                        # razonamiento se comía todo el presupuesto y el JSON salía cortado
                        # (json_validate_failed). Subimos el límite y bajamos el esfuerzo de
                        # razonamiento al mínimo, ya que esta tarea es extracción, no lógica compleja.
                        # OJO: Groq (tier gratis on_demand) tiene un límite de 8000 tokens POR
                        # MINUTO, y ese límite cuenta el max_tokens que pides + el prompt, no lo
                        # que realmente se usa. Pedir 8000 de max_tokens ya agota el presupuesto
                        # del minuto por sí solo. Lo bajamos a un valor que deje margen para el
                        # prompt (ver TAMANO_LOTE más abajo, también reducido).
                        max_tokens=3500,
                        reasoning_effort=reasoning_por_modelo.get(modelo, "low"),
                        response_format={"type": "json_object"}
                    )
                    return json.loads(res.choices[0].message.content)
                except Exception as e:
                    err_str = str(e).lower()
                    if 'rate limit' in err_str or '429' in err_str:
                        espera = 8 * (intento + 1)
                        error_log.append(f"Groq ({modelo}) rate limit, esperando {espera}s (intento {intento+1}/3)...")
                        time.sleep(espera)
                        continue
                    else:
                        # Error real (modelo inválido, servidor caído, etc.):
                        # no tiene sentido reintentar este modelo, pasamos al siguiente.
                        error_log.append(f"Groq ({modelo}) falló: {e}")
                        break
            else:
                # Se agotaron los 3 intentos por rate limit en este modelo: probamos el siguiente
                continue
    else:
        error_log.append("Groq: Key no configurada.")

    # INTENTO 2: GEMINI
    # NOTA (jul-2026): gemini-1.5-flash ya fue apagado por Google (404 siempre).
    # Se reemplaza por gemini-2.5-flash (GA, estable).
    if gemini_api_key:
        try:
            time.sleep(2)
            client_genai = genai.Client(api_key=gemini_api_key)
            res = client_genai.models.generate_content(
                model='gemini-3.5-flash',
                contents="Return ONLY JSON. Extract absolutely all items.\n\n" + prompt_texto,
                config={"response_mime_type": "application/json", "temperature": 0.0, "max_output_tokens": 8192}
            )
            return json.loads(res.text)
        except Exception as e:
            error_log.append(f"Gemini falló: {e}")
    else:
        error_log.append("Gemini: Key no configurada.")

    # INTENTO 3: OPENROUTER
    # NOTA: "openrouter/free" no es un model id válido de OpenRouter. Usa un id
    # real de un modelo gratuito (verifica cuál está disponible ahora mismo en
    # openrouter.ai/models?max_price=0 porque cambian con frecuencia).
    if openrouter_api_key:
        try:
            headers_or = {"Authorization": f"Bearer {openrouter_api_key}", "Content-Type": "application/json"}
            data_or = {
                "model": "openrouter/free",  # router oficial de OpenRouter que reparte
                # la petición entre los modelos gratis disponibles en cada momento
                "messages": [{"role": "system", "content": "Return ONLY JSON."}, {"role": "user", "content": prompt_texto}],
                "temperature": 0.0,
                "max_tokens": 6000
            }
            response_or = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers_or, json=data_or, timeout=60)

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
                            # layout=True preserva la alineación de columnas del PDF (en vez de
                            # aplanar todo a texto corrido), lo que ayuda mucho a la IA a no
                            # confundir columnas numéricas entre sí.
                            text = optimizar_tokens(page.extract_text(layout=True))
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
                    TAMANO_LOTE = 2  # páginas por llamada a la IA (antes: 1). Baja este número
                    # si tus facturas tienen tablas muy densas y notas que la IA trunca resultados.
                    chunks = [paginas_texto[i:i+TAMANO_LOTE] for i in range(0, len(paginas_texto), TAMANO_LOTE)]
                    progreso_simple = st.progress(0)
                    status_simple = st.empty()

                    for idx, chunk in enumerate(chunks):
                        status_simple.text(f"⏳ Procesando Página {idx+1}/{len(chunks)}...")
                        texto_chunk = "\n--- NUEVA PÁGINA ---\n".join(chunk)
                        
                        prompt_productos = f"""Analiza este segmento de factura y extrae TODOS los productos.
                        REGLAS ESTRICTAS:
                        1. NO TE SALTES NADA: Extrae todas las filas de productos.
                        2. CÓDIGO DIVIDIDO: Si un 'codigo' está dividido en dos líneas, únelo.
                        3. COSTO UNITARIO CORRECTO: El 'costo_unitario' es el PRIMER precio que aparece después de la cantidad. ¡NUNCA MULTIPLIQUES! ¡NUNCA uses el Amount total!
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
                        # Los códigos reales nunca traen espacios. Si la IA pegó por error
                        # una palabra suelta de otra columna (ej. el final de un PO partido
                        # en dos líneas: "DH-S256260529-" / "ECO"), esto se queda solo con
                        # el primer token, que es el código de verdad.
                        df_temp['codigo'] = df_temp['codigo'].str.split().str[0]
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
                    # ==========================================
                    # 1. EXTRACCIÓN DE LA FACTURA (INVOICE)
                    # ==========================================
                    paginas_inv_m = []
                    with pdfplumber.open(pdf_inv_m) as pdf:
                        for page in pdf.pages:
                            # layout=True preserva columnas -> menos confusión entre
                            # cantidad / costo_unitario / amount para la IA.
                            text = optimizar_tokens(page.extract_text(layout=True))
                            text = re.sub(r'-\s*\n\s*', '-', text)
                            if text: paginas_inv_m.append(text + "\n")
                    
                    texto_global_inv_m = paginas_inv_m[0]
                    if len(paginas_inv_m) > 1: texto_global_inv_m += "\n---\n" + paginas_inv_m[-1]

                    json_factura_m = preguntar_ia(f"""Extrae datos globales en JSON.
                    REGLA VITAL: NO adivines fletes. Si no dice explícitamente la palabra "Freight" o "Flete" con un monto al lado, pon "0.00".
                    {{ "proveedor": {{"nombre": "", "rif": ""}}, "numero_documento": "NRO", "ajuste": {{"flete": "0.00", "descuento": "0.00", "recargo": "0.00"}}, "totales": {{"total_neto_final": "0.00"}} }}
                    Texto: {texto_global_inv_m}""")

                    productos_factura_m = []
                    TAMANO_LOTE = 2  # páginas por llamada a la IA (antes: 1)
                    chunks_inv_m = [paginas_inv_m[i:i+TAMANO_LOTE] for i in range(0, len(paginas_inv_m), TAMANO_LOTE)]
                    progreso_inv_m = st.progress(0)
                    status_inv_m = st.empty()

                    for idx, chunk in enumerate(chunks_inv_m):
                        status_inv_m.text(f"⏳ Extrayendo Factura: Página {idx+1}/{len(chunks_inv_m)}...")
                        
                        prompt_prod_inv_m = f"""Analiza esta página de Factura (Invoice) y extrae TODOS los productos.
                        REGLAS DE VIDA O MUERTE (Si fallas, el sistema colapsa):
                        1. ¡PROHIBIDO USAR EL PO!: La última columna (ej. DH-S256260330) es la Orden de Compra (PO). NUNCA la pongas como código.
                        2. ¡COSTO UNITARIO CORRECTO!: El costo unitario es SIEMPRE el PRIMER monto que aparece inmediatamente después de la cantidad. 
                           NUNCA TÓMES EL ÚLTIMO NÚMERO (Ese es el Amount/Total). ¡NUNCA MULTIPLIQUES LA CANTIDAD POR EL PRECIO!
                           Ejemplo: "8 | 450.0000 | 3600.00" -> cantidad = 8, costo_unitario = 450.00
                        3. CÓDIGOS ROTOS: Únelos siempre, PERO SOLO dentro de la MISMA columna de código.
                           "DH-SDT6C425-4P-GB-APV-" y en la otra linea "0280" forman "DH-SDT6C425-4P-GB-APV-0280".
                        4. ¡OJO! La columna del PO (la última) también se parte en dos líneas a veces
                           (ej. "DH-S256260529-" y en la línea de abajo "ECO"). Esa palabra suelta de abajo
                           PERTENECE AL PO, no al código del producto. NUNCA la pegues al campo "codigo".
                           El campo "codigo" JAMÁS debe contener espacios ni la palabra "ECO" u otras palabras sueltas.
                        Schema JSON:
                        {{ "productos": [{{"codigo": "PART NUMBER", "cantidad": 0, "costo_unitario": "0.00"}}] }}
                        Texto:
                        {"\n--- NUEVA PÁGINA ---\n".join(chunk)}"""
                        
                        res_chunk = preguntar_ia(prompt_prod_inv_m)
                        productos_factura_m.extend(res_chunk.get("productos", []))
                        progreso_inv_m.progress((idx + 1) / len(chunks_inv_m))

                    status_inv_m.empty()
                    progreso_inv_m.empty()

                    # ==========================================
                    # 2. EXTRACCIÓN DEL PACKING LIST
                    # ==========================================
                    paginas_pl_m = []
                    with pdfplumber.open(pdf_pl_m) as pdf:
                        for page in pdf.pages:
                            # Aquí es donde más importa layout=True: la confusión
                            # CTNS vs PCS vs Gr.wt viene de perder la alineación de columnas.
                            text = optimizar_tokens(page.extract_text(layout=True))
                            text = re.sub(r'-\s*\n\s*', '-', text)
                            if text: paginas_pl_m.append(text + "\n")

                    productos_packing_m = []
                    TAMANO_LOTE = 2  # páginas por llamada a la IA (antes: 1)
                    chunks_pl_m = [paginas_pl_m[i:i+TAMANO_LOTE] for i in range(0, len(paginas_pl_m), TAMANO_LOTE)]
                    progreso_pl_m = st.progress(0)
                    status_pl_m = st.empty()

                    for idx, chunk in enumerate(chunks_pl_m):
                        status_pl_m.text(f"⏳ Extrayendo Packing List: Página {idx+1}/{len(chunks_pl_m)}...")
                        
                        prompt_prod_pl_m = f"""Analiza esta página del PACKING LIST.
                        REGLAS DE VIDA O MUERTE:
                        1. LÓGICA DE COLUMNAS CORRECTA: Las columnas de números son: [CTNS] [PCS] [Gr.wt] [Net Wt] [CBMS].
                           - 'cantidad': Tienes que extraer los PCS (Pieces). Es decir, el SEGUNDO número entero después del código. ¡NO TOMES LOS CTNS (Bultos)!
                           - 'kgs': Es el TERCER número (Gr.wt).
                           - 'cbms': Es el ÚLTIMO número de la fila.
                           Ejemplo: "25 | 500 | 230 | 205 | 1.925" -> cantidad=500, kgs=230, cbms=1.925
                        2. CÓDIGOS ROTOS: Si un código está partido, únelo (ej. DH-SDT6C...0280).
                        Schema JSON:
                        {{ "productos": [{{"codigo": "PART NUMBER", "cantidad": 0, "kgs": "0.00", "cbms": "0.00"}}] }}
                        Texto:
                        {"\n--- NUEVA PÁGINA ---\n".join(chunk)}"""
                        
                        res_chunk = preguntar_ia(prompt_prod_pl_m)
                        productos_packing_m.extend(res_chunk.get("productos", []))
                        progreso_pl_m.progress((idx + 1) / len(chunks_pl_m))

                    status_pl_m.empty()
                    progreso_pl_m.empty()

                    # ==========================================
                    # 3. PROCESAMIENTO Y LIMPIEZA CON PANDAS
                    # ==========================================
                    df_factura_m = pd.DataFrame(productos_factura_m)
                    df_packing_m = pd.DataFrame(productos_packing_m)

                    if df_factura_m.empty or df_packing_m.empty:
                        st.error("Error al consolidar. Verifica tus PDFs.")
                    else:
                        # --- Limpieza de Factura ---
                        df_factura_m['codigo'] = df_factura_m['codigo'].astype(str).str.strip().str.upper()
                        # Los códigos reales nunca traen espacios. Si la IA pegó por error
                        # una palabra suelta de otra columna (ej. el final de un PO partido
                        # en dos líneas: "DH-S256260529-" / "ECO"), esto se queda solo con
                        # el primer token, que es el código de verdad.
                        df_factura_m['codigo'] = df_factura_m['codigo'].str.split().str[0]
                        df_factura_m = df_factura_m[~df_factura_m['codigo'].str.startswith('DH-S256')]
                        
                        df_factura_m['codigo_clean'] = df_factura_m['codigo'].apply(normalizar_codigo_cruce)
                        df_factura_m = df_factura_m[df_factura_m['codigo'].str.len() > 2] 
                        df_factura_m['cantidad'] = pd.to_numeric(df_factura_m['cantidad'], errors='coerce').fillna(0)
                        df_factura_m = df_factura_m[df_factura_m['cantidad'] > 0].copy()
                        df_factura_m['costo_unitario'] = df_factura_m['costo_unitario'].apply(parse_monto_seguro)

                        ajustes_m = json_factura_m.get("ajuste", {})
                        flete_m = parse_monto_seguro(ajustes_m.get("flete", "0.0"))
                        descuento_m = parse_monto_seguro(ajustes_m.get("descuento", "0.0"))
                        recargo_m = parse_monto_seguro(ajustes_m.get("recargo", "0.0"))
                        total_neto_real_m = parse_monto_seguro(json_factura_m.get("totales", {}).get("total_neto_final", "0.0"))

                        suma_ajustes = flete_m + descuento_m + recargo_m
                        if suma_ajustes > 0 and suma_ajustes < (total_neto_real_m * 0.20):
                            suma_bruta_m = (df_factura_m['cantidad'] * df_factura_m['costo_unitario']).sum()
                            factor_m = total_neto_real_m / suma_bruta_m if suma_bruta_m > 0 else 1.0
                        else:
                            factor_m = 1.0
                        
                        df_factura_m['costo_final'] = (df_factura_m['costo_unitario'] * factor_m).round(3)

                        df_costo_final_out = df_factura_m.groupby('codigo_clean', as_index=False, sort=False).agg({
                            'codigo': 'first',
                            'costo_final': 'mean'
                        })[['codigo', 'costo_final']].rename(columns={'costo_final': 'costo'})

                        # --- Limpieza de Packing List ---
                        df_packing_m['codigo'] = df_packing_m['codigo'].astype(str).str.strip().str.upper()
                        df_packing_m['codigo'] = df_packing_m['codigo'].str.split().str[0]
                        df_packing_m = df_packing_m[~df_packing_m['codigo'].str.startswith('DH-S256')]
                        
                        df_packing_m['codigo_clean'] = df_packing_m['codigo'].apply(normalizar_codigo_cruce)
                        
                        df_packing_m = df_packing_m[df_packing_m['codigo'].str.len() > 2]
                        df_packing_m['cantidad'] = pd.to_numeric(df_packing_m.get('cantidad', 0), errors='coerce').fillna(0)
                        df_packing_m['kgs'] = df_packing_m.get('kgs', 0).apply(parse_monto_seguro)
                        df_packing_m['cbms'] = df_packing_m.get('cbms', 0).apply(parse_monto_seguro)

                        df_packing_grouped_m = df_packing_m.groupby('codigo_clean', as_index=False, sort=False).agg({
                            'codigo': 'first',
                            'cantidad': 'sum',
                            'kgs': 'sum',
                            'cbms': 'sum'
                        })
                        
                        df_packing_grouped_m = df_packing_grouped_m[df_packing_grouped_m['cantidad'] > 0]

                        # --- Construcción Final ---
                        rif_proveedor = str(json_factura_m.get("proveedor", {}).get("rif", "S_R")).strip()
                        razon_social_proveedor = str(json_factura_m.get("proveedor", {}).get("nombre", "S_R")).strip()
                        
                        df_pl_final_out = df_packing_grouped_m[['codigo', 'cantidad', 'cbms', 'kgs']].copy()
                        df_pl_final_out['descripcion'] = df_pl_final_out['codigo'] 
                        
                        df_pl_final_out.insert(0, 'razonSocial', razon_social_proveedor)
                        df_pl_final_out.insert(0, 'rif', rif_proveedor)
                        df_pl_final_out = df_pl_final_out[['rif', 'razonSocial', 'codigo', 'descripcion', 'cantidad', 'cbms', 'kgs']]

                        st.session_state.tab2_df_costo = df_costo_final_out
                        st.session_state.tab2_df_pl = df_pl_final_out
                        st.session_state.tab2_num_doc = str(json_factura_m.get("numero_documento", "S_N")).strip()

                except Exception as e:
                    st.error(f"Error crítico en Extracción Masiva: {e}")

    # ==========================================
    # 4. RENDERIZADO Y DESCARGA DE ARCHIVOS
    # ==========================================
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