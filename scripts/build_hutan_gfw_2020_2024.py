"""
Statistik tutupan hutan Jawa Timur dari Hansen/UMD Global Forest Change
(sumber yang dipakai Global Forest Watch), dibatasi tahun 2020-2024 sesuai
permintaan -- karena proses per-piksel untuk seluruh 2001-2024 + vektorisasi
cukup berat.

Tiga lapisan yang dihasilkan, semua dari band resmi Hansen GFC:
  1. loss              -> kehilangan tutupan hutan PER TAHUN (2020, 2021, ..., 2024)
  2. tutupan_tersisa   -> tutupan hutan yang MASIH ADA di akhir tahun tsb
                          (baseline 2000 dikurangi kumulatif loss s.d. tahun itu)
  3. gain              -> pertambahan tutupan hutan, periode 2000-2012 (SATU nilai
                          statis -- Hansen/UMD belum pernah merilis update band
                          "gain" ke periode lebih baru di versi manapun, jadi TIDAK
                          ADA data gain khusus 2020-2024. Tetap disertakan sebagai
                          info pelengkap, dengan label periode yang jelas.)

Definisi "hutan": tutupan kanopi tahun 2000 >= 30% (threshold default GFW).

Sumber (publik, GET-only via /vsicurl/, tanpa API key/login):
    https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/
        Hansen_GFC-2024-v1.12_lossyear_00N_110E.tif
        Hansen_GFC-2024-v1.12_treecover2000_00N_110E.tif
        Hansen_GFC-2024-v1.12_gain_00N_110E.tif

Array lossyear/treecover2000/gain untuk window Jatim sudah di-cache di
../.cache/*.npy oleh sesi sebelumnya (supaya tidak download ulang ~300MB
tiap run). Kalau cache tidak ada, didownload otomatis.

Output (ke ../data/):
    hutan_gfw_2020_2024.json     -> statistik per kabupaten & provinsi
    hutan_gfw_2020_2024.geojson  -> geometri kabupaten (dari kabupaten_jatim.geojson)
                                     + properti loss_<tahun>, tutupan_<tahun>, gain_2000_2012
    hutan_gfw_2020_2024.csv      -> versi flat (loss & tutupan_tersisa per tahun)

Cara pakai:
    python3 build_hutan_gfw_2020_2024.py
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio.features import rasterize
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parent.parent
KAB_SRC = ROOT / "Kabupaten_JawaTimur.full.geojson"
KAB_GEOM_LIGHT = ROOT / "data" / "kabupaten_jatim.geojson"
CACHE_DIR = ROOT / ".cache"
OUT_DIR = ROOT / "data"
OUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

GFC_VERSION = "GFC-2024-v1.12"
TILE = "00N_110E"
BASE_URL = f"/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/{GFC_VERSION}"
FOREST_THRESHOLD = 30
DEG_M = 111_320.0
TAHUN_LIST = [2020, 2021, 2022, 2023, 2024]
GAIN_PERIOD_LABEL = "2000-2012"


def label_kab(row):
    return ("Kota " if row["TYPE_2"] == "Kota" else "Kab. ") + row["NAME_2"]


def load_or_fetch(band: str, bounds) -> tuple[np.ndarray, Affine]:
    cache_arr = CACHE_DIR / f"{band}.npy"
    cache_tf = CACHE_DIR / "transform.json"
    if cache_arr.exists() and cache_tf.exists():
        arr = np.load(cache_arr)
        wt = Affine(*json.load(open(cache_tf)))
        return arr, wt

    url = f"{BASE_URL}/Hansen_{GFC_VERSION}_{band}_{TILE}.tif"
    print(f"  mengambil {band} via GET (vsicurl, tanpa login) ...")
    with rasterio.open(url) as src:
        win = from_bounds(*bounds, transform=src.transform)
        wt = src.window_transform(win)
        arr = src.read(1, window=win)
    np.save(cache_arr, arr)
    if not cache_tf.exists():
        json.dump(list(wt)[:6], open(cache_tf, "w"))
    return arr, wt


def main():
    print(f"Membaca batas kabupaten dari {KAB_SRC} ...")
    kab = gpd.read_file(KAB_SRC, engine="pyogrio")
    kab = kab[["fid", "NAME_2", "TYPE_2", "geometry"]].copy()
    kab["nama"] = kab.apply(label_kab, axis=1)
    bounds = tuple(kab.total_bounds)

    print("Memuat band Hansen GFC (loss, treecover2000, gain) ...")
    lossyear, wt = load_or_fetch("lossyear", bounds)
    treecover2000, _ = load_or_fetch("treecover2000", bounds)
    gain, _ = load_or_fetch("gain", bounds)

    print("Rasterize batas kabupaten ke grid yang sama ...")
    shapes = [(geom, int(fid)) for geom, fid in zip(kab.geometry, kab["fid"])]
    kab_raster = rasterize(shapes, out_shape=lossyear.shape, transform=wt, fill=0, dtype="int32")

    print("Hitung luas piksel per baris (ha, disesuaikan garis lintang) ...")
    nrows = lossyear.shape[0]
    row_lat = np.array([wt * (0, r + 0.5) for r in range(nrows)])[:, 1]
    px_w_deg, px_h_deg = wt.a, -wt.e
    row_area_ha = (px_w_deg * DEG_M * np.cos(np.radians(row_lat))) * (px_h_deg * DEG_M) / 10_000.0

    forest_baseline = treecover2000 >= FOREST_THRESHOLD
    in_jatim = kab_raster > 0
    nama_by_fid = dict(zip(kab["fid"], kab["nama"]))
    fids = list(nama_by_fid.keys())

    def sum_per_kab(mask: np.ndarray) -> dict[int, float]:
        rows, cols = np.nonzero(mask)
        fid_sel = kab_raster[rows, cols]
        area_sel = row_area_ha[rows]
        s = pd.Series(area_sel).groupby(fid_sel).sum()
        return s.to_dict()

    print("Menghitung loss & tutupan tersisa per tahun (2020-2024) ...")
    loss_per_kab_year: dict[int, dict[int, float]] = {fid: {} for fid in fids}
    tutupan_per_kab_year: dict[int, dict[int, float]] = {fid: {} for fid in fids}

    for year in TAHUN_LIST:
        loss_mask = forest_baseline & in_jatim & (lossyear == (year - 2000))
        remaining_mask = forest_baseline & in_jatim & ~((lossyear > 0) & (lossyear <= (year - 2000)))

        loss_sums = sum_per_kab(loss_mask)
        rem_sums = sum_per_kab(remaining_mask)
        for fid in fids:
            loss_per_kab_year[fid][year] = round(float(loss_sums.get(fid, 0.0)), 3)
            tutupan_per_kab_year[fid][year] = round(float(rem_sums.get(fid, 0.0)), 3)
        print(f"  tahun {year}: loss total {sum(loss_sums.values()):,.1f} ha | tutupan tersisa total {sum(rem_sums.values()):,.1f} ha")

    print(f"Menghitung gain (periode statis {GAIN_PERIOD_LABEL}) ...")
    gain_mask = (gain == 1) & in_jatim
    gain_sums = sum_per_kab(gain_mask)
    gain_per_kab = {fid: round(float(gain_sums.get(fid, 0.0)), 3) for fid in fids}
    print(f"  total gain {GAIN_PERIOD_LABEL}: {sum(gain_per_kab.values()):,.1f} ha")

    print("Menyusun output JSON ...")
    kab_out = {}
    for fid in fids:
        kab_out[str(int(fid))] = {
            "nama": nama_by_fid[fid],
            "loss_per_tahun_ha": {str(t): loss_per_kab_year[fid][t] for t in TAHUN_LIST},
            "tutupan_tersisa_per_tahun_ha": {str(t): tutupan_per_kab_year[fid][t] for t in TAHUN_LIST},
            "gain_ha": gain_per_kab[fid],
        }

    provinsi = {
        "loss_per_tahun_ha": {
            str(t): round(sum(loss_per_kab_year[fid][t] for fid in fids), 3) for t in TAHUN_LIST
        },
        "tutupan_tersisa_per_tahun_ha": {
            str(t): round(sum(tutupan_per_kab_year[fid][t] for fid in fids), 3) for t in TAHUN_LIST
        },
        "gain_ha": round(sum(gain_per_kab.values()), 3),
    }

    result = {
        "meta": {
            "sumber": "Hansen/UMD Global Forest Change (via Google Cloud Storage, publik, GET tanpa API key) -- dataset yang dipakai Global Forest Watch",
            "versi_dataset": GFC_VERSION,
            "threshold_tutupan_kanopi_persen": FOREST_THRESHOLD,
            "tahun_loss_dan_tutupan": TAHUN_LIST,
            "catatan_gain": (
                f"Band 'gain' Hansen/UMD hanya tersedia untuk periode statis {GAIN_PERIOD_LABEL} "
                "(tidak pernah diperbarui ke tahun lebih baru di versi dataset manapun), jadi "
                "TIDAK ADA data gain khusus 2020-2024. Nilai gain_ha di bawah ini mewakili "
                f"total periode {GAIN_PERIOD_LABEL}, bukan 2020-2024."
            ),
            "gain_periode": GAIN_PERIOD_LABEL,
        },
        "tahun": TAHUN_LIST,
        "provinsi": provinsi,
        "kabupaten": kab_out,
    }

    out_json = OUT_DIR / "hutan_gfw_2020_2024.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"  -> {out_json}")

    print("Menggabungkan ke geometri kabupaten (GeoJSON ringan untuk peta) ...")
    kab_geom = gpd.read_file(KAB_GEOM_LIGHT, engine="pyogrio")
    for t in TAHUN_LIST:
        kab_geom[f"loss_{t}"] = 0.0
        kab_geom[f"tutupan_{t}"] = 0.0
    kab_geom["gain_ha"] = 0.0

    for idx, row in kab_geom.iterrows():
        fid_key = str(int(row["fid"]))
        s = kab_out.get(fid_key)
        if s is None:
            continue
        for t in TAHUN_LIST:
            kab_geom.at[idx, f"loss_{t}"] = s["loss_per_tahun_ha"][str(t)]
            kab_geom.at[idx, f"tutupan_{t}"] = s["tutupan_tersisa_per_tahun_ha"][str(t)]
        kab_geom.at[idx, "gain_ha"] = s["gain_ha"]

    keep_cols = (
        ["fid", "nama"]
        + [f"loss_{t}" for t in TAHUN_LIST]
        + [f"tutupan_{t}" for t in TAHUN_LIST]
        + ["gain_ha", "geometry"]
    )
    out_geojson = OUT_DIR / "hutan_gfw_2020_2024.geojson"
    kab_geom[keep_cols].to_file(out_geojson, driver="GeoJSON")
    print(f"  -> {out_geojson} ({out_geojson.stat().st_size / 1024:.1f} KB)")

    print("Menulis CSV flat ...")
    flat_rows = []
    for fid in fids:
        nama = nama_by_fid[fid]
        for t in TAHUN_LIST:
            flat_rows.append(
                {
                    "fid": int(fid),
                    "kabupaten": nama,
                    "tahun": t,
                    "loss_ha": loss_per_kab_year[fid][t],
                    "tutupan_tersisa_ha": tutupan_per_kab_year[fid][t],
                    "gain_ha_2000_2012": gain_per_kab[fid],
                }
            )
    out_csv = OUT_DIR / "hutan_gfw_2020_2024.csv"
    pd.DataFrame(flat_rows).to_csv(out_csv, index=False)
    print(f"  -> {out_csv}")

    print("\nRingkasan provinsi Jawa Timur:")
    for t in TAHUN_LIST:
        print(
            f"  {t}: loss {provinsi['loss_per_tahun_ha'][str(t)]:,.1f} ha | "
            f"tutupan tersisa {provinsi['tutupan_tersisa_per_tahun_ha'][str(t)]:,.1f} ha"
        )
    print(f"  gain ({GAIN_PERIOD_LABEL}, statis): {provinsi['gain_ha']:,.1f} ha")
    print("\nSelesai.")


if __name__ == "__main__":
    main()
