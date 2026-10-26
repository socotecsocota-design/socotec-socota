import io
import os
import re
import unicodedata
from datetime import datetime

import pandas as pd
import streamlit as st
from reportlab.pdfgen import canvas


STORE_NAME = "SOCOTEC"
STORE_RUC = "10717601047"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INV_CSV = os.path.join(BASE_DIR, "stock_pro.csv")
FIN_CSV = os.path.join(BASE_DIR, "finanzas_pro.csv")
CLI_CSV = os.path.join(BASE_DIR, "clientes_pro.csv")
TEC_CSV = os.path.join(BASE_DIR, "servicio_tecnico.csv")

INV_COLS = ["SKU", "Producto", "Cantidad", "Precio"]
FIN_COLS = ["Fecha", "Concepto", "Tipo", "Monto", "Cliente", "Referencia", "Metodo_Pago"]
CLI_COLS = ["DNI_RUC", "Nombre", "Celular"]
TEC_COLS = [
    "Orden",
    "Fecha_Ingreso",
    "Cliente",
    "Celular",
    "Equipo",
    "Marca_Modelo",
    "Serie_IMEI",
    "Problema",
    "Accesorios",
    "Tecnico",
    "Prioridad",
    "Estado",
    "Diagnostico",
    "Solucion",
    "Anticipo",
    "Costo_Estimado",
    "Costo_Final",
    "Fecha_Entrega",
    "Observaciones",
]

ESTADOS_SERVICIO = [
    "Recibido",
    "En diagnostico",
    "Esperando repuesto",
    "En reparacion",
    "Listo para entregar",
    "Entregado",
    "No reparado",
]
PRIORIDADES = ["Normal", "Alta", "Urgente"]

# Palabras equivalentes frecuentes en accesorios. Puedes ampliar esta lista cuando quieras.
SINONIMOS_BUSQUEDA = (
    {"cargador", "cargadores", "charger", "carga"},
    {"audifono", "audifonos", "headset", "earphone", "audio"},
    {"inalambrico", "wireless", "wirless", "bluetooth", "bt"},
    {"mica", "micas", "protector", "hydrogel", "film"},
    {"mouse", "raton", "ratones"},
    {"tipo", "type"},
)


st.set_page_config(page_title=f"{STORE_NAME} PRO", layout="wide", page_icon="A")

st.markdown(
    """
    <style>
        .block-container {max-width: 1180px; padding-top: 1.5rem; padding-bottom: 2rem;}
        h1 {color: #0f766e; font-weight: 800; letter-spacing: -0.02em;}
        h2, h3 {color: #134e4a;}
        div[data-testid="stMetric"] {background: #f0fdfa; border: 1px solid #ccfbf1; border-radius: 12px; padding: 0.8rem;}
        div.stButton > button {border-radius: 9px; font-weight: 600; min-height: 2.7rem;}
        div[data-testid="stTabs"] button {font-weight: 650; font-size: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def file_version(file_path):
    """Devuelve una versión para que la caché se actualice si cambia el CSV."""
    try:
        return os.path.getmtime(file_path)
    except OSError:
        return None


@st.cache_data
def load_data(file_path, columns, version):
    # Un archivo inexistente, de 0 bytes o sin cabecera se trata como vacío.
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        df = pd.DataFrame(columns=columns)
    else:
        try:
            df = pd.read_csv(file_path)
        except pd.errors.EmptyDataError:
            df = pd.DataFrame(columns=columns)

    legacy_names = {
        "Categoría": "Categoria",
        "CategorÃ­a": "Categoria",
    }
    df = df.rename(columns=legacy_names)

    for col in columns:
        if col not in df.columns:
            df[col] = None

    df = df[columns]

    # Evita que columnas vacías de texto sean inferidas como float64 por Pandas.
    text_cols = [col for col in columns if col not in [
        "Cantidad", "Precio", "Monto", "Anticipo", "Costo_Estimado",
        "Costo_Final", "Fecha", "Fecha_Ingreso", "Fecha_Entrega",
    ]]
    for col in text_cols:
        df[col] = df[col].fillna("").astype(str)

    for col in ["Fecha", "Fecha_Ingreso", "Fecha_Entrega"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    for col in ["Cantidad"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    for col in ["Precio", "Monto", "Anticipo", "Costo_Estimado", "Costo_Final"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df


def save_data(df, file_path):
    df.to_csv(file_path, index=False)
    st.cache_data.clear()


def sonido_confirmacion():
    """Reproduce un aviso breve al completar una operación correctamente."""
    try:
        import winsound

        winsound.PlaySound(
            r"C:\Windows\Media\Windows Proximity Notification.wav",
            winsound.SND_FILENAME | winsound.SND_ASYNC,
        )
    except (ImportError, RuntimeError):
        pass


def money(value):
    return f"S/ {safe_float(value):.2f}"


def safe_float(value, default=0.0):
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalizar_busqueda(value):
    """Permite buscar sin distinguir mayúsculas, tildes ni espacios extra."""
    texto = unicodedata.normalize("NFKD", str(value))
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    texto = "".join(char if char.isalnum() else " " for char in texto)
    return " ".join(texto.casefold().split())


def alternativas_busqueda(palabra):
    for grupo in SINONIMOS_BUSQUEDA:
        if palabra in grupo:
            return grupo
    return {palabra}


def coincide_busqueda_producto(fila, palabras):
    texto = normalizar_busqueda(f"{fila['Producto']} {fila['SKU']}")
    return all(
        any(alternativa in texto for alternativa in alternativas_busqueda(palabra))
        for palabra in palabras
    )


def validate_inventory(df):
    """Valida los datos que pueden afectar el descuento de stock."""
    sku = df["SKU"].fillna("").astype(str).str.strip()
    product = df["Producto"].fillna("").astype(str).str.strip()
    quantity = pd.to_numeric(df["Cantidad"], errors="coerce")
    price = pd.to_numeric(df["Precio"], errors="coerce")

    if (sku == "").any() or (product == "").any():
        return "Cada producto debe tener SKU y nombre."
    if sku.duplicated().any():
        return "No se permiten SKU repetidos."
    if quantity.isna().any() or (quantity < 0).any() or (quantity % 1 != 0).any():
        return "La cantidad debe ser un número entero igual o mayor que cero."
    if price.isna().any() or (price < 0).any():
        return "El precio debe ser un número igual o mayor que cero."
    return None


def format_date(value):
    date_value = pd.to_datetime(value, errors="coerce")
    if pd.isna(date_value):
        return ""
    return date_value.strftime("%d/%m/%Y %H:%M")


def next_number(df, prefix, column):
    if df.empty or column not in df.columns:
        return f"{prefix}-000001"

    # Solo toma números de la misma serie (por ejemplo B001-000123).
    # Así no confunde referencias de servicios como SRV-202610161234567890.
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    numbers = []
    for reference in df[column].dropna().astype(str):
        match = pattern.match(reference.strip())
        if match:
            numbers.append(int(match.group(1)))

    current = max(numbers) if numbers else 0
    return f"{prefix}-{current + 1:06d}"


def add_finance_record(
    df_fin, concept, amount, client, tipo="Ingreso", reference="", metodo_pago=""
):
    new_row = pd.DataFrame([{
        "Fecha": datetime.now(),
        "Concepto": concept,
        "Tipo": tipo,
        "Monto": safe_float(amount),
        "Cliente": client,
        "Referencia": reference,
        "Metodo_Pago": metodo_pago,
    }])
    return pd.concat([df_fin, new_row], ignore_index=True)


def generar_pdf_venta(lista_items, monto_total, cliente, numero_boleta, metodo_pago):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 600))

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(150, 570, STORE_NAME)
    c.setFont("Helvetica", 9)
    c.drawCentredString(150, 555, f"RUC: {STORE_RUC}")
    c.line(20, 540, 280, 540)

    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(150, 525, f"NOTA DE VENTA B001-{numero_boleta:06d}")

    c.setFont("Helvetica", 8)
    c.drawString(20, 500, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.drawString(20, 485, f"Cliente: {cliente}")
    c.drawString(20, 470, f"Pago: {metodo_pago}")

    y = 445
    for item in lista_items:
        if y < 70:
            c.showPage()
            y = 560
            c.setFont("Helvetica", 8)

        c.drawString(20, y, f"{item['cant']} x {item['prod'][:24]}")
        c.drawRightString(270, y, money(item["subtotal"]))
        y -= 15

    c.line(20, y, 280, y)
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(270, y - 20, f"TOTAL: {money(monto_total)}")

    c.save()
    return buf.getvalue()


def generar_pdf_servicio(orden):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(420, 620))

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(210, 585, STORE_NAME)
    c.setFont("Helvetica", 9)
    c.drawCentredString(210, 570, f"RUC: {STORE_RUC}")
    c.line(30, 555, 390, 555)

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(210, 535, f"ORDEN DE SERVICIO {orden['Orden']}")

    c.setFont("Helvetica", 9)
    y = 510
    rows = [
        ("Fecha ingreso", orden.get("Fecha_Ingreso", "")),
        ("Cliente", orden.get("Cliente", "")),
        ("Celular", orden.get("Celular", "")),
        ("Equipo", orden.get("Equipo", "")),
        ("Marca/Modelo", orden.get("Marca_Modelo", "")),
        ("Serie/IMEI", orden.get("Serie_IMEI", "")),
        ("Problema", orden.get("Problema", "")),
        ("Accesorios", orden.get("Accesorios", "")),
        ("Estado", orden.get("Estado", "")),
        ("Tecnico", orden.get("Tecnico", "")),
        ("Anticipo", money(orden.get("Anticipo", 0))),
        ("Costo estimado", money(orden.get("Costo_Estimado", 0))),
    ]

    for label, value in rows:
        text = str(value)
        if "Fecha" in label:
            text = format_date(value)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(30, y, f"{label}:")
        c.setFont("Helvetica", 8)
        c.drawString(120, y, text[:55])
        y -= 18

    c.line(30, y - 5, 390, y - 5)
    c.setFont("Helvetica", 7)
    c.drawString(30, y - 25, "El cliente declara entregar el equipo en las condiciones descritas.")
    c.drawString(30, y - 38, "La tienda no se responsabiliza por informacion sin respaldo previo del cliente.")
    c.drawString(30, y - 75, "Firma cliente: ______________________")
    c.drawString(230, y - 75, "Recibido por: ______________________")

    c.save()
    return buf.getvalue()


df_inv = load_data(INV_CSV, INV_COLS, file_version(INV_CSV))
df_fin = load_data(FIN_CSV, FIN_COLS, file_version(FIN_CSV))
df_cli = load_data(CLI_CSV, CLI_COLS, file_version(CLI_CSV))
df_tec = load_data(TEC_CSV, TEC_COLS, file_version(TEC_CSV))

if "carrito" not in st.session_state:
    st.session_state.carrito = []

st.title(f"{STORE_NAME} PRO")
st.caption("Sistema de ventas y control de almacén")

tabs = st.tabs([
    "Ventas",
    "Almacen",
    "Historial",
])


with tabs[0]:
    st.subheader("🛒 Punto de venta")

    productos_df = df_inv[df_inv["Producto"].notna()].copy()
    productos_df["Producto"] = productos_df["Producto"].astype(str).str.strip()
    productos_df["SKU"] = (
        productos_df["SKU"].fillna("").astype(str).str.strip().replace("nan", "")
    )
    productos_df = productos_df[
        (productos_df["Producto"] != "")
        & (productos_df["Cantidad"] > 0)
    ]
    # Los productos sin código también se pueden vender usando el buscador manual.
    productos_df["ClaveCarrito"] = [
        sku if sku else f"sin-codigo-{indice}"
        for indice, sku in zip(productos_df.index, productos_df["SKU"])
    ]
    productos_df["FilaId"] = productos_df.index
    productos_por_clave = productos_df.set_index("ClaveCarrito", drop=False).to_dict("index")
    productos_por_sku = {
        fila["SKU"]: fila for fila in productos_por_clave.values() if fila["SKU"]
    }

    def agregar_producto(clave):
        fila = productos_por_clave[clave]
        fila_id = fila["FilaId"]
        cantidad_en_carrito = sum(
            item["cant"]
            for item in st.session_state.carrito
            if item.get("fila_id") == fila_id
        )
        stock = int(fila["Cantidad"])
        if cantidad_en_carrito >= stock:
            st.session_state.mensaje_escaneo = (
                "warning", f"Stock insuficiente para {fila['Producto']}."
            )
            return

        for item in st.session_state.carrito:
            if item.get("fila_id") == fila_id:
                item["cant"] += 1
                item["subtotal"] = item["cant"] * item["precio"]
                break
        else:
            st.session_state.carrito.append({
                "sku": fila["SKU"] or "Sin código",
                "prod": fila["Producto"],
                "cant": 1,
                "precio": safe_float(fila["Precio"]),
                "subtotal": safe_float(fila["Precio"]),
                "fila_id": fila_id,
            })
        st.session_state.mensaje_escaneo = ("success", f"Agregado: {fila['Producto']}")

    def agregar_codigo_escaneado():
        codigo = st.session_state.get("codigo_barras", "").strip()
        st.session_state.codigo_barras = ""

        if not codigo:
            return
        if codigo not in productos_por_sku:
            st.session_state.mensaje_escaneo = ("error", f"No se encontró el código: {codigo}")
            return

        agregar_producto(productos_por_sku[codigo]["ClaveCarrito"])

    columna_escaneo, columna_busqueda = st.columns(2)
    with columna_escaneo:
        st.markdown("#### Escáner")
        st.text_input(
            "Escanea el código de barras",
            key="codigo_barras",
            placeholder="Escanea aquí y presiona Enter",
            on_change=agregar_codigo_escaneado,
        )
        st.caption("Cada lectura agrega una unidad al carrito.")

    with columna_busqueda:
        st.markdown("#### Búsqueda manual")
        busqueda_producto = st.text_input(
            "Buscar producto",
            placeholder="Ejemplo: cable",
            key="busqueda_producto_venta",
        )
    palabras_busqueda = normalizar_busqueda(busqueda_producto).split()
    claves_encontradas = [
        clave
        for clave, fila in productos_por_clave.items()
        if coincide_busqueda_producto(fila, palabras_busqueda)
    ]
    with columna_busqueda:
        producto_manual = st.selectbox(
            "Resultados de la búsqueda",
            [""] + claves_encontradas,
            format_func=lambda clave: (
                "Selecciona un producto"
                if not clave
                else f"{productos_por_clave[clave]['Producto']} | "
                f"Código: {productos_por_clave[clave]['SKU'] or 'Sin código'} | "
                f"Precio: {money(productos_por_clave[clave]['Precio'])} | "
                f"Stock: {int(productos_por_clave[clave]['Cantidad'])}"
            ),
        )
        if busqueda_producto.strip() and not claves_encontradas:
            st.info("No se encontraron productos con esa búsqueda.")
        elif producto_manual:
            fila_elegida = productos_por_clave[producto_manual]
            st.success(
                f"{fila_elegida['Producto']} — Precio: {money(fila_elegida['Precio'])}"
            )
        if st.button("Agregar producto seleccionado", use_container_width=True):
            if not producto_manual:
                st.warning("Busca y selecciona un producto primero.")
            else:
                agregar_producto(producto_manual)

    if "mensaje_escaneo" in st.session_state:
        tipo_mensaje, texto_mensaje = st.session_state.pop("mensaje_escaneo")
        getattr(st, tipo_mensaje)(texto_mensaje)

    if st.session_state.carrito:
        df_car = pd.DataFrame(st.session_state.carrito)
        st.dataframe(df_car, use_container_width=True, hide_index=True)

        total = float(df_car["subtotal"].sum())
        st.success(f"Total a cobrar: {money(total)}")

        datos_venta_1, datos_venta_2 = st.columns(2)
        clientes = ["General"] + df_cli["Nombre"].dropna().astype(str).tolist()
        cliente = datos_venta_1.selectbox("Cliente", clientes)
        metodo_pago = datos_venta_2.selectbox(
            "Método de pago",
            ["Efectivo", "Yape", "Plin", "Transferencia", "Tarjeta", "Otro"],
        )

        c1, c2 = st.columns(2)

        if c1.button("Procesar venta", use_container_width=True):
            numero_boleta = next_number(df_fin, "B001", "Referencia").split("-")[-1]
            referencia_venta = f"B001-{numero_boleta}"

            df_fin_actualizado = add_finance_record(
                df_fin,
                "Venta: " + " | ".join([f"{i['cant']} x {i['prod']}" for i in st.session_state.carrito]),
                total,
                cliente,
                reference=referencia_venta,
                metodo_pago=metodo_pago,
            )

            df_inv_actualizado = df_inv.copy()
            for item in st.session_state.carrito:
                if item.get("fila_id") in df_inv_actualizado.index:
                    df_inv_actualizado.loc[item["fila_id"], "Cantidad"] -= item["cant"]
                else:
                    # Respaldo para carritos antiguos o si la tabla fue reordenada.
                    mask = (
                        df_inv_actualizado["SKU"].fillna("").astype(str).str.strip()
                        == str(item["sku"]).strip()
                    )
                    df_inv_actualizado.loc[mask, "Cantidad"] -= item["cant"]

            save_data(df_fin_actualizado, FIN_CSV)
            save_data(df_inv_actualizado, INV_CSV)
            sonido_confirmacion()

            st.session_state.ultimo_pdf = generar_pdf_venta(
                st.session_state.carrito,
                total,
                cliente,
                int(numero_boleta),
                metodo_pago,
            )

            st.session_state.carrito = []
            st.success("Venta completada.")
            st.rerun()

        if c2.button("Vaciar carrito", use_container_width=True):
            st.session_state.carrito = []
            st.rerun()

    with st.expander("💵 Registrar cobro de servicio", expanded=False):
        st.caption("Úsalo para servicios sin productos de almacén. El ingreso aparecerá en Historial.")
        with st.form("cobro_servicio_rapido", clear_on_submit=True):
            descripcion_servicio = st.text_input(
                "Descripción del servicio", placeholder="Ejemplo: Configuración de celular"
            )
            col_servicio_1, col_servicio_2 = st.columns(2)
            monto_servicio = col_servicio_1.number_input(
                "Monto cobrado", min_value=0.0, value=None, step=1.0,
                placeholder="Ejemplo: 20",
            )
            pago_servicio = col_servicio_2.selectbox(
                "Método de pago", ["Efectivo", "Yape", "Plin", "Transferencia", "Tarjeta", "Otro"],
                key="pago_cobro_servicio",
            )
            guardar_cobro = st.form_submit_button("Registrar ingreso de servicio")

    if guardar_cobro:
        if not descripcion_servicio.strip():
            st.error("Escribe la descripción del servicio.")
        elif monto_servicio is None or monto_servicio <= 0:
            st.error("El monto cobrado debe ser mayor que cero.")
        else:
            referencia_servicio = f"SRV-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            df_fin_actualizado = add_finance_record(
                df_fin,
                f"Servicio: {descripcion_servicio.strip()}",
                monto_servicio,
                "General",
                reference=referencia_servicio,
                metodo_pago=pago_servicio,
            )
            save_data(df_fin_actualizado, FIN_CSV)
            sonido_confirmacion()
            st.success("Ingreso de servicio registrado.")
            st.rerun()

    if "ultimo_pdf" in st.session_state:
        st.download_button(
            "Descargar ticket",
            st.session_state.ultimo_pdf,
            "ticket.pdf",
            mime="application/pdf",
        )


if False:  # Sección de Servicio técnico desactivada.
    st.subheader("Servicio tecnico")

    pendientes = df_tec[~df_tec["Estado"].isin(["Entregado", "No reparado"])]
    listos = df_tec[df_tec["Estado"] == "Listo para entregar"]
    entregados = df_tec[df_tec["Estado"] == "Entregado"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Ordenes abiertas", len(pendientes))
    m2.metric("Listos para entregar", len(listos))
    m3.metric("Entregados", len(entregados))
    m4.metric("Ingresos servicio", money(df_tec["Costo_Final"].sum()))

    service_tab1, service_tab2, service_tab3 = st.tabs([
        "Nueva orden",
        "Seguimiento",
        "Editar tabla",
    ])

    with service_tab1:
        st.markdown("#### Registrar equipo")
        clientes_existentes = ["Cliente nuevo"] + df_cli["Nombre"].dropna().astype(str).tolist()

        c1, c2, c3 = st.columns(3)
        cliente_sel = c1.selectbox("Cliente", clientes_existentes)
        cliente_nombre = ""
        celular = ""

        if cliente_sel != "Cliente nuevo":
            cliente_nombre = cliente_sel
            fila_cli = df_cli[df_cli["Nombre"].astype(str) == cliente_sel]
            if not fila_cli.empty:
                celular = str(fila_cli.iloc[0].get("Celular", "") or "")

        cliente_nombre = c2.text_input("Nombre del cliente", value=cliente_nombre)
        celular = c3.text_input("Celular", value=celular)

        c4, c5, c6 = st.columns(3)
        equipo = c4.text_input("Equipo", placeholder="Laptop, celular, impresora")
        marca_modelo = c5.text_input("Marca / modelo")
        serie_imei = c6.text_input("Serie / IMEI")

        problema = st.text_area("Problema reportado por el cliente")

        c7, c8, c9 = st.columns(3)
        accesorios = c7.text_input("Accesorios recibidos", placeholder="Cargador, funda, cable")
        tecnico = c8.text_input("Tecnico responsable")
        prioridad = c9.selectbox("Prioridad", PRIORIDADES)

        c10, c11 = st.columns(2)
        anticipo = c10.number_input("Anticipo", min_value=0.0, value=0.0, step=5.0)
        costo_estimado = c11.number_input("Costo estimado", min_value=0.0, value=0.0, step=5.0)

        observaciones = st.text_area("Observaciones internas")

        if st.button("Guardar orden de servicio", use_container_width=True):
            if not cliente_nombre.strip():
                st.error("Ingresa el nombre del cliente.")
            elif not equipo.strip():
                st.error("Ingresa el equipo recibido.")
            elif not problema.strip():
                st.error("Describe el problema del equipo.")
            else:
                orden_id = next_number(df_tec, "ST", "Orden")
                nueva_orden = pd.DataFrame([{
                    "Orden": orden_id,
                    "Fecha_Ingreso": datetime.now(),
                    "Cliente": cliente_nombre.strip(),
                    "Celular": celular.strip(),
                    "Equipo": equipo.strip(),
                    "Marca_Modelo": marca_modelo.strip(),
                    "Serie_IMEI": serie_imei.strip(),
                    "Problema": problema.strip(),
                    "Accesorios": accesorios.strip(),
                    "Tecnico": tecnico.strip(),
                    "Prioridad": prioridad,
                    "Estado": "Recibido",
                    "Diagnostico": "",
                    "Solucion": "",
                    "Anticipo": anticipo,
                    "Costo_Estimado": costo_estimado,
                    "Costo_Final": 0.0,
                    "Fecha_Entrega": pd.NaT,
                    "Observaciones": observaciones.strip(),
                }])
                df_tec_actualizado = pd.concat([df_tec, nueva_orden], ignore_index=True)
                save_data(df_tec_actualizado, TEC_CSV)

                if anticipo > 0:
                    df_fin_actualizado = add_finance_record(
                        df_fin,
                        f"Anticipo servicio {orden_id}",
                        anticipo,
                        cliente_nombre.strip(),
                        reference=f"{orden_id}-ANTICIPO",
                    )
                    save_data(df_fin_actualizado, FIN_CSV)

                st.session_state.ultima_orden_pdf = generar_pdf_servicio(nueva_orden.iloc[0].to_dict())
                st.success(f"Orden {orden_id} registrada.")
                st.rerun()

        if "ultima_orden_pdf" in st.session_state:
            st.download_button(
                "Descargar constancia de recepcion",
                st.session_state.ultima_orden_pdf,
                "orden_servicio.pdf",
                mime="application/pdf",
            )

    with service_tab2:
        st.markdown("#### Seguimiento de ordenes")

        if df_tec.empty:
            st.info("Aun no hay ordenes de servicio tecnico.")
        else:
            f1, f2, f3 = st.columns([2, 2, 3])
            estado_filtro = f1.selectbox("Estado", ["Todos"] + ESTADOS_SERVICIO)
            prioridad_filtro = f2.selectbox("Prioridad", ["Todas"] + PRIORIDADES)
            buscar = f3.text_input("Buscar", placeholder="Orden, cliente, equipo, IMEI")

            vista = df_tec.copy()
            if estado_filtro != "Todos":
                vista = vista[vista["Estado"] == estado_filtro]
            if prioridad_filtro != "Todas":
                vista = vista[vista["Prioridad"] == prioridad_filtro]
            if buscar.strip():
                texto = buscar.strip().lower()
                vista = vista[
                    vista.astype(str)
                    .apply(lambda row: row.str.lower().str.contains(texto, regex=False).any(), axis=1)
                ]

            st.dataframe(
                vista.sort_values("Fecha_Ingreso", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

            ordenes = df_tec["Orden"].dropna().astype(str).tolist()
            orden_sel = st.selectbox("Selecciona una orden para actualizar", ordenes)
            orden_idx = df_tec.index[df_tec["Orden"].astype(str) == orden_sel][0]
            orden_actual = df_tec.loc[orden_idx]

            st.markdown(f"#### Actualizar {orden_sel}")
            a1, a2, a3 = st.columns(3)
            estado_actual = orden_actual.get("Estado", "Recibido")
            estado_index = ESTADOS_SERVICIO.index(estado_actual) if estado_actual in ESTADOS_SERVICIO else 0
            nuevo_estado = a1.selectbox("Nuevo estado", ESTADOS_SERVICIO, index=estado_index)
            costo_final = a2.number_input(
                "Costo final",
                min_value=0.0,
                value=safe_float(orden_actual.get("Costo_Final", 0)),
                step=5.0,
            )
            tecnico_actual = str(orden_actual.get("Tecnico", "") or "")
            tecnico_upd = a3.text_input("Tecnico", value=tecnico_actual)

            diagnostico = st.text_area("Diagnostico", value=str(orden_actual.get("Diagnostico", "") or ""))
            solucion = st.text_area("Solucion / trabajo realizado", value=str(orden_actual.get("Solucion", "") or ""))
            observaciones_upd = st.text_area(
                "Observaciones",
                value=str(orden_actual.get("Observaciones", "") or ""),
            )

            cobrar_saldo = st.checkbox(
                "Registrar ingreso por saldo al entregar",
                value=nuevo_estado == "Entregado" and safe_float(orden_actual.get("Costo_Final", 0)) == 0,
            )

            if st.button("Actualizar orden", use_container_width=True):
                df_tec_actualizado = df_tec.copy()
                estado_anterior = str(df_tec_actualizado.loc[orden_idx, "Estado"])
                anticipo_actual = safe_float(df_tec_actualizado.loc[orden_idx, "Anticipo"])

                df_tec_actualizado.loc[orden_idx, "Estado"] = nuevo_estado
                df_tec_actualizado.loc[orden_idx, "Tecnico"] = tecnico_upd
                df_tec_actualizado.loc[orden_idx, "Diagnostico"] = diagnostico
                df_tec_actualizado.loc[orden_idx, "Solucion"] = solucion
                df_tec_actualizado.loc[orden_idx, "Costo_Final"] = costo_final
                df_tec_actualizado.loc[orden_idx, "Observaciones"] = observaciones_upd

                if nuevo_estado == "Entregado" and estado_anterior != "Entregado":
                    df_tec_actualizado.loc[orden_idx, "Fecha_Entrega"] = pd.Timestamp.now().floor("s")

                save_data(df_tec_actualizado, TEC_CSV)

                if cobrar_saldo and nuevo_estado == "Entregado":
                    saldo = max(costo_final - anticipo_actual, 0)
                    referencia_saldo = f"{orden_sel}-SALDO"
                    saldo_ya_registrado = (
                        df_fin["Referencia"].fillna("").astype(str) == referencia_saldo
                    ).any()
                    if saldo > 0 and not saldo_ya_registrado:
                        df_fin_actualizado = add_finance_record(
                            df_fin,
                            f"Saldo servicio {orden_sel}",
                            saldo,
                            str(orden_actual.get("Cliente", "") or ""),
                            reference=referencia_saldo,
                        )
                        save_data(df_fin_actualizado, FIN_CSV)
                    elif saldo_ya_registrado:
                        st.info("El saldo de esta orden ya estaba registrado en finanzas.")

                st.success("Orden actualizada.")
                st.rerun()

            orden_pdf = generar_pdf_servicio(orden_actual.to_dict())
            st.download_button(
                "Descargar constancia de esta orden",
                orden_pdf,
                f"{orden_sel}.pdf",
                mime="application/pdf",
            )

    with service_tab3:
        st.markdown("#### Edicion rapida")
        ed_tec = st.data_editor(df_tec, num_rows="dynamic", use_container_width=True, hide_index=True)
        if st.button("Guardar tabla de servicio tecnico", use_container_width=True):
            save_data(ed_tec, TEC_CSV)
            st.success("Servicio tecnico guardado.")
            st.rerun()


with tabs[1]:
    st.subheader("Gestion de almacen")

    st.markdown("#### ➕ Agregar producto nuevo")
    with st.form(
        "nuevo_producto_almacen", clear_on_submit=True, enter_to_submit=False
    ):
        codigo_nuevo = st.text_input(
            "Código de barras / SKU",
            placeholder="Escanea o escribe el código aquí",
        )
        producto_nuevo = st.text_input("Nombre del producto")
        nuevo_col_1, nuevo_col_2 = st.columns(2)
        cantidad_nueva = nuevo_col_1.number_input(
            "Cantidad inicial", min_value=0, value=1, step=1
        )
        precio_nuevo = nuevo_col_2.number_input(
            "Precio de venta", min_value=0.0, value=0.0, step=1.0
        )
        guardar_producto_nuevo = st.form_submit_button(
            "Agregar producto al almacén", use_container_width=True
        )

    if guardar_producto_nuevo:
        codigo_limpio = codigo_nuevo.strip()
        producto_limpio = producto_nuevo.strip()
        codigos_existentes = df_inv["SKU"].fillna("").astype(str).str.strip()

        if not codigo_limpio or not producto_limpio:
            st.error("Ingresa el código y el nombre del producto.")
        elif codigo_limpio in codigos_existentes.tolist():
            producto_existente = df_inv.loc[
                codigos_existentes == codigo_limpio, "Producto"
            ].iloc[0]
            st.error(
                f"El código {codigo_limpio} ya está registrado para: {producto_existente}."
            )
        else:
            texto_nuevo = normalizar_busqueda(producto_limpio)
            similares = df_inv[
                df_inv["Producto"].fillna("").astype(str).apply(
                    lambda nombre: texto_nuevo in normalizar_busqueda(nombre)
                    or normalizar_busqueda(nombre) in texto_nuevo
                )
            ]
            inventario_actualizado = pd.concat(
                [
                    df_inv,
                    pd.DataFrame([{
                        "SKU": codigo_limpio,
                        "Producto": producto_limpio,
                        "Cantidad": cantidad_nueva,
                        "Precio": precio_nuevo,
                    }]),
                ],
                ignore_index=True,
            )
            save_data(inventario_actualizado, INV_CSV)
            sonido_confirmacion()
            st.session_state.inventario_borrador = inventario_actualizado.copy()
            st.session_state.inventario_editor_version = (
                st.session_state.get("inventario_editor_version", 0) + 1
            )
            if not similares.empty:
                nombres = ", ".join(similares["Producto"].astype(str).head(3).tolist())
                st.session_state.mensaje_nuevo_producto = (
                    "warning", f"Producto agregado. Revisa nombres parecidos: {nombres}."
                )
            else:
                st.session_state.mensaje_nuevo_producto = (
                    "success", "Producto agregado al almacén."
                )
            st.rerun()

    if "mensaje_nuevo_producto" in st.session_state:
        tipo_mensaje, texto_mensaje = st.session_state.pop("mensaje_nuevo_producto")
        getattr(st, tipo_mensaje)(texto_mensaje)

    buscar_almacen = st.text_input(
        "Buscar producto en almacén",
        placeholder="Escribe nombre o código",
    )
    if buscar_almacen.strip():
        palabras_busqueda_almacen = normalizar_busqueda(buscar_almacen).split()
        coincidencias = df_inv[
            df_inv.apply(
                lambda fila: all(
                    palabra in normalizar_busqueda(
                        f"{fila.get('Producto', '')} {fila.get('SKU', '')}"
                    )
                    for palabra in palabras_busqueda_almacen
                ),
                axis=1,
            )
        ]
        if coincidencias.empty:
            st.info("No se encontraron productos con esa búsqueda.")
        else:
            st.caption("Puedes editar directamente los productos encontrados y guardar los cambios.")
            coincidencias_editadas = st.data_editor(
                coincidencias,
                num_rows="fixed",
                use_container_width=True,
                hide_index=True,
                key="editor_resultados_almacen",
            )
            if st.button("Guardar cambios de los resultados", use_container_width=True):
                inventario_actualizado = df_inv.copy()
                inventario_actualizado.loc[coincidencias_editadas.index, INV_COLS] = (
                    coincidencias_editadas[INV_COLS]
                )
                inventory_error = validate_inventory(inventario_actualizado)
                if inventory_error:
                    st.error(inventory_error)
                else:
                    inventario_actualizado["SKU"] = (
                        inventario_actualizado["SKU"].astype(str).str.strip()
                    )
                    inventario_actualizado["Producto"] = (
                        inventario_actualizado["Producto"].astype(str).str.strip()
                    )
                    inventario_actualizado["Cantidad"] = pd.to_numeric(
                        inventario_actualizado["Cantidad"]
                    ).astype(int)
                    inventario_actualizado["Precio"] = pd.to_numeric(
                        inventario_actualizado["Precio"]
                    )
                    save_data(inventario_actualizado, INV_CSV)
                    sonido_confirmacion()
                    st.session_state.inventario_borrador = inventario_actualizado.copy()
                    st.success("Cambios del producto guardados.")
                    st.rerun()

    if "inventario_borrador" not in st.session_state:
        st.session_state.inventario_borrador = df_inv.copy()
    if "inventario_editor_version" not in st.session_state:
        st.session_state.inventario_editor_version = 0

    sin_stock = df_inv[df_inv["Cantidad"] == 0]

    if not sin_stock.empty:
        st.error("Productos sin stock")
        st.dataframe(sin_stock, use_container_width=True, hide_index=True)

    ed_inv = st.data_editor(
        st.session_state.inventario_borrador,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"editor_inventario_{st.session_state.inventario_editor_version}",
    )

    if st.button("Guardar almacen"):
        inventory_error = validate_inventory(ed_inv)
        if inventory_error:
            st.error(inventory_error)
        else:
            ed_inv = ed_inv.copy()
            ed_inv["SKU"] = ed_inv["SKU"].astype(str).str.strip()
            ed_inv["Producto"] = ed_inv["Producto"].astype(str).str.strip()
            ed_inv["Cantidad"] = pd.to_numeric(ed_inv["Cantidad"]).astype(int)
            ed_inv["Precio"] = pd.to_numeric(ed_inv["Precio"])
            save_data(ed_inv, INV_CSV)
            sonido_confirmacion()
            st.session_state.inventario_borrador = ed_inv.copy()
            st.success("Almacen guardado.")
            st.rerun()


if False:  # Sección de Clientes desactivada.
    st.subheader("Gestion de clientes")

    ed_cli = st.data_editor(df_cli, num_rows="dynamic", use_container_width=True, hide_index=True)

    if st.button("Guardar clientes"):
        save_data(ed_cli, CLI_CSV)
        st.success("Clientes guardados.")
        st.rerun()


with tabs[2]:
    st.subheader("Historial y reportes")

    with st.expander("Registrar gasto", expanded=False):
        st.caption("Los gastos restan del saldo de caja, pero no modifican el almacén.")
        with st.form("registro_gasto", clear_on_submit=True):
            descripcion_gasto = st.text_input(
                "Descripción del gasto", placeholder="Ejemplo: Compra de bolsas"
            )
            gasto_col_1, gasto_col_2 = st.columns(2)
            monto_gasto = gasto_col_1.number_input(
                "Monto del gasto", min_value=0.0, value=None, step=1.0,
                placeholder="Ejemplo: 20",
            )
            pago_gasto = gasto_col_2.selectbox(
                "Método de pago",
                ["Efectivo", "Yape", "Plin", "Transferencia", "Tarjeta", "Otro"],
                key="pago_gasto",
            )
            guardar_gasto = st.form_submit_button("Registrar gasto")

        if guardar_gasto:
            if not descripcion_gasto.strip():
                st.error("Escribe la descripción del gasto.")
            elif monto_gasto is None or monto_gasto <= 0:
                st.error("El monto debe ser mayor que cero.")
            else:
                referencia_gasto = f"GTO-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                df_fin_actualizado = add_finance_record(
                    df_fin,
                    f"Gasto: {descripcion_gasto.strip()}",
                    monto_gasto,
                    "General",
                    tipo="Gasto",
                    reference=referencia_gasto,
                    metodo_pago=pago_gasto,
                )
                save_data(df_fin_actualizado, FIN_CSV)
                sonido_confirmacion()
                st.success("Gasto registrado.")
                st.rerun()

    if df_fin.empty:
        st.info("Sin registros aun.")
    else:
        df_reporte = df_fin.sort_values("Fecha", ascending=False)
        st.dataframe(df_reporte, use_container_width=True, hide_index=True)

        hoy = pd.Timestamp.now().normalize()
        movimientos_hoy = df_fin[df_fin["Fecha"].dt.normalize() == hoy]
        ingresos_hoy = movimientos_hoy[
            movimientos_hoy["Tipo"].fillna("").str.lower() != "gasto"
        ]["Monto"].sum()
        gastos_hoy = movimientos_hoy[
            movimientos_hoy["Tipo"].fillna("").str.lower() == "gasto"
        ]["Monto"].sum()
        saldo_hoy = ingresos_hoy - gastos_hoy
        ingresos_historicos = df_fin[
            df_fin["Tipo"].fillna("").str.lower() != "gasto"
        ]["Monto"].sum()
        gastos_historicos = df_fin[
            df_fin["Tipo"].fillna("").str.lower() == "gasto"
        ]["Monto"].sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("Ingresos de hoy", money(ingresos_hoy))
        col2.metric("Gastos de hoy", money(gastos_hoy))
        col3.metric("Saldo de caja hoy", money(saldo_hoy))
        st.caption(f"Saldo histórico: {money(ingresos_historicos - gastos_historicos)}")
