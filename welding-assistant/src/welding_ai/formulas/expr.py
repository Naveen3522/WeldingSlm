"""A small, safe calculator for formula expressions.

Only numbers, names, + - * / **, comparisons and a few functions can run; attribute access, calls to anything else, strings and
the rest of Python are rejected when the expression is read, so an administrator's formula can never execute code.
"""
import ast
import math
import operator

FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "log": math.log, "log10": math.log10, "exp": math.exp}
ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}
COMPARISON = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le}
MAX_LENGTH, MAX_EXPONENT, MAX_VALUE = 300, 20, 1e30
ALLOWED = "Only numbers, names, + - * / ** ( ) and sqrt, abs, min, max, log, log10, exp are allowed."


class ExprError(ValueError):
    pass


# Read an expression and refuse anything outside the allowed grammar.
def parse(text):
    text = (text or "").strip()
    if not text or len(text) > MAX_LENGTH:
        raise ExprError("The expression is empty or too long.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ExprError(f"Cannot read the expression: {text}")
    _check(tree.body)
    return tree


def _check(node):
    if isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC:
        _check(node.left)
        _check(node.right)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        _check(node.operand)
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return
    elif isinstance(node, ast.Name):
        return
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
        for arg in node.args:
            _check(arg)
    elif isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARISON:
        _check(node.left)
        _check(node.comparators[0])
    else:
        raise ExprError(ALLOWED)


# The variable names an expression uses (function names are not variables).
def names(tree):
    functions = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in functions}


def evaluate(tree, values):
    return _eval(tree.body, values)


def _real(result):
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise ExprError("The result is not a real number.")
    if not math.isfinite(result) or abs(result) > MAX_VALUE:
        raise ExprError("The result is out of range.")
    return result


def _eval(node, values):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ExprError(f"No value for {node.id}.")
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, values)
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, values), _eval(node.right, values)
        if isinstance(node.op, ast.Pow) and abs(b) > MAX_EXPONENT:
            raise ExprError("The exponent is too large.")
        try:
            return _real(ARITHMETIC[type(node.op)](a, b))
        except ZeroDivisionError:
            raise ExprError("Division by zero: check the inputs.")
        except OverflowError:
            raise ExprError("The result is out of range.")
    if isinstance(node, ast.Call):
        args = [_eval(a, values) for a in node.args]
        try:
            return _real(FUNCTIONS[node.func.id](*args))
        except (ValueError, TypeError, OverflowError):
            raise ExprError(f"{node.func.id}() cannot be computed for these inputs.")
    if isinstance(node, ast.Compare):
        return COMPARISON[type(node.ops[0])](_eval(node.left, values), _eval(node.comparators[0], values))
    raise ExprError(ALLOWED)


class _Substitute(ast.NodeTransformer):
    def __init__(self, values):
        self.values = values

    def visit_Name(self, node):
        if node.id in self.values:
            return ast.copy_location(ast.Constant(self.values[node.id]), node)
        return node


# The expression with the numbers put in, written the way a person would: "28 × 260 × 60 ÷ (350 × 1000)".
def substituted(tree, values):
    copy = ast.parse(ast.unparse(tree), mode="eval")
    text = ast.unparse(_Substitute(values).visit(copy).body)
    return text.replace(" ** ", "^").replace(" * ", " × ").replace(" / ", " ÷ ")
