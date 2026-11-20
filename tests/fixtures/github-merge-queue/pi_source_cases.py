"""Source-only Pi workloads with a bounded interpreter for the submitted functions."""

import ast
import operator
from pathlib import Path


CASES = {
    "retry-delay": ("retry_delay", ("failures", "limit"),
                    "Return min(max(0, limit), 40 + 25 * max(0, failures)).",
                    lambda failures, limit: min(max(0, limit), 40 + 25 * max(0, failures))),
    "load-bucket": ("load_bucket", ("active", "capacity"),
                    'Return "invalid" for negative active or nonpositive capacity, "idle" for zero '
                    'active, "full" for active at or above capacity and "busy" otherwise.',
                    lambda active, cap: "invalid" if active < 0 or cap <= 0 else "idle" if active == 0
                    else "full" if active >= cap else "busy"),
    "window-start": ("window_start", ("position", "width"),
                     "Return -1 for nonpositive width. Otherwise return the greatest multiple of width "
                     "less than or equal to position, including negative positions.",
                     lambda pos, width: -1 if width <= 0 else (pos // width) * width),
    "priority-score": ("priority_score", ("priority", "age"),
                       "Clamp priority to 0 through 10 and age to 0 through 100. Return 100 times "
                       "the clamped priority plus the clamped age.",
                       lambda priority, age: 100 * min(10, max(0, priority)) + min(100, max(0, age))),
    "route-tier": ("route_tier", ("latency", "limit"),
                   'Return "invalid" for negative latency or a nonpositive limit, "fast" when '
                   'twice the latency is at most the limit, "normal" when the latency is within '
                   'the limit and "slow" otherwise.',
                   lambda latency, limit: "invalid" if latency < 0 or limit <= 0 else
                   "fast" if 2 * latency <= limit else "normal" if latency <= limit else "slow"),
    "budget-left": ("budget_left", ("budget", "spent"),
                    "Clamp budget and spent individually to at least zero and return their "
                    "difference clamped to at least zero.",
                    lambda budget, spent: max(0, max(0, budget) - max(0, spent))),
    "range-width": ("range_width", ("left", "right"),
                    "Return the number of integers in the inclusive range from left to right. "
                    "Return zero if the range is reversed.",
                    lambda left, right: max(0, right - left + 1)),
    "quorum-size": ("has_quorum", ("voters", "present"),
                    "Return a boolean. A quorum requires positive voters and present in the "
                    "inclusive range from voters // 2 + 1 to voters.",
                    lambda voters, present: voters > 0 and voters // 2 < present <= voters),
    "next-slot": ("next_slot", ("current", "slots"),
                  "Return -1 if slots is nonpositive, otherwise return (current + 1) modulo slots.",
                  lambda current, slots: -1 if slots <= 0 else (current + 1) % slots),
    "batch-pages": ("batch_pages", ("items", "limit"),
                    "Return -1 for a nonpositive limit. Otherwise return the number of pages "
                    "of size limit needed to hold max(items, 0) items. Zero items use zero pages.",
                    lambda items, limit: -1 if limit <= 0 else (max(0, items) + limit - 1) // limit),
}


def prompt(cases):
    instructions = [f"{CASES[case][0]}({', '.join(CASES[case][1])}): {CASES[case][2]}" for case in cases]
    return ("Implement the requested Python functions in policy.py using file tools. Read the file if it "
            "exists. Preserve all existing functions exactly and only add requested functions that are "
            "missing. Use integer arithmetic, comparisons, if statements, local assignments, and "
            "min or max. No imports, methods, loops or other files. " + " ".join(instructions))


ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
COMPARISONS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
               ast.Gt: operator.gt, ast.GtE: operator.ge}
BUILTINS = {"min": min, "max": max}
NODES = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.If, ast.Return, ast.Assign,
         ast.Expr, ast.Constant, ast.Name, ast.Load, ast.Store, ast.BinOp, ast.UnaryOp, ast.Call,
         ast.Compare, ast.BoolOp, ast.IfExp, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
         *ARITHMETIC, *COMPARISONS)


def integer(value):
    if type(value) is not int or abs(value) > 10**12:
        raise ValueError("source case exceeded the integer bounds")
    return value


def expression(node, values):
    if isinstance(node, ast.Constant):
        value = node.value
        if type(value) is bool or type(value) is str and len(value) <= 80:
            return value
        return integer(value)
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.BinOp):
        lhs, rhs = (integer(expression(child, values)) for child in (node.left, node.right))
        return integer(ARITHMETIC[type(node.op)](lhs, rhs))
    if isinstance(node, ast.UnaryOp):
        value = expression(node.operand, values)
        if isinstance(node.op, ast.Not):
            return not value
        return integer(value) * (-1 if isinstance(node.op, ast.USub) else 1)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILTINS:
        return BUILTINS[node.func.id]([integer(expression(arg, values)) for arg in node.args])
    if isinstance(node, ast.IfExp):
        selected = node.body if expression(node.test, values) else node.orelse
        return expression(selected, values)
    if isinstance(node, ast.BoolOp):
        for item in node.values:
            result = expression(item, values)
            if bool(result) != isinstance(node.op, ast.And):
                break
        return result
    if isinstance(node, ast.Compare):
        previous = expression(node.left, values)
        for op, child in zip(node.ops, node.comparators, strict=True):
            current = expression(child, values)
            if not COMPARISONS[type(op)](previous, current):
                return False
            previous = current
        return True
    raise ValueError(f"unsupported source expression {type(node).__name__}")


NO_RETURN = object()


def statements(body, values):
    for node in body:
        if isinstance(node, ast.Return) and node.value is not None:
            return expression(node.value, values)
        if isinstance(node, ast.If):
            result = statements(node.body if expression(node.test, values) else node.orelse, values)
            if result is not NO_RETURN:
                return result
        elif isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id not in BUILTINS
                                                  for target in node.targets):
            result = expression(node.value, values)
            for target in node.targets:
                values[target.id] = result
        elif not docstring(node):
            raise ValueError(f"unsupported source statement {type(node).__name__}")
    return NO_RETURN


def docstring(node):
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def function_map(source):
    if len(source) > 6000:
        raise ValueError("source case exceeded the file size limit")
    tree = ast.parse(source)
    if sum(1 for _ in ast.walk(tree)) > 500 or any(not isinstance(node, NODES) for node in ast.walk(tree)):
        raise ValueError("source case contains an unsupported construct")
    functions = {}
    for node in tree.body:
        if docstring(node):
            continue
        if not isinstance(node, ast.FunctionDef) or node.name in functions or node.decorator_list:
            raise ValueError("expected distinct top-level Python functions")
        functions[node.name] = node
    return functions


def verify(path, cases):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 6000:
        raise ValueError("expected a small regular Python source file")
    functions = function_map(path.read_bytes())
    if set(functions) != {CASES[case][0] for case in cases}:
        raise ValueError("source must contain exactly the assigned functions")
    samples = (-151, -8, -1, 0, 1, 2, 3, 8, 10, 21, 40, 100, 151)
    total = 0
    for case in cases:
        name, args, _, expected = CASES[case]
        function = functions[name]
        signature = function.args
        if (tuple(arg.arg for arg in signature.args) != args or signature.defaults or signature.posonlyargs
                or signature.kwonlyargs or signature.vararg or signature.kwarg):
            raise ValueError(f"{name} has a different signature")
        for lhs in samples:
            for rhs in samples:
                actual = statements(function.body, dict(zip(args, (lhs, rhs), strict=True)))
                want = expected(lhs, rhs)
                if type(actual) is not type(want) or actual != want:
                    raise ValueError(f"{name} disagrees with its contract for ({lhs}, {rhs})")
                total += 1
    return total
