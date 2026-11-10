"""Backtest de coste de energía (TEV) + peajes (TETA)."""
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
                "precio_real_original": float(r["precio_real_original"]) if r.get("precio_real_original") else None,
                "coste_energia_real": float(r["coste_energia_real"]) if r.get("coste_energia_real") else None,
                "peajes_cargos_real": float(r["peajes_cargos_real"]) if r.get("peajes_cargos_real") else None,
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

def evaluar_indicador(nombre, datos_indicador, pvpc_referencia, facturas, incluye, train_hasta, fee_fijo, salida=print):
    salida(f"\n{'='*70}\n EVALUANDO: {nombre} (Coste de Energía)\n{'='*70}")

    veredicto, _ = m.diagnostico_perdidas_tcu(datos_indicador, pvpc_referencia)
    salida(f"Diagnóstico de pérdidas: {veredicto}")
    if incluye is None:
        incluye = (veredicto == "INCLUYE_PERDIDAS")
    salida(f"Se {'desincrustan' if incluye else 'NO se desincrustan'} las pérdidas 2.0TD.")

    meses = []
    for f in facturas:
        if f["coste_energia_real"] is None: continue
        ini, fin, mes = limites_mes(f["mes"])
        # AQUI ESTA LA CLAVE: solo_energia=True
        precio, _, _ = m.precio_medio_ponderado(
            datos_indicador, ini, fin, incluye, 0.0, kwh_periodos=f["kwh_p"],
            mes_calendario=mes, solo_energia=True)
        meses.append({**f, "precio_modelo_sin_fee": precio})

    train = [x for x in meses if train_hasta is None or x["mes"] <= train_hasta]
    test = [x for x in meses if train_hasta is not None and x["mes"] > train_hasta]
    train_k = [x for x in train if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]

    fee_media = m.calibrar_fee(train_k)
    fee_mediana = m.calibrar_fee_robusto(train_k)

    salida(f"\nFee calibrado (sobre Coste Energía): media {fee_media:+.5f}, mediana {fee_mediana:+.5f}")

    salida("\nMes      | TEV real | TEV modelo | +fee mediana | Error TEV % | Precio Final (TEV+TETA) | Error Final %")
    for x in meses:
        est_tev = x["precio_modelo_sin_fee"] + fee_mediana
        err_tev_pct = 100 * (est_tev / x["coste_energia_real"] - 1)
        
        # El precio final es TEV predicho + TETA real
        pred_final = est_tev + x["peajes_cargos_real"]
        err_final_pct = 100 * (pred_final / x["precio_real_original"] - 1) if x["precio_real_original"] else 0.0
        
        etiqueta = "train" if x in train else "TEST"
        salida(f"{x['mes']} |  {x['coste_energia_real']:.4f}  |   {x['precio_modelo_sin_fee']:.4f}   |   {est_tev:.4f}   |   {err_tev_pct:+5.1f} %   |      {pred_final:.4f}       |   {err_final_pct:+5.1f} % [{etiqueta}]")

    def mostrar(nom, conjunto, fee):
        if not conjunto: return
        r_tev = m.metricas(conjunto, fee, columna_real="coste_energia_real")
        salida(f"  {nom:<20} TEV MAPE {r_tev['mape']:5.2f} % | TEV sesgo {r_tev['sesgo_pct']:+6.2f} %")

    for etiqueta, conjunto in (("ENTRENAMIENTO", train), ("PRUEBA (TEST)", test)):
        if conjunto:
            salida(f"\n{etiqueta} ({nombre}): {len(conjunto)} meses")
            mostrar("sin fee", conjunto, 0.0)
            mostrar(f"fee mediana", conjunto, fee_mediana)

    return {"fee_mediana": fee_mediana, "incluye": incluye}

def walk_forward(facturas, datos_indicador, incluye, min_train=6, ventana=6):
    meses = []
    for f in facturas:
        if f["coste_energia_real"] is None: continue
        ini, fin, mes = limites_mes(f["mes"])
        precio, _, _ = m.precio_medio_ponderado(
            datos_indicador, ini, fin, incluye, 0.0, kwh_periodos=f["kwh_p"],
            mes_calendario=mes, solo_energia=True)
        meses.append({**f, "precio_modelo_sin_fee": precio})

    resultados = []
    for t in range(min_train, len(meses)):
        train = [x for x in meses[max(0, t - ventana):t] if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]
        if len(train) < 3: continue
        test = meses[t]
        fee = m.calibrar_fee_robusto(train)
        est_tev = test["precio_modelo_sin_fee"] + fee
        resultados.append({
            "mes": test["mes"], "real_tev": test["coste_energia_real"],
            "pred_tev": est_tev, "pct_tev": 100 * (est_tev / test["coste_energia_real"] - 1),
            "real_final": test["precio_real_original"],
            "pred_final": est_tev + test["peajes_cargos_real"],
            "pct_final": 100 * ((est_tev + test["peajes_cargos_real"]) / test["precio_real_original"] - 1)
        })
    return resultados

def reporte_walk_forward(resultados, nombre):
    import statistics as st
    if not resultados: return
    pcts_tev = [r["pct_tev"] for r in resultados]
    pcts_final = [r["pct_final"] for r in resultados]
    print(f"\nWALK-FORWARD {nombre}: {len(resultados)} meses OOS")
    print("Mes      | TEV real | TEV pred | Err TEV % | Final real | Final pred | Err Final %")
    for r in resultados:
        print(f"{r['mes']} |  {r['real_tev']:.4f}  |  {r['pred_tev']:.4f}  | {r['pct_tev']:+6.1f} %  |   {r['real_final']:.4f}   |   {r['pred_final']:.4f}   |  {r['pct_final']:+6.1f} %")
    print(f"  > TEV   -> MAPE {st.mean(abs(p) for p in pcts_tev):5.2f} % | sesgo {st.mean(pcts_tev):+6.2f} % | peor {max(abs(p) for p in pcts_tev):5.1f} %")
    print(f"  > FINAL -> MAPE {st.mean(abs(p) for p in pcts_final):5.2f} % | sesgo {st.mean(pcts_final):+6.2f} % | peor {max(abs(p) for p in pcts_final):5.1f} %")

def main(argv):
    if len(argv) < 2: return 1
    train_hasta = argv[argv.index("--train-hasta") + 1] if "--train-hasta" in argv else None
    fee_fijo = float(argv[argv.index("--fee-fijo") + 1]) if "--fee-fijo" in argv else 0.0052
    ventana = int(argv[argv.index("--ventana") + 1]) if "--ventana" in argv else 6

    facturas = leer_facturas(argv[1])
    ini = limites_mes(facturas[0]["mes"])[0]
    fin = limites_mes(facturas[-1]["mes"])[1]

    print(f"Descargando ESIOS desde {ini.date()} hasta {fin.date()}...")
    pvpc_data, spot_data = descargar_ambos_esios(ini, fin)

    evaluar_indicador("SPOT 600", spot_data, pvpc_data, facturas, False, train_hasta, fee_fijo)
    evaluar_indicador("PVPC 1001", pvpc_data, pvpc_data, facturas, None, train_hasta, fee_fijo)

    print(f"\n{'='*70}\n WALK-FORWARD (ventana={ventana})\n{'='*70}")
    for nombre, datos, incluye in (("SPOT", spot_data, False), ("PVPC", pvpc_data, None)):
        wf = walk_forward(facturas, datos, incluye, ventana=ventana)
        reporte_walk_forward(wf, nombre)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
