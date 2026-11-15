# Nexus 发布文案（PCBS2 mod 页）

## 标题
pcbs2-part-linter - why is my part a blank white card?

## 摘要 / About this mod（Nexus "About" 字段，约 250 字符）

Lints your PCBS2 custom part XMLs and tells you exactly which ones will show up as a blank white card in the shop. It also explains how the card image is actually resolved - something no other tool or guide covers.

---

## 完整描述（正文）

### Why your custom part is a blank white card

If you have ever made a PCBS2 part and watched it appear in the shop as a featureless white rectangle, this is the answer. It is not documented anywhere else - not in PCBS2.XPL's readme, not in PCBS2 Part Creator, not in any guide.

**The shop card image is NOT taken from `Icon Path`. It is taken from `Asset Path`.**

The game resolves the card like this:

1. Take the **last path segment** of `Asset Path`
   → `Prefabs/GPU/MSI/GPU_MSI_GeForce_RTX_5090_Suprim` gives `GPU_MSI_GeForce_RTX_5090_Suprim`
2. Look for a **Sprite** in `PCBS2_Data/resources.assets` named `<segment>_MINI`
3. Found → the card shows that artwork. Not found → blank white card.

`Icon Path` is a dead field. Filling it in changes nothing.

I proved this with a controlled experiment: two parts visible in the same shop screenshot, with **identical** `Icon Path` values, rendered differently. The only difference between them was the last segment of `Asset Path`.

Two more details that matter:

- **The lookup ignores case.** Vanilla ships mixed-case prefab names (`..._RTX_5090_Suprim`) against upper-case sprites (`..._RTX_5090_SUPRIM_MINI`) and those cards render fine. If your tool compares case-sensitively it will report hundreds of false white cards.
- **The 512px artwork is all blank placeholder.** Only the `_MINI` variants contain real pixels. Decoding the large image and finding it empty does not mean the card is broken.

### What this tool does

It runs a set of checks over your mods folder and tells you which parts will misbehave before you launch the game.

| Code | Meaning | What the player sees |
|---|---|---|
| E001 | `Asset Path` is empty | part has no prefab |
| E002 | prefab is not a container in any shipped bundle | crash, or part silently dropped |
| E003 | no `<segment>_MINI` sprite exists | **blank white card in the shop** |
| E004 | `Part Type`, `ID`, or `In Game=Yes` missing | row silently skipped by the loader |
| E006 | duplicate `ID` | last one loaded wins, earlier parts vanish |
| E007 | `Variant Of` points at an ID that exists nowhere | variant styling lost |
| E008 | `Incompatible PC Parts IDs` references an ID that exists nowhere | no effect, usually a typo |
| W001 | `Icon Path` points at another part's artwork | cosmetic only |
| W003 | icon only resolves because the game ignores case | harmless |
| I002 | the shell's own bundle contains no mesh | usually still renders - see below |

It does not modify anything. Read-only.

### Cross-references are checked against the shipped game too

`Variant Of` and `Incompatible PC Parts IDs` are validated against your mods folder **and** the ~2600 parts that ship with the game (scraped from the TextAsset tables in `sharedassets0.assets`). A case that lists a vanilla Hyte cooler as incompatible is correct. Flagging it as an error is a false positive that will make you delete working data.

### A warning about `MeshRenderer == 0`

Most PCBS2 prefabs are **variant patches**: the mesh is pulled from external `CAB-*` dependencies or assembled at runtime. In my own 2822-part set, 674 prefabs had no mesh inside their own bundle and 500 of those declared external dependencies. Reporting all of them as broken produced 1545 false positives.

So this tool only mentions it as an informational note, and only when there is no mesh **and** no external dependency. Verify in-game before changing anything - AMD CPU models legitimately have no runtime mesh.

### Install

1. Install Python 3.9+ (tick "Add Python to PATH")
2. `pip install UnityPy`
3. Run:

```
python pcbs2_part_linter.py --game "C:/Games/PCBS2"
```

It looks in `<game>/mods` by default. The first run indexes ~3000 bundles (1-4 minutes, cached afterwards in `%LOCALAPPDATA%\pcbs2-part-linter\`). Later runs take a few seconds.

Useful flags: `--mods PATH`, `--json report.json`, `--no-mesh` (skip the bundle scan), `--verbose`, `--limit N`, `--no-color`, `--quiet`. See `--help`.

Exit code is 1 if any error fired, 0 if clean.

### Self test

```
python self_test.py
```

42 cases. Runs without a PCBS2 install.

Three of those cases exist because my first draft was wrong:

1. Strict case matching produced 1564 phantom white cards.
2. Treating `MeshRenderer == 0` as invisible produced 1545 phantom errors.
3. Checking cross-references only against `mods/` produced 7 phantom dangling IDs - those IDs were vanilla parts all along.

### Credits

The bundle/index approach leans on UnityPy by K0lb3.

Source and downloads: https://github.com/SEFUI12/pcbs2-part-linter

Issues and pull requests welcome - especially if you find a case where the linter disagrees with what the game actually does.

### Permissions

MIT. Use it, fork it, bundle it, whatever.

---

## 标签（Nexus tags）
Utilities for Modders, Utilities for Players

## 分类
Miscellaneous

## 文件
`pcbs2-part-linter-1.0.0.zip`（含 py / README / QUICKSTART / LICENSE / requirements）