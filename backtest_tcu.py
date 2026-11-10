"""Backtest de coste de energía (TEV) + peajes (TETA) con ventana móvil."""
import os, sys, calendar, datetime as dt
import umbrales as u
import tcu_modelo as m
from dotenv import load_dotenv
load_dotenv()

def leer_facturas(ruta):
    filas = []
    try:
        with open(ruta, encoding="utf-8-sig") as f:
            lineas = [l.strip() for l in f.readlines() if l.strip()]
    except FileNotFoundError:
        print(f"ERROR CRÍTICO: No se encuentra el archivo {ruta}")
        return []

    if not lineas: return []

    separador = ";" if ";" in lineas[0] else ","
    cabeceras = [c.strip() for c in lineas[0].split(separador)]
    
    if "mes" not in cabeceras: return []

    for linea in lineas[1:]:
        partes = linea.split(separador)
        if len(partes) < len(cabeceras): continue
        datos = dict(zip(cabeceras, [p.strip() for p in partes]))
        if not datos.get("mes"): continue

        def parse_float(val):
            if not val or val == "": return None
            try: return float(val.replace(",", "."))
            except ValueError: return None

        kwh_p = {}
        for p in range(1, 7):
            val = parse_float(datos.get(f"kwh_p{p}"))
            if val is not None and val > 0: kwh_p[p] = val

        total = parse_float(datos.get("kwh_total"))
        filas.append({
            "mes": datos["mes"],
            "precio_real_original": parse_float(datos.get("precio_real_original")),
            "coste_energia_real": parse_float(datos.get("coste_energia_real")),
            "peajes_cargos_real": parse_float(datos.get("peajes_cargos_real")),
            "kwh": total if total else (sum(kwh_p.values()) if kwh_p else 0.0),
            "kwh_p": kwh_p or None,
        })
    return sorted(filas, key=lambda x: x["mes"])

def limites_mes(texto):
    anio, mes = (int(x) for x in texto.split("-"))
    ini = u.medianoche(dt.date(anio, mes, 1))
    fin = u.medianoche(dt.date(anio, mes, calendar.monthrange(anio, mes)[1]) + dt.timedelta(days=1))
    return ini, fin, mes

def descargar_ambos_esios(ini, fin):
    u.SERIES["tcu_pvpc"] = {"indicador": 1001, "geo": 8741, "cache": os.path.join("datos", "tcu_1001.json")}
    pvpc = u.pedir_esios(ini, fin, "tcu_pvpc")
    u.SERIES["tcu_spot"] = {"indicador": 600, "geo": 3, "cache": os.path.join("datos", "tcu_600.json")}
    spot = u.pedir_esios(ini, fin, "tcu_spot")
    return pvpc, spot

def evaluar_indicador(nombre, datos_indicador, pvpc_referencia, facturas, incluye, train_hasta, fee_fijo, salida=print):
    salida(f"\n{'='*70}\n EVALUANDO: {nombre} (Coste de Energía)\n{'='*70}")

    veredicto, _ = m.diagnostico_perdidas_tcu(datos_indicador, pvpc_referencia)
    salida(f"Diagnóstico de pérdidas: {veredicto}")
    if incluye is None: incluye = (veredicto == "INCLUYE_PERDIDAS")
    salida(f"Se {'desincrustan' if incluye else 'NO se desincrustan'} las pérdidas 2.0TD.")

    meses = []
    historico = []
    for f in facturas:
        if f["coste_energia_real"] is None: continue
        ini, fin, mes = limites_mes(f["mes"])
        
        precio, detalle, _ = m.precio_medio_ponderado(
            datos_indicador, ini, fin, incluye, 0.0, kwh_periodos=f["kwh_p"],
            mes_calendario=mes, solo_energia=True, historico=historico)
            
        meses.append({**f, "precio_modelo_sin_fee": precio, "detalle": detalle})
        historico.append({"mes": mes, "detalle": detalle})

    train = [x for x in meses if train_hasta is None or x["mes"] <= train_hasta]
    test = [x for x in meses if train_hasta is not None and x["mes"] > train_hasta]
    
    # Fee adaptativo con ventana móvil de 3 meses
    fee_mediana = 0.0
    if train:
        ventana_train = train[-3:] if len(train) >= 3 else train
        fee_mediana = m.calibrar_fee_robusto(ventana_train)

    salida(f"\nFee calibrado (ventana móvil 3m): {fee_mediana:+.5f}")

    salida("\nMes      | TEV real | TEV modelo | +fee | Error TEV % | Precio Final Predicho | Error Final %")
    for x in meses:
        est_tev = x["precio_modelo_sin_fee"] + fee_mediana
        err_tev_pct = 100 * (est_tev / x["coste_energia_real"] - 1)
        
        # LA CLAVE: El "fijo" (Potencia + Impuestos) es la diferencia entre el precio original y (TEV_real + TETA_real)
        fijo_real = x["precio_real_original"] - x["coste_energia_real"] - x["peajes_cargos_real"]
        
        # Predicción final = TEV_predicho + TETA_real + Fijo_real
        pred_final = est_tev + x["peajes_cargos_real"] + fijo_real
        err_final_pct = 100 * (pred_final / x["precio_real_original"] - 1) if x["precio_real_original"] else 0.0
        
        etiqueta = "train" if x in train else "TEST"
        salida(f"{x['mes']} |  {x['coste_energia_real']:.4f}  |   {x['precio_modelo_sin_fee']:.4f}   | {est_tev:.4f} |   {err_tev_pct:+5.1f} %   |        {pred_final:.4f}        |   {err_final_pct:+5.1f} % [{etiqueta}]")

    def mostrar(nom, conjunto, fee):
        if not conjunto: return
        r_tev = m.metricas(conjunto, fee, columna_real="coste_energia_real")
        salida(f"  {nom:<20} TEV MAPE {r_tev['mape']:5.2f} % | TEV sesgo {r_tev['sesgo_pct']:+6.2f} %")

    for etiqueta, conjunto in (("ENTRENAMIENTO", train), ("PRUEBA (TEST)", test)):
        if conjunto:
            salida(f"\n{etiqueta} ({nombre}): {len(conjunto)} meses")
            mostrar("sin fee", conjunto, 0.0)
            mostrar(f"fee mediana", conjunto, fee_mediana)

def main(argv):
    if len(argv) < 2: return 1
    train_hasta = argv[argv.index("--train-hasta") + 1] if "--train-hasta" in argv else None
    fee_fijo = float(argv[argv.index("--fee-fijo") + 1]) if "--fee-fijo" in argv else 0.0052

    facturas = leer_facturas(argv[1])
    if not facturas: return 1
        
    ini = limites_mes(facturas[0]["mes"])[0]
    fin = limites_mes(facturas[-1]["mes"])[1]

    print(f"Descargando ESIOS desde {ini.date()} hasta {fin.date()}...")
    pvpc_data, spot_data = descargar_ambos_esios(ini, fin)

    evaluar_indicador("SPOT 600", spot_data, pvpc_data, facturas, False, train_hasta, fee_fijo)
    evaluar_indicador("PVPC 1001", pvpc_data, pvpc_data, facturas, None, train_hasta, fee_fijo)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
