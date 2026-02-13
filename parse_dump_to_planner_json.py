#!/usr/bin/env python3
"""Convert CraftTweaker recipe_dump.txt into mc_prod_planner_v2 JSON format."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


INPUT_HINTS = {
    "ingredient", "ingredients", "input", "inputs", "seed", "soil", "template", "base", "addition",
    "catalyst", "consumes", "consume", "waste", "held_item", "item1", "item2", "item3", "left", "right",
}
OUTPUT_HINTS = {
    "result", "results", "output", "outputs", "produces", "produce", "shears_result", "render", "spawn",
}
IGNORE_HINTS = {
    "id", "type", "recipe", "category", "mode", "power", "duration", "energy", "time", "entity", "dimension"
}


@dataclass
class MaterialRef:
    key: str
    qty: float
    kind: str  # item | fluid


def split_top_level(text: str, sep: str = ",") -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth_paren = depth_brace = depth_bracket = depth_angle = 0
    in_string = False
    string_quote = ""
    esc = False

    for ch in text:
        if in_string:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == string_quote:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            string_quote = ch
            buf.append(ch)
            continue

        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle = max(0, depth_angle - 1)

        if (
            ch == sep
            and depth_paren == 0
            and depth_brace == 0
            and depth_bracket == 0
            and depth_angle == 0
        ):
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def try_number(text: str, default: float = 1.0) -> float:
    try:
        return float(text)
    except Exception:
        return default


def parse_ref_token(token: str, default_qty: float = 1.0) -> List[MaterialRef]:
    token = token.strip()
    if not token or token == "IIngredientEmpty.getInstance()":
        return []

    # Handle OR list
    alts = split_top_level(token, sep="|")
    refs: List[MaterialRef] = []
    for alt in alts:
        alt = alt.strip()
        m = re.search(r"<\s*(item|tag|fluid|chemical):([^>]+)>\s*(?:\*\s*([0-9.]+))?", alt)
        if not m:
            continue
        t, ident, q = m.groups()
        qty = try_number(q, default_qty) if q else default_qty
        kind = "fluid" if t in {"fluid", "chemical"} else "item"
        key = ident if t != "tag" else f"tag:{ident}"
        refs.append(MaterialRef(key=key, qty=qty, kind=kind))
    return refs


def parse_args(statement: str) -> Tuple[str, str, List[str]]:
    m = re.match(r"\s*([^\s.]+)\.([A-Za-z_][A-Za-z0-9_]*)\((.*)\);\s*$", statement)
    if not m:
        raise ValueError("unparseable statement")
    root, method, body = m.groups()
    return root, method, split_top_level(body)


def extract_recipe_id(args: List[str], fallback: str) -> str:
    if not args:
        return fallback
    m = re.match(r'"([^"]+)"', args[0].strip())
    if m:
        return m.group(1)
    return fallback


class JSLikeParser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def parse(self) -> Any:
        self._ws()
        v = self._value()
        self._ws()
        return v

    def _peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _next(self) -> str:
        ch = self._peek()
        self.i += 1
        return ch

    def _ws(self) -> None:
        while self._peek() and self._peek().isspace():
            self.i += 1

    def _value(self) -> Any:
        self._ws()
        ch = self._peek()
        if ch == "{":
            return self._obj()
        if ch == "[":
            return self._arr()
        if ch in ('"', "'"):
            return self._str()
        if ch == "<":
            return self._angle_token()
        if ch == "" or ch == ")":
            return None
        return self._atom()

    def _str(self) -> str:
        quote = self._next()
        out: List[str] = []
        esc = False
        while self._peek():
            ch = self._next()
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                break
            else:
                out.append(ch)
        return "".join(out)

    def _angle_token(self) -> str:
        depth = 0
        out: List[str] = []
        while self._peek():
            ch = self._next()
            out.append(ch)
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
                if depth <= 0:
                    break
        return "".join(out)

    def _atom(self) -> Any:
        start = self.i
        while self._peek() and self._peek() not in ",]}":
            if self._peek().isspace():
                break
            self.i += 1
        raw = self.s[start:self.i]
        if raw in {"true", "false"}:
            return raw == "true"
        if raw in {"null", "None"}:
            return None
        if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", raw):
            return float(raw) if "." in raw else int(raw)
        return raw

    def _key(self) -> str:
        self._ws()
        ch = self._peek()
        if ch in ('"', "'"):
            return self._str()
        start = self.i
        while self._peek() and re.match(r"[A-Za-z0-9_-]", self._peek()):
            self.i += 1
        return self.s[start:self.i]

    def _obj(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        self._next()  # {
        while True:
            self._ws()
            if self._peek() == "}":
                self._next()
                break
            key = self._key()
            self._ws()
            if self._peek() == ":":
                self._next()
            self._ws()
            out[key] = self._value()
            self._ws()
            if self._peek() == ",":
                self._next()
                continue
            if self._peek() == "}":
                self._next()
                break
        return out

    def _arr(self) -> List[Any]:
        out: List[Any] = []
        self._next()  # [
        while True:
            self._ws()
            if self._peek() == "]":
                self._next()
                break
            out.append(self._value())
            self._ws()
            if self._peek() == ",":
                self._next()
                continue
            if self._peek() == "]":
                self._next()
                break
        return out


def extract_from_object(value: Any, path: List[str], in_hint: str | None = None) -> Tuple[List[MaterialRef], List[MaterialRef]]:
    ins: List[MaterialRef] = []
    outs: List[MaterialRef] = []

    if isinstance(value, dict):
        mode = str(value.get("mode", "")).lower()
        local_hint = in_hint
        if mode == "input":
            local_hint = "in"
        elif mode == "output":
            local_hint = "out"

        # local direct item/tag description
        item_id = None
        kind = "item"
        qty = 1.0
        if isinstance(value.get("item"), str):
            item_id = value["item"]
            kind = "item"
        elif isinstance(value.get("items"), str):
            item_id = value["items"]
            kind = "item"
        elif isinstance(value.get("tag"), str):
            item_id = f"tag:{value['tag']}"
            kind = "item"
        elif isinstance(value.get("fluid"), str):
            item_id = f"fluid:{value['fluid']}"
            kind = "fluid"
        elif isinstance(value.get("id"), str) and any(k in value for k in ("count", "amount", "fluid", "chemical", "ingredient")):
            item_id = value["id"]
            if "amount" in value:
                kind = "fluid"

        if item_id:
            if "count" in value:
                qty = try_number(str(value.get("count", 1)), 1.0)
            elif "amount" in value:
                qty = try_number(str(value.get("amount", 1)), 1.0)
            ref = MaterialRef(item_id, qty, kind)
            direction = local_hint
            if not direction:
                pset = {p.lower() for p in path}
                if pset & OUTPUT_HINTS:
                    direction = "out"
                elif pset & INPUT_HINTS:
                    direction = "in"
            if direction == "out":
                outs.append(ref)
            else:
                ins.append(ref)

        for k, v in value.items():
            lk = k.lower()
            child_hint = local_hint
            if child_hint is None and lk in OUTPUT_HINTS:
                child_hint = "out"
            elif child_hint is None and lk in INPUT_HINTS:
                child_hint = "in"
            elif lk in IGNORE_HINTS and child_hint is None:
                child_hint = None
            ci, co = extract_from_object(v, path + [lk], child_hint)
            ins.extend(ci)
            outs.extend(co)

    elif isinstance(value, list):
        for v in value:
            ci, co = extract_from_object(v, path, in_hint)
            ins.extend(ci)
            outs.extend(co)
    elif isinstance(value, str) and value.startswith("<"):
        refs = parse_ref_token(value)
        if in_hint == "out":
            outs.extend(refs)
        else:
            ins.extend(refs)

    return ins, outs


def normalize_refs(refs: List[MaterialRef]) -> List[MaterialRef]:
    by: Dict[Tuple[str, str], float] = {}
    for r in refs:
        if r.qty <= 0:
            continue
        key = (r.key, r.kind)
        by[key] = by.get(key, 0.0) + r.qty
    return [MaterialRef(k, q, kind) for (k, kind), q in sorted(by.items())]


def parse_statement(statement: str, idx: int) -> Dict[str, Any] | None:
    root, method, args = parse_args(statement)
    rid = extract_recipe_id(args, f"line_{idx}")
    inputs: List[MaterialRef] = []
    outputs: List[MaterialRef] = []

    if method == "addShaped" and len(args) >= 3:
        outputs.extend(parse_ref_token(args[1]))
        # grid is arg2
        for tok in split_top_level(args[2].strip().strip("[]"), sep=","):
            inputs.extend(parse_ref_token(tok))

    elif method == "addShapeless" and len(args) >= 3:
        outputs.extend(parse_ref_token(args[1]))
        for tok in split_top_level(args[2].strip().strip("[]"), sep=","):
            inputs.extend(parse_ref_token(tok))

    elif method == "addTransformRecipe" and len(args) >= 5:
        outputs.extend(parse_ref_token(args[1]))
        inputs.extend(parse_ref_token(args[2]))
        inputs.extend(parse_ref_token(args[3]))
        inputs.extend(parse_ref_token(args[4]))

    elif method == "addRecipe":
        if len(args) >= 3:
            outputs.extend(parse_ref_token(args[1]))
            inputs.extend(parse_ref_token(args[2]))
        # additionally scan remaining args for possible fluid/chemical outputs
        for a in args[3:]:
            refs = parse_ref_token(a)
            # heuristic: trailing refs often output in mekanism recipes
            if refs:
                outputs.extend(refs)

    elif method == "addJsonRecipe" and len(args) >= 2:
        # keep custommachinery safe: always try deep parse of entire payload object
        obj_text = args[1]
        try:
            obj = JSLikeParser(obj_text).parse()
            ins, outs = extract_from_object(obj, path=[])
            inputs.extend(ins)
            outputs.extend(outs)
        except Exception:
            # fallback token extraction
            for m in re.finditer(r"<(item|tag|fluid|chemical):[^>]+>(?:\s*\*\s*[0-9.]+)?", obj_text):
                inputs.extend(parse_ref_token(m.group(0)))

    else:
        return None

    inputs = normalize_refs(inputs)
    outputs = normalize_refs(outputs)

    if not outputs:
        return None

    category = "process"
    if "crafting" in root or root == "craftingTable":
        category = ""
    elif "smithing" in root:
        category = "other"

    return {
        "id": f"rec_{idx}",
        "category": category,
        "name": f"{root}.{method}::{rid}",
        "inputs": [{"matId": r.key, "qty": round(r.qty, 6)} for r in inputs if r.qty > 0],
        "outputs": [{"matId": r.key, "qty": round(r.qty, 6)} for r in outputs if r.qty > 0],
        "isSample": False,
        "_sourceRoot": root,
    }


def build_materials(recipes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mats: Dict[str, str] = {}
    for r in recipes:
        for io in r["inputs"] + r["outputs"]:
            mid = io["matId"]
            kind = "fluid" if mid.startswith("fluid:") or mid.startswith("chemical:") else "item"
            if mid.startswith("tag:") and "/water" in mid:
                kind = "fluid"
            mats[mid] = mats.get(mid, kind)
    out = []
    for mid in sorted(mats):
        out.append({
            "id": mid,
            "name": mid,
            "kind": mats[mid],
            "isSample": False,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse recipe_dump.txt into mc_prod_planner_v2 JSON")
    ap.add_argument("input", help="path to recipe_dump.txt")
    ap.add_argument("-o", "--output", default="parsed_state.json", help="output JSON path")
    args = ap.parse_args()

    lines = open(args.input, "r", encoding="utf-8", errors="replace").read().splitlines()
    recipes: List[Dict[str, Any]] = []
    custommachinery_count = 0

    for idx, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("[") or not s.endswith(");"):
            continue
        try:
            parsed = parse_statement(s, idx)
        except Exception:
            continue
        if not parsed:
            continue
        if "custommachinery" in parsed.get("_sourceRoot", ""):
            custommachinery_count += 1
        recipes.append(parsed)

    for r in recipes:
        r.pop("_sourceRoot", None)

    materials = build_materials(recipes)
    state = {
        "version": 2,
        "materials": materials,
        "recipes": recipes,
        "selectedRecipeByOutput": {},
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"recipes={len(recipes)} materials={len(materials)} custommachinery_recipes={custommachinery_count}")


if __name__ == "__main__":
    main()
