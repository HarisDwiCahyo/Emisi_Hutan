"""
Preprocessing: hitung statistik luas kawasan hutan per kabupaten (per jenis),
dan siapkan versi ringan untuk ditampilkan di peta (dissolve per jenis +
simplifikasi geometri, karena 593 fitur asli totalnya 118MB akibat vertex
sangat padat).

Input:
    ../Hutan_Jawa_Timur_dari_API.geojson   (593 fitur, kode_prov=35)
    ../Kabupaten_JawaTimur.full.geojson    (38 kab/kota Jatim)

Output (ke ../data/):
    hutan_jatim.geojson           -> dissolve per 'deskripsi', disederhanakan,
                                      dipakai layer peta "Kawasan Hutan"
    kabupaten_jatim.geojson       -> batas kab/kota, disederhanakan
    hutan_stats_kabupaten.json    -> { fid: {nama, tipe, luas_kab_ha,
                                        hutan: [{deskripsi, luas_ha, persen}],
                                        luas_hutan_total_ha, persen_hutan_total} }

Proyeksi untuk hitung luas: EPSG:32749 (UTM 49S) -- cukup akurat utk
seluruh Jawa Timur (sedikit distorsi di ujung timur dekat Banyuwangi,
diabaikan karena hanya dipakai relatif per kabupaten).
"""

import json
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
HUTAN_SRC = ROOT / "Hutan_Jawa_Timur_dari_API.geojson"
KAB_SRC = ROOT / "Kabupaten_JawaTimur.full.geojson"
OUT_DIR = ROOT / "data"
OUT_DIR.mkdir(exist_ok=True)

UTM = "EPSG:32749"
SIMPLIFY_HUTAN_DEG = 0.0008   # ~80m, cukup utk tampilan peta skala provinsi
SIMPLIFY_KAB_DEG = 0.0003     # ~30m, batas kab masih perlu agak detail


def label_kab(row):
    return ("Kota " if row["TYPE_2"] == "Kota" else "Kab. ") + row["NAME_2"]


def main():
    print("Membaca kawasan hutan ...")
    hutan = gpd.read_file(HUTAN_SRC, engine="pyogrio")
    hutan["deskripsi"] = hutan["deskripsi"].fillna("Tidak Terdefinisi")
    print(f"  {len(hutan)} fitur, jenis: {sorted(hutan['deskripsi'].unique())}")

    print("Membaca batas kabupaten ...")
    kab = gpd.read_file(KAB_SRC, engine="pyogrio")
    kab = kab[["fid", "NAME_1", "NAME_2", "TYPE_2", "geometry"]].copy()
    kab["nama"] = kab.apply(label_kab, axis=1)

    print("Reproject ke UTM 49S untuk hitung luas ...")
    hutan_utm = hutan.to_crs(UTM)
    kab_utm = kab.to_crs(UTM)
    kab_utm["luas_kab_ha"] = kab_utm.geometry.area / 10000.0

    print("Overlay hutan x kabupaten (intersection) ...")
    overlay = gpd.overlay(
        hutan_utm[["deskripsi", "geometry"]],
        kab_utm[["fid", "nama", "luas_kab_ha", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    overlay["luas_ha"] = overlay.geometry.area / 10000.0
    overlay = overlay[overlay["luas_ha"] > 0.001]

    print("Menyusun statistik per kabupaten ...")
    stats = {}
    for fid, grp in overlay.groupby("fid"):
        row0 = grp.iloc[0]
        luas_kab = float(row0["luas_kab_ha"])
        by_tipe = (
            grp.groupby("deskripsi")["luas_ha"].sum().sort_values(ascending=False)
        )
        hutan_list = []
        total_hutan = 0.0
        for tipe, luas in by_tipe.items():
            luas = float(luas)
            total_hutan += luas
            hutan_list.append(
                {
                    "deskripsi": tipe,
                    "luas_ha": round(luas, 2),
                    "persen": round(100.0 * luas / luas_kab, 2) if luas_kab else 0,
                }
            )
        stats[str(int(fid))] = {
            "nama": row0["nama"],
            "luas_kab_ha": round(luas_kab, 2),
            "hutan": hutan_list,
            "luas_hutan_total_ha": round(total_hutan, 2),
            "persen_hutan_total": round(100.0 * total_hutan / luas_kab, 2) if luas_kab else 0,
        }

    # kabupaten tanpa singgungan hutan sama sekali tetap dicatat (0%)
    for _, row in kab_utm.iterrows():
        key = str(int(row["fid"]))
        if key not in stats:
            stats[key] = {
                "nama": row["nama"],
                "luas_kab_ha": round(float(row["luas_kab_ha"]), 2),
                "hutan": [],
                "luas_hutan_total_ha": 0,
                "persen_hutan_total": 0,
            }

    with open(OUT_DIR / "hutan_stats_kabupaten.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=None)
    print(f"  -> {OUT_DIR / 'hutan_stats_kabupaten.json'}")

    print("Membuat layer peta kawasan hutan (dissolve per jenis + simplify) ...")
    hutan_dissolve = hutan.dissolve(by="deskripsi", as_index=False)[["deskripsi", "geometry"]]
    hutan_dissolve["geometry"] = hutan_dissolve.geometry.simplify(
        SIMPLIFY_HUTAN_DEG, preserve_topology=True
    )
    hutan_dissolve.to_file(OUT_DIR / "hutan_jatim.geojson", driver="GeoJSON")
    print(f"  -> {OUT_DIR / 'hutan_jatim.geojson'} ({len(hutan_dissolve)} fitur)")

    print("Membuat layer batas kabupaten (simplify) ...")
    kab_light = kab[["fid", "nama", "NAME_2", "TYPE_2", "geometry"]].copy()
    kab_light["geometry"] = kab_light.geometry.simplify(
        SIMPLIFY_KAB_DEG, preserve_topology=True
    )
    kab_light.to_file(OUT_DIR / "kabupaten_jatim.geojson", driver="GeoJSON")
    print(f"  -> {OUT_DIR / 'kabupaten_jatim.geojson'} ({len(kab_light)} fitur)")

    print("Selesai.")


if __name__ == "__main__":
    main()
