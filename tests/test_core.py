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
    LinerModel, Pchip, build_liner_geometry, ring_thickness)

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
