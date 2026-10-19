"""
Ambil data kehilangan tutupan hutan (tree cover loss) per tahun untuk Jawa
Timur dari dataset Hansen/UMD Global Forest Change -- dataset mentah yang
sama yang dipakai Global Forest Watch di balik layar.

Kenapa tidak lewat API resmi GFW (data-api.globalforestwatch.org)?
Endpoint query-nya mewajibkan API key (perlu daftar akun + request token,
semua lewat POST). Jadi dipakai sumber publiknya langsung: tile GeoTIFF
Hansen GFC di Google Cloud Storage, yang bisa diakses full lewat GET/HTTP
range request -- tanpa login, tanpa API key, tanpa signup.

Sumber tile (publik, GET only):
    https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/
        Hansen_GFC-2024-v1.12_lossyear_00N_110E.tif      (kode tahun loss, 1-24 = 2001-2024)
        Hansen_GFC-2024-v1.12_treecover2000_00N_110E.tif (persen tutupan kanopi tahun 2000)

Tile "00N_110E" sudah mencakup seluruh bbox Jawa Timur (lon 110-120E,
lat 0 - -10), jadi cukup 1 tile. Dibaca lewat /vsicurl/ (GDAL) dengan
windowed read, jadi TIDAK download seluruh tile (~ratusan MB/tile), hanya
byte range yang kena bbox Jatim saja.

Definisi "hutan" memakai threshold tutupan kanopi tahun 2000 >= 30%, sama
seperti default yang dipakai GFW di dashboard publiknya.

Input:
    ../Kabupaten_JawaTimur.full.geojson   (38 kab/kota Jatim, EPSG:4326)

Output (ke ../data/):
    hutan_loss_tahun.json  -> { "meta": {...},
                                 "tahun": [2001..2024],
                                 "provinsi": {tahun: luas_ha},
                                 "kabupaten": {fid: {nama, per_tahun: {...}, total_ha}} }
    hutan_loss_tahun.csv   -> kabupaten,fid,tahun,luas_ha  (flat, gampang dibuka Excel)

Cara pakai:
    python3 build_hutan_loss_tahun.py

Dependensi: geopandas, rasterio, pandas, numpy, pyogrio
    pip install --user geopandas rasterio pandas numpy pyogrio
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parent.parent
KAB_SRC = ROOT / "Kabupaten_JawaTimur.full.geojson"
OUT_DIR = ROOT / "data"
OUT_DIR.mkdir(exist_ok=True)

GFC_VERSION = "GFC-2024-v1.12"
TILE = "00N_110E"  # mencakup seluruh Jatim (lon 110-120E, lat 0..-10)
BASE_URL = f"/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/{GFC_VERSION}"
LOSSYEAR_URL = f"{BASE_URL}/Hansen_{GFC_VERSION}_lossyear_{TILE}.tif"
TREECOVER_URL = f"{BASE_URL}/Hansen_{GFC_VERSION}_treecover2000_{TILE}.tif"

FOREST_THRESHOLD = 30  # persen tutupan kanopi tahun 2000, sama dgn default GFW
DEG_M = 111_320.0  # meter per derajat lintang (konstan, dipakai juga utk bujur x cos(lat))


def label_kab(row):
    return ("Kota " if row["TYPE_2"] == "Kota" else "Kab. ") + row["NAME_2"]


def main():
    print(f"Membaca batas kabupaten dari {KAB_SRC} ...")
    kab = gpd.read_file(KAB_SRC, engine="pyogrio")
    kab = kab[["fid", "NAME_2", "TYPE_2", "geometry"]].copy()
    kab["nama"] = kab.apply(label_kab, axis=1)

    bounds = tuple(kab.total_bounds)  # (xmin, ymin, xmax, ymax), WGS84
    print(f"  bbox Jatim: {bounds}")

    print(f"Membuka tile Hansen GFC ({GFC_VERSION}, tile {TILE}) via /vsicurl/ (GET, tanpa login) ...")
    with rasterio.open(LOSSYEAR_URL) as src_loss:
        win = from_bounds(*bounds, transform=src_loss.transform)
        win_transform = src_loss.window_transform(win)
        lossyear = src_loss.read(1, window=win)
    print(f"  lossyear window: {lossyear.shape} px")

    with rasterio.open(TREECOVER_URL) as src_tc:
        tc_win = from_bounds(*bounds, transform=src_tc.transform)
        treecover2000 = src_tc.read(1, window=tc_win)

    assert treecover2000.shape == lossyear.shape, "window lossyear & treecover2000 tidak sinkron"

    print("Rasterize batas kabupaten ke grid yang sama (fid per piksel) ...")
    shapes = [(geom, int(fid)) for geom, fid in zip(kab.geometry, kab["fid"])]
    kab_raster = rasterize(
        shapes,
        out_shape=lossyear.shape,
        transform=win_transform,
        fill=0,
        dtype="int32",
    )

    print("Hitung luas piksel per baris (meter, disesuaikan garis lintang) ...")
    nrows = lossyear.shape[0]
    row_lat = np.array(
        [win_transform * (0, r + 0.5) for r in range(nrows)]
    )[:, 1]  # lintang titik tengah tiap baris
    px_w_deg = win_transform.a
    px_h_deg = -win_transform.e
    row_area_ha = (
        (px_w_deg * DEG_M * np.cos(np.radians(row_lat))) * (px_h_deg * DEG_M)
    ) / 10_000.0  # m^2 -> ha, per baris

    print("Masking: hutan tahun 2000 (tutupan >= {}%) & ada loss & di dalam batas Jatim ...".format(FOREST_THRESHOLD))
    mask = (treecover2000 >= FOREST_THRESHOLD) & (lossyear > 0) & (kab_raster > 0)
    rows, cols = np.nonzero(mask)
    print(f"  piksel loss terhitung: {len(rows):,}")

    tahun = 2000 + lossyear[rows, cols].astype(int)
    fid_sel = kab_raster[rows, cols]
    area_sel = row_area_ha[rows]

    df = pd.DataFrame({"fid": fid_sel, "tahun": tahun, "luas_ha": area_sel})
    agg = df.groupby(["fid", "tahun"], as_index=False)["luas_ha"].sum()

    tahun_list = list(range(2001, 2000 + int(lossyear.max()) + 1))

    print("Menyusun statistik per kabupaten & total provinsi ...")
    kab_stats = {}
    nama_by_fid = dict(zip(kab["fid"], kab["nama"]))
    for fid, nama in nama_by_fid.items():
        sub = agg[agg["fid"] == fid].set_index("tahun")["luas_ha"]
        per_tahun = {str(t): round(float(sub.get(t, 0.0)), 3) for t in tahun_list}
        kab_stats[str(int(fid))] = {
            "nama": nama,
            "per_tahun_ha": per_tahun,
            "total_ha": round(sum(per_tahun.values()), 3),
        }

    provinsi_per_tahun = {
        str(t): round(float(agg[agg["tahun"] == t]["luas_ha"].sum()), 3) for t in tahun_list
    }

    result = {
        "meta": {
            "sumber": "Hansen/UMD Global Forest Change (via Google Cloud Storage, publik, tanpa API key)",
            "versi_dataset": GFC_VERSION,
            "tile": TILE,
            "threshold_tutupan_kanopi_persen": FOREST_THRESHOLD,
            "catatan": (
                "Luas kehilangan tutupan hutan (tree cover loss) tahunan, dihitung dari "
                "piksel yang tutupan kanopi tahun 2000-nya >= {}% DAN terdeteksi loss pada "
                "tahun tsb, diclip ke batas administratif kab/kota Jawa Timur."
            ).format(FOREST_THRESHOLD),
        },
        "tahun": tahun_list,
        "provinsi_per_tahun_ha": provinsi_per_tahun,
        "kabupaten": kab_stats,
    }

    out_json = OUT_DIR / "hutan_loss_tahun.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=None)
    print(f"  -> {out_json}")

    flat_rows = []
    for fid, nama in nama_by_fid.items():
        per_tahun = kab_stats[str(int(fid))]["per_tahun_ha"]
        for t in tahun_list:
            flat_rows.append({"fid": int(fid), "kabupaten": nama, "tahun": t, "luas_ha": per_tahun[str(t)]})
    out_csv = OUT_DIR / "hutan_loss_tahun.csv"
    pd.DataFrame(flat_rows).to_csv(out_csv, index=False)
    print(f"  -> {out_csv}")

    print("\nRingkasan total kehilangan tutupan hutan Jawa Timur per tahun (ha):")
    for t in tahun_list:
        print(f"  {t}: {provinsi_per_tahun[str(t)]:,.1f} ha")
    print(f"\nTotal {tahun_list[0]}-{tahun_list[-1]}: {sum(provinsi_per_tahun.values()):,.1f} ha")

    print("\nSelesai.")


if __name__ == "__main__":
    main()
