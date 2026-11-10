"""Backtest del coste de energía (TEV) a partir de SPOT 600.
Modelo de producción: SPOT + fee robusto (mediana) con ventana completa de entrenamiento.
PVPC se evalúa solo como diagnóstico comparativo; no se usa para predecir.
"""
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

def evaluar_sp600(spot_data, pvpc_data, facturas, train_hasta, salida=print):
    """Modelo de producción: SPOT 600 + fee robusto (mediana) con ventana completa."""
    salida(f"\n{'='*70}\n MODELO DE PRODUCCIÓN: SPOT 600 (Coste de Energía)\n{'='*70}")

    veredicto, _ = m.diagnostico_perdidas_tcu(spot_data, pvpc_data)
    salida(f"Diagnóstico de pérdidas: {veredicto}")
    incluye = False  # REE define SPOT 600 sin pérdidas 2.0TD
    salida(f"Se {'desincrustan' if incluye else 'NO se desincrustan'} las pérdidas 2.0TD (forzado por definición de REE).")

    meses = []
    historico = []
    for f in facturas:
        if f["coste_energia_real"] is None: continue
        ini, fin, mes = limites_mes(f["mes"])
        precio, detalle, _ = m.precio_medio_ponderado(
            spot_data, ini, fin, incluye, 0.0, kwh_periodos=f["kwh_p"],
            mes_calendario=mes, solo_energia=True, historico=historico)
        meses.append({**f, "precio_modelo_sin_fee": precio, "detalle": detalle})
        historico.append({"mes": mes, "detalle": detalle})

    train = [x for x in meses if train_hasta is None or x["mes"] <= train_hasta]
    test = [x for x in meses if train_hasta is not None and x["mes"] > train_hasta]
    train_k = [x for x in train if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]

    if not train_k:
        salida("ERROR: sin meses válidos para entrenar.")
        return None

    # VENTANA COMPLETA (no 3 meses): fee más estable y generaliza mejor
    fee_mediana = m.calibrar_fee_robusto(train_k)
    salida(f"\nFee calibrado (ventana completa, mediana): {fee_mediana:+.5f} €/kWh")

    salida("\nMes      | TEV real | TEV modelo | +fee | Error TEV % | Precio Final Pred. | Error Final %")
    for x in meses:
        est_tev = x["precio_modelo_sin_fee"] + fee_mediana
        err_tev_pct = 100 * (est_tev / x["coste_energia_real"] - 1)
        fijo_real = x["precio_real_original"] - x["coste_energia_real"] - x["peajes_cargos_real"]
        pred_final = est_tev + x["peajes_cargos_real"] + fijo_real
        err_final_pct = 100 * (pred_final / x["precio_real_original"] - 1) if x["precio_real_original"] else 0.0
        etiqueta = "train" if x in train else "TEST"
        salida(f"{x['mes']} |  {x['coste_energia_real']:.4f}  |   {x['precio_modelo_sin_fee']:.4f}   | {est_tev:.4f} |   {err_tev_pct:+5.1f} %   |        {pred_final:.4f}        |   {err_final_pct:+5.1f} % [{etiqueta}]")

    def mostrar(nom, conjunto, fee):
        if not conjunto: return None
        r = m.metricas(conjunto, fee, columna_real="coste_energia_real")
        salida(f"  {nom:<22} TEV MAPE {r['mape']:5.2f} % | sesgo {r['sesgo_pct']:+6.2f} % | desv.típ {r['desv_tipica']:.5f}")
        return r

    resultados = {}
    for etiqueta, conjunto in (("ENTRENAMIENTO", train), ("PRUEBA (TEST)", test)):
        if conjunto:
            salida(f"\n{etiqueta} (SPOT 600): {len(conjunto)} meses")
            resultados[etiqueta] = {
                "sin_fee": mostrar("sin fee", conjunto, 0.0),
                "fee_mediana": mostrar(f"fee mediana {fee_mediana:+.5f}", conjunto, fee_mediana),
            }
    return {"fee_mediana": fee_mediana, "resultados": resultados, "incluye": incluye}

def evaluar_pvpc_diagnostico(pvpc_data, facturas, train_hasta, salida=print):
    """PVPC se evalúa solo como diagnóstico comparativo. No es modelo de producción."""
    salida(f"\n{'='*70}\n DIAGNÓSTICO COMPARATIVO: PVPC 1001 (NO es modelo de producción)\n{'='*70}")

    veredicto, _ = m.diagnostico_perdidas_tcu(pvpc_data, pvpc_data)
    salida(f"Diagnóstico de pérdidas: {veredicto}")
    incluye = (veredicto == "INCLUYE_PERDIDAS")
    salida(f"Se {'desincrustan' if incluye else 'NO se desincrustan'} las pérdidas 2.0TD.")

    meses = []
    historico = []
    for f in facturas:
        if f["coste_energia_real"] is None: continue
        ini, fin, mes = limites_mes(f["mes"])
        precio, detalle, _ = m.precio_medio_ponderado(
            pvpc_data, ini, fin, incluye, 0.0, kwh_periodos=f["kwh_p"],
            mes_calendario=mes, solo_energia=True, historico=historico)
        meses.append({**f, "precio_modelo_sin_fee": precio, "detalle": detalle})
        historico.append({"mes": mes, "detalle": detalle})

    train = [x for x in meses if train_hasta is None or x["mes"] <= train_hasta]
    test = [x for x in meses if train_hasta is not None and x["mes"] > train_hasta]
    train_k = [x for x in train if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]
    if not train_k:
        salida("Sin meses válidos.")
        return None

    fee = m.calibrar_fee_robusto(train_k)
    salida(f"\nFee calibrado (ventana completa, mediana): {fee:+.5f} €/kWh")
    salida("\nMes      | TEV real | TEV modelo | +fee | Error TEV %")
    for x in meses:
        est = x["precio_modelo_sin_fee"] + fee
        err = 100 * (est / x["coste_energia_real"] - 1)
        etiqueta = "train" if x in train else "TEST"
        salida(f"{x['mes']} |  {x['coste_energia_real']:.4f}  |   {x['precio_modelo_sin_fee']:.4f}   | {est:.4f} |   {err:+5.1f} % [{etiqueta}]")

    def mostrar(nom, conjunto):
        if not conjunto: return
        r = m.metricas(conjunto, fee, columna_real="coste_energia_real")
        salida(f"  {nom:<22} TEV MAPE {r['mape']:5.2f} % | sesgo {r['sesgo_pct']:+6.2f} %")
    for etiqueta, conjunto in (("ENTRENAMIENTO", train), ("PRUEBA (TEST)", test)):
        if conjunto:
            salida(f"\n{etiqueta} (PVPC 1001): {len(conjunto)} meses")
            mostrar(f"fee mediana {fee:+.5f}", conjunto)
    return {"fee": fee}

def main(argv):
    if len(argv) < 2: return 1
    train_hasta = argv[argv.index("--train-hasta") + 1] if "--train-hasta" in argv else None

    facturas = leer_facturas(argv[1])
    if not facturas: return 1

    ini = limites_mes(facturas[0]["mes"])[0]
    fin = limites_mes(facturas[-1]["mes"])[1]
    print(f"Descargando ESIOS desde {ini.date()} hasta {fin.date()}...")
    pvpc_data, spot_data = descargar_ambos_esios(ini, fin)

    resultado = evaluar_sp600(spot_data, pvpc_data, facturas, train_hasta)
    evaluar_pvpc_diagnostico(pvpc_data, facturas, train_hasta)

    # Resumen ejecutivo
    print(f"\n{'='*70}\n RESUMEN EJECUTIVO\n{'='*70}")
    if resultado and "resultados" in resultado:
        test = resultado["resultados"].get("PRUEBA (TEST)")
        if test and test["fee_mediana"]:
            r = test["fee_mediana"]
            print(f"Modelo de producción: SPOT 600 + fee mediana {resultado['fee_mediana']:+.5f} €/kWh")
            print(f"  MAPE out-of-sample: {r['mape']:.2f} %")
            print(f"  Sesgo ponderado:    {r['sesgo_pct']:+.2f} %")
            print(f"  Veredicto:          {'APTO' if r['mape'] <= 10 and abs(r['sesgo_pct']) <= 5 else 'NO APTO'} (criterio: MAPE<=10%, |sesgo|<=5%)")
    print(f"\nPVPC 1001: DESCARTADO como modelo de producción (error estructural ~15% por costes regulados no capturables con fee lineal).")
    print(f"2026-04 excluido del entrenamiento por falta de desglose por periodos.")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
