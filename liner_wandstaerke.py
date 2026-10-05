# SPDX-License-Identifier: GPL-3.0-or-later
"""
Liner-Wandstärken – Blender-Add-on

Erfasst die Wandstärke eines prothetischen Liners an definierten Höhen
(gemessen von distal) und an je vier Punkten pro Höhe (anterior, medial,
posterior, lateral = 0°, 90°, 180°, 270°) und erzeugt daraus ein 3D-Modell
des Liners mit farbiger Wandstärken-Karte.

Benutzung:
  * Als Add-on: Bearbeiten > Einstellungen > Add-ons > "Von Datenträger
    installieren" und diese .py-Datei wählen.
  * Oder: Datei im Text-Editor öffnen und "Skript ausführen".
Danach im 3D-Viewport die Seitenleiste (Taste N) öffnen, Reiter "Liner".
"""

bl_info = {
    "name": "Liner-Wandstärken",
    "author": "liner_mapping",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "3D-Viewport > Seitenleiste (N) > Liner",
    "description": "Wandstärken eines Prothesen-Liners erfassen und als 3D-Modell darstellen",
    "category": "Object",
}

import csv
import math

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
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ExportHelper, ImportHelper


# ---------------------------------------------------------------------------
# Standardwerte
# ---------------------------------------------------------------------------

# Spalten der Messtabelle: Winkel im Uhrzeigersinn (von oben, rechtes Bein)
SIDES = ("A", "M", "P", "L")
SIDE_NAMES = ("Anterior", "Medial", "Posterior", "Lateral")
SIDE_ANGLES_DEG = (0.0, 90.0, 180.0, 270.0)

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
THICKNESS_ATTR = "Wandstaerke_mm"
COLOR_ATTR = "Wandstaerke_Farbe"


# ---------------------------------------------------------------------------
# Reine Geometrie (ohne bpy, damit sie auch außerhalb von Blender testbar ist)
# ---------------------------------------------------------------------------

def _pchip_slopes(xs, ys):
    """Steigungen für monotone kubische Hermite-Interpolation (Fritsch-Carlson)."""
    n = len(xs)
    if n == 1:
        return [0.0]
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    if n == 2:
        return [d[0], d[0]]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0.0:
            m[i] = 0.0
        else:
            w1 = 2.0 * h[i] + h[i - 1]
            w2 = h[i] + 2.0 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def end_slope(h0, h1, d0, d1):
        s = ((2.0 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if s * d0 <= 0.0:
            return 0.0
        if d0 * d1 <= 0.0 and abs(s) > abs(3.0 * d0):
            return 3.0 * d0
        return s

    m[0] = end_slope(h[0], h[1], d[0], d[1])
    m[-1] = end_slope(h[-1], h[-2], d[-1], d[-2])
    return m


def pchip(xs, ys, x):
    """Monotone kubische Interpolation; außerhalb des Bereichs konstant."""
    if len(xs) == 1 or x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    m = _pchip_slopes(xs, ys)
    i = 0
    while x > xs[i + 1]:
        i += 1
    h = xs[i + 1] - xs[i]
    t = (x - xs[i]) / h
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])


def ring_thickness(values, theta):
    """Glatte, periodische Interpolation der 4 Messwerte (0°, 90°, 180°, 270°).

    Trigonometrisches Polynom, das exakt durch alle vier Werte läuft; auf den
    Bereich [min, max] der Messwerte begrenzt, damit kein Überschwingen entsteht.
    """
    v0, v1, v2, v3 = values
    a0 = (v0 + v1 + v2 + v3) / 4.0
    a1 = (v0 - v2) / 2.0
    b1 = (v1 - v3) / 2.0
    a2 = (v0 - v1 + v2 - v3) / 4.0
    t = a0 + a1 * math.cos(theta) + b1 * math.sin(theta) + a2 * math.cos(2.0 * theta)
    return min(max(t, min(values)), max(values))


def build_liner_geometry(rows, *, distal_mm=8.0, total_length_cm=None,
                         circ_reference="INNER", right_side=True,
                         segments=64, step_mm=5.0, cap_rings=12):
    """Erzeugt die Liner-Geometrie.

    rows: Liste von (hoehe_cm, (A, M, P, L) in mm, umfang_cm), beliebige Reihenfolge.
    Rückgabe: (verts in Metern, faces, thickness_mm pro Vertex, info-dict)

    Koordinaten: Z nach oben (Z=0 = distales Ende außen), -Y = anterior,
    medial = +X beim rechten Bein bzw. -X beim linken Bein.
    """
    rows = sorted(rows, key=lambda r: r[0])
    if not rows:
        raise ValueError("Keine Messhöhen vorhanden")
    hs = [r[0] * 10.0 for r in rows]                      # mm
    if len(set(hs)) != len(hs):
        raise ValueError("Messhöhen müssen eindeutig sein")
    if hs[0] <= distal_mm:
        raise ValueError("Unterste Messhöhe muss über der distalen Wandstärke liegen")
    cols = [[r[1][k] for r in rows] for k in range(4)]   # mm je Seite
    if min(min(c) for c in cols) <= 0.0:
        raise ValueError("Wandstärken müssen größer als 0 sein")

    # Innenradius je Messhöhe aus dem Umfang
    r_in = []
    for (_, t, circ) in rows:
        r = circ * 10.0 / (2.0 * math.pi)
        if circ_reference == "OUTER":
            r -= sum(t) / 4.0
        if r <= 0.0:
            raise ValueError("Umfang zu klein für die angegebene Wandstärke")
        r_in.append(r)

    top = max(hs[-1], (total_length_cm or 0.0) * 10.0)
    side = 1.0 if right_side else -1.0
    thetas = [2.0 * math.pi * j / segments for j in range(segments)]
    dirs = [(side * math.sin(th), -math.cos(th)) for th in thetas]

    def thickness_at(z, th):
        vals = [pchip(hs, cols[k], z) for k in range(4)]
        return ring_thickness(vals, th)

    def radius_at(z):
        return pchip(hs, r_in, z)

    # Höhen des zylindrischen Teils (inkl. exakter Messhöhen)
    zs = set(hs)
    z = hs[0]
    while z < top:
        zs.add(round(z, 6))
        z += step_mm
    zs.add(top)
    zs = sorted(zs)

    z1 = hs[0]
    r1 = r_in[0]
    t1 = [thickness_at(z1, th) for th in thetas]

    outer_rings, inner_rings, ring_t = [], [], []

    # Distale Kappe: Ellipsoid-Viertel unterhalb der untersten Messhöhe.
    # Außen: von z1 bis z=0, innen: von z1 bis z=distal_mm.
    for i in range(cap_rings, 0, -1):
        phi = 0.5 * math.pi * i / cap_rings          # 90° = Spitze, 0° = z1
        c, s = math.cos(phi), math.sin(phi)
        zo = z1 - z1 * s
        zi = z1 - (z1 - distal_mm) * s
        orow, irow, trow = [], [], []
        for j, (dx, dy) in enumerate(dirs):
            ro = (r1 + t1[j]) * c
            ri = r1 * c
            orow.append((dx * ro, dy * ro, zo))
            irow.append((dx * ri, dy * ri, zi))
            # lineare Überblendung der Wandstärke zur distalen Stärke
            trow.append(t1[j] * c + distal_mm * s)
        outer_rings.append(orow)
        inner_rings.append(irow)
        ring_t.append(trow)

    for z in zs:
        ri = radius_at(z)
        orow, irow, trow = [], [], []
        for j, ((dx, dy), th) in enumerate(zip(dirs, thetas)):
            t = thickness_at(z, th)
            ro = ri + t
            orow.append((dx * ro, dy * ro, z))
            irow.append((dx * ri, dy * ri, z))
            trow.append(t)
        outer_rings.append(orow)
        inner_rings.append(irow)
        ring_t.append(trow)

    # ---- Mesh zusammensetzen ----
    verts, thick, faces = [], [], []
    n_rings = len(outer_rings)

    def add_surface(rings, tip, tip_t):
        tip_idx = len(verts)
        verts.append(tip)
        thick.append(tip_t)
        start = len(verts)
        for ring, trow in zip(rings, ring_t):
            verts.extend(ring)
            thick.extend(trow)
        return tip_idx, start

    o_tip, o0 = add_surface(outer_rings, (0.0, 0.0, 0.0), distal_mm)
    i_tip, i0 = add_surface(inner_rings, (0.0, 0.0, distal_mm), distal_mm)

    def idx(base, r, j):
        return base + r * segments + (j % segments)

    # Außenfläche (Normalen nach außen)
    for j in range(segments):
        faces.append((o_tip, idx(o0, 0, j + 1), idx(o0, 0, j)))
    for r in range(n_rings - 1):
        for j in range(segments):
            faces.append((idx(o0, r, j), idx(o0, r, j + 1),
                          idx(o0, r + 1, j + 1), idx(o0, r + 1, j)))
    # Innenfläche (Normalen in den Hohlraum, also umgekehrte Reihenfolge)
    for j in range(segments):
        faces.append((i_tip, idx(i0, 0, j), idx(i0, 0, j + 1)))
    for r in range(n_rings - 1):
        for j in range(segments):
            faces.append((idx(i0, r, j), idx(i0, r + 1, j),
                          idx(i0, r + 1, j + 1), idx(i0, r, j + 1)))
    # Proximaler Rand
    last = n_rings - 1
    for j in range(segments):
        faces.append((idx(o0, last, j), idx(o0, last, j + 1),
                      idx(i0, last, j + 1), idx(i0, last, j)))

    # orientation sanity: windings above are chosen for this (dx, dy) layout;
    # beim linken Bein ist die Drehrichtung gespiegelt -> Faces umkehren
    if not right_side:
        faces = [tuple(reversed(f)) for f in faces]

    verts_m = [(x / 1000.0, y / 1000.0, zz / 1000.0) for (x, y, zz) in verts]
    info = {
        "min_mm": min(thick),
        "max_mm": max(thick),
        "length_cm": top / 10.0,
        "heights_mm": hs,
        "r_in_mm": r_in,
    }
    return verts_m, faces, thick, info


def marker_positions(rows, *, circ_reference="INNER", right_side=True):
    """Außenpunkte der Messstellen in Metern: Liste von (name, (x, y, z), mm)."""
    side = 1.0 if right_side else -1.0
    out = []
    for (h, t, circ) in sorted(rows, key=lambda r: r[0]):
        r = circ * 10.0 / (2.0 * math.pi)
        if circ_reference == "OUTER":
            r -= sum(t) / 4.0
        for k, ang in enumerate(SIDE_ANGLES_DEG):
            th = math.radians(ang)
            ro = r + t[k]
            x, y = side * math.sin(th) * ro, -math.cos(th) * ro
            name = "%gcm_%s" % (h, SIDES[k])
            out.append((name, (x / 1000.0, y / 1000.0, h / 100.0), t[k]))
    return out


def heat_color(t, tmin, tmax):
    """Farbskala blau (dünn) -> grün -> gelb -> rot (dick)."""
    if tmax - tmin < 1e-9:
        f = 0.5
    else:
        f = min(max((t - tmin) / (tmax - tmin), 0.0), 1.0)
    stops = ((0.0, (0.05, 0.15, 0.9)), (0.33, (0.05, 0.75, 0.3)),
             (0.66, (0.95, 0.85, 0.05)), (1.0, (0.9, 0.08, 0.05)))
    for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
        if f <= f1:
            u = (f - f0) / (f1 - f0)
            return tuple(c0[i] + (c1[i] - c0[i]) * u for i in range(3)) + (1.0,)
    return stops[-1][1] + (1.0,)


# ---------------------------------------------------------------------------
# Daten (werden in der .blend-Datei gespeichert)
# ---------------------------------------------------------------------------

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


class LINER_PG_settings(PropertyGroup):
    rows: CollectionProperty(type=LINER_PG_row)
    active_index: IntProperty(default=0)
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


def _settings(context):
    return context.scene.liner_settings


def _ensure_defaults(st):
    if len(st.rows) == 0:
        for h, t, c in DEFAULT_ROWS:
            row = st.rows.add()
            row.height_cm = h
            row.thickness_mm = t
            row.circumference_cm = c


def _rows_as_tuples(st):
    return [(r.height_cm, tuple(r.thickness_mm), r.circumference_cm) for r in st.rows]


# ---------------------------------------------------------------------------
# Operatoren
# ---------------------------------------------------------------------------

class LINER_OT_reset(Operator):
    bl_idname = "liner.reset_rows"
    bl_label = "Standardhöhen"
    bl_description = "Messtabelle mit den Höhen 4, 8, 12, 16, 20, 25, 30 cm neu anlegen"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        st = _settings(context)
        st.rows.clear()
        _ensure_defaults(st)
        st.active_index = 0
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
        row = _settings(context).rows[_settings(context).active_index]
        v = row.thickness_mm[0]
        row.thickness_mm = (v, v, v, v)
        return {"FINISHED"}


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


def _clear_markers():
    coll = bpy.data.collections.get(MARKER_COLLECTION)
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

        # Mesh neu aufbauen (Objekt bleibt erhalten, falls vorhanden)
        mesh = bpy.data.meshes.new(OBJECT_NAME)
        mesh.from_pydata(verts, [], faces)
        mesh.validate()
        mesh.update()
        for p in mesh.polygons:
            p.use_smooth = True

        attr = mesh.attributes.new(THICKNESS_ATTR, "FLOAT", "POINT")
        attr.data.foreach_set("value", thick)

        tmin = st.scale_min_mm if st.scale_max_mm > 0.0 else info["min_mm"]
        tmax = st.scale_max_mm if st.scale_max_mm > 0.0 else info["max_mm"]
        col = mesh.color_attributes.new(COLOR_ATTR, "FLOAT_COLOR", "POINT")
        flat = []
        for t in thick:
            flat.extend(heat_color(t, tmin, tmax))
        col.data.foreach_set("color", flat)
        mesh.color_attributes.active_color = col
        mesh.materials.append(_get_material())

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

        # Messwerte zusätzlich als Custom Properties am Objekt ablegen
        obj["liner_messwerte"] = [
            {"hoehe_cm": h, "A": t[0], "M": t[1], "P": t[2], "L": t[3], "umfang_cm": c}
            for (h, t, c) in sorted(rows)
        ]
        obj["liner_seite"] = st.side

        _clear_markers()
        if st.show_markers:
            self._make_markers(context, st, rows, right, tmin, tmax, obj)

        for area in context.screen.areas if context.screen else []:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D" and space.shading.type == "SOLID":
                        space.shading.color_type = "VERTEX"

        st.last_info = "min %.1f mm  ·  max %.1f mm  ·  Länge %.1f cm" % (
            info["min_mm"], info["max_mm"], info["length_cm"])
        self.report({"INFO"}, "Liner erzeugt: " + st.last_info)
        return {"FINISHED"}

    def _make_markers(self, context, st, rows, right, tmin, tmax, parent):
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
            ob.color = heat_color(t, tmin, tmax)
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
    bl_description = "Messtabelle als CSV speichern"
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
            w.writerow(["seite", st.side])
            w.writerow(["umfang_bezug", st.circ_reference])
            w.writerow(["distal_mm", "%g" % st.distal_mm])
            w.writerow(["gesamtlaenge_cm", "%g" % st.total_length_cm])
        self.report({"INFO"}, "Gespeichert: " + self.filepath)
        return {"FINISHED"}


class LINER_OT_import_csv(Operator, ImportHelper):
    bl_idname = "liner.import_csv"
    bl_label = "Messwerte importieren"
    bl_description = "Messtabelle aus CSV laden (Trennzeichen ; oder ,)"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})

    def execute(self, context):
        st = _settings(context)
        with open(self.filepath, newline="", encoding="utf-8-sig") as f:
            text = f.read()
        delim = ";" if text.count(";") >= text.count(",") else ","
        rows, meta = [], {}
        for rec in csv.reader(text.splitlines(), delimiter=delim):
            rec = [c.strip() for c in rec if c.strip() != ""]
            if not rec:
                continue
            try:
                nums = [float(c.replace(",", ".")) for c in rec]
            except ValueError:
                if len(rec) == 2:
                    meta[rec[0].lower()] = rec[1]
                continue
            if len(nums) >= 6:
                rows.append((nums[0], tuple(nums[1:5]), nums[5]))
        if not rows:
            self.report({"ERROR"}, "Keine Messzeilen gefunden")
            return {"CANCELLED"}
        st.rows.clear()
        for h, t, c in sorted(rows):
            r = st.rows.add()
            r.height_cm, r.thickness_mm, r.circumference_cm = h, t, c
        if meta.get("seite") in ("RIGHT", "LEFT"):
            st.side = meta["seite"]
        if meta.get("umfang_bezug") in ("INNER", "OUTER"):
            st.circ_reference = meta["umfang_bezug"]
        for key, prop in (("distal_mm", "distal_mm"), ("gesamtlaenge_cm", "total_length_cm")):
            if key in meta:
                try:
                    setattr(st, prop, float(meta[key].replace(",", ".")))
                except ValueError:
                    pass
        st.active_index = 0
        self.report({"INFO"}, "%d Messhöhen geladen" % len(rows))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Oberfläche
# ---------------------------------------------------------------------------

class LINER_UL_rows(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.scale_x = 0.9
        sub.prop(item, "height_cm", text="")
        for k in range(4):
            row.prop(item, "thickness_mm", index=k, text="")
        sub = row.row(align=True)
        sub.prop(item, "circumference_cm", text="")


class LINER_PT_main(Panel):
    bl_label = "Liner-Wandstärken"
    bl_idname = "LINER_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Liner"

    def draw(self, context):
        layout = self.layout
        st = _settings(context)

        col = layout.column(align=True)
        col.prop(st, "side", expand=True)

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

        if len(st.rows) == 0:
            box.label(text="Tabelle leer – Standardhöhen laden", icon="INFO")

        col = layout.column(align=True)
        col.prop(st, "circ_reference")
        col.prop(st, "distal_mm")
        col.prop(st, "total_length_cm")

        layout.operator("liner.build", icon="MESH_CYLINDER")
        if st.last_info:
            layout.label(text=st.last_info, icon="INFO")

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
        col.label(text="Farbskala (0 = automatisch)")
        row = col.row(align=True)
        row.prop(st, "scale_min_mm", text="min")
        row.prop(st, "scale_max_mm", text="max")


# ---------------------------------------------------------------------------
# Registrierung
# ---------------------------------------------------------------------------

classes = (
    LINER_PG_row,
    LINER_PG_settings,
    LINER_OT_reset,
    LINER_OT_add_row,
    LINER_OT_remove_row,
    LINER_OT_set_all,
    LINER_OT_build,
    LINER_OT_export_csv,
    LINER_OT_import_csv,
    LINER_UL_rows,
    LINER_PT_main,
    LINER_PT_options,
)


@bpy.app.handlers.persistent
def _on_load(_dummy):
    for scene in bpy.data.scenes:
        _ensure_defaults(scene.liner_settings)


def _init_defaults():
    try:
        for scene in bpy.data.scenes:
            _ensure_defaults(scene.liner_settings)
    except AttributeError:
        # während der Registrierung ist bpy.data u. U. eingeschränkt
        return 0.5
    return None


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.liner_settings = PointerProperty(type=LINER_PG_settings)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_init_defaults, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    del bpy.types.Scene.liner_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    # Beim Ausführen aus dem Text-Editor ggf. vorherige Version entfernen
    try:
        unregister()
    except Exception:
        pass
    register()
    _init_defaults()
