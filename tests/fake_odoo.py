"""A tiny in-memory stand-in for Odoo's XML-RPC `object` proxy.

Supports the handful of models and methods the project-code code uses, with a
small domain evaluator (=, !=, in, ilike, =like, and '|' / '&' prefix operators).
"""
import re
from typing import Any


def _like_to_regex(pattern: str) -> re.Pattern:
    return re.compile("^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$", re.S)


def _match_leaf(rec: dict, leaf) -> bool:
    field, op, value = leaf
    have = rec.get(field)
    if op == "=":
        return have == value
    if op == "!=":
        return have != value
    if op == "in":
        return have in value
    if op == "ilike":
        return isinstance(have, str) and str(value).lower() in have.lower()
    if op == "=like":
        return isinstance(have, str) and bool(_like_to_regex(value).match(have))
    raise NotImplementedError(op)


def _eval(domain: list, rec: dict) -> bool:
    def parse(i: int):
        item = domain[i]
        if item == "|":
            a, i = parse(i + 1)
            b, i = parse(i)
            return (lambda r, a=a, b=b: a(r) or b(r)), i
        if item == "&":
            a, i = parse(i + 1)
            b, i = parse(i)
            return (lambda r, a=a, b=b: a(r) and b(r)), i
        return (lambda r, leaf=item: _match_leaf(r, leaf)), i + 1

    i, fns = 0, []
    while i < len(domain):
        fn, i = parse(i)
        fns.append(fn)
    return all(fn(rec) for fn in fns)


class FakeOdoo:
    """Pass as `obj`; use uid=1. Records every call in `.calls` for assertions."""

    def __init__(self):
        self.data: dict[str, dict[int, dict]] = {
            "res.partner": {}, "res.partner.category": {},
            "project.project": {}, "ir.sequence": {},
        }
        self._next_id = {m: 1 for m in self.data}
        self.calls: list[tuple] = []
        self.data["ir.sequence"][77] = {
            "id": 77, "code": "nxt.customer", "prefix": "C", "padding": 4,
            "number_next_actual": 200,
        }

    # test helpers
    def add(self, model: str, **vals) -> int:
        rid = self._next_id[model]
        self._next_id[model] += 1
        self.data[model][rid] = {"id": rid, **vals}
        return rid

    def rows(self, model: str) -> list[dict]:
        return list(self.data[model].values())

    # xmlrpc surface
    def execute_kw(self, db, uid, password, model, method, args=(), kwargs=None):
        kwargs = kwargs or {}
        self.calls.append((model, method))
        table = self.data[model]
        if method == "next_by_code":
            seq = next((r for r in table.values() if r["code"] == args[0]), None)
            if not seq:
                return False
            n = seq["number_next_actual"]
            seq["number_next_actual"] += 1
            return f"{seq['prefix']}{n:0{seq['padding']}d}"
        if method in ("search", "search_read"):
            domain = args[0] if args else []
            found = [r for r in table.values() if _eval(domain, r)]
            found.sort(key=lambda r: r["id"])
            if kwargs.get("limit"):
                found = found[: kwargs["limit"]]
            if method == "search":
                return [r["id"] for r in found]
            fields = kwargs.get("fields")
            return [{k: v for k, v in r.items() if not fields or k in fields or k == "id"} for r in found]
        if method == "read":
            ids, fields = args[0], kwargs.get("fields")
            return [{k: v for k, v in table[i].items() if not fields or k in fields or k == "id"} for i in ids]
        if method == "create":
            vals = dict(args[0])
            if "category_id" in vals:
                vals["category_id"] = self._apply_m2m([], vals["category_id"])
            return self.add(model, **vals)
        if method == "write":
            ids, vals = args[0], dict(args[1])
            for i in ids:
                if "category_id" in vals:
                    table[i]["category_id"] = self._apply_m2m(table[i].get("category_id", []), vals["category_id"])
                table[i].update({k: v for k, v in vals.items() if k != "category_id"})
            return True
        raise NotImplementedError(f"{model}.{method}")

    @staticmethod
    def _apply_m2m(current: list, commands: list) -> list:
        ids = list(current)
        for cmd in commands:
            if cmd[0] == 6:
                ids = list(cmd[2])
            elif cmd[0] == 4 and cmd[1] not in ids:
                ids.append(cmd[1])
        return ids
