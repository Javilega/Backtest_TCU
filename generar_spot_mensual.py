"""Genera spot_mensual.csv con el precio medio mensual del SPOT 600."""
import os
import datetime as dt
import calendar
import csv
import umbrales as u
from dotenv import load_dotenv
load_dotenv()

def generar_spot_mensual(ruta_salida="spot_mensual.csv"):
    # Rango: enero 2025 a septiembre 2026 (ajusta si tu CSV tiene otro rango)
    ini = u.medianoche(dt.date(2025, 1, 1))
    fin = u.medianoche(dt.date(2026, 10, 1))

    print(f"Descargando SPOT 600 desde {ini.date()} hasta {fin.date()}...")
    u.SERIES["spot"] = {"indicador": 600, "geo": 3, "cache": os.path.join("datos", "spot.json")}
    datos = u.pedir_esios(ini, fin, "spot")

    # Agrupar por mes (año-mes) y calcular media
    por_mes = {}
    for t, precio in datos:
        clave = f"{t.year:04d}-{t.month:02d}"
        por_mes.setdefault(clave, []).append(precio)

    # Escribir CSV
    with open(ruta_salida, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["mes", "spot_medio"])
        for mes in sorted(por_mes):
            media = sum(por_mes[mes]) / len(por_mes[mes])
            writer.writerow([mes, round(media, 6)])

    print(f"Generado {ruta_salida} con {len(por_mes)} meses.")
    for mes in sorted(por_mes):
        media = sum(por_mes[mes]) / len(por_mes[mes])
        print(f"  {mes}: {media:.6f} €/kWh ({len(por_mes[mes])} horas)")

if __name__ == "__main__":
    generar_spot_mensual()