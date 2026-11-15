# pcbs2-part-linter

A linter for **PC Building Simulator 2** custom part XML files.

It answers the question every PCBS2 mod author eventually asks:

> Why does my custom part show up as a blank white card in the shop?

Nobody has published the answer. This tool contains it, encoded as checks you
can run against your own mods folder.

Download the zip from [Releases](../../releases), or just grab
`pcbs2_part_linter.py` — it is a single file with no install step beyond
`pip install UnityPy`.

---

## The finding

**`Icon Path` does not control the shop card. `Asset Path` does.**

The shop card image is resolved like this:

1. Take the **last path segment** of `Asset Path`
   → `Prefabs/GPU/MSI/GPU_MSI_GeForce_RTX_5090_Suprim` gives `GPU_MSI_GeForce_RTX_5090_Suprim`
2. Look for a **Sprite** in `PCBS2_Data/resources.assets` named
   `<segment>_MINI`, falling back to `<segment>`
3. Found → the card shows that artwork. Not found → **blank white card**.

`Icon Path` is a dead field as far as the card is concerned. Filling it in
changes nothing. This was established by controlled experiment: two parts in
the same screenshot with **identical** `Icon Path` values rendered differently,
and the only difference between them was the last segment of `Asset Path`.

Two corollaries that also matter:

- The lookup is **case-insensitive**. Vanilla ships mixed-case prefab names
  (`..._RTX_5090_Suprim`) against upper-case sprites (`..._RTX_5090_SUPRIM_MINI`)
  and those cards render fine.
- The 512px artwork in `resources.assets` is all blank placeholder. Only the
  `_MINI` variants carry real pixels.

### Cross-references resolve against the shipped game too

`Variant Of` and `Incompatible PC Parts IDs` are read against **both** your
mods folder **and** the ~2600 parts that ship with the game (scraped from the
TextAsset tables in `sharedassets0.assets`). A case that lists a vanilla Hyte
cooler as incompatible is correct, not dangling — flagging it as an error is a
false positive that will make you delete working data.

None of this is documented anywhere — not in `PCBS2.XPL`'s README, not in
`PCBS2 Part Creator`, not on the Nexus page, not in any guide. Guides that tell
you to look for an official "Modding Hub → SDK" menu inside the game are
hallucinated; no such menu exists.

---

## Install

```
pip install UnityPy
```

Python 3.9+ on Windows. Nothing else.

## Run

```
python pcbs2_part_linter.py --game "C:/Games/PCBS2"
```

It looks in `<game>/mods` by default. Point it elsewhere with `--mods`.

First run scans ~3000 Addressables bundles to build a prefab index (~1–4 min
depending on disk). That index is cached under
`%LOCALAPPDATA%\pcbs2-part-linter\` and keyed on a hash of bundle names and
sizes, so later runs take a couple of seconds. Force a rebuild with
`--rebuild-index`.

### Options

| Flag | What it does |
|---|---|
| `--game PATH` | PCBS2 install folder (contains `PCBS2.exe`). Auto-detected from Steam/Epic if omitted. |
| `--mods PATH` | mods folder (default `<game>/mods`) |
| `--json PATH` | also write a machine-readable report |
| `--no-mesh` | skip the prefab index (fast, disables `E002` / `I002`) |
| `--check-level` | add the Level-distribution note |
| `--strict-format` | flag comma-list formatting differences |
| `--rebuild-index` | ignore the cached bundle index |
| `--verbose` | do not group high-volume notes |
| `--limit N` | show at most N issues |
| `--no-color` | plain text output |
| `--quiet` | one summary line only |

Exit code is `1` if any error fired, `0` otherwise — handy in a pre-release
script. `2` means the tool could not run (bad path, missing dependency).

---

## Checks

### Errors

| Code | Meaning | Player-visible symptom |
|---|---|---|
| `E001` | `Asset Path` is empty | part has no prefab |
| `E002` | prefab is not a container in any shipped bundle | crash, or part silently dropped |
| `E003` | no `<segment>_MINI` sprite exists | **blank white card in the shop** |
| `E004` | `Part Type`, `ID`, or `In Game=Yes` missing | row silently skipped by the loader |
| `E006` | duplicate `ID` | the last one loaded wins, earlier parts vanish |
| `E007` | `Variant Of` points at an ID that exists neither in your mods nor in the shipped game | variant styling lost |

### Warnings

| Code | Meaning |
|---|---|
| `W001` | `Icon Path` points at a different part's artwork (the field has no effect — this is about file readability) |
| `E008` | `Incompatible PC Parts IDs` references an ID that exists nowhere |
| `W003` | the icon only resolves because the game ignores case |

### Informational

| Code | Meaning |
|---|---|
| `I001` | ≥90% of parts are Level 1 (`--check-level`) |
| `I002` | the shell's own bundle contains no mesh — see the caveat below |
| `W004` | comma-list field does not use the vanilla `value, value, ` format (`--strict-format`) |

### Caveat on `I002`

`MeshRenderer == 0` inside a bundle does **not** mean the part is invisible.
Most PCBS2 prefabs are *variant patches*: the mesh is pulled from external
`CAB-*` dependencies or assembled at runtime. In a 2822-part test set, 674
prefabs had no mesh of their own and 500 of those declared external
dependencies. Reporting all of them as errors produced 1545 false positives.
So `I002` only fires when there is no mesh **and** no external dependency, and
it is informational — verify in-game before changing anything.

---

## What it deliberately does not do

- It does not modify any file. Read-only.
- It does not validate numeric ranges, prices, or benchmark scores.
- It cannot see whether your own retexture actually looks right.
- It does not detect a part that is correct in the XML but missing from
  `catalog.bin` — that is a separate pipeline.

---

## Self test

```
python self_test.py
```

42 cases covering the resolver, the XML reader, vanilla table scraping, an
end-to-end lint over synthetic fixtures, and the CLI contract. Runs without a
PCBS2 install. Every assertion encodes a fact established against the retail
game — if you change the resolver, these must keep passing.

Three of those cases exist because the first draft of this tool was wrong:

1. **Strict case matching** produced 1564 phantom white cards. The lookup is
   case-insensitive.
2. **`MeshRenderer == 0` treated as invisible** produced 1545 phantom errors.
   Most PCBS2 prefabs are variant patches whose mesh lives in external CABs.
3. **Cross-references checked only against `mods/`** produced 7 phantom dangling
   IDs. Those IDs were vanilla parts all along.

---

## Before you trust it on your own set

The numbers this produces are only as good as the icon library it reads. If you
have replaced `resources.assets` or `resources.assets.resS` with a modded copy,
the linter inspects *your* copy — which is what you want.

## License

MIT.