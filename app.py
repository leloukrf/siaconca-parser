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

st.title("📄 Siaconca: Extractor v4.1 (Detección por Contexto)")

archivo_pdf = st.file_uploader("Sube el PDF del proveedor", type=["pdf"])

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

                # Prompt mejorado: Ahora la IA es la responsable de buscar el número de factura/pedido
                prompt = f"""Analiza este texto de una factura o presupuesto y extrae los datos en JSON.
                IMPORTANTE: Busca el número de identificación del documento (Invoice Number, Presupuesto Nro, Control, etc).
                
                Schema JSON esperado:
                {{
                  "proveedor": {{"nombre": "", "rif": ""}},
                  "numero_documento": "AQUÍ EL NÚMERO DE FACTURA O PEDIDO",
                  "ajuste": {{"flete": 0.00, "descuento": 0.00}},
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
                
                # --- LIMPIEZA DE NÚMERO DE DOCUMENTO ---
                num_doc = str(datos.get("numero_documento", "S_N")).strip()
                # Si la IA trae texto largo, intentamos quedarnos solo con el código
                if len(num_doc) > 20: 
                    temp_match = re.search(r'([A-Z0-9-]{4,})', num_doc)
                    if temp_match: num_doc = temp_match.group(1)

                # --- LÓGICA DE PRORRATEO ---
                items = datos.get("productos", [])
                total_neto_real = float(datos.get("totales", {}).get("total_neto_final", 0))
                if items:
                    df_temp = pd.DataFrame(items)
                    suma_bruta = (df_temp['cantidad'] * df_temp['costo_unitario']).sum()
                    # Factor de ajuste (solo si la diferencia es mayor a 10 centavos)
                    factor = total_neto_real / suma_bruta if (suma_bruta > 0 and abs(suma_bruta - total_neto_real) > 0.1) else 1.0
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
        
        # --- UI DE MUESTRA ---
        st.info(f"📄 **Documento:** {st.session_state.num_doc} | **Proveedor:** {datos['proveedor']['nombre']}")
        
        col1, col2, col3 = st.columns(3)
        with col1: st.metric("Flete", f"$ {datos['ajuste']['flete']:,.2f}")
        with col2: st.metric("Descuento", f"$ {datos['ajuste']['descuento']:,.2f}")
        with col3: st.metric("TOTAL NETO PDF", f"$ {datos['totales']['total_neto_final']:,.2f}")

        df_display = pd.DataFrame(datos['productos'])[["codigo", "descripcion", "cantidad", "costo_unitario", "costo_final"]]
        df_display.columns = ["CODIGO", "DESCRIPCION", "CANTIDAD", "COSTO LISTA", "COSTO FINAL"]
        
        st.dataframe(
            df_display.style.format({"COSTO LISTA": "{:,.3f}", "COSTO FINAL": "{:,.3f}", "CANTIDAD": "{:,.0f}"})
            .set_table_styles([{'selector': 'th', 'props': [('background-color', '#D9EAD3'), ('color', 'black'), ('font-weight', 'bold')]}])
            , use_container_width=True
        )

        # --- EXCEL DISEÑO PROFESIONAL ---
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_display.to_excel(writer, sheet_name='Carga', index=False, startrow=8)
            wb, ws = writer.book, writer.sheets['Carga']
            
            # Formatos
            fmt_verde = wb.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1})
            fmt_normal = wb.add_format({'border': 1})
            fmt_num = wb.add_format({'border': 1, 'num_format': '#,##0.000'})
            fmt_header = wb.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1, 'align': 'center'})

            # Encabezados de información general (Separados y claros)
            ws.write(0, 0, "DATOS DEL PROVEEDOR", fmt_verde); ws.write(0, 1, "", fmt_verde)
            ws.write(1, 0, "Nombre / Razón Social:", fmt_verde); ws.write(1, 1, datos['proveedor']['nombre'], fmt_normal)
            ws.write(2, 0, "RIF / Identificación:", fmt_verde); ws.write(2, 1, datos['proveedor']['rif'], fmt_normal)
            
            ws.write(4, 0, "DETALLES DEL DOCUMENTO", fmt_verde); ws.write(4, 1, "", fmt_verde)
            ws.write(5, 0, "Número de Factura/Pedido:", fmt_verde); ws.write(5, 1, st.session_state.num_doc, fmt_normal)
            ws.write(6, 0, "Flete Extra:", fmt_verde); ws.write(6, 1, datos['ajuste']['flete'], fmt_num)
            ws.write(6, 2, "Descuento:", fmt_verde); ws.write(6, 3, datos['ajuste']['descuento'], fmt_num)

            # Ajuste de columnas
            ws.set_column('A:A', 30); ws.set_column('B:B', 50); ws.set_column('C:E', 15)
            
            # Cabecera de la tabla
            for col_num, value in enumerate(df_display.columns.values):
                ws.write(8, col_num, value, fmt_header)
                
            # Datos de la tabla
            for r_idx, row in enumerate(df_display.values):
                ws.write(9 + r_idx, 0, row[0], fmt_normal)
                ws.write(9 + r_idx, 1, row[1], fmt_normal)
                ws.write(9 + r_idx, 2, row[2], fmt_normal)
                ws.write(9 + r_idx, 3, row[3], fmt_num)
                ws.write(9 + r_idx, 4, row[4], fmt_num)

        nombre_archivo = f"PEDIDO_{st.session_state.num_doc}.xlsx"
        st.download_button(f"📥 Descargar {nombre_archivo}", buffer.getvalue(), nombre_archivo)