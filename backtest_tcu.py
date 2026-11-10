"""Backtest del modelo TCU-PVPC contra facturas reales evaluando simultáneamente 1001 y 600.

Modelos comparados:
  - sin fee
  - fee aditivo media (original)
  - fee aditivo robusto (mediana winsorizada)  <- RECOMENDADO
  - fee por bloque (EXPERIMENTO, los datos lo rechazan)

Walk-forward con ventana móvil y ancla a media móvil 3m del precio real.

Uso:
    python backtest_tcu.py facturas.csv [--train-hasta AAAA-MM] [--ventana 6] [--alpha 0.5]
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

    fee_media = m.calibrar_fee(train_k)
    fee_mediana = m.calibrar_fee_robusto(train_k)
    fee_bloques = m.calibrar_fee_por_bloque(train_k)

    salida(f"\nFees calibrados con {len(train_k)} meses:")
    salida(f"  aditivo media   : {fee_media:+.5f} €/kWh")
    salida(f"  aditivo mediana : {fee_mediana:+.5f} €/kWh  <- RECOMENDADO")
    salida(f"  por bloque (exp): " + ", ".join(f"{b}: {v:+.5f}" for b, v in fee_bloques.items()))

    salida("\nMes      | real    | sin fee | +fee media | +fee mediana | +fee bloques | desv. media | desv. mediana")
    for x in meses:
        est_media = x["precio_modelo_sin_fee"] + fee_media
        est_mediana = x["precio_modelo_sin_fee"] + fee_mediana
        if x.get("kwh_p"):
            b_dom = m._bloque_de_periodo(max(x["kwh_p"], key=x["kwh_p"].get))
        else:
            b_dom = "P1P2"
        est_bloq = x["precio_modelo_sin_fee"] * (1 + fee_bloques.get(b_dom, 0.0))
        etiqueta = "train" if x in train else "TEST"
        salida(f"{x['mes']} | {x['precio_real']:.4f} | {x['precio_modelo_sin_fee']:.4f} |"
               f"  {est_media:.4f}  |   {est_mediana:.4f}   |   {est_bloq:.4f}    |"
               f" {100*(est_media/x['precio_real']-1):+5.1f} % | {100*(est_mediana/x['precio_real']-1):+5.1f} % [{etiqueta}]")

    def mostrar(nom, conjunto, fee):
        if not conjunto:
            return None
        r = m.metricas(conjunto, fee)
        salida(f"  {nom:<28} MAE {r['mae']:.5f} | MAPE {r['mape']:.2f} % | sesgo {r['sesgo']:+.5f} "
               f"({r['sesgo_pct']:+.2f} %) | desv. típica {r['desv_tipica']:.5f}")
        return r

    resultados = {}
    for etiqueta, conjunto in (("ENTRENAMIENTO", train), ("PRUEBA (fuera de muestra)", test)):
        if conjunto:
            salida(f"\n{etiqueta} ({nombre}): {len(conjunto)} meses")
            resultados[etiqueta] = {
                "sin_fee": mostrar("sin fee", conjunto, 0.0),
                "media": mostrar(f"fee media {fee_media:+.5f}", conjunto, fee_media),
                "mediana": mostrar(f"fee mediana {fee_mediana:+.5f}", conjunto, fee_mediana),
                "bloques": mostrar("fee por bloque (exp)", conjunto, fee_bloques),
            }

    evaluados = test if test else [x for x in meses if x["kwh_p"]]
    r_sin = m.metricas(evaluados, 0.0)
    r_media = m.metricas(evaluados, fee_media)
    r_mediana = m.metricas(evaluados, fee_mediana)
    r_bloq = m.metricas(evaluados, fee_bloques)

    peor_sin = max(abs(p) for p in r_sin["por_mes_pct"])
    peor_media = max(abs(p) for p in r_media["por_mes_pct"])
    peor_mediana = max(abs(p) for p in r_mediana["por_mes_pct"])
    peor_bloq = max(abs(p) for p in r_bloq["por_mes_pct"])

    salida(f"\nCRITERIO RELAJADO para {nombre} (MAPE<=8%, |sesgo|<=5%, peor<=15%):")
    for nom, r, peor in (("sin fee", r_sin, peor_sin),
                          ("fee media", r_media, peor_media),
                          ("fee mediana", r_mediana, peor_mediana),
                          ("fee bloques", r_bloq, peor_bloq)):
        ok = r["mape"] <= 8.0 and abs(r["sesgo_pct"]) <= 5.0 and peor <= 15.0
        salida(f"  {nom:<14} -> MAPE {r['mape']:5.2f} %, sesgo {r['sesgo_pct']:+6.2f} %, peor {peor:5.1f} % => {'APTO' if ok else 'NO APTO'}")

    candidatos = {"sin fee": r_sin["mape"], "fee media": r_media["mape"],
                  "fee mediana": r_mediana["mape"], "fee bloques": r_bloq["mape"]}
    mejor = min(candidatos, key=candidatos.get)
    salida(f"  MODELO RECOMENDADO: {mejor} (MAPE {candidatos[mejor]:.2f} %)")

    return {"fee_media": fee_media, "fee_mediana": fee_mediana, "fee_bloques": fee_bloques,
            "mejor": mejor, "incluye": incluye}


def walk_forward(facturas, datos_indicador, incluye, min_train=6, ventana=6, alpha=0.5):
    """Walk-forward con ventana móvil y ancla a media móvil 3m del precio real.

    Para cada mes t:
      - Entrena con los últimos `ventana` meses con datos completos (excluye 2026-04).
      - Fee = mediana robusta de residuales en esa ventana.
      - pred_modelo = precio_modelo_sin_fee(t) + fee
      - ancla = media móvil 3m del precio_real (meses con kwh_p)
      - pred = alpha * pred_modelo + (1 - alpha) * ancla
    """
    meses = []
    for f in facturas:
        ini, fin, mes = limites_mes(f["mes"])
        precio, _, nivel = m.precio_medio_ponderado(
            datos_indicador, ini, fin, incluye, 0.0,
            kwh_periodos=f["kwh_p"],
            pesos=u.PESOS_PERIODOS[mes], mes_calendario=mes)
        meses.append({**f, "precio_modelo_sin_fee": precio, "nivel": nivel})

    resultados = []
    for t in range(min_train, len(meses)):
        train = [x for x in meses[max(0, t - ventana):t]
                 if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]
        if len(train) < 3:
            continue
        test = meses[t]
        fee = m.calibrar_fee_robusto(train)
        pred_modelo = test["precio_modelo_sin_fee"] + fee
        previos = [x["precio_real"] for x in meses[max(0, t - 3):t] if x["kwh_p"]]
        ancla = sum(previos) / len(previos) if previos else pred_modelo
        pred_ancla = alpha * pred_modelo + (1 - alpha) * ancla
        resultados.append({
            "mes": test["mes"], "real": test["precio_real"],
            "pred_media": test["precio_modelo_sin_fee"] + m.calibrar_fee(train),
            "pred_mediana": pred_modelo,
            "pred_ancla": pred_ancla,
            "pct_media": 100 * ((test["precio_modelo_sin_fee"] + m.calibrar_fee(train)) / test["precio_real"] - 1),
            "pct_mediana": 100 * (pred_modelo / test["precio_real"] - 1),
            "pct_ancla": 100 * (pred_ancla / test["precio_real"] - 1),
        })
    return resultados


def reporte_walk_forward(resultados, nombre):
    import statistics as st
    if not resultados:
        print(f"\nWALK-FORWARD {nombre}: sin meses suficientes")
        return
    print(f"\nWALK-FORWARD {nombre}: {len(resultados)} meses out-of-sample")
    print("Mes      | real    | fee media | fee mediana | fee+ancla(α) | desv media | desv mediana | desv ancla")
    for r in resultados:
        print(f"{r['mes']} | {r['real']:.4f} |  {r['pred_media']:.4f}   |   {r['pred_mediana']:.4f}    |"
              f"   {r['pred_ancla']:.4f}     | {r['pct_media']:+6.1f} % |  {r['pct_mediana']:+6.1f} %  | {r['pct_ancla']:+6.1f} %")
    for clave, nom in (("pct_media", "fee media      "),
                       ("pct_mediana", "fee mediana    "),
                       ("pct_ancla", "fee mediana+ancla")):
        pcts = [r[clave] for r in resultados]
        print(f"  {nom} -> MAPE {st.mean(abs(p) for p in pcts):5.2f} % | "
              f"sesgo {st.mean(pcts):+6.2f} % | peor {max(abs(p) for p in pcts):5.1f} %")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    train_hasta = argv[argv.index("--train-hasta") + 1] if "--train-hasta" in argv else None
    fee_fijo = float(argv[argv.index("--fee-fijo") + 1]) if "--fee-fijo" in argv else 0.0052
    ventana = int(argv[argv.index("--ventana") + 1]) if "--ventana" in argv else 6
    alpha = float(argv[argv.index("--alpha") + 1]) if "--alpha" in argv else 0.5

    facturas = leer_facturas(argv[1])

    ini = limites_mes(facturas[0]["mes"])[0]
    fin = limites_mes(facturas[-1]["mes"])[1]

    print(f"Descargando datos históricos de ESIOS desde {ini.date()} hasta {fin.date()}...")
    pvpc_data, spot_data = descargar_ambos_esios(ini, fin)

    evaluar_indicador(
        nombre="Mercado Diario (SPOT)", id_esios=600, facturas=facturas,
        datos_indicador=spot_data, pvpc_referencia=pvpc_data,
        incluye=False, train_hasta=train_hasta, fee_fijo=fee_fijo)

    evaluar_indicador(
        nombre="TCU PVPC", id_esios=1001, facturas=facturas,
        datos_indicador=pvpc_data, pvpc_referencia=pvpc_data,
        incluye=None, train_hasta=train_hasta, fee_fijo=fee_fijo)

    print(f"\n{'='*70}")
    print(f" WALK-FORWARD (ventana={ventana}m, alpha={alpha}, 2026-04 fuera del train)")
    print(f"{'='*70}")
    for nombre, datos, incluye in (("SPOT", spot_data, False), ("PVPC", pvpc_data, False)):
        wf = walk_forward(facturas, datos, incluye, ventana=ventana, alpha=alpha)
        reporte_walk_forward(wf, nombre)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
