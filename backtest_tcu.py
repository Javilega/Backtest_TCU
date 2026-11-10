"""Backtest TEV + TETA con predicción operativa (sin leakage).

- TEV: SPOT 600 × (1 + pérdidas) ponderado por kWh reales (retrospectivo).
- TETA: peajes + cargos oficiales ponderados por PERFIL DE CONSUMO PREVISTO
  (solo datos anteriores al mes objetivo, sin leakage).

El perfil previsto usa, en este orden:
  1. Perfil real del mismo mes en años anteriores.
  2. Perfil del mes anterior disponible.
  3. Media de los perfiles de los últimos 3 meses disponibles.
"""
import os, sys, calendar, datetime as dt
import umbrales as u
import tcu_modelo as m
import teta_regulado as teta
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
    if not lineas:
        return []

    sep = ";" if ";" in lineas[0] else ","
    cab = [c.strip() for c in lineas[0].split(sep)]
    if "mes" not in cab:
        return []

    for linea in lineas[1:]:
        partes = linea.split(sep)
        if len(partes) < len(cab):
            continue
        d = dict(zip(cab, [p.strip() for p in partes]))
        if not d.get("mes"):
            continue

        def pf(v):
            if not v:
                return None
            try:
                return float(v.replace(",", "."))
            except ValueError:
                return None

        kwh_p = {}
        for p in range(1, 7):
            v = pf(d.get(f"kwh_p{p}"))
            if v and v > 0:
                kwh_p[p] = v
        total = pf(d.get("kwh_total"))
        filas.append({
            "mes": d["mes"],
            "precio_real_original": pf(d.get("precio_real_original")),
            "tev_real": pf(d.get("coste_energia_real")),
            "teta_real": pf(d.get("peajes_cargos_real")),
            "kwh": total if total else (sum(kwh_p.values()) if kwh_p else 0),
            "kwh_p": kwh_p or None,
        })
    return sorted(filas, key=lambda x: x["mes"])


def limites_mes(texto):
    a, mes = (int(x) for x in texto.split("-"))
    ini = u.medianoche(dt.date(a, mes, 1))
    fin = u.medianoche(dt.date(a, mes, calendar.monthrange(a, mes)[1]) + dt.timedelta(days=1))
    return ini, fin, mes


def perfil_consumo_previsto(mes_objetivo, historico):
    """Perfil de consumo por periodo previsto para un mes futuro.

    Solo usa datos ANTERIORES al mes objetivo. Sin leakage.
    Devuelve dict {periodo: fraccion} que suma 1.
    """
    anio, mes = (int(x) for x in mes_objetivo.split("-"))

    # 1. Mismo mes en años anteriores
    mismo_mes = [f for f in historico
                 if f["mes"].endswith(f"-{mes:02d}")
                 and int(f["mes"].split("-")[0]) < anio
                 and f["kwh_p"]]
    if mismo_mes:
        total = sum(sum(f["kwh_p"].values()) for f in mismo_mes)
        return {p: sum(f["kwh_p"].get(p, 0) for f in mismo_mes) / total
                for p in range(1, 7)
                if any(f["kwh_p"].get(p, 0) > 0 for f in mismo_mes)}

    # 2. Mes anterior
    anteriores = [f for f in historico if f["mes"] < mes_objetivo and f["kwh_p"]]
    if anteriores:
        ultimo = anteriores[-1]
        total = sum(ultimo["kwh_p"].values())
        return {p: k / total for p, k in ultimo["kwh_p"].items() if k > 0}

    # 3. Media de últimos 3
    ultimos_3 = anteriores[-3:] if len(anteriores) >= 3 else anteriores
    if ultimos_3:
        total = sum(sum(f["kwh_p"].values()) for f in ultimos_3)
        return {p: sum(f["kwh_p"].get(p, 0) for f in ultimos_3) / total
                for p in range(1, 7)
                if any(f["kwh_p"].get(p, 0) > 0 for f in ultimos_3)}

    raise ValueError(f"Sin histórico para predecir perfil de {mes_objetivo}")


def evaluar_operativo(spot_data, facturas, train_hasta):
    """Backtest operativo: TEV + TETA sin usar datos reales del mes objetivo."""
    print(f"\n{'='*70}\n BACKTEST OPERATIVO (sin leakage)\n{'='*70}")

    meses = []
    for i, f in enumerate(facturas):
        if f["tev_real"] is None:
            continue

        # Histórico: solo meses ANTERIORES a f
        historico = facturas[:i]

        ini, fin, mes = limites_mes(f["mes"])
        anio = int(f["mes"].split("-")[0])

        # TEV base: SPOT × (1+pérdidas), ponderado por kWh REALES
        # (retrospectivo para evaluar el TEV, pero el TETA será operativo)
        tev_base = m.tev_base_ponderado(spot_data, ini, fin, f["kwh_p"], mes)

        # TETA operativo: usa PERFIL PREVISTO, no kWh reales
        try:
            pesos_prev = perfil_consumo_previsto(f["mes"], historico)
            teta_est = teta.teta_estimado(anio, pesos_prev)
            origen_perfil = "mismo_mes" if any(
                h["mes"].endswith(f"-{mes:02d}") and int(h["mes"].split("-")[0]) < anio
                for h in historico) else "fallback"
        except ValueError as e:
            print(f"AVISO {f['mes']}: {e}")
            teta_est = None
            origen_perfil = "sin_datos"

        meses.append({**f, "tev_base": tev_base, "teta_est": teta_est,
                      "anio": anio, "origen_perfil": origen_perfil})

    train = [x for x in meses if train_hasta is None or x["mes"] <= train_hasta]
    test = [x for x in meses if train_hasta is not None and x["mes"] > train_hasta]
    train_k = [x for x in train if x["kwh"] > 0 and x["kwh_p"] and x["mes"] != "2026-04"]

    if not train_k:
        print("ERROR: sin meses válidos para entrenar.")
        return

    fee = m.calibrar_fee_tev(train_k)
    print(f"Fee TEV calibrado (train hasta {train_hasta}): {fee:+.5f} €/kWh")

    # --- Evaluación TEV (retrospectiva, kWh reales) ---
    print(f"\n{'='*70}\n EVALUACIÓN TEV (retrospectiva, kWh reales)\n{'='*70}")
    print("Mes      | TEV real | TEV base | TEV+fee | Error %")
    for x in meses:
        tev_est = x["tev_base"] + fee
        err = 100 * (tev_est / x["tev_real"] - 1) if x["tev_real"] else 0
        etiqueta = "train" if x in train else "TEST"
        print(f"{x['mes']} |  {x['tev_real']:.4f}  |  {x['tev_base']:.4f}  | {tev_est:.4f} | {err:+6.1f} % [{etiqueta}]")

    # --- Evaluación TETA (operativa, perfil previsto) ---
    print(f"\n{'='*70}\n EVALUACIÓN TETA (operativa, perfil previsto)\n{'='*70}")
    print("Mes      | TETA est | TETA real | Error % | Origen perfil")
    errores_teta = []
    for x in meses:
        if x.get("teta_est") and x["teta_real"]:
            err = 100 * (x["teta_est"] / x["teta_real"] - 1)
            errores_teta.append(abs(err))
            print(f"{x['mes']} |  {x['teta_est']:.4f}  |  {x['teta_real']:.4f}  | {err:+6.2f} % | {x['origen_perfil']}")
    if errores_teta:
        print(f"\n  TETA MAPE (sobre meses con TETA real): {sum(errores_teta) / len(errores_teta):.2f} %")
        print(f"  TETA peor mes: {max(errores_teta):.2f} %")

    # --- Evaluación final: TEV + TETA ---
    print(f"\n{'='*70}\n EVALUACIÓN FINAL: TEV + TETA (sin impuestos)\n{'='*70}")
    print("Mes      | TEV est | TETA est | TOTAL est | TOTAL real | Error %")
    errores_final = []
    for x in meses:
        if x.get("teta_est") is None:
            continue
        tev_est = x["tev_base"] + fee
        total_est = tev_est + x["teta_est"]
        if x["tev_real"] and x["teta_real"]:
            total_real = x["tev_real"] + x["teta_real"]
            err = 100 * (total_est / total_real - 1)
            errores_final.append(abs(err))
            etiqueta = "train" if x in train else "TEST"
            print(f"{x['mes']} | {tev_est:.4f} | {x['teta_est']:.4f} |  {total_est:.4f}  |   {total_real:.4f}   | {err:+6.1f} % [{etiqueta}]")
    if errores_final:
        print(f"\n  TOTAL MAPE (sobre meses con TEV+TETA reales): {sum(errores_final) / len(errores_final):.2f} %")
        print(f"  TOTAL peor mes: {max(errores_final):.2f} %")

    # --- Veredicto ---
    print(f"\n{'='*70}\n VEREDICTO\n{'='*70}")
    if test:
        r = m.metricas_tev(test, fee)
        ok_tev = r["mape"] <= 10 and abs(r["sesgo_pct"]) <= 5
        print(f"TEV OOS: MAPE {r['mape']:.2f} %, sesgo {r['sesgo_pct']:+.2f} % → {'APTO' if ok_tev else 'NO APTO'}")
    if errores_final:
        mape_total = sum(errores_final) / len(errores_final)
        ok_total = mape_total <= 10
        print(f"TOTAL (TEV+TETA) OOS: MAPE {mape_total:.2f} % → {'APTO' if ok_total else 'NO APTO'}")
    print("TETA: calculado con tablas regulatorias del BOE y perfil de consumo previsto.")
    print("2026-04 excluido del entrenamiento (sin desglose por periodos).")


def main(argv):
    if len(argv) < 2:
        return 1
    train_hasta = argv[argv.index("--train-hasta") + 1] if "--train-hasta" in argv else None
    facturas = leer_facturas(argv[1])
    if not facturas:
        return 1

    ini = limites_mes(facturas[0]["mes"])[0]
    fin = limites_mes(facturas[-1]["mes"])[1]
    print(f"Descargando ESIOS 600 desde {ini.date()} hasta {fin.date()}...")
    u.SERIES["spot"] = {"indicador": 600, "geo": 3, "cache": os.path.join("datos", "spot.json")}
    spot = u.pedir_esios(ini, fin, "spot")

    evaluar_operativo(spot, facturas, train_hasta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
