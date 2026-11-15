#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pcbs2-part-linter
=================

A linter for PC Building Simulator 2 custom part XML files.

It answers the question every PCBS2 mod author eventually asks:

    "Why does my custom part show up as a blank white card in the shop?"

The answer, established by controlled experiment against the shipped game
assets, is:

    The shop card image is NOT taken from the `Icon Path` field.

    It is looked up in `PCBS2_Data/resources.assets` by taking the LAST path
    segment of `Asset Path` and searching for a Sprite named `<segment>_MINI`.

    Found -> the card shows that artwork.
    Not found -> the card renders blank/white.

`Icon Path` is a dead field as far as the shop card is concerned. Filling it
in has no effect on what the card looks like.

This tool does not modify anything. It only reads your XML files and the
game's own asset bundles, and reports what the game will actually do with
them.

Usage
-----
    python pcbs2_part_linter.py --game "C:/Games/PCBS2"
    python pcbs2_part_linter.py --game "C:/Games/PCBS2" --mods "./mods"
    python pcbs2_part_linter.py --json report.json
    python pcbs2_part_linter.py --no-mesh          (skip the slow 3D check)

Requires: UnityPy  (pip install UnityPy)
License: MIT
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

CACHE_VERSION = 1
ICON_ARCHIVE = os.path.join("PCBS2_Data", "resources.assets")
BUNDLE_DIR = os.path.join("PCBS2_Data", "StreamingAssets", "aa", "StandaloneWindows64")
# Vanilla part tables live here as TextAssets (CPU / GPU / Cases / Motherboard / ...).
# IDs referenced by `Variant Of` and `Incompatible PC Parts IDs` may point at these
# vanilla parts, so they have to be part of the known-ID set.
VANILLA_ARCHIVE = os.path.join("PCBS2_Data", "sharedassets0.assets")
VANILLA_TABLES = (
    "CPU", "GPU", "RAM", "Motherboard", "Cases", "Coolers", "Case Fans",
    "Water Cooling", "Water Cooled GPU", "PSU", "Storage", "Cables",
    "Mice", "Keyboards", "Mousepads", "Monitors", "Headsets", "Thermal Paste",
)

# Known `Part Type` values -> the prefab folder the part is expected to live in.
# Used by W002 (type/folder mismatch). Values are prefixes of `Asset Path`.
PART_TYPE_FOLDERS = {
    "CPU": ("Prefabs/CPU",),
    "Motherboard": ("Prefabs/MotherBoards",),
    "GPU": ("Prefabs/GPU",),
    "RAM": ("Prefabs/RAM",),
    "M2": ("Prefabs/SSD", "Prefabs/M2"),
    "SSD": ("Prefabs/SSD",),
    "PSU": ("Prefabs/PSU",),
    "Case": ("Prefabs/Cases",),
    "Air Cooler": ("Prefabs/CoolingSystems", "Prefabs/AirCoolers"),
    "Water Cooler": ("Prefabs/CoolingSystems", "Prefabs/Radiators"),
    "Radiator": ("Prefabs/Radiators",),
    "Fan": ("Prefabs/CoolingSystems", "Prefabs/Fans"),
    "Thermal Paste": ("Prefabs/Generic",),
}

# Fields that, if present, hold a comma separated list. Used by W004 to flag
# inconsistent separators, since the vanilla files use "value, value, " with a
# trailing comma.
LIST_FIELDS = (
    "Supported Memory Types",
    "Memory Speed Steps",
    "CPU Socket List",
    "Motherboard Size",
)

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
SEVERITY_LABEL = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}


# --------------------------------------------------------------------------
# tiny terminal colour helper
# --------------------------------------------------------------------------

class _C:
    on = True

    @staticmethod
    def wrap(code: str, text: str) -> str:
        if not _C.on:
            return text
        return "\x1b[%sm%s\x1b[0m" % (code, text)

    @classmethod
    def red(cls, t):
        return cls.wrap("31", t)

    @classmethod
    def yellow(cls, t):
        return cls.wrap("33", t)

    @classmethod
    def green(cls, t):
        return cls.wrap("32", t)

    @classmethod
    def cyan(cls, t):
        return cls.wrap("36", t)

    @classmethod
    def dim(cls, t):
        return cls.wrap("90", t)

    @classmethod
    def bold(cls, t):
        return cls.wrap("1", t)


def _enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


# --------------------------------------------------------------------------
# game directory discovery
# --------------------------------------------------------------------------

_STEAM_HINTS = (
    r"C:\Program Files (x86)\Steam\steamapps\common\PC Building Simulator 2",
    r"C:\Program Files\Steam\steamapps\common\PC Building Simulator 2",
    r"D:\Steam\steamapps\common\PC Building Simulator 2",
    r"D:\SteamLibrary\steamapps\common\PC Building Simulator 2",
    r"E:\SteamLibrary\steamapps\common\PC Building Simulator 2",
)
_EPIC_HINTS = (
    r"C:\Program Files\Epic Games\PCBuildingSimulator2",
    r"D:\Epic Games\PCBuildingSimulator2",
)


def looks_like_game(path: str) -> bool:
    return bool(path) and os.path.isdir(path) and os.path.isdir(os.path.join(path, "PCBS2_Data"))


def find_game_dir(explicit: str | None, announce_explicit: bool = True) -> str | None:
    """
    Locate the PCBS2 install folder.

    Returns None when the caller passed an explicit path that does not look like
    a game folder, so main() can print a precise message instead of silently
    continuing with a bogus path.
    """
    if explicit:
        path = os.path.abspath(explicit)
        if announce_explicit and not looks_like_game(path):
            return None
        return path
    env = os.environ.get("PCBS2_DIR")
    if looks_like_game(env or ""):
        return os.path.abspath(env)
    for cand in _STEAM_HINTS + _EPIC_HINTS:
        if looks_like_game(cand):
            return cand
    # last resort: scan a couple of common roots one level deep
    for root in ("C:\\Games", "D:\\Games", "C:\\Program Files (x86)", "D:\\"):
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                cand = os.path.join(root, name)
                if looks_like_game(cand):
                    return os.path.abspath(cand)
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# XML parsing (tolerant, mirrors the game's HTMLTableReader)
# --------------------------------------------------------------------------

_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td\b([^>]*)>(.*?)</td>", re.S | re.I)
_DIV_RE = re.compile(r'div\s*=\s*"([^"]*)"', re.I)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return text.strip()


def parse_part_file(path: str):
    """Return (asset_name, [row_dict, ...]). Each row maps div -> value."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        raw = fh.read()

    # The first non-empty line is an arbitrary asset name used only for logging.
    lines = raw.splitlines()
    asset_name = ""
    for ln in lines:
        if ln.strip():
            asset_name = ln.strip()
            break

    # Everything from the first <tr> onward is data.
    start = raw.lower().find("<tr")
    body = raw[start:] if start >= 0 else raw

    rows = []
    for tr in _TR_RE.finditer(body):
        row = {}
        for td in _TD_RE.finditer(tr.group(1)):
            m = _DIV_RE.search(td.group(1))
            if not m:
                continue
            key = m.group(1).strip()
            if not key:
                continue
            row[key] = _strip_tags(td.group(2))
        if row:
            rows.append(row)
    return asset_name, rows


# --------------------------------------------------------------------------
# icon index: Sprite names inside resources.assets
# --------------------------------------------------------------------------

class NameSet:
    """
    A case-insensitive name lookup over the icon library.

    The game resolves `<shell>_MINI` case-insensitively: vanilla prefab names are
    mixed case (`..._RTX_5090_Suprim`) while the shipped sprites are upper case
    (`..._RTX_5090_SUPRIM_MINI`), and those cards still render. So a strict
    comparison produces thousands of false positives. We keep both maps so the
    report can still tell you when only the casing differs.
    """

    __slots__ = ("exact", "lower")

    def __init__(self, names=()):
        self.exact = set(names)
        self.lower = {}
        for n in self.exact:
            self.lower.setdefault(n.lower(), n)

    def get(self, name: str, ci: bool = True):
        """Return (True, canonical_name, case_differs) or (False, None, False)."""
        if name in self.exact:
            return True, name, False
        if ci:
            hit = self.lower.get(name.lower())
            if hit is not None:
                return True, hit, True
        return False, None, False

    def __len__(self):
        return len(self.exact)

    def __contains__(self, name):
        return name in self.exact


def build_icon_index(game: str):
    """Return (NameSet_of_sprites, NameSet_of_textures)."""
    import UnityPy

    archive = os.path.join(game, ICON_ARCHIVE)
    if not os.path.exists(archive):
        raise FileNotFoundError(
            "Cannot find %s\n"
            "Point --game at the folder that contains PCBS2.exe." % archive
        )

    sprites, textures = set(), set()
    env = UnityPy.load(archive)
    for obj in env.objects:
        type_name = obj.type.name
        if type_name not in ("Sprite", "Texture2D"):
            continue
        try:
            name = obj.read().m_Name
        except Exception:
            continue
        if type_name == "Sprite":
            sprites.add(name)
        else:
            textures.add(name)
    return NameSet(sprites), NameSet(textures)


def resolve_icon(asset_path: str, sprites: NameSet, textures: NameSet):
    """
    Reproduce the game's shop-card lookup.

    Returns (kind, resolved_name, case_differs) where kind is one of:
        "MINI"    sprite `<segment>_MINI` exists  -> artwork shown
        "BASE"    sprite named `<segment>` exists -> artwork shown
        "TEXONLY" only a raw Texture2D exists     -> usually still renders
        None      nothing found                   -> blank white card
    """
    if not asset_path:
        return None, "", False
    segment = [p for p in asset_path.replace("\\", "/").split("/") if p]
    if not segment:
        return None, "", False
    base = segment[-1]
    mini = base + "_MINI"

    ok, canon, diff = sprites.get(mini)
    if ok:
        return "MINI", canon, diff
    ok, canon, diff = sprites.get(base)
    if ok:
        return "BASE", canon, diff
    ok, canon, diff = textures.get(mini)
    if ok:
        return "TEXONLY", canon, diff
    ok, canon, diff = textures.get(base)
    if ok:
        return "TEXONLY", canon, diff
    return None, base, False


# --------------------------------------------------------------------------
# prefab index: which bundle holds each prefab, plus mesh stats
# --------------------------------------------------------------------------

def _cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "pcbs2-part-linter")
    os.makedirs(path, exist_ok=True)
    return path


def _bundle_signature(bundle_dir: str) -> str:
    items = []
    try:
        for name in sorted(os.listdir(bundle_dir)):
            if name.endswith(".bundle"):
                items.append("%s:%d" % (name, os.path.getsize(os.path.join(bundle_dir, name))))
    except Exception:
        return ""
    return hashlib.sha1("\n".join(items).encode("utf-8", "replace")).hexdigest()


def build_prefab_index(game: str, rebuild: bool = False, show_progress: bool = True):
    """
    Map prefab address -> {bundle, mr, v, tex}.

    mr  = number of MeshRenderer / SkinnedMeshRenderer in that bundle
    v   = max vertex count among Meshes in that bundle
    tex = number of Texture2D in that bundle
    """
    bundle_dir = os.path.join(game, BUNDLE_DIR)
    if not os.path.isdir(bundle_dir):
        raise FileNotFoundError(
            "Cannot find the Addressables bundle folder:\n  %s\n"
            "Is this really the PCBS2 install folder?" % bundle_dir
        )

    sig = _bundle_signature(bundle_dir)
    cache_path = os.path.join(_cache_dir(), "prefab_index.json")

    if not rebuild and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("version") == CACHE_VERSION and cached.get("sig") == sig:
                if show_progress:
                    print(_C.dim("  (prefab index loaded from cache: %d entries)" % len(cached["prefabs"])))
                return cached["prefabs"]
        except Exception:
            pass

    import UnityPy

    files = sorted(f for f in os.listdir(bundle_dir) if f.endswith(".bundle"))
    prefabs: dict[str, dict] = {}
    if show_progress:
        print(_C.dim("  index not cached, scanning %d bundles (one time, ~1 min)..." % len(files)))

    t0 = time.time()
    for i, name in enumerate(files, 1):
        path = os.path.join(bundle_dir, name)
        try:
            env = UnityPy.load(path)
            addrs = []
            for key in env.container:
                addr = key.replace("Assets/AddressableAssets/", "").rsplit(".", 1)[0]
                addrs.append(addr)

            mr = verts = tex = 0
            for obj in env.objects:
                kind = obj.type.name
                if kind in ("MeshRenderer", "SkinnedMeshRenderer"):
                    mr += 1
                elif kind == "Mesh":
                    try:
                        verts = max(verts, obj.read().m_VertexData.m_VertexCount)
                    except Exception:
                        pass
                elif kind == "Texture2D":
                    tex += 1

            # A prefab that lives alone in its bundle is usually a *variant patch*:
            # the visual mesh is pulled in from external CAB files (or assembled at
            # runtime), so counting MeshRenderers here says nothing about whether the
            # part is visible. Record how many external dependencies exist so we can
            # avoid crying wolf.
            ext = 0
            try:
                for _name, outer in env.files.items():
                    for _inner, sf in getattr(outer, "files", {}).items():
                        ext = max(ext, len(getattr(sf, "externals", []) or []))
            except Exception:
                ext = 0

            stats = {"b": name, "mr": mr, "v": verts, "tex": tex, "ext": ext}
            for addr in addrs:
                prefabs.setdefault(addr, stats)
        except Exception:
            continue

        if show_progress and (i % 500 == 0 or i == len(files)):
            elapsed = time.time() - t0
            if sys.stdout.isatty():
                print("\r  scanning bundles %d/%d  (%.0fs)" % (i, len(files), elapsed), end="", flush=True)
            else:
                print("  scanning bundles %d/%d  (%.0fs)" % (i, len(files), elapsed), flush=True)
    if show_progress and files:
        print()

    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump({"version": CACHE_VERSION, "sig": sig, "prefabs": prefabs}, fh)
    except Exception:
        pass

    return prefabs


# --------------------------------------------------------------------------
# vanilla part IDs: needed so that cross-references to shipped parts are not
# reported as dangling. A case whose `Incompatible PC Parts IDs` names a vanilla
# Hyte cooler is completely correct - only IDs that exist *nowhere* are broken.
# --------------------------------------------------------------------------

_TR_RE_ANY = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE_ANY = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)


def _vanilla_ids_from_table(text: str) -> set:
    """
    Pull the `ID` column out of a vanilla TextAsset table.

    Vanilla tables have no `div="..."` attributes: the first <tr> is a header row
    naming the columns, and every following <tr> is positional data. So we locate
    the ID column by header name instead of by attribute.
    """
    rows = _TR_RE_ANY.findall(text)
    if len(rows) < 2:
        return set()

    def cells(chunk):
        return [
            re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
            for c in _TD_RE_ANY.findall(chunk)
        ]

    header = cells(rows[0])
    try:
        idx = header.index("ID")
    except ValueError:
        return set()

    ids = set()
    for chunk in rows[1:]:
        vals = cells(chunk)
        if len(vals) > idx:
            val = vals[idx]
            if val:
                ids.add(val)
    return ids


def build_vanilla_id_set(game: str) -> set:
    """IDs of every part shipped with the game, scraped from its TextAsset tables."""
    archive = os.path.join(game, VANILLA_ARCHIVE)
    if not os.path.exists(archive):
        return set()

    import UnityPy

    ids = set()
    try:
        env = UnityPy.load(archive)
    except Exception:
        return set()

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = getattr(data, "m_Name", "") or ""
        if name not in VANILLA_TABLES:
            continue
        blob = getattr(data, "m_Script", None)
        if blob is None:
            continue
        if isinstance(blob, bytes):
            text = blob.decode("utf-8", "ignore")
        elif isinstance(blob, str):
            text = blob
        else:
            continue
        ids |= _vanilla_ids_from_table(text)
    return ids


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

class Issue(dict):
    def __init__(self, code, severity, message, **kw):
        super().__init__(code=code, severity=severity, message=message, **kw)


def _norm_ids(rows):
    ids = {}
    for r in rows:
        pid = r.get("ID", "").strip()
        if pid:
            ids.setdefault(pid, []).append(r)
    return ids


def check(game, mods_dir, *, use_mesh=True, check_level=False, strict_format=False, show_progress=True):
    """
    Run every check. Returns (issues, stats).
    """
    if not os.path.isdir(mods_dir):
        raise FileNotFoundError("mods folder not found: %s" % mods_dir)

    files = sorted(glob.glob(os.path.join(mods_dir, "**", "*.xml"), recursive=True))
    if not files:
        raise FileNotFoundError("no .xml files found under %s" % mods_dir)

    if show_progress:
        print("Reading game assets ...")
    sprites, textures = build_icon_index(game)
    vanilla_ids = build_vanilla_id_set(game)
    if show_progress and vanilla_ids:
        print(_C.dim("  (vanilla part IDs: %d)" % len(vanilla_ids)))

    prefabs = {}
    if use_mesh:
        prefabs = build_prefab_index(game, show_progress=show_progress)
    else:
        # Cheap path: only need to know whether the prefab exists, so reuse the
        # cached index if present, otherwise skip the existence check entirely.
        cache_path = os.path.join(_cache_dir(), "prefab_index.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("version") == CACHE_VERSION:
                    prefabs = cached["prefabs"]
            except Exception:
                prefabs = {}

    if show_progress:
        print("Linting %d XML files ...\n" % len(files))

    issues: list[Issue] = []
    all_rows: list[tuple[str, dict]] = []
    per_file = {}

    for path in files:
        rel = os.path.relpath(path, mods_dir)
        try:
            asset_name, rows = parse_part_file(path)
        except Exception as exc:
            issues.append(Issue(
                "E000", "error",
                "XML could not be parsed: %s" % exc,
                file=rel, part_name="", field="", value="",
                hint="Check for an unclosed <td> or <tr> tag.",
            ))
            continue

        per_file[rel] = len(rows)
        if not rows:
            issues.append(Issue(
                "E000", "error",
                "No <tr> data row found - the game will import 0 parts from this file.",
                file=rel, part_name="", field="", value="",
                hint="The first line is a free-text asset name; every part needs its own <tr> row.",
            ))
            continue

        for row in rows:
            all_rows.append((rel, row))

    # ---- per-row checks -------------------------------------------------
    for rel, row in all_rows:
        name = row.get("Part Name", "") or row.get("ID", "")
        ptype = row.get("Part Type", "").strip()
        pid = row.get("ID", "").strip()
        asset_path = row.get("Asset Path", "").strip()
        icon_path = row.get("Icon Path", "").strip()
        in_game = row.get("In Game", "").strip()

        # E004 required fields
        if not ptype:
            issues.append(Issue(
                "E004", "error", "Missing `Part Type` - the game silently skips this row.",
                file=rel, part_name=name, field="Part Type", value="",
                hint="Must be one of: CPU, Motherboard, GPU, RAM, M2, SSD, PSU, Case, Air Cooler, ...",
            ))
        if not pid:
            issues.append(Issue(
                "E004", "error", "Missing `ID` - the game silently skips this row.",
                file=rel, part_name=name, field="ID", value="",
                hint="Pick a unique ID, e.g. GPU_MyBrand_Model_001.",
            ))
        if in_game.lower() != "yes":
            issues.append(Issue(
                "E004", "error",
                "`In Game` is %r, must be exactly 'Yes' or the part never appears."
                % in_game,
                file=rel, part_name=name, field="In Game", value=in_game,
                hint="Set <td div=\"In Game\">Yes</td>",
            ))

        # E001 / E002 asset path
        if not asset_path:
            issues.append(Issue(
                "E001", "error",
                "`Asset Path` is empty - the part has no prefab to instantiate.",
                file=rel, part_name=name, field="Asset Path", value="",
                hint="Copy the Asset Path of a vanilla part of the same type.",
            ))
        else:
            prefab_missing = False
            if prefabs and asset_path not in prefabs:
                prefab_missing = True
                issues.append(Issue(
                    "E002", "error",
                    "Prefab not found: %s" % asset_path,
                    file=rel, part_name=name, field="Asset Path", value=asset_path,
                    hint="This path is not a container in any shipped bundle. "
                         "The game will crash or silently drop the part when it tries to instantiate it.",
                ))
            elif prefabs and prefabs[asset_path].get("mr", 0) == 0 and not prefabs[asset_path].get("ext"):
                issues.append(Issue(
                    "I002", "info",
                    "No mesh inside this shell's own bundle (MeshRenderer=0, vertices=0) "
                    "and it declares no external dependencies.",
                    file=rel, part_name=name, field="Asset Path", value=asset_path,
                    hint="Most such shells still render because the mesh comes from a variant prefab "
                         "or is assembled at runtime (AMD CPU models do exactly this). "
                         "Only change this if the part is actually invisible in-game.",
                ))

            # E003 white card. Skip it when the prefab itself is missing - that is a
            # single root cause and reporting both just buries the real problem.
            if not prefab_missing:
                kind, resolved, ci = resolve_icon(asset_path, sprites, textures)
                if kind is None:
                    issues.append(Issue(
                        "E003", "error",
                        "Blank white card in the shop - no sprite named `%s_MINI` or `%s`." % (resolved, resolved),
                        file=rel, part_name=name, field="Asset Path", value=asset_path,
                        hint="The shop card is looked up by the LAST segment of Asset Path. "
                             "Find a shell whose name exists as `<name>_MINI` in resources.assets, "
                             "or add the artwork yourself.",
                    ))
                elif kind == "TEXONLY":
                    issues.append(Issue(
                        "E003", "warn",
                        "Only a raw Texture2D named `%s` exists, no Sprite wrapper." % resolved,
                        file=rel, part_name=name, field="Asset Path", value=asset_path,
                        hint="Usually still renders, but check the card in-game.",
                    ))
                elif ci:
                    issues.append(Issue(
                        "W003", "info",
                        "Icon resolves only because the game ignores case: `%s` -> `%s`."
                        % (asset_path.replace("\\", "/").rstrip("/").split("/")[-1], resolved),
                        file=rel, part_name=name, field="Asset Path", value=asset_path,
                        hint="Harmless - the card renders. But if you ever ship your own artwork, "
                             "match the prefab's casing exactly.",
                    ))

            # W002 type/folder mismatch
            expected = PART_TYPE_FOLDERS.get(ptype)
            if expected and not any(asset_path.startswith(p) for p in expected):
                issues.append(Issue(
                    "W002", "warn",
                    "Part Type `%s` but Asset Path points at `%s`."
                    % (ptype, asset_path.split("/")[1] if "/" in asset_path else asset_path),
                    file=rel, part_name=name, field="Part Type", value=ptype,
                    hint="Expected one of: %s" % ", ".join(expected),
                ))

        # W001 dead Icon Path field
        # Only flag it when the Icon Path points at a genuinely different part.
        # Two cases are normal and must stay quiet:
        #   - pointing at a shared generic icon (ComponentIcons/Generic/CPU)
        #   - differing only by letter case, which is how vanilla ships its sprites
        if icon_path and asset_path:
            seg = asset_path.replace("\\", "/").rstrip("/").split("/")[-1]
            icon_parts = [p for p in icon_path.replace("\\", "/").split("/") if p]
            icon_seg = icon_parts[-1] if icon_parts else ""
            different = bool(icon_seg) and icon_seg != seg
            same_modulo_case = icon_seg.lower() == seg.lower()
            # `ComponentIcons/Generic/GPU` is a deliberate shared placeholder icon.
            generic = any(p.lower() == "generic" for p in icon_parts[:-1])
            if different and not same_modulo_case and not generic:
                issues.append(Issue(
                    "W001", "warn",
                    "`Icon Path` points at `%s` but the shop card is keyed on `%s`." % (icon_seg, seg),
                    file=rel, part_name=name, field="Icon Path", value=icon_path,
                    hint="Icon Path has no effect on the shop card - the card comes from Asset Path. "
                         "Point it at the same shell name so the file stays readable.",
                ))

        # W004 comma list format
        if strict_format:
            for fld in LIST_FIELDS:
                val = row.get(fld)
                if val is None:
                    continue
                if val and not val.endswith(","):
                    issues.append(Issue(
                        "W004", "info",
                        "`%s` has no trailing comma: %r" % (fld, val),
                        file=rel, part_name=name, field=fld, value=val,
                        hint="Vanilla files use 'value, value, ' with a trailing comma. "
                             "Matching that keeps diffs clean.",
                    ))

    # ---- cross-file checks ---------------------------------------------
    ids = _norm_ids([r for _, r in all_rows])
    seen_dup = set()
    for pid, rows in ids.items():
        if len(rows) > 1 and pid not in seen_dup:
            seen_dup.add(pid)
            where = [rel for rel, r in all_rows if r.get("ID", "").strip() == pid]
            issues.append(Issue(
                "E006", "error",
                "Duplicate ID `%s` appears %d times." % (pid, len(rows)),
                file=", ".join(sorted(set(where))[:3]), part_name=pid,
                field="ID", value=pid,
                hint="The last row loaded wins; earlier parts silently disappear. "
                     "Keep IDs unique across every XML in the mods folder.",
            ))

    # Anything the game can resolve counts as known: parts you shipped, plus every
    # part that comes with the game. A case that forbids a vanilla Hyte cooler is
    # correct, not dangling.
    known_ids = set(ids) | vanilla_ids

    for rel, row in all_rows:
        name = row.get("Part Name", "") or row.get("ID", "")

        variant = row.get("Variant Of", "").strip()
        if variant and variant not in known_ids:
            issues.append(Issue(
                "E007", "error",
                "`Variant Of` references unknown ID `%s`." % variant,
                file=rel, part_name=name, field="Variant Of", value=variant,
                hint="Neither your mods folder nor the shipped game defines this ID. "
                     "Either the referenced part is missing, or the ID has a typo.",
            ))

        incompat = row.get("Incompatible PC Parts IDs", "").strip()
        if incompat:
            for ref in re.split(r"[,\s]+", incompat):
                ref = ref.strip()
                if ref and ref not in known_ids:
                    issues.append(Issue(
                        "E008", "warn",
                        "`Incompatible PC Parts IDs` references unknown ID `%s`." % ref,
                        file=rel, part_name=name, field="Incompatible PC Parts IDs", value=ref,
                        hint="Neither your mods folder nor the shipped game defines this ID. "
                             "The entry has no effect, but it usually means a typo.",
                    ))

    # ---- optional level distribution ------------------------------------
    if check_level:
        levels = []
        for _, r in all_rows:
            v = r.get("Level", "").strip()
            if v.isdigit():
                levels.append(int(v))
        if levels:
            ones = sum(1 for v in levels if v <= 1)
            pct = 100.0 * ones / len(levels)
            if pct >= 90:
                issues.append(Issue(
                    "I001", "info",
                    "%.0f%% of parts are Level 1 (%d of %d)." % (pct, ones, len(levels)),
                    file="(all files)", part_name="", field="Level", value="1",
                    hint="Level 1 means everything is buyable immediately. That is what most "
                         "creators do, but if you want the shop to feel like a progression, "
                         "scale Level with Price.",
                ))

    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["code"], i["file"], i["part_name"]))
    stats = {
        "files": len(files),
        "parts": len(all_rows),
        "sprite_names": len(sprites),
        "prefabs_indexed": len(prefabs),
        "unique_ids": len(ids),
        "vanilla_ids": len(vanilla_ids),
    }
    return issues, stats


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

_COLOR = {"error": _C.red, "warn": _C.yellow, "info": _C.cyan}

# Codes that are usually high volume and low signal. When one of them fires more
# than COLLAPSE_THRESHOLD times it is printed as a single line instead of one
# entry per part, so the handful of real problems stay visible.
COLLAPSE_CODES = ("I002", "W003", "W004", "I001")
COLLAPSE_THRESHOLD = 6


def report(issues, stats, mods_dir, *, limit=None, verbose=False):
    print(_C.bold("pcbs2-part-linter") + _C.dim("   scanned %(files)d files / %(parts)d parts" % stats))
    print(_C.dim("  mods: %s" % mods_dir))
    print(_C.dim("  icon library: %(sprite_names)d sprites   prefab index: %(prefabs_indexed)d entries" % stats))
    print()

    if not issues:
        print(_C.green("No problems found. Every part should render correctly."))
        return 0

    # Collapse noisy repeated codes unless the caller asked for everything.
    collapsed = {}
    if not verbose:
        for code in COLLAPSE_CODES:
            group = [i for i in issues if i["code"] == code]
            if len(group) >= COLLAPSE_THRESHOLD:
                collapsed[code] = group
        issues = [i for i in issues if i["code"] not in collapsed]

    counts = {"error": 0, "warn": 0, "info": 0}
    for i in issues:
        counts[i["severity"]] += 1
    for group in collapsed.values():
        for i in group:
            counts[i["severity"]] += 1

    shown = issues if limit is None else issues[:limit]
    current_file = None
    for i in shown:
        if i["file"] != current_file:
            current_file = i["file"]
            print(_C.bold("  %s" % current_file))
        tag = _COLOR[i["severity"]](i["code"])
        label = _COLOR[i["severity"]](SEVERITY_LABEL[i["severity"]].strip())
        who = i["part_name"] or "-"
        print("    %s %s  %s" % (label, tag, who))
        print("        %s" % i["message"])
        if i.get("field"):
            print(_C.dim("        field: %s" % i["field"]))
        if i.get("value") and i["value"] not in i["message"]:
            print(_C.dim("        value: %s" % i["value"]))
        if i.get("hint"):
            print(_C.dim("        -> %s") % i["hint"])
        print()

    if limit is not None and len(issues) > limit:
        print(_C.dim("  ... %d more issue(s) not shown (use --limit 0 to show all)" % (len(issues) - limit)))
        print()

    if collapsed:
        print(_C.bold("  Grouped notes"))
        for code, group in collapsed.items():
            first = group[0]
            print("    %s %s  %d parts" % (
                _COLOR[first["severity"]](SEVERITY_LABEL[first["severity"]].strip()),
                _COLOR[first["severity"]](code),
                len(group)))
            print("        %s" % first["message"])
            if first.get("hint"):
                print(_C.dim("        -> %s") % first["hint"])
        print(_C.dim("        (use --verbose to list every one of these)"))
        print()

    print(_C.bold("Summary"))
    print("  %s %d" % (_C.red("error"), counts["error"]))
    print("  %s %d" % (_C.yellow("warn "), counts["warn"]))
    print("  %s %d" % (_C.cyan("info "), counts["info"]))
    print()
    if counts["error"] == 0:
        print(_C.green("All errors clear. Warnings above are cosmetic or informational."))
    else:
        print(_C.red("Fix the errors above first - they are the ones players will notice."))
    return counts["error"]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pcbs2_part_linter",
        description="Check PCBS2 custom part XML files for problems the game will silently ignore.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Checks\n"
            "  E001 empty Asset Path          E006 duplicate ID\n"
            "  E002 prefab does not exist     E007 Variant Of points at nothing\n"
            "  E003 blank white shop card     E008 Incompatible IDs points at nothing\n"
            "  E004 missing required field\n"
            "  W001 Icon Path is a dead field\n"
            "  W002 Part Type does not match the Asset Path folder\n"
            "  W003 icon only matches because the game ignores case\n"
            "  W004 inconsistent list separators        (--strict-format)\n"
            "  I001 everything is Level 1               (--check-level)\n"
            "  I002 shell bundle contains no mesh       (informational)\n"
        ),
    )
    ap.add_argument("--game", help="PCBS2 install folder (the one containing PCBS2.exe)")
    ap.add_argument("--mods", help="mods folder (default: <game>/mods)")
    ap.add_argument("--json", metavar="PATH", help="also write a machine readable report")
    ap.add_argument("--no-mesh", action="store_true",
                    help="skip the prefab/mesh scan (much faster, skips E002 and I002)")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    ap.add_argument("--check-level", action="store_true", help="add the Level distribution hint")
    ap.add_argument("--strict-format", action="store_true", help="flag comma list formatting")
    ap.add_argument("--rebuild-index", action="store_true", help="ignore the bundle index cache")
    ap.add_argument("--limit", type=int, default=0,
                    help="show at most N issues (0 = all, default 0)")
    ap.add_argument("--verbose", action="store_true",
                    help="do not group high-volume notes; print every single one")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    args = ap.parse_args(argv)

    if args.no_color:
        _C.on = False
    else:
        _enable_windows_ansi()

    game = find_game_dir(args.game)
    if not game:
        if args.game:
            print(_C.red("That does not look like a PCBS2 install folder:"), file=sys.stderr)
            print("  %s" % os.path.abspath(args.game), file=sys.stderr)
            print("Expected a folder containing PCBS2.exe and a PCBS2_Data subfolder.",
                  file=sys.stderr)
        else:
            print(_C.red("Could not find PC Building Simulator 2."), file=sys.stderr)
            print("Pass the install folder explicitly:", file=sys.stderr)
            print('  python pcbs2_part_linter.py --game "C:/Games/PCBS2"', file=sys.stderr)
        return 2

    mods_dir = args.mods or os.path.join(game, "mods")
    if not os.path.isdir(mods_dir):
        print(_C.red("mods folder not found: %s" % mods_dir), file=sys.stderr)
        print("Create it, or pass --mods <path>.", file=sys.stderr)
        return 2

    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print(_C.red("UnityPy is required."), file=sys.stderr)
        print("  pip install UnityPy", file=sys.stderr)
        return 2

    t0 = time.time()
    try:
        issues, stats = check(
            game, mods_dir,
            use_mesh=not args.no_mesh,
            check_level=args.check_level,
            strict_format=args.strict_format,
            show_progress=not args.quiet,
        )
    except Exception as exc:
        print(_C.red("Lint failed: %s" % exc), file=sys.stderr)
        return 2

    if args.quiet:
        counts = {"error": 0, "warn": 0, "info": 0}
        for i in issues:
            counts[i["severity"]] += 1
        print("files=%d parts=%d errors=%d warnings=%d info=%d  (%.1fs)"
              % (stats["files"], stats["parts"], counts["error"], counts["warn"], counts["info"],
                 time.time() - t0))
        errors = counts["error"]
    else:
        errors = report(issues, stats, mods_dir, limit=(args.limit or None), verbose=args.verbose)
        print(_C.dim("  finished in %.1fs" % (time.time() - t0)))

    if args.json:
        payload = {
            "tool": "pcbs2-part-linter",
            "game": game,
            "mods": mods_dir,
            "stats": stats,
            "counts": {
                s: sum(1 for i in issues if i["severity"] == s)
                for s in ("error", "warn", "info")
            },
            "issues": issues,
        }
        try:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            if not args.quiet:
                print(_C.dim("  report written to %s" % args.json))
        except Exception as exc:
            print(_C.red("could not write JSON: %s" % exc), file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())