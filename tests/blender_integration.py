"""Integrationstest mit Blender-Python (pip install bpy oder blender -b --python).

    python3 tests/blender_integration.py
"""

import os
import sys
import tempfile

import bpy  # noqa: I001  (bpy muss vor bmesh importiert werden)
import bmesh

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import liner_wandstaerke  # noqa: E402
from liner_wandstaerke import ui  # noqa: E402
from test_core import make_record  # noqa: E402

tmp = tempfile.mkdtemp()
ui.DB_OVERRIDE = os.path.join(tmp, "test.sqlite")
liner_wandstaerke.register()
ui._init_scenes()
st = bpy.context.scene.liner_settings
assert len(st.rows) == 7, len(st.rows)

# 1) Liner erfassen und speichern
st.hersteller, st.artikel, st.groesse, st.form, st.nenn_mm = "Alpha", "Classic", "26", "konisch", 3.0
ui.set_row_values(st.rows[2], (7, 4, 9, 3))
assert bpy.ops.liner.build() == {"FINISHED"}
assert bpy.ops.liner.save_record() == {"FINISHED"}
first = st.current_id
assert first == 1

# Speichern ohne Hersteller/Artikel wird abgelehnt
bpy.ops.liner.new_record()
assert st.current_id == 0
try:
    res = bpy.ops.liner.save_record()
except RuntimeError:
    res = {"CANCELLED"}
assert res == {"CANCELLED"}, res

# 1b) variable Punktzahl je Höhe
bpy.ops.liner.new_record()
st.hersteller, st.artikel = "Delta", "Varia"
st.active_index = 2
row = st.rows[2]
ui.set_row_values(row, (7, 4, 9, 3))
row.n_points = 8                                   # umrechnen 4 -> 8
vals = ui.row_values(row)
assert len(vals) == 8 and [round(v, 4) for v in vals[::2]] == [7, 4, 9, 3], vals
row.n_points = 4                                   # zurück: Messwerte bleiben erhalten
assert [round(v, 4) for v in ui.row_values(row)] == [7, 4, 9, 3]
row.n_points = 1
assert len(row.values) == 1
# Einfügen aus Text (Excel-Spalte, Dezimalkomma)
assert bpy.ops.liner.paste_values(text="5,5\n6\n6,5\n7\n6,5\n6") == {"FINISHED"}
assert ui.row_values(row) == [5.5, 6.0, 6.5, 7.0, 6.5, 6.0] and row.n_points == 6
assert ui.parse_numbers("4.5,5,6") == [4.5, 5.0, 6.0]
assert ui.parse_numbers("4,5; 5; 6") == [4.5, 5.0, 6.0]
# 360 Punkte in einer Höhe, 1 Punkt in einer anderen
st.active_index = 4
bpy.ops.liner.paste_values(text=" ".join("%.2f" % (4 + (k % 90) / 90) for k in range(360)))
assert st.rows[4].n_points == 360
ui.set_row_values(st.rows[0], [6.0])
import time
t0 = time.time()
assert bpy.ops.liner.build() == {"FINISHED"}
print("Build mit 1/4/6/360 Punkten: %.2f s" % (time.time() - t0))
assert len(bpy.data.collections["Liner-Messpunkte"].objects) <= 7 * 36
assert bpy.ops.liner.save_record() == {"FINISHED"}
delta_id = st.current_id
# CSV der Erfassung: Export und Re-Import
ed_csv = os.path.join(tmp, "delta.csv")
bpy.ops.liner.export_csv(filepath=ed_csv)
before = ui._rows_as_tuples(st)
bpy.ops.liner.new_record()
assert bpy.ops.liner.import_csv(filepath=ed_csv) == {"FINISHED"}
after = ui._rows_as_tuples(st)
assert [len(t) for _, t, _ in after] == [len(t) for _, t, _ in before]
assert st.hersteller == "Delta"
# Punktzahl für alle Höhen
bpy.ops.liner.set_points_all(n_points=12)
assert all(r.n_points == 12 and len(r.values) == 12 for r in st.rows)
with ui.open_db() as db:
    db.delete(delta_id)

# 2) weitere Liner direkt in die DB
with ui.open_db() as db:
    db.save(make_record("Alpha", "28", form="zylindrisch", nenn=6, offset=(2, 2, 2, 2), circ_add=2))
    db.save(make_record("Beta", "26", form="konisch", offset=(0.3, 0.1, 0.4, 0.2)))
    db.save(make_record("Gamma", "M", form="anatomisch", offset=(-1, 0.5, 1, 0), circ_add=-1))
ui._invalidate_suggestions()
ui._refresh_list(bpy.context)
assert len(st.db_items) == 4, len(st.db_items)

# Filter
st.f_hersteller = "Alpha"
assert len(st.db_items) == 2
st.f_wand_min = 5.0
assert [it.groesse for it in st.db_items] == ["28"]
bpy.ops.liner.db_clear_filter()
assert len(st.db_items) == 4
st.f_form = "konisch"
assert sorted(it.name for it in st.db_items) == ["Alpha Classic Gr. 26", "Beta Beta Liner Gr. 26"]
bpy.ops.liner.db_clear_filter()
# Suchvorschläge
assert ui._suggestions("hersteller") == ["Alpha", "Beta", "Gamma"]

# 3) Laden zum Bearbeiten
st.db_index = [it.db_id for it in st.db_items].index(first)
assert bpy.ops.liner.db_load() == {"FINISHED"}
assert (st.hersteller, st.current_id, ui.row_values(st.rows[2])) == ("Alpha", first, [7, 4, 9, 3])

# 4) Referenz + Ähnlichkeitssuche
st.db_index = [it.name for it in st.db_items].index("Beta Beta Liner Gr. 26")
beta_id = st.db_items[st.db_index].db_id
bpy.ops.liner.db_set_ref()
assert st.ref_id == beta_id
assert bpy.ops.liner.db_similar() == {"FINISHED"}
order = [it.db_id for it in st.db_items]
scores = [round(it.score) for it in st.db_items]
print("Ähnlichkeit zu Beta:", list(zip([it.name for it in st.db_items], scores)))
assert order[0] == beta_id and scores == sorted(scores, reverse=True)

# 5) Vergleich
bpy.ops.liner.db_select(action="ALL")
assert len(ui._sel_ids(st)) == 4
assert bpy.ops.liner.compare() == {"FINISHED"}
coll = bpy.data.collections["Liner-Vergleich"]
meshes = [o for o in coll.objects if o.type == "MESH"]
assert len(meshes) == 4 + 3, len(meshes)
for ob in meshes:
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    assert all(e.is_manifold for e in bm.edges), ob.name
    assert bm.calc_volume(signed=True) > 0, ob.name
    bm.free()
assert len(st.results) == 3
for r in st.results:
    print("  #%d %-28s %5.1f %% (Wand %5.1f, Form %5.1f) MAE %.2f max %+.2f @ %s"
          % (r.slot + 1, r.name, r.score, r.s_wall, r.s_form, r.mae, r.maxd, r.where))
# Wiederholen ersetzt den alten Vergleich
bpy.ops.liner.compare()
assert len([o for o in coll.objects if o.type == "MESH"]) == 7

# 6) Bericht
path = os.path.join(tmp, "bericht.html")
assert bpy.ops.liner.report(filepath=path, open_browser=False) == {"FINISHED"}
assert os.path.getsize(path) > 10000
print("Bericht:", path)

# 7) Export / Import der Datenbank
csv_path = os.path.join(tmp, "db.csv")
bpy.ops.liner.db_export(filepath=csv_path)
bpy.ops.liner.db_import(filepath=csv_path)
assert st.db_count == 8

# 8) Löschen
st.db_index = 0
bpy.ops.liner.db_delete()
assert st.db_count == 7

bpy.ops.liner.compare_clear()
liner_wandstaerke.unregister()
print("OK", tmp)
