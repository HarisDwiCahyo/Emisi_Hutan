"""
Gabungkan statistik kehilangan tutupan hutan per tahun (hasil
build_hutan_loss_tahun.py) ke geometri batas kabupaten/kota yang sudah ada
(data/kabupaten_jatim.geojson), jadi satu layer GeoJSON yang siap dipakai
di peta (choropleth per tahun, mis. pakai slider tahun di webapp).

Ini versi "ringan": geometrinya tetap batas administratif kab/kota (sudah
disederhanakan di build_hutan_data.py), BUKAN bentuk asli piksel area yang
hilang. Kalau butuh bentuk asli (vektorisasi raster per tahun), itu file
terpisah karena jauh lebih berat (berpotensi ratusan ribu polygon kecil).

Input:
    ../data/kabupaten_jatim.geojson   (geometri 38 kab/kota, sudah simplify)
    ../data/hutan_loss_tahun.json     (statistik per tahun, dari build_hutan_loss_tahun.py)

Output:
    ../data/hutan_loss_tahun.geojson  -> tiap fitur = 1 kabupaten, properti:
        fid, nama, total_ha, loss_<tahun> (per tahun, ha) untuk tiap tahun di dataset

Cara pakai:
    python3 build_hutan_loss_geojson.py
"""

import json
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
KAB_GEOM = ROOT / "data" / "kabupaten_jatim.geojson"
STATS_JSON = ROOT / "data" / "hutan_loss_tahun.json"
OUT_FILE = ROOT / "data" / "hutan_loss_tahun.geojson"


def main():
    print(f"Membaca geometri kabupaten dari {KAB_GEOM} ...")
    kab = gpd.read_file(KAB_GEOM, engine="pyogrio")

    print(f"Membaca statistik dari {STATS_JSON} ...")
    with open(STATS_JSON, encoding="utf-8") as f:
        stats = json.load(f)

    tahun_list = stats["tahun"]
    kab_stats = stats["kabupaten"]

    print("Menggabungkan properti per tahun ke tiap fitur kabupaten ...")
    for col in tahun_list:
        kab[f"loss_{col}"] = 0.0
    kab["total_ha"] = 0.0

    for idx, row in kab.iterrows():
        fid_key = str(int(row["fid"]))
        s = kab_stats.get(fid_key)
        if s is None:
            continue
        for t in tahun_list:
            kab.at[idx, f"loss_{t}"] = s["per_tahun_ha"].get(str(t), 0.0)
        kab.at[idx, "total_ha"] = s["total_ha"]

    keep_cols = ["fid", "nama"] + [f"loss_{t}" for t in tahun_list] + ["total_ha", "geometry"]
    kab_out = kab[keep_cols].copy()

    kab_out.to_file(OUT_FILE, driver="GeoJSON")
    print(f"  -> {OUT_FILE} ({len(kab_out)} fitur, {len(tahun_list)} kolom tahun)")
    print(f"  ukuran file: {OUT_FILE.stat().st_size / 1024:.1f} KB")
    print("\nSelesai.")


if __name__ == "__main__":
    main()
