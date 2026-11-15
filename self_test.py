#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-test for pcbs2-part-linter.

Runs against synthetic fixtures and the real icon-name resolver, so it works
without a PCBS2 install. This is what you run before publishing a change:

    python self_test.py

Every case below encodes a fact that was established by experiment against the
retail game. If you change the resolver, these must keep passing.
"""

import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pcbs2_part_linter as L

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ok    %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


def section(title):
    print("\n%s" % title)


# ---------------------------------------------------------------------------
# 1. the icon resolver
# ---------------------------------------------------------------------------

def test_resolver():
    section("icon resolver")

    sprites = L.NameSet([
        "GPU_MSI_GeForce_RTX_5090_SUPRIM_MINI",   # upper case, as shipped
        "CAS_Corsair_4000D_Airflow",              # bare sprite, no _MINI
        "PSU_Foo_MINI",
    ])
    textures = L.NameSet(["CPU_Bar_MINI"])         # texture only, no sprite

    # exact casing
    check("exact _MINI hit",
          L.resolve_icon("Prefabs/GPU/MSI/GPU_MSI_GeForce_RTX_5090_SUPRIM", sprites, textures),
          ("MINI", "GPU_MSI_GeForce_RTX_5090_SUPRIM_MINI", False))

    # case-insensitive hit - vanilla ships mixed-case prefabs against upper-case sprites
    check("case-insensitive _MINI hit",
          L.resolve_icon("Prefabs/GPU/MSI/GPU_MSI_GeForce_RTX_5090_Suprim", sprites, textures),
          ("MINI", "GPU_MSI_GeForce_RTX_5090_SUPRIM_MINI", True))

    # bare sprite with no _MINI suffix still renders
    check("bare sprite fallback",
          L.resolve_icon("Prefabs/Cases/Corsair/CAS_Corsair_4000D_Airflow", sprites, textures),
          ("BASE", "CAS_Corsair_4000D_Airflow", False))

    # texture without sprite
    check("texture only",
          L.resolve_icon("Prefabs/CPU/Intel/CPU_Bar", sprites, textures),
          ("TEXONLY", "CPU_Bar_MINI", False))

    # nothing at all -> this is the blank card case
    check("missing -> blank card",
          L.resolve_icon("Prefabs/GPU/NVidia/GPU_Does_Not_Exist", sprites, textures),
          (None, "GPU_Does_Not_Exist", False))

    # only the last path segment matters
    check("only last segment is used",
          L.resolve_icon("a/b/c/d/PSU_Foo", sprites, textures)[0], "MINI")

    check("empty asset path",
          L.resolve_icon("", sprites, textures), (None, "", False))

    check("trailing slash ignored",
          L.resolve_icon("Prefabs/PSU/PSU_Foo/", sprites, textures)[0], "MINI")


# ---------------------------------------------------------------------------
# 2. the XML reader
# ---------------------------------------------------------------------------

def test_parser():
    section("xml parser")

    good = textwrap.dedent("""\
        GPU_MyCard
        <table>
          <tr>
            <td div=""></td>
            <td div="Part Type">GPU</td>
            <td div="ID">GPU_MyCard_001</td>
            <td div="In Game">Yes</td>
            <td div="Part Name">My Card</td>
            <td div="Asset Path">Prefabs/GPU/MyBrand/GPU_MyBrand_Card</td>
          </tr>
        </table>
        """)

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "good.xml")
        open(p, "w", encoding="utf-8").write(good)
        asset_name, rows = L.parse_part_file(p)
        check("asset name from first line", asset_name, "GPU_MyCard")
        check("one row parsed", len(rows), 1)
        check("Part Type read", rows[0].get("Part Type"), "GPU")
        check("Asset Path read", rows[0].get("Asset Path"),
              "Prefabs/GPU/MyBrand/GPU_MyBrand_Card")
        check("empty div skipped", "" in rows[0], False)

        multi = good.replace("</table>", """\
          <tr>
            <td div="Part Type">GPU</td>
            <td div="ID">GPU_MyCard_002</td>
            <td div="In Game">Yes</td>
            <td div="Part Name">My Card 2</td>
          </tr>
        </table>""")
        p2 = os.path.join(td, "multi.xml")
        open(p2, "w", encoding="utf-8").write(multi)
        _, rows2 = L.parse_part_file(p2)
        check("two rows parsed", len(rows2), 2)
        check("second row name", rows2[1].get("Part Name"), "My Card 2")

        # entities and nested tags should not leak into the value
        messy = """\
        X
        <table><tr><td div="Part Name">Foo &amp; <b>Bar</b></td></tr></table>"""
        p3 = os.path.join(td, "messy.xml")
        open(p3, "w", encoding="utf-8").write(messy)
        _, rows3 = L.parse_part_file(p3)
        check("entities and tags stripped", rows3[0].get("Part Name"), "Foo & Bar")

        # BOM tolerance
        p4 = os.path.join(td, "bom.xml")
        open(p4, "w", encoding="utf-8-sig").write(good)
        _, rows4 = L.parse_part_file(p4)
        check("utf-8 BOM tolerated", len(rows4), 1)


# ---------------------------------------------------------------------------
# 3. vanilla table scraping
# ---------------------------------------------------------------------------

def test_vanilla_tables():
    section("vanilla part tables")

    # Vanilla TextAssets carry no div="" attributes: row 0 is a header naming the
    # columns, rows 1..n are positional data. Parsing them by position is what
    # keeps E007/E008 from flagging legitimate references to shipped parts.
    table = textwrap.dedent("""\
        Cases
        <table>
        \t<tr>
        \t\t<td></td><td>Part Type</td><td>Class</td><td>Manufacturer</td><td>ID</td><td>Part Name</td>
        \t</tr>
        \t<tr>
        \t\t<td></td><td>Case</td><td>Case</td><td>ADATA</td><td>Case_Adata_1</td><td>XPG Battlecruiser</td>
        \t</tr>
        \t<tr>
        \t\t<td></td><td>Case</td><td>Case</td><td>be quiet!</td><td>Case_bequiet_39</td><td>Dark Base 700</td>
        \t</tr>
        </table>
        """)

    got = L._vanilla_ids_from_table(table)
    check("vanilla IDs scraped by header position", got, {"Case_Adata_1", "Case_bequiet_39"})
    check("table with no header returns nothing",
          L._vanilla_ids_from_table("<table><tr><td>x</td></tr></table>"), set())
    check("empty input", L._vanilla_ids_from_table(""), set())


# ---------------------------------------------------------------------------
# 4. end-to-end lint on fixtures with a stubbed asset layer
# ---------------------------------------------------------------------------

def test_lint():
    section("end-to-end lint")

    sprites = L.NameSet([
        "GPU_Good_MINI",
        "CAS_Good_MINI",
        "CAS_Good_MS_MINI",
    ])
    textures = L.NameSet()
    prefabs = {
        "Prefabs/GPU/Good/GPU_Good": {"b": "x.bundle", "mr": 4, "v": 1000, "tex": 2, "ext": 3},
        "Prefabs/Cases/Good/CAS_Good": {"b": "y.bundle", "mr": 6, "v": 900, "tex": 3, "ext": 6},
        "Prefabs/Cases/Good/CAS_Good_MS": {"b": "z.bundle", "mr": 0, "v": 0, "tex": 0, "ext": 0},
        # exists, renders, but has no shop sprite -> E003 on its own, no E002
        "Prefabs/GPU/Bad/GPU_Bad": {"b": "w.bundle", "mr": 4, "v": 800, "tex": 2, "ext": 3},
    }

    def part(**kw):
        cells = "".join(
            '<td div="%s">%s</td>' % (k.replace("_", " "), v) for k, v in kw.items()
        )
        return "<tr>%s</tr>" % cells

    files = {
        "ok.xml": "GOOD\n<table>\n" + part(
            Part_Type="GPU", ID="GPU_Good_1", In_Game="Yes", Part_Name="Good GPU",
            Asset_Path="Prefabs/GPU/Good/GPU_Good", Icon_Path="ComponentIcons/GPU/GPU_Good",
            Level="12") + "\n</table>",

        "blankcard.xml": "BAD\n<table>\n" + part(
            Part_Type="GPU", ID="GPU_Bad_1", In_Game="Yes", Part_Name="Bad GPU",
            Asset_Path="Prefabs/GPU/Bad/GPU_Bad", Level="3") + "\n</table>",

        "noprefab.xml": "BAD2\n<table>\n" + part(
            Part_Type="GPU", ID="GPU_Ghost_1", In_Game="Yes", Part_Name="Ghost",
            Asset_Path="Prefabs/GPU/Ghost/Nowhere") + "\n</table>",

        "deadicon.xml": "DEAD\n<table>\n" + part(
            Part_Type="GPU", ID="GPU_Good_2", In_Game="Yes", Part_Name="Good GPU II",
            Asset_Path="Prefabs/GPU/Good/GPU_Good",
            Icon_Path="ComponentIcons/GPU/GPU_SomethingElse") + "\n</table>",

        "genericicon.xml": "GEN\n<table>\n" + part(
            Part_Type="GPU", ID="GPU_Good_3", In_Game="Yes", Part_Name="Generic Icon GPU",
            Asset_Path="Prefabs/GPU/Good/GPU_Good",
            Icon_Path="ComponentIcons/Generic/GPU") + "\n</table>",

        "typo.xml": "TYPO\n<table>\n" + part(
            Part_Type="Case", ID="Case_X", In_Game="Yes", Part_Name="Wrong Folder",
            Asset_Path="Prefabs/GPU/Good/GPU_Good") + "\n</table>",

        "varof.xml": "VAR\n<table>\n" + part(
            Part_Type="Case", ID="Case_A", In_Game="Yes", Part_Name="A",
            Asset_Path="Prefabs/Cases/Good/CAS_Good", Variant_Of="Case_Missing") +
            part(Part_Type="Case", ID="Case_B", In_Game="Yes", Part_Name="B",
                 Asset_Path="Prefabs/Cases/Good/CAS_Good", Variant_Of="Case_A") +
            "\n</table>",

        # References a part that ships with the game. Must NOT be reported.
        "vanillaref.xml": "VREF\n<table>\n" + part(
            Part_Type="Case", ID="Case_V", In_Game="Yes", Part_Name="V",
            Asset_Path="Prefabs/Cases/Good/CAS_Good",
            Variant_Of="Case_bequiet_39",
            Incompatible_PC_Parts_IDs="Liquid_Cooler_HYTE_1, Liquid_Cooler_HYTE_2") +
            "\n</table>",

        "dupe.xml": "DUPE\n<table>\n" + part(
            Part_Type="GPU", ID="GPU_Good_1", In_Game="Yes", Part_Name="Dup",
            Asset_Path="Prefabs/GPU/Good/GPU_Good") + "\n</table>",

        "required.xml": "REQ\n<table>\n" + part(
            Part_Type="GPU", ID="", In_Game="Maybe", Part_Name="No ID",
            Asset_Path="Prefabs/GPU/Good/GPU_Good") + "\n</table>",

        "emptymesh.xml": "EMPTY\n<table>\n" + part(
            Part_Type="Case", ID="Case_E", In_Game="Yes", Part_Name="Empty Shell",
            Asset_Path="Prefabs/Cases/Good/CAS_Good_MS") + "\n</table>",
    }

    with tempfile.TemporaryDirectory() as td:
        for name, body in files.items():
            open(os.path.join(td, name), "w", encoding="utf-8").write(body)

        # Stub out the three expensive asset readers.
        vanilla = {"Case_bequiet_39", "Liquid_Cooler_HYTE_1", "Liquid_Cooler_HYTE_2"}
        orig_icon, orig_prefab, orig_vanilla = (
            L.build_icon_index, L.build_prefab_index, L.build_vanilla_id_set)
        L.build_icon_index = lambda game: (sprites, textures)
        L.build_prefab_index = lambda game, **kw: prefabs
        L.build_vanilla_id_set = lambda game: vanilla
        try:
            issues, stats = L.check("X:/fake", td, use_mesh=True, show_progress=False)
        finally:
            (L.build_icon_index, L.build_prefab_index, L.build_vanilla_id_set) = (
                orig_icon, orig_prefab, orig_vanilla)

    by_code = {}
    for i in issues:
        by_code.setdefault(i["code"], []).append(i)

    check("total parts", stats["parts"], 12)
    check("unique ids", stats["unique_ids"], 10)
    check("vanilla ids counted", stats["vanilla_ids"], 3)

    check("E003 blank card found", len(by_code.get("E003", [])), 1)
    check("E002 missing prefab found", len(by_code.get("E002", [])), 1)
    check("E003 not duplicated on a missing prefab",
          len([i for i in by_code.get("E003", []) if "Nowhere" in (i.get("value") or "")]), 0)
    check("E004 missing ID / bad In Game", len(by_code.get("E004", [])), 2)
    check("E006 duplicate ID", len(by_code.get("E006", [])), 1)
    check("E007 bad Variant Of", len(by_code.get("E007", [])), 1)
    check("E008 dangling Incompatible ID",
          len([i for i in by_code.get("E008", []) if "ASROCK" in (i.get("value") or "")
               or "MSI_43" in (i.get("value") or "")]), 0)

    # A reference to a shipped part is valid and must stay silent.
    vanilla_noise = [
        i for i in issues
        if i["part_name"] == "V" and i["code"] in ("E007", "E008")
    ]
    check("vanilla references are not reported", len(vanilla_noise), 0)
    check("W001 dead Icon Path", len(by_code.get("W001", [])), 1)

    # Case-only difference and shared generic icons must NOT be flagged - vanilla
    # ships upper-case sprites against mixed-case prefabs, and generic icons are
    # a legitimate choice.
    check("W001 ignores case-only difference",
          len([i for i in by_code.get("W001", []) if "somethingelse" in (i.get("value") or "").lower()]), 1)
    check("W001 ignores shared generic icons",
          len([i for i in by_code.get("W001", []) if "generic" in (i.get("value") or "").lower()]), 0)
    check("W002 type/folder mismatch", len(by_code.get("W002", [])), 1)
    check("I002 empty shell", len(by_code.get("I002", [])), 1)

    # The good part must be silent: correct icon, correct folder, matching Icon Path.
    good_issues = [
        i for i in issues
        if i["part_name"] == "Good GPU" and i["code"] in ("E003", "W001", "W002", "E002")
    ]
    check("clean part produces no icon/type noise", len(good_issues), 0)

    # Severity must be sorted errors first.
    order = [L.SEVERITY_ORDER[i["severity"]] for i in issues]
    check("issues sorted by severity", order == sorted(order), True)


# ---------------------------------------------------------------------------
# 4. CLI smoke test
# ---------------------------------------------------------------------------

def test_cli():
    section("cli")

    import subprocess

    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "pcbs2_part_linter.py")

    r = subprocess.run([sys.executable, script, "--help"], capture_output=True, text=True)
    check("--help exits 0", r.returncode, 0)
    check("--help mentions the white card", "blank white" in r.stdout.lower(), True)

    r = subprocess.run([sys.executable, script, "--game", "X:/nonexistent"],
                       capture_output=True, text=True)
    check("bad --game exits 2", r.returncode, 2)

    r = subprocess.run([sys.executable, script, "--game", "X:/nonexistent", "--no-color"],
                       capture_output=True, text=True)
    check("bad folder gets a precise message",
          "does not look like a PCBS2 install folder" in r.stderr, True)


def main():
    print("pcbs2-part-linter self test")
    test_resolver()
    test_parser()
    test_vanilla_tables()
    test_lint()
    test_cli()

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())