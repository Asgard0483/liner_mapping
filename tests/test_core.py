"""Tests ohne Blender: python3 -m unittest discover -s tests"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from liner_wandstaerke import compare, report  # noqa: E402
from liner_wandstaerke.database import LinerDB, empty_record  # noqa: E402
from liner_wandstaerke.geometry import (  # noqa: E402
    LinerModel, PeriodicPchip, Pchip, build_liner_geometry, marker_positions, point_angles,
    resample_ring, ring_thickness)

HEIGHTS = (4, 8, 12, 16, 20, 25, 30)
CIRC = (24, 27, 29.5, 31.5, 33, 34.5, 36)


def make_record(name="Alpha", size="26", thick=(6, 5.5, 5, 4.5, 4, 3.5, 3), form="konisch",
                offset=(0, 0, 0, 0), circ_add=0.0, nenn=0.0, heights=HEIGHTS):
    rec = empty_record()
    rec.update(hersteller=name, artikel=name + " Liner", groesse=size, form=form,
               nenn_wandstaerke_mm=nenn, laenge_cm=32.0)
    rec["messungen"] = [(h, tuple(t + o for o in offset), c + circ_add)
                        for h, t, c in zip(heights, thick, CIRC)]
    return rec


class GeometryTests(unittest.TestCase):
    def test_ring_interpolation_hits_measurements(self):
        vals = (7, 4, 9, 3)
        for k, v in enumerate(vals):
            self.assertAlmostEqual(ring_thickness(vals, math.radians(90 * k)), v)

    def test_pchip_monotone(self):
        p = Pchip([0, 1, 2, 3], [0, 1, 1, 2])
        xs = [i / 50 for i in range(151)]
        ys = [p(x) for x in xs]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(ys, ys[1:])))

    def test_mesh_closed_and_samples(self):
        rec = make_record()
        verts, faces, thick, info = build_liner_geometry(rec["messungen"], segments=32)
        self.assertEqual(len(info["samples"]), len(verts))
        edges = {}
        for f in faces:
            for a, b in zip(f, f[1:] + f[:1]):
                key = (min(a, b), max(a, b))
                edges[key] = edges.get(key, 0) + 1
        self.assertTrue(all(n == 2 for n in edges.values()))
        m = info["model"]
        for (z, th, w), t in zip(info["samples"], thick):
            self.assertAlmostEqual(m.sample(z, th, w), t, places=6)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            LinerModel([(4, (5, 5, 0, 5), 25)])
        with self.assertRaises(ValueError):
            LinerModel([(4, (5, 5, 5, 5), 25), (4, (5, 5, 5, 5), 26)])


class VariablePointsTests(unittest.TestCase):
    def test_ring_exact_and_bounded(self):
        for n in (1, 2, 3, 4, 7, 12, 360):
            vals = [3.0 + (k * 7 % 5) * 0.6 for k in range(n)]
            ring = PeriodicPchip(vals)
            for a, v in zip(point_angles(n), vals):
                self.assertAlmostEqual(ring(a), v)
            xs = [ring(2 * math.pi * i / 1000) for i in range(1000)]
            self.assertGreaterEqual(min(xs), min(vals) - 1e-9)
            self.assertLessEqual(max(xs), max(vals) + 1e-9)

    def test_single_point_is_constant(self):
        ring = PeriodicPchip([4.2])
        self.assertTrue(all(abs(ring(a) - 4.2) < 1e-12 for a in (0, 1, 2, 3, 6)))

    def test_resample_keeps_measured_points(self):
        vals = [7.0, 4.0, 9.0, 3.0]
        r8 = resample_ring(vals, 8)
        self.assertEqual([round(v, 9) for v in r8[::2]], vals)
        self.assertEqual([round(v, 9) for v in resample_ring(r8, 4)], vals)

    def test_mixed_counts_model(self):
        n360 = [5.0 + math.sin(math.radians(k)) for k in range(360)]
        rows = [(4, (6.0,), 24), (12, (7, 4, 9, 3), 29), (20, tuple(n360), 33),
                (30, (3, 3.5, 3, 2.5, 3, 3.5), 36)]
        m = LinerModel(rows)
        self.assertEqual(m.grid_n, 360)
        for h, t, _ in rows:
            for a, v in zip(point_angles(len(t)), t):
                self.assertAlmostEqual(m.thickness(h * 10, a), v, places=9)
                self.assertTrue(m.measured_at(h * 10, a))
        self.assertFalse(m.measured_at(120, math.radians(45)))
        verts, faces, thick, info = build_liner_geometry(rows, segments=48)
        edges = {}
        for f in faces:
            for a, b in zip(f, f[1:] + f[:1]):
                key = (min(a, b), max(a, b))
                edges[key] = edges.get(key, 0) + 1
        self.assertTrue(all(n == 2 for n in edges.values()))
        # Marker: höchstens 36 pro Höhe
        marks = marker_positions(rows)
        self.assertEqual(len(marks), 1 + 4 + 36 + 6)

    def test_limits(self):
        with self.assertRaises(ValueError):
            LinerModel([(4, tuple([5.0] * 361), 25)])
        with self.assertRaises(ValueError):
            LinerModel([(4, (), 25)])

    def test_compare_mixed_counts(self):
        a = make_record("A")
        b = make_record("B")
        b["messungen"] = [(h, tuple(resample_ring(t, 8)), c) for h, t, c in b["messungen"]]
        b["messungen"][2] = (12, (5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5), 29.5)
        a["id"], b["id"] = 1, 2
        r = compare.compare_models(compare.model_from_record(a), compare.model_from_record(b))
        self.assertGreater(r["aehnlichkeit"], 90)
        pts = compare.point_table([compare.model_from_record(a), compare.model_from_record(b)])
        at12 = [p for p in pts if p[0] == 12.0]
        self.assertEqual(len(at12), 12)  # Vereinigung aus 4 und 12 Punkten
        labels = [p[2] for p in at12]
        self.assertEqual(labels[:4], ["A", "30°", "60°", "M"])
        self.assertEqual(at12[3][4], [True, True])  # M bei beiden gemessen
        self.assertEqual(at12[1][4], [False, True])
        html = report.render_html(compare.build_comparison([a, b]))
        self.assertIn("8–12", html)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LinerDB(os.path.join(self.tmp.name, "db.sqlite"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_save_get_update_delete(self):
        rid = self.db.save(make_record())
        rec = self.db.get(rid)
        self.assertEqual(rec["hersteller"], "Alpha")
        self.assertEqual(len(rec["messungen"]), 7)
        rec["groesse"] = "28"
        rec["messungen"] = rec["messungen"][:3]
        self.assertEqual(self.db.save(rec), rid)
        rec2 = self.db.get(rid)
        self.assertEqual((rec2["groesse"], len(rec2["messungen"])), ("28", 3))
        self.db.delete(rid)
        self.assertIsNone(self.db.get(rid))
        self.assertEqual(self.db.con.execute("SELECT COUNT(*) FROM messung").fetchone()[0], 0)

    def test_filters(self):
        self.db.save(make_record("Alpha", "26", form="konisch", nenn=3))
        self.db.save(make_record("Alpha", "28", form="zylindrisch", nenn=6))
        self.db.save(make_record("Beta", "10", form="konisch"))
        self.assertEqual(len(self.db.search(hersteller="alpha")), 2)
        self.assertEqual(len(self.db.search(form="konisch")), 2)
        self.assertEqual(len(self.db.search(groesse="28")), 1)
        self.assertEqual([d["groesse"] for d in self.db.search(wand_min=5)], ["28"])
        # ohne Nennwert zählt der Mittelwert der Messungen (~4.6 mm)
        self.assertEqual([d["hersteller"] for d in self.db.search(wand_min=4, wand_max=5)], ["Beta"])
        self.assertEqual(len(self.db.search(text="zylin")), 1)
        self.assertEqual(self.db.distinct("groesse"), ["10", "26", "28"])
        self.assertEqual(self.db.distinct("groesse", {"hersteller": "Alpha"}), ["26", "28"])

    def test_variable_points_roundtrip(self):
        rec = make_record()
        rec["messungen"][0] = (4, (6.5,), 24)
        rec["messungen"][3] = (16, tuple(4 + k / 360 for k in range(360)), 31.5)
        rid = self.db.save(rec)
        got = self.db.get(rid)["messungen"]
        self.assertEqual(got[0][1], (6.5,))
        self.assertEqual(len(got[3][1]), 360)
        self.assertEqual(self.db.search()[0]["punkte"], [1, 4, 4, 360, 4, 4, 4])
        path = os.path.join(self.tmp.name, "v.csv")
        self.db.export_csv(path)
        ids = self.db.import_csv(path)
        back = self.db.get(ids[0])["messungen"]
        self.assertEqual([len(t) for _, t, _ in back], [len(t) for _, t, _ in got])
        for (_, t1, _), (_, t2, _) in zip(back, got):
            self.assertTrue(all(abs(a - b) < 1e-4 for a, b in zip(t1, t2)))
        with self.assertRaises(ValueError):
            self.db.save(dict(rec, id=None, messungen=[(4, tuple([5.0] * 361), 24)]))

    def test_migration_from_v1(self):
        import sqlite3
        path = os.path.join(self.tmp.name, "alt.sqlite")
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE liner (id INTEGER PRIMARY KEY AUTOINCREMENT,
              hersteller TEXT NOT NULL DEFAULT '', artikel TEXT NOT NULL DEFAULT '',
              groesse TEXT NOT NULL DEFAULT '', form TEXT NOT NULL DEFAULT '',
              material TEXT NOT NULL DEFAULT '', notiz TEXT NOT NULL DEFAULT '',
              nenn_wandstaerke_mm REAL NOT NULL DEFAULT 0, seite TEXT NOT NULL DEFAULT 'RIGHT',
              umfang_bezug TEXT NOT NULL DEFAULT 'INNER', distal_mm REAL NOT NULL DEFAULT 8,
              laenge_cm REAL NOT NULL DEFAULT 0, erstellt TEXT NOT NULL, geaendert TEXT NOT NULL);
            CREATE TABLE messung (liner_id INTEGER NOT NULL REFERENCES liner(id) ON DELETE CASCADE,
              hoehe_cm REAL NOT NULL, a_mm REAL NOT NULL, m_mm REAL NOT NULL, p_mm REAL NOT NULL,
              l_mm REAL NOT NULL, umfang_cm REAL NOT NULL, PRIMARY KEY (liner_id, hoehe_cm));
            INSERT INTO liner (hersteller, erstellt, geaendert) VALUES ('Alt', 'x', 'x');
            INSERT INTO messung VALUES (1, 4, 6, 5, 7, 4, 24), (1, 8, 5, 5, 5, 5, 27);
        """)
        con.commit()
        con.close()
        db = LinerDB(path)
        rec = db.get(1)
        self.assertEqual(rec["messungen"], [(4.0, (6.0, 5.0, 7.0, 4.0), 24.0),
                                            (8.0, (5.0, 5.0, 5.0, 5.0), 27.0)])
        db.save(dict(rec, id=None))
        self.assertEqual(db.count(), 2)
        db.close()
        LinerDB(path).close()  # erneutes Öffnen ohne Fehler

    def test_import_v1_csv(self):
        path = os.path.join(self.tmp.name, "v1.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("liner_nr;hersteller;artikel;groesse;form;material;notiz;"
                    "nenn_wandstaerke_mm;distal_mm;laenge_cm;seite;umfang_bezug;hoehe_cm;"
                    "anterior_mm;medial_mm;posterior_mm;lateral_mm;umfang_cm\n"
                    "1;Alt;X;26;;;;3;8;32;RIGHT;INNER;4;6;5;7;4;24\n"
                    "1;Alt;X;26;;;;3;8;32;RIGHT;INNER;8;5,5;5;5;5;27\n")
        ids = self.db.import_csv(path)
        self.assertEqual(self.db.get(ids[0])["messungen"][1], (8.0, (5.5, 5.0, 5.0, 5.0), 27.0))

    def test_csv_roundtrip(self):
        self.db.save(make_record("Alpha"))
        self.db.save(make_record("Beta", offset=(1, 0, 0, 0)))
        path = os.path.join(self.tmp.name, "x.csv")
        self.assertEqual(self.db.export_csv(path), 2)
        other = LinerDB(os.path.join(self.tmp.name, "db2.sqlite"))
        ids = other.import_csv(path)
        self.assertEqual(len(ids), 2)
        a, b = self.db.get(2), other.get(ids[1])
        self.assertEqual(a["messungen"], b["messungen"])
        self.assertEqual(a["hersteller"], b["hersteller"])
        other.close()


class CompareTests(unittest.TestCase):
    def test_identical(self):
        m = compare.model_from_record(make_record())
        r = compare.compare_models(m, m)
        self.assertAlmostEqual(r["aehnlichkeit"], 100.0)
        self.assertAlmostEqual(r["mae_mm"], 0.0)
        self.assertAlmostEqual(r["korrelation"], 1.0)

    def test_offset_and_order(self):
        ref = make_record()
        near = make_record("Near", offset=(0.2, 0.2, 0.2, 0.2))
        far = make_record("Far", offset=(2, 2, 2, 2), circ_add=3)
        r_near = compare.compare_models(compare.model_from_record(ref), compare.model_from_record(near))
        self.assertAlmostEqual(r_near["bias_mm"], 0.2, places=6)
        self.assertAlmostEqual(r_near["mae_mm"], 0.2, places=6)
        ref["id"], near["id"], far["id"] = 1, 2, 3
        ranked = compare.rank_similar(ref, [far, near, ref])
        self.assertEqual([r["hersteller"] for r, _ in ranked], ["Near", "Far"])
        self.assertGreater(ranked[0][1]["aehnlichkeit"], ranked[1][1]["aehnlichkeit"])

    def test_local_difference_location(self):
        ref = make_record()
        oth = make_record("Oth")
        h, t, c = oth["messungen"][2]
        oth["messungen"][2] = (h, (t[0], t[1], t[2] + 3, t[3]), c)  # 12 cm posterior
        r = compare.compare_models(compare.model_from_record(ref), compare.model_from_record(oth))
        self.assertAlmostEqual(r["max_mm"], 3.0, places=6)
        self.assertEqual((r["max_hoehe_cm"], r["max_winkel"]), (12.0, "P"))

    def test_partial_overlap_and_none(self):
        a = make_record()
        b = make_record("B", heights=(20, 24, 28, 32, 36, 40, 44))
        c = make_record("C", heights=(50, 54, 58, 62, 66, 70, 74))
        ma, mb, mc = (compare.model_from_record(x) for x in (a, b, c))
        self.assertEqual(compare.compare_models(ma, mb)["bereich_cm"], (20.0, 30.0))
        self.assertIsNone(compare.compare_models(ma, mc))
        d = compare.vertex_differences(ma, mc, [(400.0, 0.0, 1.0), (40.0, 0.0, 0.0)])
        self.assertIsNone(d[0])
        self.assertEqual(d[1], 0.0)

    def test_report(self):
        recs = [make_record("A"), make_record("B", offset=(0.5, -0.3, 1, 0)),
                make_record("C", circ_add=2, heights=(4, 8, 12, 16, 20, 24, 28))]
        for i, r in enumerate(recs):
            r["id"] = i + 1
        comp = compare.build_comparison(recs, ref_index=1)
        self.assertEqual(comp["labels"][0], "B B Liner Gr. 26")
        html = report.render_html(comp)
        self.assertIn("Ähnlichkeit", html)
        self.assertNotIn("__DATA__", html)
        self.assertEqual(len(comp["matrix"]), 3)
        self.assertEqual(comp["matrix"][0][1], comp["matrix"][1][0])

    def test_report_escapes_script(self):
        a, b = make_record("</script><b>x"), make_record("B")
        a["id"], b["id"] = 1, 2
        html = report.render_html(compare.build_comparison([a, b]))
        self.assertNotIn("</script><b>", html)


if __name__ == "__main__":
    unittest.main()
