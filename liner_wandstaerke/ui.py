"""Blender-Oberfläche: Erfassung, Datenbank, Vergleich."""

import csv
import json
import math
import os
import pathlib
import webbrowser

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import colors
from .compare import build_comparison, model_from_record, rank_similar, vertex_differences
from .database import LinerDB, empty_record, record_label
from .geometry import SIDES, LinerModel, build_liner_geometry, marker_positions
from .report import write_report

ADDON_ID = __package__

# (Höhe cm, Wandstärke mm [A, M, P, L], Umfang cm) – Beispielwerte, bitte ersetzen
DEFAULT_ROWS = (
    (4.0, (6.0, 6.0, 6.0, 6.0), 24.0),
    (8.0, (5.5, 5.5, 5.5, 5.5), 27.0),
    (12.0, (5.0, 5.0, 5.0, 5.0), 29.5),
    (16.0, (4.5, 4.5, 4.5, 4.5), 31.5),
    (20.0, (4.0, 4.0, 4.0, 4.0), 33.0),
    (25.0, (3.5, 3.5, 3.5, 3.5), 34.5),
    (30.0, (3.0, 3.0, 3.0, 3.0), 36.0),
)

OBJECT_NAME = "Liner"
MARKER_COLLECTION = "Liner-Messpunkte"
COMPARE_COLLECTION = "Liner-Vergleich"
THICKNESS_ATTR = "Wandstaerke_mm"
DIFF_ATTR = "Differenz_mm"
COLOR_ATTR = "Wandstaerke_Farbe"
DEFAULT_DB = os.path.join(os.path.expanduser("~"), "liner_datenbank.sqlite")

# Wird von Tests gesetzt, wenn keine Add-on-Einstellungen existieren
DB_OVERRIDE = None


# ---------------------------------------------------------------------------
# Datenbank-Hilfen
# ---------------------------------------------------------------------------

def db_path(context=None):
    if DB_OVERRIDE:
        return DB_OVERRIDE
    context = context or bpy.context
    try:
        p = context.preferences.addons[ADDON_ID].preferences.db_path
    except (KeyError, AttributeError):
        p = ""
    return bpy.path.abspath(p) if p else DEFAULT_DB


def open_db(context=None):
    return LinerDB(db_path(context))


_SUGGEST = {}


def _invalidate_suggestions():
    _SUGGEST.clear()


def _suggestions(field):
    if field not in _SUGGEST:
        try:
            with open_db() as db:
                _SUGGEST[field] = db.distinct(field)
        except Exception:
            _SUGGEST[field] = []
    return _SUGGEST[field]


def _search(field):
    def fn(self, context, edit_text):
        t = (edit_text or "").lower()
        return [v for v in _suggestions(field) if t in v.lower()]
    return fn


# ---------------------------------------------------------------------------
# Daten (werden in der .blend-Datei gespeichert)
# ---------------------------------------------------------------------------

def _on_db_path(self, context):
    _invalidate_suggestions()
    _refresh_all(context)


class LINER_AP_prefs(AddonPreferences):
    bl_idname = ADDON_ID

    db_path: StringProperty(
        name="Datenbank", subtype="FILE_PATH", default=DEFAULT_DB,
        description="SQLite-Datei der Liner-Datenbank (wird bei Bedarf angelegt)",
        update=_on_db_path)

    def draw(self, context):
        self.layout.prop(self, "db_path")


class LINER_PG_row(PropertyGroup):
    height_cm: FloatProperty(name="Höhe", description="Höhe von distal in cm",
                             default=4.0, min=0.0, soft_max=60.0, precision=1)
    thickness_mm: FloatVectorProperty(
        name="Wandstärke", size=4, default=(5.0, 5.0, 5.0, 5.0),
        min=0.0, soft_max=30.0, precision=1,
        description="Wandstärke in mm: Anterior, Medial, Posterior, Lateral")
    circumference_cm: FloatProperty(
        name="Umfang", description="Umfang in dieser Höhe in cm",
        default=30.0, min=1.0, soft_max=80.0, precision=1)


_REFRESHING = False


def _sel_ids(st):
    return [int(x) for x in st.sel_ids.split(",") if x.strip()]


def _set_sel_ids(st, ids):
    st.sel_ids = ",".join(str(i) for i in ids)


def _on_select(self, context):
    if _REFRESHING:
        return
    st = context.scene.liner_settings
    ids = _sel_ids(st)
    if self.select and self.db_id not in ids:
        ids.append(self.db_id)
    elif not self.select and self.db_id in ids:
        ids.remove(self.db_id)
    _set_sel_ids(st, ids)


class LINER_PG_dbitem(PropertyGroup):
    db_id: IntProperty()
    groesse: StringProperty()
    form: StringProperty()
    wand_mm: FloatProperty()
    nenn: BoolProperty()
    score: FloatProperty(default=-1.0)
    select: BoolProperty(name="Vergleichen", description="Für den Vergleich auswählen",
                         update=_on_select)


class LINER_PG_result(PropertyGroup):
    slot: IntProperty()
    score: FloatProperty()
    s_wall: FloatProperty()
    s_form: FloatProperty()
    mae: FloatProperty()
    bias: FloatProperty()
    maxd: FloatProperty()
    where: StringProperty()
    valid: BoolProperty(default=True)


def _on_filter(self, context):
    _refresh_list(context)


class LINER_PG_settings(PropertyGroup):
    # --- Erfassung ---
    rows: CollectionProperty(type=LINER_PG_row)
    active_index: IntProperty(default=0)
    current_id: IntProperty(default=0, description="ID des geladenen Datensatzes (0 = neu)")
    hersteller: StringProperty(name="Hersteller", search=_search("hersteller"))
    artikel: StringProperty(name="Artikel", search=_search("artikel"))
    groesse: StringProperty(name="Größe", search=_search("groesse"))
    form: StringProperty(name="Form", search=_search("form"),
                         description="z. B. zylindrisch, konisch, anatomisch")
    material: StringProperty(name="Material", search=_search("material"))
    notiz: StringProperty(name="Notiz")
    nenn_mm: FloatProperty(name="Nenn-Wandstärke", min=0.0, soft_max=20.0, precision=1,
                           description="Wandstärke laut Hersteller in mm (0 = unbekannt)")
    side: EnumProperty(
        name="Seite",
        items=(("RIGHT", "Rechts", "Rechtes Bein: medial = +X"),
               ("LEFT", "Links", "Linkes Bein: medial = -X")),
        default="RIGHT")
    circ_reference: EnumProperty(
        name="Umfang bezogen auf",
        items=(("INNER", "Innen (Stumpf)", "Umfang des Stumpfes / Liner-Innenseite"),
               ("OUTER", "Außen (Liner)", "Umfang außen über dem Liner gemessen")),
        default="INNER")
    distal_mm: FloatProperty(name="Distale Wandstärke", description="Wandstärke am distalen Ende in mm",
                             default=8.0, min=0.5, soft_max=30.0, precision=1)
    total_length_cm: FloatProperty(name="Gesamtlänge", description="Länge des Liners in cm (mind. höchste Messhöhe)",
                                   default=32.0, min=1.0, soft_max=60.0, precision=1)
    # --- Darstellung ---
    segments: IntProperty(name="Segmente", description="Unterteilungen am Umfang",
                          default=64, min=8, max=256)
    step_mm: FloatProperty(name="Ringabstand", description="Abstand der Ringe in mm",
                           default=5.0, min=0.5, max=50.0, precision=1)
    show_markers: BoolProperty(name="Messpunkte anzeigen", default=True)
    show_labels: BoolProperty(name="Beschriftungen", default=False)
    scale_min_mm: FloatProperty(name="Skala min", default=0.0, min=0.0, precision=1)
    scale_max_mm: FloatProperty(name="Skala max", default=0.0, min=0.0, precision=1,
                                description="0 = automatisch aus den Messwerten")
    last_info: StringProperty(default="")
    # --- Datenbank ---
    f_hersteller: StringProperty(name="Hersteller", search=_search("hersteller"), update=_on_filter)
    f_artikel: StringProperty(name="Artikel", search=_search("artikel"), update=_on_filter)
    f_groesse: StringProperty(name="Größe", search=_search("groesse"), update=_on_filter)
    f_form: StringProperty(name="Form", search=_search("form"), update=_on_filter)
    f_wand_min: FloatProperty(name="Wand min", min=0.0, precision=1, update=_on_filter,
                              description="Nenn-Wandstärke (sonst gemessener Mittelwert) mindestens, mm")
    f_wand_max: FloatProperty(name="Wand max", min=0.0, precision=1, update=_on_filter,
                              description="Nenn-Wandstärke (sonst gemessener Mittelwert) höchstens, mm (0 = egal)")
    f_text: StringProperty(name="Suche", update=_on_filter,
                           description="Freitext in Hersteller, Artikel, Form, Material, Notiz")
    db_items: CollectionProperty(type=LINER_PG_dbitem)
    db_index: IntProperty(default=0)
    db_count: IntProperty(default=0)
    sel_ids: StringProperty(default="")
    ref_id: IntProperty(default=0)
    scores_json: StringProperty(default="")
    scores_ref: IntProperty(default=0)
    # --- Vergleich ---
    wall_weight: FloatProperty(name="Gewicht Wand", default=0.7, min=0.0, max=1.0,
                               subtype="FACTOR",
                               description="Anteil der Wandstärke an der Gesamt-Ähnlichkeit (Rest: Form)")
    show_diff: BoolProperty(name="Differenz-Modelle", default=True,
                            description="Zusätzlich je Liner die Abweichung zur Referenz farbig darstellen")
    diff_limit: FloatProperty(name="Differenz-Skala ±", default=0.0, min=0.0, precision=1,
                              description="Farbskala der Differenz in mm (0 = automatisch)")
    compare_ids: StringProperty(default="")
    compare_labels: StringProperty(default="")
    results: CollectionProperty(type=LINER_PG_result)
    compare_info: StringProperty(default="")


def _settings(context):
    return context.scene.liner_settings


def _ensure_defaults(st):
    if len(st.rows) == 0:
        _fill_rows(st, DEFAULT_ROWS)


def _fill_rows(st, rows):
    st.rows.clear()
    for h, t, c in sorted(rows, key=lambda r: r[0]):
        row = st.rows.add()
        row.height_cm = h
        row.thickness_mm = t
        row.circumference_cm = c
    st.active_index = 0


def _rows_as_tuples(st):
    return [(r.height_cm, tuple(r.thickness_mm), r.circumference_cm) for r in st.rows]


def _editor_record(st):
    rec = empty_record()
    rec.update(
        id=st.current_id or None, hersteller=st.hersteller, artikel=st.artikel,
        groesse=st.groesse, form=st.form, material=st.material, notiz=st.notiz,
        nenn_wandstaerke_mm=st.nenn_mm, seite=st.side, umfang_bezug=st.circ_reference,
        distal_mm=st.distal_mm, laenge_cm=st.total_length_cm,
        messungen=_rows_as_tuples(st))
    return rec


def _load_into_editor(st, rec):
    st.current_id = rec["id"] or 0
    for k in ("hersteller", "artikel", "groesse", "form", "material", "notiz"):
        setattr(st, k, rec.get(k) or "")
    st.nenn_mm = rec.get("nenn_wandstaerke_mm") or 0.0
    st.side = rec.get("seite") or "RIGHT"
    st.circ_reference = rec.get("umfang_bezug") or "INNER"
    st.distal_mm = rec.get("distal_mm") or 8.0
    top = max(r[0] for r in rec["messungen"])
    st.total_length_cm = max(rec.get("laenge_cm") or 0.0, top)
    _fill_rows(st, rec["messungen"])


def _refresh_list(context):
    """Liest die gefilterte Liste neu aus der Datenbank."""
    global _REFRESHING
    st = _settings(context)
    try:
        with open_db(context) as db:
            items = db.search(hersteller=st.f_hersteller.strip(), artikel=st.f_artikel.strip(),
                              groesse=st.f_groesse.strip(), form=st.f_form.strip(),
                              wand_min=st.f_wand_min, wand_max=st.f_wand_max,
                              text=st.f_text.strip())
            st.db_count = db.count()
    except Exception as exc:  # z. B. Pfad nicht beschreibbar
        print("Liner-Datenbank:", exc)
        items = []
    scores = json.loads(st.scores_json) if st.scores_json else {}
    if scores:
        items.sort(key=lambda d: -scores.get(str(d["id"]), -1.0))
    sel = set(_sel_ids(st))
    active_id = st.db_items[st.db_index].db_id if 0 <= st.db_index < len(st.db_items) else 0
    _REFRESHING = True
    try:
        st.db_items.clear()
        for d in items:
            it = st.db_items.add()
            it.db_id = d["id"]
            it.name = d["label"]
            it.groesse = d["groesse"]
            it.form = d["form"]
            it.wand_mm = d["wand_mm"]
            it.nenn = bool(d["nenn_wandstaerke_mm"])
            it.score = scores.get(str(d["id"]), -1.0)
            it.select = d["id"] in sel
    finally:
        _REFRESHING = False
    ids = [it.db_id for it in st.db_items]
    st.db_index = ids.index(active_id) if active_id in ids else 0


def _refresh_all(context):
    try:
        for scene in bpy.data.scenes:
            with context.temp_override(scene=scene):
                _refresh_list(context)
    except Exception:
        pass


def _active_item(st):
    if 0 <= st.db_index < len(st.db_items):
        return st.db_items[st.db_index]
    return None


# ---------------------------------------------------------------------------
# Mesh-Hilfen
# ---------------------------------------------------------------------------

def _get_material():
    mat = bpy.data.materials.get("Liner_Wandstaerke")
    if mat is None:
        mat = bpy.data.materials.new("Liner_Wandstaerke")
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        attr = nt.nodes.new("ShaderNodeVertexColor")
        attr.layer_name = COLOR_ATTR
        attr.location = (-300, 300)
        if bsdf is not None:
            nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.6
    return mat


def _get_collection(context, name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
    return coll


def _clear_collection(name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        return
    for ob in list(coll.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)


def _make_mesh(name, verts, faces, rgba, attrs):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    for p in mesh.polygons:
        p.use_smooth = True
    for attr_name, values in attrs.items():
        a = mesh.attributes.new(attr_name, "FLOAT", "POINT")
        a.data.foreach_set("value", [math.nan if v is None else v for v in values])
    col = mesh.color_attributes.new(COLOR_ATTR, "FLOAT_COLOR", "POINT")
    flat = []
    for c in rgba:
        flat.extend(c)
    col.data.foreach_set("color", flat)
    mesh.color_attributes.active_color = col
    mesh.materials.append(_get_material())
    return mesh


def _make_text(name, body, location, coll, size=0.012):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = body
    cu.size = size
    cu.align_x = "CENTER"
    ob = bpy.data.objects.new(name, cu)
    ob.location = location
    ob.rotation_euler = (math.pi / 2, 0.0, 0.0)
    coll.objects.link(ob)
    return ob


def _show_vertex_colors(context):
    screen = getattr(context, "screen", None)
    for area in (screen.areas if screen else []):
        if area.type == "VIEW_3D":
            for space in area.spaces:
                if space.type == "VIEW_3D" and space.shading.type == "SOLID":
                    space.shading.color_type = "VERTEX"


# ---------------------------------------------------------------------------
# Operatoren: Erfassung
# ---------------------------------------------------------------------------

class LINER_OT_reset(Operator):
    bl_idname = "liner.reset_rows"
    bl_label = "Standardhöhen"
    bl_description = "Messtabelle mit den Höhen 4, 8, 12, 16, 20, 25, 30 cm neu anlegen"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        _fill_rows(_settings(context), DEFAULT_ROWS)
        return {"FINISHED"}


class LINER_OT_add_row(Operator):
    bl_idname = "liner.add_row"
    bl_label = "Höhe hinzufügen"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = _settings(context)
        prev = st.rows[-1] if len(st.rows) else None
        row = st.rows.add()
        if prev:
            row.height_cm = prev.height_cm + 5.0
            row.thickness_mm = prev.thickness_mm
            row.circumference_cm = prev.circumference_cm
        st.active_index = len(st.rows) - 1
        return {"FINISHED"}


class LINER_OT_remove_row(Operator):
    bl_idname = "liner.remove_row"
    bl_label = "Höhe entfernen"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(_settings(context).rows) > 0

    def execute(self, context):
        st = _settings(context)
        st.rows.remove(st.active_index)
        st.active_index = max(0, min(st.active_index, len(st.rows) - 1))
        return {"FINISHED"}


class LINER_OT_set_all(Operator):
    bl_idname = "liner.set_all"
    bl_label = "Alle 4 Seiten gleich"
    bl_description = "Den Anterior-Wert der aktiven Zeile auf M, P und L übertragen"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        st = _settings(context)
        return 0 <= st.active_index < len(st.rows)

    def execute(self, context):
        st = _settings(context)
        row = st.rows[st.active_index]
        v = row.thickness_mm[0]
        row.thickness_mm = (v, v, v, v)
        return {"FINISHED"}


class LINER_OT_new_record(Operator):
    bl_idname = "liner.new_record"
    bl_label = "Neuer Liner"
    bl_description = "Eingabe leeren und einen neuen Liner erfassen"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        st = _settings(context)
        rec = empty_record()
        rec["messungen"] = list(DEFAULT_ROWS)
        rec["laenge_cm"] = 32.0
        _load_into_editor(st, rec)
        st.last_info = ""
        return {"FINISHED"}


class LINER_OT_save_record(Operator):
    bl_idname = "liner.save_record"
    bl_label = "In Datenbank speichern"
    bl_description = "Aktuellen Liner in der Datenbank speichern bzw. aktualisieren"
    bl_options = {"REGISTER"}

    as_new: BoolProperty(name="Als neuen Datensatz", default=False)

    def execute(self, context):
        st = _settings(context)
        rec = _editor_record(st)
        if not (rec["hersteller"].strip() or rec["artikel"].strip()):
            self.report({"ERROR"}, "Bitte mindestens Hersteller oder Artikel angeben")
            return {"CANCELLED"}
        try:
            LinerModel(rec["messungen"], distal_mm=rec["distal_mm"],
                       circ_reference=rec["umfang_bezug"])
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if self.as_new:
            rec["id"] = None
        with open_db(context) as db:
            rid = db.save(rec)
        st.current_id = rid
        _invalidate_suggestions()
        _refresh_list(context)
        self.report({"INFO"}, "Gespeichert als #%d: %s" % (rid, record_label(rec)))
        return {"FINISHED"}


class LINER_OT_build(Operator):
    bl_idname = "liner.build"
    bl_label = "Liner-Modell erzeugen"
    bl_description = "Erzeugt bzw. aktualisiert das 3D-Modell aus der Messtabelle"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = _settings(context)
        rows = _rows_as_tuples(st)
        right = st.side == "RIGHT"
        try:
            verts, faces, thick, info = build_liner_geometry(
                rows, distal_mm=st.distal_mm, total_length_cm=st.total_length_cm,
                circ_reference=st.circ_reference, right_side=right,
                segments=st.segments, step_mm=st.step_mm)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        tmin = st.scale_min_mm if st.scale_max_mm > 0.0 else info["min_mm"]
        tmax = st.scale_max_mm if st.scale_max_mm > 0.0 else info["max_mm"]
        mesh = _make_mesh(OBJECT_NAME, verts, faces,
                          [colors.thickness_rgba(t, tmin, tmax) for t in thick],
                          {THICKNESS_ATTR: thick})

        obj = bpy.data.objects.get(OBJECT_NAME)
        if obj is None or obj.type != "MESH":
            obj = bpy.data.objects.new(OBJECT_NAME, mesh)
            context.scene.collection.objects.link(obj)
        else:
            old = obj.data
            obj.data = mesh
            if old.users == 0:
                bpy.data.meshes.remove(old)
            mesh.name = OBJECT_NAME

        obj["liner_messwerte"] = [
            {"hoehe_cm": h, "A": t[0], "M": t[1], "P": t[2], "L": t[3], "umfang_cm": c}
            for (h, t, c) in sorted(rows)
        ]
        obj["liner_seite"] = st.side
        if st.current_id:
            obj["liner_db_id"] = st.current_id

        _clear_collection(MARKER_COLLECTION)
        if st.show_markers:
            self._make_markers(context, st, rows, right, tmin, tmax)
        _show_vertex_colors(context)

        st.last_info = "min %.1f mm  ·  max %.1f mm  ·  Länge %.1f cm" % (
            info["min_mm"], info["max_mm"], info["length_cm"])
        self.report({"INFO"}, "Liner erzeugt: " + st.last_info)
        return {"FINISHED"}

    def _make_markers(self, context, st, rows, right, tmin, tmax):
        coll = _get_collection(context, MARKER_COLLECTION)
        sphere = bpy.data.meshes.get("Liner_Messpunkt")
        if sphere is None:
            import bmesh
            sphere = bpy.data.meshes.new("Liner_Messpunkt")
            bm = bmesh.new()
            bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.0025)
            bm.to_mesh(sphere)
            bm.free()
        for name, pos, t in marker_positions(rows, circ_reference=st.circ_reference,
                                             right_side=right):
            ob = bpy.data.objects.new("MP_%s_%.1fmm" % (name, t), sphere)
            ob.location = pos
            ob.color = colors.thickness_rgba(t, tmin, tmax)
            ob["wandstaerke_mm"] = t
            coll.objects.link(ob)
            if st.show_labels:
                cu = bpy.data.curves.new("MP_Text_" + name, "FONT")
                cu.body = "%s %.1f" % (name.split("_")[1], t)
                cu.size = 0.008
                cu.align_x = "CENTER"
                txt = bpy.data.objects.new("MP_Text_" + name, cu)
                x, y, z = pos
                scale = 1.0 + 0.012 / max(math.hypot(x, y), 1e-6)
                txt.location = (x * scale, y * scale, z)
                txt.rotation_euler = (math.pi / 2, 0.0, math.atan2(y, x) + math.pi / 2)
                coll.objects.link(txt)


class LINER_OT_export_csv(Operator, ExportHelper):
    bl_idname = "liner.export_csv"
    bl_label = "Messwerte exportieren"
    bl_description = "Messtabelle des aktuellen Liners als CSV speichern"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})

    def execute(self, context):
        st = _settings(context)
        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["hoehe_cm", "anterior_mm", "medial_mm", "posterior_mm",
                        "lateral_mm", "umfang_cm"])
            for h, t, c in sorted(_rows_as_tuples(st)):
                w.writerow(["%g" % h] + ["%g" % v for v in t] + ["%g" % c])
            w.writerow([])
            for key in ("hersteller", "artikel", "groesse", "form", "material", "notiz"):
                w.writerow([key, getattr(st, key)])
            w.writerow(["nenn_wandstaerke_mm", "%g" % st.nenn_mm])
            w.writerow(["seite", st.side])
            w.writerow(["umfang_bezug", st.circ_reference])
            w.writerow(["distal_mm", "%g" % st.distal_mm])
            w.writerow(["gesamtlaenge_cm", "%g" % st.total_length_cm])
        self.report({"INFO"}, "Gespeichert: " + self.filepath)
        return {"FINISHED"}


class LINER_OT_import_csv(Operator, ImportHelper):
    bl_idname = "liner.import_csv"
    bl_label = "Messwerte importieren"
    bl_description = "Messtabelle eines Liners aus CSV laden (Trennzeichen ; oder ,)"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})

    def execute(self, context):
        st = _settings(context)
        with open(self.filepath, newline="", encoding="utf-8-sig") as f:
            text = f.read()
        delim = ";" if text.count(";") >= text.count(",") else ","
        rows, meta = [], {}
        for rec in csv.reader(text.splitlines(), delimiter=delim):
            rec = [c.strip() for c in rec]
            if not any(rec):
                continue
            try:
                nums = [float(c.replace(",", ".")) for c in rec if c]
            except ValueError:
                if len(rec) >= 1:
                    meta[rec[0].lower()] = rec[1] if len(rec) > 1 else ""
                continue
            if len(nums) >= 6:
                rows.append((nums[0], tuple(nums[1:5]), nums[5]))
        if not rows:
            self.report({"ERROR"}, "Keine Messzeilen gefunden")
            return {"CANCELLED"}
        _fill_rows(st, rows)
        st.current_id = 0
        for key in ("hersteller", "artikel", "groesse", "form", "material", "notiz"):
            if key in meta:
                setattr(st, key, meta[key])
        if meta.get("seite") in ("RIGHT", "LEFT"):
            st.side = meta["seite"]
        if meta.get("umfang_bezug") in ("INNER", "OUTER"):
            st.circ_reference = meta["umfang_bezug"]
        for key, prop in (("distal_mm", "distal_mm"), ("gesamtlaenge_cm", "total_length_cm"),
                          ("nenn_wandstaerke_mm", "nenn_mm")):
            if key in meta:
                try:
                    setattr(st, prop, float(meta[key].replace(",", ".")))
                except ValueError:
                    pass
        self.report({"INFO"}, "%d Messhöhen geladen" % len(rows))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operatoren: Datenbank
# ---------------------------------------------------------------------------

class LINER_OT_db_refresh(Operator):
    bl_idname = "liner.db_refresh"
    bl_label = "Liste aktualisieren"
    bl_options = {"REGISTER"}

    def execute(self, context):
        _invalidate_suggestions()
        _refresh_list(context)
        return {"FINISHED"}


class LINER_OT_db_clear_filter(Operator):
    bl_idname = "liner.db_clear_filter"
    bl_label = "Filter zurücksetzen"
    bl_options = {"REGISTER"}

    def execute(self, context):
        st = _settings(context)
        for k in ("f_hersteller", "f_artikel", "f_groesse", "f_form", "f_text"):
            setattr(st, k, "")
        st.f_wand_min = st.f_wand_max = 0.0
        st.scores_json = ""
        st.scores_ref = 0
        _refresh_list(context)
        return {"FINISHED"}


class LINER_OT_db_load(Operator):
    bl_idname = "liner.db_load"
    bl_label = "Bearbeiten"
    bl_description = "Ausgewählten Liner zum Bearbeiten in die Erfassung laden"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_item(_settings(context)) is not None

    def execute(self, context):
        st = _settings(context)
        with open_db(context) as db:
            rec = db.get(_active_item(st).db_id)
        if rec is None:
            self.report({"ERROR"}, "Datensatz nicht gefunden")
            return {"CANCELLED"}
        _load_into_editor(st, rec)
        bpy.ops.liner.build()
        return {"FINISHED"}


class LINER_OT_db_delete(Operator):
    bl_idname = "liner.db_delete"
    bl_label = "Liner löschen"
    bl_description = "Ausgewählten Liner endgültig aus der Datenbank löschen"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _active_item(_settings(context)) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        st = _settings(context)
        item = _active_item(st)
        rid, label = item.db_id, item.name
        with open_db(context) as db:
            db.delete(rid)
        _set_sel_ids(st, [i for i in _sel_ids(st) if i != rid])
        if st.current_id == rid:
            st.current_id = 0
        if st.ref_id == rid:
            st.ref_id = 0
        _invalidate_suggestions()
        _refresh_list(context)
        self.report({"INFO"}, "Gelöscht: " + label)
        return {"FINISHED"}


class LINER_OT_db_select(Operator):
    bl_idname = "liner.db_select"
    bl_label = "Auswahl"
    bl_options = {"REGISTER"}

    action: EnumProperty(items=(("ALL", "Alle sichtbaren", ""), ("NONE", "Keine", "")))

    def execute(self, context):
        st = _settings(context)
        ids = _sel_ids(st)
        if self.action == "ALL":
            for it in st.db_items:
                if it.db_id not in ids:
                    ids.append(it.db_id)
        else:
            ids = []
        _set_sel_ids(st, ids)
        _refresh_list(context)
        return {"FINISHED"}


class LINER_OT_db_set_ref(Operator):
    bl_idname = "liner.db_set_ref"
    bl_label = "Als Referenz"
    bl_description = "Ausgewählten Liner als Referenz für Vergleich und Ähnlichkeitssuche setzen"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _active_item(_settings(context)) is not None

    def execute(self, context):
        st = _settings(context)
        item = _active_item(st)
        st.ref_id = item.db_id
        ids = _sel_ids(st)
        if item.db_id not in ids:
            ids.insert(0, item.db_id)
            _set_sel_ids(st, ids)
            _refresh_list(context)
        return {"FINISHED"}


class LINER_OT_db_similar(Operator):
    bl_idname = "liner.db_similar"
    bl_label = "Ähnliche suchen"
    bl_description = ("Alle Liner der gefilterten Liste mit der Referenz (bzw. dem markierten Liner) "
                      "vergleichen und nach Ähnlichkeit sortieren")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        st = _settings(context)
        return st.ref_id > 0 or _active_item(st) is not None

    def execute(self, context):
        st = _settings(context)
        ref_id = st.ref_id or _active_item(st).db_id
        with open_db(context) as db:
            ref = db.get(ref_id)
            cands = db.get_many([it.db_id for it in st.db_items])
        if ref is None:
            self.report({"ERROR"}, "Referenz nicht gefunden")
            return {"CANCELLED"}
        try:
            ranked = rank_similar(ref, cands, st.wall_weight)
        except ValueError as exc:
            self.report({"ERROR"}, "Referenz ungültig: %s" % exc)
            return {"CANCELLED"}
        scores = {str(ref_id): 100.0}
        for rec, res in ranked:
            scores[str(rec["id"])] = res["aehnlichkeit"] if res else -1.0
        st.scores_json = json.dumps(scores)
        st.scores_ref = ref_id
        st.ref_id = ref_id
        _refresh_list(context)
        best = [(r, x) for r, x in ranked if x][:3]
        if best:
            self.report({"INFO"}, "Am ähnlichsten zu %s: %s" % (
                record_label(ref),
                ", ".join("%s (%.0f %%)" % (record_label(r), x["aehnlichkeit"]) for r, x in best)))
        return {"FINISHED"}


class LINER_OT_db_export(Operator, ExportHelper):
    bl_idname = "liner.db_export"
    bl_label = "Datenbank exportieren"
    bl_description = "Alle (bzw. die ausgewählten) Liner als CSV speichern, z. B. für Excel"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})
    only_selected: BoolProperty(name="Nur ausgewählte", default=False)

    def execute(self, context):
        st = _settings(context)
        with open_db(context) as db:
            n = db.export_csv(self.filepath, _sel_ids(st) if self.only_selected else None)
        self.report({"INFO"}, "%d Liner exportiert" % n)
        return {"FINISHED"}


class LINER_OT_db_import(Operator, ImportHelper):
    bl_idname = "liner.db_import"
    bl_label = "In Datenbank importieren"
    bl_description = "Liner aus einer exportierten Datenbank-CSV hinzufügen"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})

    def execute(self, context):
        try:
            with open_db(context) as db:
                ids = db.import_csv(self.filepath)
        except (ValueError, KeyError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _invalidate_suggestions()
        _refresh_list(context)
        self.report({"INFO"}, "%d Liner importiert" % len(ids))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operatoren: Vergleich
# ---------------------------------------------------------------------------

def _compare_records(context, st):
    """Lädt die ausgewählten Liner; Referenz zuerst."""
    ids = _sel_ids(st)
    with open_db(context) as db:
        recs = db.get_many(ids)
    ref_idx = 0
    for i, r in enumerate(recs):
        if r["id"] == st.ref_id:
            ref_idx = i
    return recs, ref_idx


class LINER_OT_compare(Operator):
    bl_idname = "liner.compare"
    bl_label = "Vergleichen"
    bl_description = ("Ausgewählte Liner nebeneinander darstellen (gemeinsame Farbskala), "
                      "Abweichungen zur Referenz berechnen und anzeigen")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(_sel_ids(_settings(context))) >= 2

    def execute(self, context):
        st = _settings(context)
        recs, ref_idx = _compare_records(context, st)
        if len(recs) < 2:
            self.report({"ERROR"}, "Mindestens 2 Liner auswählen")
            return {"CANCELLED"}
        if len(recs) > colors.MAX_COMPARE:
            self.report({"ERROR"}, "Höchstens %d Liner gleichzeitig vergleichen" % colors.MAX_COMPARE)
            return {"CANCELLED"}
        try:
            comp = build_comparison(recs, ref_idx, st.wall_weight)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self._build_scene(context, st, comp)

        st.compare_ids = ",".join(str(r["id"]) for r in comp["records"])
        st.results.clear()
        for i, (label, pair) in enumerate(zip(comp["labels"][1:], comp["pairs"]), start=1):
            r = st.results.add()
            r.name = label
            r.slot = i
            if pair is None:
                r.valid = False
                continue
            r.score = pair["aehnlichkeit"]
            r.s_wall = pair["aehnlichkeit_wand"]
            r.s_form = pair["aehnlichkeit_form"]
            r.mae = pair["mae_mm"]
            r.bias = pair["bias_mm"]
            r.maxd = pair["max_mm"]
            r.where = "%.0f cm %s" % (pair["max_hoehe_cm"], pair["max_winkel"])
        st.compare_info = "Referenz: " + comp["labels"][0]
        self.report({"INFO"}, "%d Liner verglichen" % len(recs))
        return {"FINISHED"}

    def _build_scene(self, context, st, comp):
        _clear_collection(COMPARE_COLLECTION)
        coll = _get_collection(context, COMPARE_COLLECTION)
        recs, models, labels = comp["records"], comp["models"], comp["labels"]
        right = (recs[0].get("seite") or "RIGHT") == "RIGHT"

        geos = []
        for rec in recs:
            geos.append(build_liner_geometry(
                rec["messungen"], distal_mm=rec["distal_mm"],
                total_length_cm=rec.get("laenge_cm") or None,
                circ_reference=rec["umfang_bezug"], right_side=right,
                segments=st.segments, step_mm=st.step_mm))
        tmin = st.scale_min_mm if st.scale_max_mm > 0.0 else min(g[3]["min_mm"] for g in geos)
        tmax = st.scale_max_mm if st.scale_max_mm > 0.0 else max(g[3]["max_mm"] for g in geos)
        spacing = 2.0 * max(g[3]["max_radius_m"] for g in geos) + 0.04
        top = max(g[3]["length_cm"] for g in geos) / 100.0

        diffs = [None]
        lim = st.diff_limit
        if st.show_diff:
            for m, g in zip(models[1:], geos[1:]):
                d = vertex_differences(models[0], m, g[3]["samples"])
                diffs.append(d)
            if lim <= 0.0:
                lim = max([abs(v) for d in diffs[1:] for v in d if v is not None] + [0.5])

        for i, ((verts, faces, thick, info), label) in enumerate(zip(geos, labels)):
            x = i * spacing
            name = ("V%d %s" % (i + 1, label))[:60]
            mesh = _make_mesh(name, verts, faces,
                              [colors.thickness_rgba(t, tmin, tmax) for t in thick],
                              {THICKNESS_ATTR: thick})
            ob = bpy.data.objects.new(name, mesh)
            ob.location = (x, 0.0, 0.0)
            ob["liner_db_id"] = recs[i]["id"]
            coll.objects.link(ob)
            pair = comp["pairs"][i - 1] if i else None
            sub = "Referenz" if i == 0 else ("%.0f %% ähnlich" % pair["aehnlichkeit"] if pair else "kein Vergleich")
            _make_text("V%d Text" % (i + 1), "#%d %s\n%s" % (i + 1, label, sub),
                       (x, 0.0, top + 0.03), coll)

            if i and st.show_diff:
                d = diffs[i]
                dname = ("D%d %s" % (i + 1, label))[:60]
                dmesh = _make_mesh(dname, verts, faces,
                                   [colors.difference_rgba(v, lim) for v in d],
                                   {DIFF_ATTR: d, THICKNESS_ATTR: thick})
                dob = bpy.data.objects.new(dname, dmesh)
                dob.location = (x, 0.0, -(top + 0.10))
                coll.objects.link(dob)
                _make_text("D%d Text" % (i + 1), "Δ #%d − #1" % (i + 1),
                           (x, 0.0, -0.07), coll)

        legend = "Wandstärke: hell = %.1f mm … dunkel = %.1f mm" % (tmin, tmax)
        if st.show_diff and len(recs) > 1:
            legend += "\nUnten: Abweichung zu #1 – blau = dünner, grau = gleich, rot = dicker (±%.1f mm)" % lim
        _make_text("Vergleich Legende", legend, (-spacing, 0.0, top / 2), coll, size=0.01)
        _show_vertex_colors(context)


class LINER_OT_compare_clear(Operator):
    bl_idname = "liner.compare_clear"
    bl_label = "Vergleich entfernen"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _clear_collection(COMPARE_COLLECTION)
        st = _settings(context)
        st.results.clear()
        st.compare_info = ""
        return {"FINISHED"}


class LINER_OT_report(Operator, ExportHelper):
    bl_idname = "liner.report"
    bl_label = "Vergleichsbericht"
    bl_description = ("HTML-Bericht mit Diagrammen, Ähnlichkeitswerten und Messwert-Tabelle "
                      "der ausgewählten Liner erstellen und im Browser öffnen")
    filename_ext = ".html"
    filter_glob: StringProperty(default="*.html", options={"HIDDEN"})
    open_browser: BoolProperty(name="Im Browser öffnen", default=True)

    @classmethod
    def poll(cls, context):
        return len(_sel_ids(_settings(context))) >= 2

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = os.path.join(os.path.dirname(db_path(context)), "liner_vergleich.html")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        st = _settings(context)
        recs, ref_idx = _compare_records(context, st)
        if len(recs) > colors.MAX_COMPARE:
            self.report({"ERROR"}, "Höchstens %d Liner gleichzeitig vergleichen" % colors.MAX_COMPARE)
            return {"CANCELLED"}
        try:
            comp = build_comparison(recs, ref_idx, st.wall_weight)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        write_report(comp, self.filepath)
        if self.open_browser:
            webbrowser.open(pathlib.Path(self.filepath).resolve().as_uri())
        self.report({"INFO"}, "Bericht gespeichert: " + self.filepath)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Oberfläche
# ---------------------------------------------------------------------------

class LINER_UL_rows(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "height_cm", text="")
        for k in range(4):
            row.prop(item, "thickness_mm", index=k, text="")
        row.prop(item, "circumference_cm", text="")


class LINER_UL_db(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        st = data
        row = layout.row(align=True)
        row.prop(item, "select", text="")
        row.label(text=("#%d " % item.db_id) + item.name,
                  icon="SOLO_ON" if item.db_id == st.ref_id else "NONE")
        sub = row.row()
        sub.alignment = "RIGHT"
        if item.form:
            sub.label(text=item.form)
        sub.label(text=("%.1f mm" % item.wand_mm) + ("" if item.nenn else " Ø"))
        if item.score >= 0.0:
            sub.label(text="%.0f %%" % item.score)


class LINER_PT_main(Panel):
    bl_label = "Liner erfassen"
    bl_idname = "LINER_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Liner"

    def draw(self, context):
        layout = self.layout
        st = _settings(context)

        box = layout.box()
        head = box.row()
        head.label(text="Datensatz #%d" % st.current_id if st.current_id else "Neuer Liner",
                   icon="FILE_TICK" if st.current_id else "FILE_NEW")
        head.operator("liner.new_record", text="", icon="ADD")
        col = box.column(align=True)
        for p in ("hersteller", "artikel", "groesse", "form", "nenn_mm", "material", "notiz"):
            col.prop(st, p)

        layout.prop(st, "side", expand=True)

        box = layout.box()
        box.label(text="Messtabelle (Höhe cm · Wandstärke mm · Umfang cm)")
        hdr = box.row(align=True)
        for label in ("Höhe", "A 0°", "M 90°", "P 180°", "L 270°", "Umfang"):
            hdr.label(text=label)
        row = box.row()
        row.template_list("LINER_UL_rows", "", st, "rows", st, "active_index", rows=8)
        side = row.column(align=True)
        side.operator("liner.add_row", text="", icon="ADD")
        side.operator("liner.remove_row", text="", icon="REMOVE")
        side.separator()
        side.operator("liner.set_all", text="", icon="LINKED")
        side.separator()
        side.operator("liner.reset_rows", text="", icon="FILE_REFRESH")

        col = layout.column(align=True)
        col.prop(st, "circ_reference")
        col.prop(st, "distal_mm")
        col.prop(st, "total_length_cm")

        layout.operator("liner.build", icon="MESH_CYLINDER")
        if st.last_info:
            layout.label(text=st.last_info, icon="INFO")

        row = layout.row(align=True)
        row.scale_y = 1.2
        op = row.operator("liner.save_record",
                          text="Speichern" if st.current_id else "In Datenbank speichern",
                          icon="FILE_TICK")
        op.as_new = False
        if st.current_id:
            op = row.operator("liner.save_record", text="Als neu", icon="DUPLICATE")
            op.as_new = True

        row = layout.row(align=True)
        row.operator("liner.import_csv", icon="IMPORT", text="CSV laden")
        row.operator("liner.export_csv", icon="EXPORT", text="CSV speichern")


class LINER_PT_options(Panel):
    bl_label = "Darstellung"
    bl_parent_id = "LINER_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Liner"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        st = _settings(context)
        col = self.layout.column(align=True)
        col.prop(st, "segments")
        col.prop(st, "step_mm")
        col.separator()
        col.prop(st, "show_markers")
        sub = col.row()
        sub.enabled = st.show_markers
        sub.prop(st, "show_labels")
        col.separator()
        col.label(text="Farbskala Wandstärke (0 = automatisch)")
        row = col.row(align=True)
        row.prop(st, "scale_min_mm", text="min")
        row.prop(st, "scale_max_mm", text="max")


class LINER_PT_database(Panel):
    bl_label = "Datenbank"
    bl_idname = "LINER_PT_database"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Liner"

    def draw(self, context):
        layout = self.layout
        st = _settings(context)
        try:
            prefs = context.preferences.addons[ADDON_ID].preferences
            layout.prop(prefs, "db_path", text="")
        except KeyError:
            layout.label(text=db_path(context), icon="FILE")

        box = layout.box()
        head = box.row()
        head.label(text="Filter", icon="FILTER")
        head.operator("liner.db_clear_filter", text="", icon="X")
        col = box.column(align=True)
        col.prop(st, "f_hersteller")
        col.prop(st, "f_artikel")
        col.prop(st, "f_groesse")
        col.prop(st, "f_form")
        row = col.row(align=True)
        row.prop(st, "f_wand_min", text="Wand ≥")
        row.prop(st, "f_wand_max", text="≤")
        col.prop(st, "f_text", icon="VIEWZOOM")

        layout.label(text="%d von %d Liner · %d ausgewählt" % (
            len(st.db_items), st.db_count, len(_sel_ids(st))))
        row = layout.row()
        row.template_list("LINER_UL_db", "", st, "db_items", st, "db_index", rows=8)
        side = row.column(align=True)
        side.operator("liner.db_refresh", text="", icon="FILE_REFRESH")
        side.separator()
        side.operator("liner.db_load", text="", icon="GREASEPENCIL")
        side.operator("liner.db_set_ref", text="", icon="SOLO_ON")
        side.separator()
        side.operator("liner.db_select", text="", icon="CHECKBOX_HLT").action = "ALL"
        side.operator("liner.db_select", text="", icon="CHECKBOX_DEHLT").action = "NONE"
        side.separator()
        side.operator("liner.db_delete", text="", icon="TRASH")

        layout.operator("liner.db_similar", icon="SORTSIZE")
        row = layout.row(align=True)
        row.operator("liner.db_import", icon="IMPORT", text="Import")
        row.operator("liner.db_export", icon="EXPORT", text="Export")


class LINER_PT_compare(Panel):
    bl_label = "Vergleich"
    bl_idname = "LINER_PT_compare"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Liner"

    def draw(self, context):
        layout = self.layout
        st = _settings(context)
        n = len(_sel_ids(st))
        layout.label(text="%d Liner ausgewählt (mind. 2, höchstens %d)" % (n, colors.MAX_COMPARE),
                     icon="CHECKBOX_HLT")
        ref = next((it.name for it in st.db_items if it.db_id == st.ref_id), None)
        layout.label(text="Referenz: " + (ref or ("#%d" % st.ref_id if st.ref_id else "erster ausgewählter")),
                     icon="SOLO_ON")
        col = layout.column(align=True)
        col.prop(st, "wall_weight", slider=True)
        col.prop(st, "show_diff")
        sub = col.row()
        sub.enabled = st.show_diff
        sub.prop(st, "diff_limit")

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("liner.compare", icon="MOD_ARRAY")
        row.operator("liner.report", icon="TEXT", text="Bericht (HTML)")

        if st.compare_info:
            box = layout.box()
            box.label(text=st.compare_info, icon="SOLO_ON")
            for r in st.results:
                b = box.column(align=True)
                b.label(text="#%d %s" % (r.slot + 1, r.name), icon="DOT")
                if not r.valid:
                    b.label(text="   keine gemeinsamen Messhöhen")
                    continue
                b.label(text="   Ähnlichkeit %.0f %%  (Wand %.0f %% · Form %.0f %%)"
                        % (r.score, r.s_wall, r.s_form))
                b.label(text="   Ø|Δ| %.2f mm · Ø Δ %+.2f mm · max %+.1f mm (%s)"
                        % (r.mae, r.bias, r.maxd, r.where))
            layout.operator("liner.compare_clear", icon="X")


# ---------------------------------------------------------------------------
# Registrierung
# ---------------------------------------------------------------------------

classes = (
    LINER_AP_prefs,
    LINER_PG_row,
    LINER_PG_dbitem,
    LINER_PG_result,
    LINER_PG_settings,
    LINER_OT_reset,
    LINER_OT_add_row,
    LINER_OT_remove_row,
    LINER_OT_set_all,
    LINER_OT_new_record,
    LINER_OT_save_record,
    LINER_OT_build,
    LINER_OT_export_csv,
    LINER_OT_import_csv,
    LINER_OT_db_refresh,
    LINER_OT_db_clear_filter,
    LINER_OT_db_load,
    LINER_OT_db_delete,
    LINER_OT_db_select,
    LINER_OT_db_set_ref,
    LINER_OT_db_similar,
    LINER_OT_db_export,
    LINER_OT_db_import,
    LINER_OT_compare,
    LINER_OT_compare_clear,
    LINER_OT_report,
    LINER_UL_rows,
    LINER_UL_db,
    LINER_PT_main,
    LINER_PT_options,
    LINER_PT_database,
    LINER_PT_compare,
)


def _init_scenes():
    for scene in bpy.data.scenes:
        _ensure_defaults(scene.liner_settings)
    _invalidate_suggestions()
    _refresh_all(bpy.context)


@bpy.app.handlers.persistent
def _on_load(_dummy):
    _init_scenes()


def _init_timer():
    try:
        _init_scenes()
    except AttributeError:
        return 0.5
    return None


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.liner_settings = PointerProperty(type=LINER_PG_settings)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_init_timer, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_init_timer):
        bpy.app.timers.unregister(_init_timer)
    del bpy.types.Scene.liner_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
