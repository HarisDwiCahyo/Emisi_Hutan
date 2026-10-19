/* ============================================================
   App Emisi & Kawasan Hutan — WebGIS (baca GeoTIFF live, unit kabupaten)
   Emisi per kabupaten/kota = jumlah nilai piksel grid (Scope 1 & Scope 2)
   yang titik tengahnya jatuh di dalam poligon kabupaten. Tidak ada level
   grid — semua tampil & diagregasi di skala kabupaten.
   Kawasan hutan: layer poligon per jenis (dissolve), + statistik luas per
   jenis per kabupaten (hasil overlay, dihitung di build_hutan_data.py)
   ditampilkan saat kabupaten diklik.
   Butuh Leaflet + parseGeoraster.
   ============================================================ */
(function () {
  "use strict";

  // ---------------- KONFIG ----------------
  var SEKTOR = [
    { key: "foodbev",  nama: "Makanan & Minuman", kbli: "10 & 11", dot: "#c7a24a", f: "FoodBeverage" },
    { key: "tekstil",  nama: "Tekstil",            kbli: "13",      dot: "#6f9e8f", f: "Tekstil" },
    { key: "kimia",    nama: "Kimia",              kbli: "20",      dot: "#8b9a6e", f: "Kimia" },
    { key: "pupuk",    nama: "Pupuk",              kbli: "20",      dot: "#9487b5", f: "Pupuk" },
    { key: "besibaja", nama: "Besi & Baja",        kbli: "24",      dot: "#b56a5a", f: "BesiBaja" }
  ];
  function files(f) {
    return {
      s1: "data/GRID_Emisi_" + f + "_Jatim.tif",
      s2: "data/GRID_Emisi_" + f + "_Scope2_Jatim.tif"
    };
  }
  var MODES = [
    { key: "total", nama: "Total (S1+S2)", field: "emisi", unit: "t CO₂e" },
    { key: "s1",    nama: "Scope 1",       field: "s1",    unit: "t CO₂e" },
    { key: "s2",    nama: "Scope 2",       field: "s2",    unit: "t CO₂e" }
  ];
  var GEOJSON_URL = "data/kabupaten_jatim.geojson";
  var HUTAN_URL = "data/hutan_jatim.geojson";
  var HUTAN_STATS_URL = "data/hutan_stats_kabupaten.json";
  var GFW_STATS_URL = "data/hutan_gfw_2020_2024.json";

  // jenis kawasan hutan (deskripsi) -> warna
  var HUTAN_TYPES = [
    { key: "Hutan Lindung",            color: "#2f6b3f" },
    { key: "Hutan Produksi Tetap",     color: "#5c8f52" },
    { key: "Hutan Produksi Terbatas",  color: "#8fb87a" },
    { key: "Kawasan Konservasi",       color: "#2f6f6f" },
    { key: "Kawasan Konservasi Laut",  color: "#3f7ea8" },
    { key: "Tubuh Air",                color: "#7fb3d1" },
    { key: "Areal Penggunaan Lain",    color: "#c7b27a" },
    { key: "Tidak Terdefinisi",        color: "#9a927f" }
  ];
  function hutanColor(deskripsi) {
    for (var i = 0; i < HUTAN_TYPES.length; i++) if (HUTAN_TYPES[i].key === deskripsi) return HUTAN_TYPES[i].color;
    return "#9a927f";
  }

  // ---- ramp warna emisi (rendah→tinggi): hitam→merah→kuning terang ----
  var RAMP_EMISI = ["#0d0d0d", "#470c0c", "#991c1c", "#e0481f", "#f59a2b", "#ffe24a"];
  function hex(c) { return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)]; }
  function rampOf(arr) {
    var rgb = arr.map(hex);
    return function (t) {
      t = Math.max(0, Math.min(1, t));
      var seg = (rgb.length - 1) * t, i = Math.floor(seg), ff = seg - i;
      if (i >= rgb.length - 1) { i = rgb.length - 2; ff = 1; }
      var a = rgb[i], b = rgb[i + 1];
      return "rgb(" + Math.round(a[0] + (b[0] - a[0]) * ff) + "," +
                      Math.round(a[1] + (b[1] - a[1]) * ff) + "," +
                      Math.round(a[2] + (b[2] - a[2]) * ff) + ")";
    };
  }
  var rampEmisi = rampOf(RAMP_EMISI);

  var fmt = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });
  var fmt1 = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 1 });
  function fnum(n) { return fmt.format(n); }
  function fval(n) { if (n >= 1e9) return fmt1.format(n / 1e9) + " M"; if (n >= 1e6) return fmt1.format(n / 1e6) + " jt"; if (n >= 1e3) return fmt1.format(n / 1e3) + " rb"; return fmt.format(n); }
  function fha(n) { return fnum(Math.round(n)) + " ha"; }
  function fpct(n) { return fmt1.format(n) + "%"; }
  function isData(v, nd) { return v !== null && v !== undefined && isFinite(v) && v > 0 && v !== nd; }

  // ---------------- PETA ----------------
  var map = L.map("map", { zoomControl: false, minZoom: 6, maxZoom: 12, preferCanvas: true });
  L.control.zoom({ position: "bottomright" }).addTo(map);
  map.setView([-7.6, 112.8], 7);
  var renderer = L.canvas({ padding: 0.5 });
  map.createPane("hutanPane"); map.getPane("hutanPane").style.zIndex = 620;
  var hutanRenderer = L.svg({ pane: "hutanPane", padding: 0.5 });
  var bases = {
    osm: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap contributors", maxZoom: 19 }),
    sat: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri", maxZoom: 19 })
  };
  bases.osm.addTo(map);

  var readout = document.getElementById("readout");
  var loader = document.getElementById("loader");
  var layer = L.layerGroup().addTo(map);
  var activeKey = SEKTOR[0].key;
  var activeMode = MODES[0].key;

  var GEO = null;                 // FeatureCollection kabupaten/kota
  var featureLayers = {};         // fid -> leaflet layer (untuk zoom dari tabel)
  var HUTAN_GEO = null;           // FeatureCollection kawasan hutan (dissolve per jenis)
  var HUTAN_STATS = {};           // fid(string) -> {nama, luas_kab_ha, hutan:[...], luas_hutan_total_ha, persen_hutan_total}
  var hutanLayers = {};           // deskripsi -> L.geoJSON
  var GFW_STATS = null;           // { meta, tahun, provinsi, kabupaten: {fid: {...}} } -- Global Forest Watch (Hansen GFC), 2020-2024, dipakai di popup saja (bukan layer peta, biar ringan)

  // ---------------- MUAT 1 GEOTIFF ----------------
  function loadTif(url) {
    return fetch(url)
      .then(function (r) { if (!r.ok) throw new Error(r.status + " " + url); return r.arrayBuffer(); })
      .then(parseGeoraster)
      .then(function (gr) {
        var rows = gr.values[0], nd = gr.noDataValue, m = {};
        for (var r = 0; r < rows.length; r++)
          for (var c = 0; c < rows[r].length; c++) {
            var v = rows[r][c];
            if (isData(v, nd)) m[r + "_" + c] = v;
          }
        return { pw: gr.pixelWidth, ph: gr.pixelHeight, x0: gr.xmin, y0: gr.ymax, m: m };
      })
      .catch(function (e) { console.warn("lewati", url, e.message); return null; });
  }

  // ray-casting; menangani lubang (hole) per poligon
  function pointInRing(lng, lat, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if (((yi > lat) !== (yj > lat)) && (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }
  function pointInFeature(lng, lat, geom) {
    var polys = geom.type === "MultiPolygon" ? geom.coordinates : [geom.coordinates];
    for (var p = 0; p < polys.length; p++) {
      var rings = polys[p];
      if (pointInRing(lng, lat, rings[0])) {
        var inHole = false;
        for (var h = 1; h < rings.length; h++) { if (pointInRing(lng, lat, rings[h])) { inHole = true; break; } }
        if (!inHole) return true;
      }
    }
    return false;
  }

  function labelKab(p) { return (p.TYPE_2 === "Kota" ? "Kota " : "Kab. ") + p.NAME_2; }

  // ---------------- MUAT 1 SEKTOR → agregasi langsung ke kabupaten ----------------
  function muat(s) {
    var fl = files(s.f);
    return Promise.all([loadTif(fl.s1), loadTif(fl.s2)]).then(function (d) {
      var kabAgg = {};
      GEO.features.forEach(function (f) { kabAgg[f.properties.fid] = { s1: 0, s2: 0 }; });

      function accumulate(dd, key) {
        if (!dd) return;
        for (var k in dd.m) {
          var p = k.split("_"), r = +p[0], c = +p[1];
          var north = dd.y0 - r * dd.ph, west = dd.x0 + c * dd.pw;
          var lat = north - dd.ph / 2, lng = west + dd.pw / 2;
          for (var i = 0; i < GEO.features.length; i++) {
            if (pointInFeature(lng, lat, GEO.features[i].geometry)) {
              kabAgg[GEO.features[i].properties.fid][key] += dd.m[k];
              break;
            }
          }
        }
      }
      accumulate(d[0], "s1"); accumulate(d[1], "s2");

      var kab = GEO.features.map(function (f) {
        var fid = f.properties.fid, a = kabAgg[fid];
        var emisi = a.s1 + a.s2;
        return { fid: fid, nama: labelKab(f.properties), tipe: f.properties.TYPE_2, feature: f,
                 s1: a.s1, s2: a.s2, emisi: emisi, ada: emisi > 0 };
      });
      var mE = 0, m1 = 0, m2 = 0;
      kab.forEach(function (k) { if (k.emisi > mE) mE = k.emisi; if (k.s1 > m1) m1 = k.s1; if (k.s2 > m2) m2 = k.s2; });

      s.kab = kab;
      s.kabByFid = {}; kab.forEach(function (k) { s.kabByFid[k.fid] = k; });
      s.maxE = mE; s.maxS1 = m1; s.maxS2 = m2;
      s.totEmisi = kab.reduce(function (a, k) { return a + k.emisi; }, 0);
      s.totS1 = kab.reduce(function (a, k) { return a + k.s1; }, 0);
      s.totS2 = kab.reduce(function (a, k) { return a + k.s2; }, 0);
      s.kabNada = kab.filter(function (k) { return k.ada; }).length;
      return s;
    });
  }

  // ---------------- GEOJSON KABUPATEN ----------------
  function loadGeo() {
    return fetch(GEOJSON_URL)
      .then(function (r) { if (!r.ok) throw new Error(r.status + " " + GEOJSON_URL); return r.json(); })
      .then(function (gj) { GEO = gj; return gj; });
  }

  // ---------------- KAWASAN HUTAN: layer + statistik ----------------
  function loadHutan() {
    return Promise.all([
      fetch(HUTAN_URL).then(function (r) { if (!r.ok) throw new Error(r.status + " " + HUTAN_URL); return r.json(); }),
      fetch(HUTAN_STATS_URL).then(function (r) { if (!r.ok) throw new Error(r.status + " " + HUTAN_STATS_URL); return r.json(); })
    ]).then(function (d) {
      HUTAN_GEO = d[0]; HUTAN_STATS = d[1];
    }).catch(function (e) { console.warn("gagal muat kawasan hutan", e.message); HUTAN_GEO = null; HUTAN_STATS = {}; });
  }

  function hutanPopup(deskripsi, luasHa) {
    return '<div class="popup-eyebrow">Kawasan Hutan</div>' +
           '<div class="popup-rank">' + deskripsi + '</div>' +
           '<div class="popup-score">Total ' + fha(luasHa) + ' di Jawa Timur</div>';
  }

  function makeHutanLayer(type) {
    var fc = { type: "FeatureCollection", features: (HUTAN_GEO.features || []).filter(function (f) { return f.properties.deskripsi === type.key; }) };
    var totalLuas = 0;
    return L.geoJSON(fc, {
      pane: "hutanPane", renderer: hutanRenderer,
      style: { color: type.color, weight: 1, opacity: 0.85, fillColor: type.color, fillOpacity: 0.32 },
      onEachFeature: function (f, lyr) {
        lyr.bindPopup(hutanPopup(type.key, hutanTotals[type.key] || 0), { maxWidth: 260 });
        lyr.on("mouseover", function () { this.setStyle({ fillOpacity: 0.55, weight: 1.8 }); });
        lyr.on("mouseout", function () { this.setStyle({ fillOpacity: 0.32, weight: 1 }); });
      }
    });
  }

  function toggleHutan(type, on) {
    if (on) { if (!hutanLayers[type.key]) hutanLayers[type.key] = makeHutanLayer(type); hutanLayers[type.key].addTo(map); }
    else if (hutanLayers[type.key]) map.removeLayer(hutanLayers[type.key]);
  }

  // total luas per jenis (seluruh Jatim), dihitung dari stats per kabupaten
  var hutanTotals = {};
  function computeHutanTotals() {
    hutanTotals = {};
    Object.keys(HUTAN_STATS).forEach(function (fid) {
      (HUTAN_STATS[fid].hutan || []).forEach(function (h) {
        hutanTotals[h.deskripsi] = (hutanTotals[h.deskripsi] || 0) + h.luas_ha;
      });
    });
  }

  function buildHutanToc() {
    computeHutanTotals();
    var grandTotal = 0; Object.keys(hutanTotals).forEach(function (k) { grandTotal += hutanTotals[k]; });
    document.getElementById("hutanTotalLuas").textContent = "· " + fha(grandTotal);

    var el = document.getElementById("hutanList"); el.innerHTML = "";
    HUTAN_TYPES.forEach(function (type) {
      var lab = document.createElement("label");
      lab.className = "toc-item";
      lab.innerHTML =
        '<input type="checkbox">' +
        '<span class="toc-swatch" style="background:' + type.color + '"></span>' +
        '<span class="toc-nama">' + type.key + '</span>' +
        '<span class="toc-count">' + fha(hutanTotals[type.key] || 0) + '</span>';
      el.appendChild(lab);
      lab.querySelector("input").addEventListener("change", function () { toggleHutan(type, this.checked); });
    });
  }

  // ---------------- KAWASAN HUTAN GFW (Hansen/UMD, 2020-2024) ----------------
  // Dipakai di popup kabupaten saja (statistik dari JSON) -- tidak ada layer
  // peta/GeoJSON terpisah karena berat.
  function loadGfw() {
    return fetch(GFW_STATS_URL)
      .then(function (r) { if (!r.ok) throw new Error(r.status + " " + GFW_STATS_URL); return r.json(); })
      .then(function (d) { GFW_STATS = d; })
      .catch(function (e) { console.warn("gagal muat data GFW", e.message); GFW_STATS = null; });
  }

  // ---------------- POPUP KABUPATEN: emisi + kawasan hutan ----------------
  function hutanBreakdownHtml(fid) {
    var st = HUTAN_STATS[String(fid)];
    if (!st || !st.hutan || !st.hutan.length) {
      return '<div class="popup-sub"><div class="popup-eyebrow">Kawasan Hutan (ESDM · 2020)</div><p class="popup-empty">Tidak ada data kawasan hutan di wilayah ini.</p></div>';
    }
    var rows = st.hutan.map(function (h) {
      return '<tr><td><span class="hutan-dot" style="background:' + hutanColor(h.deskripsi) + '"></span>' + h.deskripsi + '</td>' +
             '<td>' + fha(h.luas_ha) + '</td><td>' + fpct(h.persen) + '</td></tr>';
    }).join("");
    return '<div class="popup-sub">' +
           '<div class="popup-eyebrow">Kawasan Hutan (ESDM · 2020) · ' + fpct(st.persen_hutan_total) + ' dari luas wilayah</div>' +
           '<table class="popup-hutan-tab">' + rows + '</table>' +
           '</div>';
  }

  function gfwBreakdownHtml(fid) {
    if (!GFW_STATS) return "";
    var st = GFW_STATS.kabupaten[String(fid)];
    if (!st) return "";
    var tahunList = GFW_STATS.tahun || [2020, 2021, 2022, 2023, 2024];
    var rows = tahunList.map(function (t) {
      var y = String(t);
      var tutupan = st.tutupan_tersisa_per_tahun_ha[y] || 0;
      var loss = st.loss_per_tahun_ha[y] || 0;
      return '<tr><td>' + t + '</td><td>' + fha(tutupan) + '</td><td>' + fha(loss) + '</td></tr>';
    }).join("");
    return '<div class="popup-sub">' +
           '<div class="popup-eyebrow">Tutupan Hutan (Global Forest Watch)</div>' +
           '<table class="popup-hutan-tab">' +
             '<tr><th>Tahun</th><th>Tutupan tersisa</th><th>Loss</th></tr>' +
             rows +
           '</table>' +
           '<p class="popup-empty">Gain (periode statis 2000-2012): ' + fha(st.gain_ha) + '</p>' +
           '</div>';
  }

  function openKab(s, k) {
    var c = featureLayers[k.fid] ? featureLayers[k.fid].getBounds().getCenter() : map.getCenter();
    L.popup({ maxWidth: 290 }).setLatLng(c).setContent(
      '<div class="popup-eyebrow">' + s.nama + ' · KBLI ' + s.kbli + '</div>' +
      '<div class="popup-rank">' + k.nama + '</div>' +
      '<div class="popup-score">' + (k.rank ? 'Peringkat #' + k.rank + ' dari ' + s.kabNada : 'Tanpa data sektor ini') + '</div>' +
      '<table class="popup-tab">' +
        '<tr><td>Emisi Scope 1</td><td>' + fval(k.s1) + ' t</td></tr>' +
        '<tr><td>Emisi Scope 2</td><td>' + fval(k.s2) + ' t</td></tr>' +
        '<tr><td>Total (S1+S2)</td><td>' + fval(k.emisi) + ' t</td></tr>' +
      '</table>' +
      hutanBreakdownHtml(k.fid) +
      gfwBreakdownHtml(k.fid)
    ).openOn(map);
  }

  // ---------------- RENDER KABUPATEN (choropleth emisi) ----------------
  function render() {
    var s = sektorByKey(activeKey), mode = modeByKey(activeMode);
    layer.clearLayers();
    featureLayers = {};

    var vals = s.kab.filter(function (k) { return k.ada && k[mode.field] > 0; }).map(function (k) { return k[mode.field]; });
    var mn = vals.length ? Math.min.apply(null, vals) : 0, mx = vals.length ? Math.max.apply(null, vals) : 1;
    var lmin = Math.log(mn || 1), lmax = Math.log(mx || 1), span = (lmax - lmin) || 1;

    var ranked = s.kab.filter(function (k) { return k.ada; }).slice().sort(function (a, b) { return b[mode.field] - a[mode.field]; });
    ranked.forEach(function (k, i) { k.rank = i + 1; });
    s.kab.forEach(function (k) { if (!k.ada) k.rank = null; });

    function colorFor(k) {
      var v = k[mode.field];
      if (!k.ada || v <= 0) return "#d9d6c8";
      var t = (Math.log(v) - lmin) / span;
      return rampEmisi(t);
    }

    var gl = L.geoJSON(GEO, {
      renderer: renderer,
      style: function (f) {
        var k = s.kabByFid[f.properties.fid];
        var col = colorFor(k);
        return { color: "#5b5c4e", weight: 0.8, opacity: 0.7, fillColor: col, fillOpacity: k.ada ? 0.82 : 0.18 };
      },
      onEachFeature: function (f, lyr) {
        var k = s.kabByFid[f.properties.fid];
        featureLayers[f.properties.fid] = lyr;
        lyr.on("mouseover", function () {
          this.setStyle({ weight: 2.2, color: "#33332b", fillOpacity: 0.95 }); this.bringToFront();
          readout.innerHTML = '<div class="r-lab">' + k.nama + (k.rank ? ' · peringkat #' + k.rank : '') + '</div>' +
            '<div class="r-val">' + fval(k[mode.field]) + ' <span class="r-unit">' + mode.unit + '</span></div>';
          readout.hidden = false;
        });
        lyr.on("mouseout", function () { gl.resetStyle(this); readout.hidden = true; });
        lyr.on("click", function () { openKab(s, k); });
      }
    }).addTo(layer);

    updateKPI(s, mode); updateLegend(mode, mn, mx); renderTable(s, mode, ranked);
    var b = gl.getBounds();
    if (b.isValid()) map.fitBounds(b.pad(0.05), { animate: false });
  }

  function sektorByKey(k) { for (var i = 0; i < SEKTOR.length; i++) if (SEKTOR[i].key === k) return SEKTOR[i]; }
  function modeByKey(k) { for (var i = 0; i < MODES.length; i++) if (MODES[i].key === k) return MODES[i]; }

  function updateKPI(s, mode) {
    var elT = document.getElementById("kpiTotal"), elC = document.getElementById("kpiCells"), elM = document.getElementById("kpiMax");
    var sum = mode.key === "total" ? s.totEmisi : mode.key === "s1" ? s.totS1 : s.totS2;
    var mx = mode.key === "total" ? s.maxE : mode.key === "s1" ? s.maxS1 : s.maxS2;
    document.getElementById("kpiTotalLabel").textContent = "Total " + mode.nama;
    document.getElementById("kpiAltLabel").textContent = "Kab./Kota Berdata";
    document.getElementById("kpiMaxLabel").textContent = "Wilayah Tertinggi";
    document.getElementById("kpiUnit").textContent = mode.unit;
    elT.textContent = fval(sum);
    elC.textContent = fnum(s.kabNada) + " / " + fnum(s.kab.length);
    elM.textContent = fval(mx);
  }

  function updateLegend(mode, mn, mx) {
    document.getElementById("legendMode").textContent = mode.nama;
    document.getElementById("legMin").textContent = fval(mn);
    document.getElementById("legMax").textContent = fval(mx);
    var grad = [];
    for (var i = 0; i <= 10; i++) grad.push(rampEmisi(i / 10) + " " + (i * 10) + "%");
    document.getElementById("legendBar").style.background = "linear-gradient(90deg," + grad.join(",") + ")";
  }

  function renderTable(s, mode, ranked) {
    document.getElementById("rankSektor").textContent = "· " + s.nama;
    var body = document.getElementById("rankBody");
    body.innerHTML = "";
    ranked.forEach(function (k) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="rk">' + k.rank + '</td>' +
        '<td class="loc">' + k.nama + '</td>' +
        '<td>' + fval(k.s1) + '</td>' +
        '<td>' + fval(k.s2) + '</td>' +
        '<td class="sc">' + fval(k.emisi) + '</td>';
      tr.addEventListener("click", function () {
        var lyr = featureLayers[k.fid];
        if (lyr) map.fitBounds(lyr.getBounds().pad(0.1), { animate: true });
        openKab(s, k);
      });
      body.appendChild(tr);
    });
  }

  // ---------------- UI: MODE & SEKTOR ----------------
  function buildModes() {
    var el = document.getElementById("modeList"); el.innerHTML = "";
    MODES.forEach(function (m) {
      var b = document.createElement("button");
      b.className = "mode-btn" + (m.key === activeMode ? " active" : "");
      b.dataset.mode = m.key; b.textContent = m.nama;
      b.addEventListener("click", function () {
        activeMode = m.key;
        Array.prototype.forEach.call(el.children, function (x) { x.classList.toggle("active", x.dataset.mode === m.key); });
        render();
      });
      el.appendChild(b);
    });
  }

  function buildList() {
    var listEl = document.getElementById("sektorList"); listEl.innerHTML = "";
    SEKTOR.forEach(function (s, idx) {
      var li = document.createElement("li");
      li.className = "sektor-item" + (s.key === activeKey ? " active" : "");
      li.dataset.key = s.key;
      li.innerHTML =
        '<span class="sektor-num">' + String(idx + 1).padStart(2, "0") + '</span>' +
        '<span class="sektor-dot" style="background:' + s.dot + '"></span>' +
        '<span class="sektor-meta">' +
          '<span class="sektor-nama">' + s.nama + '</span>' +
          '<span class="sektor-sub">KBLI ' + s.kbli + ' · ' + fnum(s.kabNada) + ' kab. berdata</span>' +
        '</span>' +
        '<span class="sektor-total">' + fval(s.totEmisi) + '</span>';
      li.addEventListener("click", function () { select(s.key); });
      listEl.appendChild(li);
    });
  }

  function select(key) {
    activeKey = key;
    var listEl = document.getElementById("sektorList");
    Array.prototype.forEach.call(listEl.children, function (li) { li.classList.toggle("active", li.dataset.key === key); });
    render();
    if (window.innerWidth <= 720) document.getElementById("panel").classList.remove("hidden");
  }

  // ---------------- BASEMAP & TOGGLE ----------------
  document.getElementById("basemaps").addEventListener("click", function (e) {
    var btn = e.target.closest("button"); if (!btn) return;
    Object.keys(bases).forEach(function (k) { map.removeLayer(bases[k]); });
    bases[btn.dataset.base].addTo(map);
    Array.prototype.forEach.call(this.children, function (b) { b.classList.toggle("active", b === btn); });
  });
  document.getElementById("panelToggle").addEventListener("click", function () {
    document.getElementById("panel").classList.toggle("hidden");
  });

  // ---------------- BOOT ----------------
  loadGeo()
    .then(function () { return Promise.all([Promise.all(SEKTOR.map(muat)), loadHutan(), loadGfw()]); })
    .then(function () {
      loader.classList.add("hidden");
      buildModes(); buildList(); buildHutanToc(); select(activeKey);
    })
    .catch(function (err) {
      loader.textContent = "Gagal memuat data. Jalankan lewat local server lalu muat ulang.";
      loader.style.color = "#b56a5a"; console.error(err);
    });
})();
