"""Reine Geometrie des Liner-Modells (ohne bpy, auch außerhalb von Blender nutzbar).

Konventionen:
  * Höhen werden intern in mm gerechnet, Z = 0 ist das distale Ende (außen).
  * Anatomischer Winkel theta: 0° = anterior, 90° = medial, 180° = posterior,
    270° = lateral. Die Seite (rechts/links) bestimmt nur die Lage im Raum.
  * Raum: -Y = anterior, medial = +X (rechtes Bein) bzw. -X (linkes Bein).
"""

import math

SIDES = ("A", "M", "P", "L")
SIDE_NAMES = ("Anterior", "Medial", "Posterior", "Lateral")
SIDE_ANGLES_DEG = (0.0, 90.0, 180.0, 270.0)


class Pchip:
    """Monotone kubische Hermite-Interpolation (Fritsch-Carlson).

    Kein Überschwingen zwischen den Stützstellen; außerhalb konstant.
    """

    def __init__(self, xs, ys):
        if len(xs) != len(ys) or not xs:
            raise ValueError("Pchip braucht gleich viele x- und y-Werte")
        self.xs = list(xs)
        self.ys = list(ys)
        self.m = self._slopes(self.xs, self.ys)

    @staticmethod
    def _slopes(xs, ys):
        n = len(xs)
        if n == 1:
            return [0.0]
        h = [xs[i + 1] - xs[i] for i in range(n - 1)]
        d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
        if n == 2:
            return [d[0], d[0]]
        m = [0.0] * n
        for i in range(1, n - 1):
            if d[i - 1] * d[i] > 0.0:
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

    def __call__(self, x):
        xs, ys, m = self.xs, self.ys, self.m
        if len(xs) == 1 or x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        i = lo
        h = xs[i + 1] - xs[i]
        t = (x - xs[i]) / h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
                + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])


def pchip(xs, ys, x):
    return Pchip(xs, ys)(x)


MAX_POINTS = 360


def point_angles(n):
    """Winkel (Radiant) von n gleichmäßig verteilten Messpunkten, beginnend anterior
    und in Richtung medial laufend (A -> M -> P -> L)."""
    return [2.0 * math.pi * k / n for k in range(n)]


def point_angle_deg(k, n):
    return 360.0 * k / n


class PeriodicPchip:
    """Monotone kubische Interpolation über den Umfang für n gleichmäßig verteilte
    Werte (Startpunkt anterior). Läuft exakt durch alle Messwerte, ohne
    Überschwingen; ein einzelner Wert ergibt eine konstante Wandstärke."""

    def __init__(self, values):
        self.y = [float(v) for v in values]
        n = len(self.y)
        if n == 0:
            raise ValueError("Keine Messwerte")
        self.n = n
        self.h = 2.0 * math.pi / n
        if n == 1:
            self.m = [0.0]
            return
        d = [(self.y[(k + 1) % n] - self.y[k]) / self.h for k in range(n)]
        m = []
        for k in range(n):
            d0, d1 = d[k - 1], d[k]
            m.append(0.0 if d0 * d1 <= 0.0 else 2.0 * d0 * d1 / (d0 + d1))
        self.m = m

    def __call__(self, theta):
        n = self.n
        if n == 1:
            return self.y[0]
        x = (theta % (2.0 * math.pi)) / self.h
        k = int(x)
        if k >= n:
            k = n - 1
        t = x - k
        k1 = (k + 1) % n
        t2, t3 = t * t, t * t * t
        h = self.h
        return ((2 * t3 - 3 * t2 + 1) * self.y[k] + (t3 - 2 * t2 + t) * h * self.m[k]
                + (-2 * t3 + 3 * t2) * self.y[k1] + (t3 - t2) * h * self.m[k1])


def ring_thickness(values, theta):
    """Wandstärke bei Winkel theta aus n gleichmäßig verteilten Messwerten."""
    return PeriodicPchip(values)(theta)


def resample_ring(values, n_new):
    """Rechnet die Messwerte eines Rings auf n_new gleichmäßig verteilte Punkte um."""
    ring = PeriodicPchip(values)
    return [ring(a) for a in point_angles(n_new)]


def inner_radius_mm(thickness, circumference_cm, circ_reference):
    """Innenradius aus dem Umfang (innen am Stumpf oder außen über dem Liner)."""
    r = circumference_cm * 10.0 / (2.0 * math.pi)
    if circ_reference == "OUTER":
        r -= sum(thickness) / len(thickness)
    return r


def _grid_size(counts):
    """Gemeinsames Winkelraster: kleinstes gemeinsames Vielfaches der Punktzahlen
    (damit jeder Messpunkt exakt auf dem Raster liegt), höchstens 360."""
    m = 1
    for c in counts:
        m = m * c // math.gcd(m, c)
        if m > MAX_POINTS:
            return max(MAX_POINTS, max(counts))
    return m


class LinerModel:
    """Kontinuierliches Modell eines Liners aus den Messwerten.

    rows: Liste von (hoehe_cm, [Wandstärken in mm], umfang_cm). Pro Höhe sind
    1 bis 360 Werte möglich, gleichmäßig über den Umfang verteilt, beginnend
    anterior in Richtung medial (bei 4 Werten also A, M, P, L).
    """

    def __init__(self, rows, *, distal_mm=8.0, circ_reference="INNER"):
        rows = sorted(rows, key=lambda r: r[0])
        if not rows:
            raise ValueError("Keine Messhöhen vorhanden")
        self.rows = [(float(h), tuple(float(v) for v in t), float(c)) for h, t, c in rows]
        self.hs = [r[0] * 10.0 for r in self.rows]
        if len(set(self.hs)) != len(self.hs):
            raise ValueError("Messhöhen müssen eindeutig sein")
        for h, t, _ in self.rows:
            if not 1 <= len(t) <= MAX_POINTS:
                raise ValueError("Pro Höhe sind 1 bis %d Messpunkte möglich (%g cm: %d)"
                                 % (MAX_POINTS, h, len(t)))
        if min(min(r[1]) for r in self.rows) <= 0.0:
            raise ValueError("Wandstärken müssen größer als 0 sein")
        if distal_mm <= 0.0:
            raise ValueError("Distale Wandstärke muss größer als 0 sein")
        if self.hs[0] <= distal_mm:
            raise ValueError("Unterste Messhöhe muss über der distalen Wandstärke liegen")
        self.distal_mm = float(distal_mm)
        self.circ_reference = circ_reference
        self.r_in = [inner_radius_mm(t, c, circ_reference) for (_, t, c) in self.rows]
        if min(self.r_in) <= 0.0:
            raise ValueError("Umfang zu klein für die angegebene Wandstärke")
        self.counts = [len(r[1]) for r in self.rows]
        self.grid_n = _grid_size(self.counts)
        grid = [resample_ring(r[1], self.grid_n) for r in self.rows]
        self._cols = [Pchip(self.hs, [g[j] for g in grid]) for j in range(self.grid_n)]
        self._rin = Pchip(self.hs, self.r_in)
        self._ring_cache = {}

    @property
    def z_min(self):
        return self.hs[0]

    @property
    def z_max(self):
        return self.hs[-1]

    def ring(self, z_mm):
        """Interpolator über den Umfang in Höhe z (zwischengespeichert)."""
        key = round(z_mm, 6)
        ring = self._ring_cache.get(key)
        if ring is None:
            if len(self._ring_cache) > 4096:
                self._ring_cache.clear()
            ring = PeriodicPchip([c(z_mm) for c in self._cols])
            self._ring_cache[key] = ring
        return ring

    def side_values(self, z_mm):
        """Wandstärke anterior, medial, posterior, lateral in Höhe z."""
        ring = self.ring(z_mm)
        return [ring(math.radians(a)) for a in SIDE_ANGLES_DEG]

    def thickness(self, z_mm, theta):
        return self.ring(z_mm)(theta)

    def measured_at(self, z_mm, theta, tol=1e-6):
        """True, wenn genau an dieser Höhe und diesem Winkel gemessen wurde."""
        for h, (_, t, _) in zip(self.hs, self.rows):
            if abs(h - z_mm) < tol:
                x = (theta % (2.0 * math.pi)) / (2.0 * math.pi) * len(t)
                return abs(x - round(x)) < 1e-6
        return False

    def radius_in(self, z_mm):
        return self._rin(z_mm)

    def circumference_in_cm(self, z_mm):
        return 2.0 * math.pi * self.radius_in(z_mm) / 10.0

    def sample(self, z_mm, theta, weight):
        """Wandstärke an einem Geometrie-Sample (siehe build_liner_geometry)."""
        if weight <= 0.0:
            return self.distal_mm
        return weight * self.thickness(z_mm, theta) + (1.0 - weight) * self.distal_mm


def build_liner_geometry(rows, *, distal_mm=8.0, total_length_cm=None,
                         circ_reference="INNER", right_side=True,
                         segments=64, step_mm=5.0, cap_rings=12):
    """Erzeugt die Liner-Geometrie.

    Rückgabe: (verts in Metern, faces, thickness_mm pro Vertex, info-dict).
    info["samples"] enthält pro Vertex (z_mm, theta, gewicht); damit lässt sich
    jede Größe, die von der Wandstärke abhängt (z. B. die Differenz zu einem
    anderen Liner), exakt für jeden Vertex auswerten.
    """
    model = LinerModel(rows, distal_mm=distal_mm, circ_reference=circ_reference)
    hs = model.hs
    top = max(hs[-1], (total_length_cm or 0.0) * 10.0)
    side = 1.0 if right_side else -1.0
    thetas = [2.0 * math.pi * j / segments for j in range(segments)]
    dirs = [(side * math.sin(th), -math.cos(th)) for th in thetas]

    zs = set(hs)
    z = hs[0]
    while z < top:
        zs.add(round(z, 6))
        z += step_mm
    zs.add(top)
    zs = sorted(zs)

    z1 = hs[0]
    r1 = model.radius_in(z1)
    t1 = [model.thickness(z1, th) for th in thetas]

    outer_rings, inner_rings, ring_t, ring_s = [], [], [], []

    # Distale Kappe: Ellipsoid-Viertel unterhalb der untersten Messhöhe.
    # Außen von z1 bis z=0, innen von z1 bis z=distal_mm.
    for i in range(cap_rings, 0, -1):
        phi = 0.5 * math.pi * i / cap_rings
        c, s = math.cos(phi), math.sin(phi)
        zo = z1 - z1 * s
        zi = z1 - (z1 - distal_mm) * s
        orow, irow, trow, srow = [], [], [], []
        for j, (dx, dy) in enumerate(dirs):
            ro = (r1 + t1[j]) * c
            ri = r1 * c
            orow.append((dx * ro, dy * ro, zo))
            irow.append((dx * ri, dy * ri, zi))
            trow.append(t1[j] * c + distal_mm * (1.0 - c))
            srow.append((z1, thetas[j], c))
        outer_rings.append(orow)
        inner_rings.append(irow)
        ring_t.append(trow)
        ring_s.append(srow)

    for z in zs:
        ri = model.radius_in(z)
        orow, irow, trow, srow = [], [], [], []
        for (dx, dy), th in zip(dirs, thetas):
            t = model.thickness(z, th)
            ro = ri + t
            orow.append((dx * ro, dy * ro, z))
            irow.append((dx * ri, dy * ri, z))
            trow.append(t)
            srow.append((z, th, 1.0))
        outer_rings.append(orow)
        inner_rings.append(irow)
        ring_t.append(trow)
        ring_s.append(srow)

    verts, thick, samples, faces = [], [], [], []
    n_rings = len(outer_rings)

    def add_surface(rings, tip):
        tip_idx = len(verts)
        verts.append(tip)
        thick.append(distal_mm)
        samples.append((z1, 0.0, 0.0))
        start = len(verts)
        for ring, trow, srow in zip(rings, ring_t, ring_s):
            verts.extend(ring)
            thick.extend(trow)
            samples.extend(srow)
        return tip_idx, start

    o_tip, o0 = add_surface(outer_rings, (0.0, 0.0, 0.0))
    i_tip, i0 = add_surface(inner_rings, (0.0, 0.0, distal_mm))

    def idx(base, r, j):
        return base + r * segments + (j % segments)

    # Außenfläche (Normalen nach außen)
    for j in range(segments):
        faces.append((o_tip, idx(o0, 0, j + 1), idx(o0, 0, j)))
    for r in range(n_rings - 1):
        for j in range(segments):
            faces.append((idx(o0, r, j), idx(o0, r, j + 1),
                          idx(o0, r + 1, j + 1), idx(o0, r + 1, j)))
    # Innenfläche (Normalen in den Hohlraum)
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

    # Beim linken Bein ist die Umlaufrichtung gespiegelt
    if not right_side:
        faces = [tuple(reversed(f)) for f in faces]

    verts_m = [(x / 1000.0, y / 1000.0, zz / 1000.0) for (x, y, zz) in verts]
    info = {
        "min_mm": min(thick),
        "max_mm": max(thick),
        "length_cm": top / 10.0,
        "max_radius_m": max(math.hypot(v[0], v[1]) for v in verts_m),
        "samples": samples,
        "model": model,
    }
    return verts_m, faces, thick, info


def point_label(k, n):
    """Kurzname eines Messpunkts, z. B. 'A', 'M' oder '45°'."""
    deg = point_angle_deg(k, n)
    for name, a in zip(SIDES, SIDE_ANGLES_DEG):
        if abs(deg - a) < 1e-9:
            return name
    return ("%g°" % round(deg, 2)).replace(".", ",")


def marker_positions(rows, *, circ_reference="INNER", right_side=True, max_per_ring=36):
    """Außenpunkte der Messstellen in Metern: Liste von (name, (x, y, z), mm).

    Bei sehr vielen Punkten pro Höhe wird nur jeder k-te markiert (höchstens
    max_per_ring je Höhe), damit die Szene übersichtlich bleibt."""
    side = 1.0 if right_side else -1.0
    out = []
    for (h, t, circ) in sorted(rows, key=lambda r: r[0]):
        r = inner_radius_mm(t, circ, circ_reference)
        n = len(t)
        step = max(1, -(-n // max_per_ring))
        for k in range(0, n, step):
            th = 2.0 * math.pi * k / n
            ro = r + t[k]
            x, y = side * math.sin(th) * ro, -math.cos(th) * ro
            name = "%gcm_%s" % (h, point_label(k, n))
            out.append((name, (x / 1000.0, y / 1000.0, h / 100.0), t[k]))
    return out
