"""Config comparison: side-by-side and unified rows for templates."""
import difflib


def side_by_side(old, new, context=None):
    """Return list of rows: (kind, old_no, old_line, new_no, new_line) where kind in
    equal|replace|delete|insert|skip. If context is an int, collapse unchanged runs."""
    a, b = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            n = i2 - i1
            if context is not None and n > context * 2 + 1:
                for k in range(context):
                    rows.append(("equal", i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k]))
                rows.append(("skip", None, f"... {n - context * 2} unchanged lines ...", None, ""))
                for k in range(n - context, n):
                    rows.append(("equal", i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k]))
            else:
                for k in range(n):
                    rows.append(("equal", i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k]))
        else:
            la, lb = i2 - i1, j2 - j1
            for k in range(max(la, lb)):
                ol = a[i1 + k] if k < la else None
                nl = b[j1 + k] if k < lb else None
                kind = "replace" if ol is not None and nl is not None else ("delete" if ol is not None else "insert")
                rows.append((kind, i1 + k + 1 if ol is not None else None, ol or "",
                             j1 + k + 1 if nl is not None else None, nl or ""))
    return rows


def unified(old, new, name_a, name_b, context=3):
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), name_a, name_b,
                                          lineterm="", n=context))


def stats(old, new):
    add = rem = 0
    for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if l.startswith("+") and not l.startswith("+++"):
            add += 1
        elif l.startswith("-") and not l.startswith("---"):
            rem += 1
    return add, rem
