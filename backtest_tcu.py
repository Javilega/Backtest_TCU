"""Backtest del modelo TCU-PVPC contra facturas reales evaluando simultáneamente 1001 y 600.

Uso:
    python backtest_tcu.py facturas.csv [--train-hasta AAAA-MM] [--fee-fijo 0.0052]
"""
import os, sys, csv, calendar, datetime as dt

import umbrales as u
import tcu_modelo as m
from dotenv import load_dotenv
load_dotenv()


def leer_facturas(ruta):
    filas = []
    with open(ruta, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            kwh_p = {p: float(r[f"kwh_p{p}"]) for p in range(1, 7)
                     if (r.get(f"kwh_p{p}") or "").strip()}
            total = (r.get("kwh_total") or "").strip()
            filas.append({
                "mes": r["mes"].strip(),
                "precio_real": float(r["precio_real"].replace(",", ".")),
                "kwh": float(total) if total else (sum(kwh_p.values()) if kwh_p else 0.0),
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


def evaluar_indicador(nombre, id_esios, facturas, datos_indicador, pvpc_referencia,
                      incluye, train_hasta, fee_fijo, salida=print):
    salida(f"\n{'='*70}")
    salida(f" EVALUANDO INDICADOR: {nombre} (ESIOS ID: {id_esios})")
    salida(f"{'='*70}")

    veredicto, pendientes = m.diagnostico_perdidas_tcu(datos_indicador, pvpc_referencia)
    salida(f"Diagnóstico de pérdidas: {veredicto} | pendientes por periodo 2.0TD: "
           + ", ".join(f"P{p}: {s:.3f}" for p, s in pendientes.items()))

    if incluye is None:
        if veredicto == "INCLUYE_PERDIDAS":
            incluye = True
        elif veredicto == "NO_INCLUYE_PERDIDAS":
            incluye = False
        else:
            salida("PENDIENTE: no se puede decidir si incluye pérdidas automáticamente.")
            return None

    salida(f"Se {'desincrustan' if incluye else 'NO se desincrustan'} las pérdidas 2.0TD antes de aplicar las 3.0TD.")

    meses = []
    for f in facturas:
        ini, fin, mes = limites_mes(f["mes"])
        precio, _, nivel = m.precio_medio_ponderado(
            datos_indicador, ini, fin, incluye, 0.0, kwh_periodos=f["kwh_p"],
            pesos=u.PESOS_PERIODOS[mes], mes_calendario=mes)
        meses.append({**f, "precio_modelo_sin_fee": precio, "nivel": nivel})

    train = [x for x in meses if train_hasta is None or x["mes"] <= train_hasta]
    test = [x for x in meses if train_hasta is not None and x["mes"] > train_hasta]
    train_k = [x for x in train if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]

    fee_aditivo = m.calibrar_fee(train_k)
    fee_bloques = m.calibrar_fee_por_bloque(train_k)

    salida(f"\nFee aditivo calibrado con {len(train_k)} meses: {fee_aditivo:.5f} €/kWh")
    salida(f"Fee por bloque (ridge fuerte, comparativo): "
           + ", ".join(f"{b}: {v:+.5f}" for b, v in fee_bloques.items()))

    salida("\nMes      | real    | modelo sin fee | +fee aditivo | +fee bloques | desv. aditivo | desv. bloques")
    for x in meses:
        est_adit = x["precio_modelo_sin_fee"] + fee_aditivo
        if x.get("kwh_p"):
            b_dom = m._bloque_de_periodo(max(x["kwh_p"], key=x["kwh_p"].get))
        else:
            b_dom = "P1P2"
        est_bloq = x["precio_modelo_sin_fee"] * (1 + fee_bloques.get(b_dom, 0.0))
        etiqueta = "train" if x in train else "TEST"
        salida(f"{x['mes']} | {x['precio_real']:.4f} |     {x['precio_modelo_sin_fee']:.4f}     |"
               f"  {est_adit:.4f}   |  {est_bloq:.4f}   |"
               f" {100*(est_adit/x['precio_real']-1):+5.1f} % | {100*(est_bloq/x['precio_real']-1):+5.1f} % [{etiqueta}]")

    def mostrar(nom, conjunto, fee):
        if not conjunto:
            return None
        r = m.metricas(conjunto, fee)
        salida(f"  {nom:<30} MAE {r['mae']:.5f} | MAPE {r['mape']:.2f} % | sesgo {r['sesgo']:+.5f} "
               f"({r['sesgo_pct']:+.2f} %) | desv. típica {r['desv_tipica']:.5f}")
        return r

    for etiqueta, conjunto in (("ENTRENAMIENTO", train), ("PRUEBA (fuera de muestra)", test)):
        if conjunto:
            salida(f"\n{etiqueta} ({nombre}): {len(conjunto)} meses")
            mostrar("sin fee", conjunto, 0.0)
            mostrar(f"fee aditivo {fee_aditivo:.5f}", conjunto, fee_aditivo)
            mostrar("fee por bloque (ridge fuerte)", conjunto, fee_bloques)
            mostrar(f"fee fijo {fee_fijo}", conjunto, fee_fijo)

    evaluados = test if test else [x for x in meses if x["kwh_p"]]
    r_adit = m.metricas(evaluados, fee_aditivo)
    r_bloq = m.metricas(evaluados, fee_bloques)
    r_sin = m.metricas(evaluados, 0.0)

    peor_adit = max(abs(p) for p in r_adit["por_mes_pct"])
    peor_bloq = max(abs(p) for p in r_bloq["por_mes_pct"])
    peor_sin = max(abs(p) for p in r_sin["por_mes_pct"])

    ok_adit = r_adit["mape"] <= 8.0 and abs(r_adit["sesgo_pct"]) <= 5.0 and peor_adit <= 15.0
    ok_sin = r_sin["mape"] <= 8.0 and abs(r_sin["sesgo_pct"]) <= 5.0 and peor_sin <= 15.0
    ok_bloq = r_bloq["mape"] <= 8.0 and abs(r_bloq["sesgo_pct"]) <= 5.0 and peor_bloq <= 15.0

    salida(f"\nCRITERIO RELAJADO para {nombre} (MAPE<=8%, |sesgo|<=5%, peor<=15%):")
    salida(f"  sin fee     -> MAPE {r_sin['mape']:.2f} %, sesgo {r_sin['sesgo_pct']:+.2f} %, peor {peor_sin:.1f} % => {'APTO' if ok_sin else 'NO APTO'}")
    salida(f"  fee aditivo -> MAPE {r_adit['mape']:.2f} %, sesgo {r_adit['sesgo_pct']:+.2f} %, peor {peor_adit:.1f} % => {'APTO' if ok_adit else 'NO AP
