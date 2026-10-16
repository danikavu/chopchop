"""Import workflow presets from a file: node workflows (flows.Flow) and batch recipes
(workflows.Workflow), as exported by the app or `chopchop workflow NAME`.

A file holds one preset, a list of them, or {"presets": [...]}. Everything is checked
before anything is saved, so a bad file imports nothing. A name that's taken gets
" (imported)" added rather than overwriting the preset you have.
"""

from __future__ import annotations

from pydantic import ValidationError

from . import flows, workflows

RECIPE_KEYS = {"separate", "tools", "kit", "bits", "export"}


def _items(data) -> list:
    if isinstance(data, dict) and isinstance(data.get("presets"), list):
        data = data["presets"]
    items = [data] if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError("no workflow presets in this file")
    return items


def _parse(item, i: int):
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise ValueError(f"item {i + 1} isn't a ChopChop workflow preset")
    is_flow = item.get("kind") == "flow" or "nodes" in item
    if not is_flow and not RECIPE_KEYS & set(item):
        raise ValueError(f"“{item['name']}” isn't a ChopChop workflow preset")
    try:
        obj = flows.Flow(**item) if is_flow else workflows.Workflow(**item)
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        raise ValueError(f"“{item['name']}”: {where}: {err['msg']}") from e
    try:
        (flows.check if is_flow else workflows.check)(obj)
    except ValueError as e:
        hint = " (add it on the Models page first)" if "unknown model" in str(e) else ""
        raise ValueError(f"“{obj.name}”: {e}{hint}") from e
    return is_flow, obj


def import_presets(data) -> dict:
    """Validate every preset in the file, then save them all. Returns the saved names:
    {"flows": [...], "workflows": [...], "renamed": {old: new}}."""
    parsed = [_parse(item, i) for i, item in enumerate(_items(data))]
    out: dict = {"flows": [], "workflows": [], "renamed": {}}
    for is_flow, obj in parsed:
        mod = flows if is_flow else workflows
        taken = {p["name"] for p in mod.saved()} | {p.name for p in mod.PRESETS}
        name, n = obj.name, 1
        while name in taken:
            name = f"{obj.name} (imported)" if n == 1 else f"{obj.name} (imported {n})"
            n += 1
        if name != obj.name:
            out["renamed"][obj.name] = name
            obj.name = name
        mod.save(obj)
        out["flows" if is_flow else "workflows"].append(name)
    return out
