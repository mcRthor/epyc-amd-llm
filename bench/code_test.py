#!/usr/bin/env python3
"""code_test.py - coding benchmark for the local models behind the llama.cpp router.

Gives every model the same Python tasks (one attempt each), takes the code from its reply,
runs it against hidden checks in a sandbox, and prints a score table.

Usage:
  python3 code_test.py                                     # all models the router lists (except quick)
  python3 code_test.py --models research-qwen3.6,fast-qwen # only these, in this order
  python3 code_test.py --thinking                          # let the models think first (slower, often better)
  python3 code_test.py --tasks roman,lru_cache             # only some tasks (names: --list-tasks)
  python3 code_test.py --set hard --repeat 3               # the 7 harder tasks, 3 attempts each (--set all: both)
  python3 code_test.py --url http://192.168.1.50:9700      # from another machine
  python3 code_test.py --validate                          # check the tests with the reference solutions
  python3 code_test.py --report results.json               # print the table again from a saved run
  python3 code_test.py --rescore results.json              # re-check saved answers against the current checks

The key is read from ~/locai/llama/api-key.txt unless you pass --key-file or set LLAMA_API_KEY.
Each model's answers, check results and timings are saved to code_test_<date>.json (or --out).
Generated code runs with no network (unshare -rn), 1 GB of memory, 10 s per check, 60 s of CPU and 90 s in total.
Uses only the Python standard library. The run swaps models on the router, so app requests wait meanwhile.
"""
import argparse, json, os, re, resource, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request

# ------------------------------------------------------------------ tasks
# Each task: a prompt, hidden checks, and a reference solution used by --validate.
# A check is an expression that must be truthy; ("raises", "ValueError", expr) expects that exception.

TASKS = []
def task(name, prompt, checks, reference, helpers="", level="basic"):
    TASKS.append({"name": name, "prompt": prompt.strip(), "checks": checks, "reference": reference.strip(),
                  "helpers": helpers.strip(), "level": level})

task("parse_duration", """
Write a Python function `parse_duration(s: str) -> int` that converts a duration string to seconds.
- The format is any combination of the units d, h, m, s in that order, each with a non-negative integer, e.g. "1h30m", "2d", "45s", "1d2h3m4s".
- Whitespace between parts is allowed ("1h 30m").
- Units must appear in the order d, h, m, s and at most once each.
- Raise ValueError for an empty string, unknown units, wrong order, repeated units, missing numbers, or anything else invalid.
""", [
    'parse_duration("45s") == 45',
    'parse_duration("1h30m") == 5400',
    'parse_duration("2d") == 172800',
    'parse_duration("1d2h3m4s") == 93784',
    'parse_duration("1h 30m") == 5400',
    'parse_duration("0s") == 0',
    'parse_duration("90m") == 5400',
    ("raises", "ValueError", 'parse_duration("")'),
    ("raises", "ValueError", 'parse_duration("10x")'),
    ("raises", "ValueError", 'parse_duration("30m1h")'),
    ("raises", "ValueError", 'parse_duration("1h1h")'),
    ("raises", "ValueError", 'parse_duration("h")'),
    ("raises", "ValueError", 'parse_duration("1.5h")'),
    ("raises", "ValueError", 'parse_duration("-1h")'),
], """
import re
def parse_duration(s):
    t = s.replace(" ", "")
    m = re.fullmatch(r"(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?", t)
    if not t or not m:
        raise ValueError(s)
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se
""")

task("merge_intervals", """
Write a Python function `merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]`.
- Each interval is (start, end) with start <= end, inclusive.
- Merge overlapping intervals and intervals that touch (end == next start), e.g. (1, 3) and (3, 5) become (1, 5).
- The input may be unsorted. Return the merged intervals sorted by start, as tuples.
- Return an empty list for empty input. Do not modify the input list.
- Raise ValueError if any interval has start > end.
""", [
    'merge_intervals([]) == []',
    'merge_intervals([(1, 3)]) == [(1, 3)]',
    'merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]',
    'merge_intervals([(8, 10), (1, 3), (2, 6)]) == [(1, 6), (8, 10)]',
    'merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]',
    'merge_intervals([(1, 10), (2, 3), (4, 5)]) == [(1, 10)]',
    'merge_intervals([(5, 5), (5, 5)]) == [(5, 5)]',
    'merge_intervals([(1, 2), (4, 5)]) == [(1, 2), (4, 5)]',
    'merge_intervals([(1, 3), (4, 6)]) == [(1, 3), (4, 6)]',   # a gap of one is not touching
    '(lambda x: (merge_intervals(x), x)[1] == [(3, 4), (1, 2)])([(3, 4), (1, 2)])',
    ("raises", "ValueError", 'merge_intervals([(5, 1)])'),
], """
def merge_intervals(intervals):
    for a, b in intervals:
        if a > b:
            raise ValueError((a, b))
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
""")

task("lru_cache", """
Write a Python class `LRUCache` with:
- `__init__(self, capacity: int)`: raise ValueError if capacity < 1.
- `get(self, key)`: return the value, or None if the key is missing. A successful get makes the key most recently used.
- `put(self, key, value)`: insert or update the key (an update also makes it most recently used). When inserting a new key would exceed the capacity, evict the least recently used key first.
- `__len__(self)`: the number of stored keys.
Both get and put must be O(1). Do not use functools.lru_cache.
""", [
    '(lambda c: (c.put(1, "a"), c.get(1))[1] == "a")(LRUCache(2))',
    'LRUCache(2).get(99) is None',
    '(lambda c: (c.put(1, 1), c.put(2, 2), c.put(3, 3), c.get(1))[3] is None)(LRUCache(2))',
    '(lambda c: (c.put(1, 1), c.put(2, 2), c.get(1), c.put(3, 3), c.get(2), c.get(1))[4:] == (None, 1))(LRUCache(2))',
    '(lambda c: (c.put(1, 1), c.put(2, 2), c.put(1, 10), c.put(3, 3), c.get(2), c.get(1))[4:] == (None, 10))(LRUCache(2))',
    '(lambda c: (c.put(1, 1), c.put(1, 2), len(c))[2] == 1)(LRUCache(3))',
    '(lambda c: (c.put("a", None), c.put("b", 2), len(c))[2] == 2)(LRUCache(2))',
    '(lambda c: [c.put(i, i) for i in range(1000)] and len(c) == 3 and c.get(999) == 999 and c.get(996) is None)(LRUCache(3))',
    ("raises", "ValueError", 'LRUCache(0)'),
], """
from collections import OrderedDict
class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError(capacity)
        self.cap, self.d = capacity, OrderedDict()
    def get(self, key):
        if key not in self.d:
            return None
        self.d.move_to_end(key)
        return self.d[key]
    def put(self, key, value):
        if key in self.d:
            self.d.move_to_end(key)
        elif len(self.d) >= self.cap:
            self.d.popitem(last=False)
        self.d[key] = value
    def __len__(self):
        return len(self.d)
""")

task("fix_moving_average", """
This function has bugs. Fix it and return the corrected function.

```python
def moving_average(xs, k):
    \"\"\"Return the list of averages of every window of k consecutive values.
    moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
    Raise ValueError if k < 1 or k > len(xs). Must run in O(n).\"\"\"
    if k < 0:
        raise ValueError("bad k")
    out = []
    s = sum(xs[:k])
    for i in range(k, len(xs)):
        out.append(s / k)
        s += xs[i] - xs[i - k + 1]
    return out
```
""", [
    'moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]',
    'moving_average([5], 1) == [5.0]',
    'moving_average([1, 2, 3], 3) == [2.0]',
    'moving_average([2, 4, 6, 8, 10], 3) == [4.0, 6.0, 8.0]',
    'moving_average([1, -1, 1, -1], 1) == [1.0, -1.0, 1.0, -1.0]',
    ("raises", "ValueError", 'moving_average([1, 2], 0)'),
    ("raises", "ValueError", 'moving_average([1, 2], 3)'),
    'len(moving_average(list(range(200000)), 1000)) == 199001',
], """
def moving_average(xs, k):
    if k < 1 or k > len(xs):
        raise ValueError("bad k")
    s = sum(xs[:k]); out = [s / k]
    for i in range(k, len(xs)):
        s += xs[i] - xs[i - k]
        out.append(s / k)
    return out
""")

task("topo_order", """
Write a Python function `topo_order(deps: dict[str, list[str]]) -> list[str]`.
- `deps` maps each task to the tasks it depends on (they must come before it).
- Tasks that only appear inside dependency lists are tasks too.
- Return an order where every task comes after all its dependencies. When several tasks are ready at the same time, take them in alphabetical order, so the result is deterministic.
- Raise ValueError if there is a cycle (including a task that depends on itself).
""", [
    'topo_order({}) == []',
    'topo_order({"b": ["a"]}) == ["a", "b"]',
    'topo_order({"c": ["a", "b"], "b": ["a"]}) == ["a", "b", "c"]',
    'topo_order({"x": [], "a": [], "m": []}) == ["a", "m", "x"]',
    'topo_order({"d": ["b", "c"], "b": ["a"], "c": ["a"]}) == ["a", "b", "c", "d"]',
    'topo_order({"b": [], "a": ["c"]}) == ["b", "c", "a"]',
    'topo_order({"c": ["a", "b"], "d": []}) == ["a", "b", "c", "d"]',   # c becomes ready after d is waiting; still alphabetical
    ("raises", "ValueError", 'topo_order({"a": ["b"], "b": ["a"]})'),
    ("raises", "ValueError", 'topo_order({"a": ["a"]})'),
    ("raises", "ValueError", 'topo_order({"a": ["b"], "b": ["c"], "c": ["a"], "d": []})'),
], """
import heapq
def topo_order(deps):
    nodes = set(deps)
    for v in deps.values():
        nodes.update(v)
    indeg = {n: 0 for n in nodes}
    users = {n: [] for n in nodes}
    for t, ds in deps.items():
        for d in set(ds):
            indeg[t] += 1
            users[d].append(t)
    ready = [n for n in nodes if indeg[n] == 0]
    heapq.heapify(ready)
    out = []
    while ready:
        n = heapq.heappop(ready); out.append(n)
        for u in users[n]:
            indeg[u] -= 1
            if indeg[u] == 0:
                heapq.heappush(ready, u)
    if len(out) != len(nodes):
        raise ValueError("cycle")
    return out
""")

task("roman", """
Write two Python functions:
- `to_roman(n: int) -> str` for 1 <= n <= 3999 using standard subtractive notation (4 = IV, 9 = IX, 40 = XL, 90 = XC, 400 = CD, 900 = CM). Raise ValueError outside that range.
- `from_roman(s: str) -> int` that accepts only valid, canonical numerals (exactly what to_roman would produce, uppercase). Raise ValueError for anything else, e.g. "IIII", "VV", "IC", "MMMM", "", "iv".
""", [
    'to_roman(1) == "I"', 'to_roman(4) == "IV"', 'to_roman(1994) == "MCMXCIV"', 'to_roman(3999) == "MMMCMXCIX"',
    'from_roman("MCMXCIV") == 1994', 'from_roman("XLII") == 42',
    'all(from_roman(to_roman(n)) == n for n in range(1, 4000))',
    ("raises", "ValueError", 'to_roman(0)'), ("raises", "ValueError", 'to_roman(4000)'),
    ("raises", "ValueError", 'from_roman("IIII")'), ("raises", "ValueError", 'from_roman("VV")'),
    ("raises", "ValueError", 'from_roman("IC")'), ("raises", "ValueError", 'from_roman("MMMM")'),
    ("raises", "ValueError", 'from_roman("")'), ("raises", "ValueError", 'from_roman("iv")'),
], """
V = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
def to_roman(n):
    if not isinstance(n, int) or not 1 <= n <= 3999:
        raise ValueError(n)
    out = ""
    for v, r in V:
        while n >= v:
            out += r; n -= v
    return out
_TABLE = {}
def from_roman(s):
    if not _TABLE:
        _TABLE.update({to_roman(i): i for i in range(1, 4000)})
    if s not in _TABLE:
        raise ValueError(s)
    return _TABLE[s]
""")


# ------------------------------------------------------------------ hard tasks (--set hard)
# Longer specs, more edge cases, performance limits and property checks. "helpers" is hidden
# code added after the model's code (names start with _h_) for checks too long for one line.

task("eval_expr", """
Write a Python function `eval_expr(s: str)` that evaluates an arithmetic expression and returns an int or float.
- Numbers: non-negative integers (e.g. 42) or decimals with digits on both sides of the point (e.g. 3.25).
- Operators, with Python's meaning and precedence: + - * / // % **, unary + and -, and parentheses.
  ** binds tighter than unary minus and is right-associative: -2 ** 2 == -4, 2 ** 3 ** 2 == 512, 2 ** -1 == 0.5.
  All other binary operators are left-associative: 8 - 3 - 2 == 3.
- Whitespace between tokens is allowed.
- Raise ValueError for any syntax error (empty input, unknown characters, missing operands, unbalanced parentheses, two numbers in a row).
- Division or modulo by zero raises ZeroDivisionError.
- Do not use eval, exec, compile or the ast module: write the parser yourself.
""", [
    'eval_expr("1 + 2 * 3") == 7',
    'eval_expr("(1 + 2) * 3") == 9',
    'eval_expr("2 ** 3 ** 2") == 512',
    'eval_expr("-2 ** 2") == -4',
    'eval_expr("2 ** -1") == 0.5',
    'eval_expr("7 // 2") == 3',
    'eval_expr("-7 // 2") == -4',
    'eval_expr("-7 % 3") == 2',
    'eval_expr("10 / 4") == 2.5',
    'eval_expr("2 * -3") == -6',
    'eval_expr("--3") == 3',
    'eval_expr("1.5 * 2") == 3.0',
    'eval_expr("  42  ") == 42 and isinstance(eval_expr("42"), int)',
    'eval_expr("8 - 3 - 2") == 3',
    'eval_expr("100 // 10 // 3") == 3',
    'eval_expr("2 * (3 + (4 - 1)) ** 2") == 72',
    ("raises", "ValueError", 'eval_expr("1 +")'),
    ("raises", "ValueError", 'eval_expr("(1 + 2")'),
    ("raises", "ValueError", 'eval_expr("1 + 2)")'),
    ("raises", "ValueError", 'eval_expr("1 2")'),
    ("raises", "ValueError", 'eval_expr("")'),
    ("raises", "ValueError", 'eval_expr("2 *** 3")'),
    ("raises", "ValueError", 'eval_expr("abc")'),
    ("raises", "ZeroDivisionError", 'eval_expr("1 / 0")'),
    '_h_no_eval()',
], """
import re
def eval_expr(s):
    toks = re.findall(r"\\s*(\\d+\\.\\d+|\\d+|\\*\\*|//|[-+*/%()]|\\S)", s)
    if "".join(toks).replace(" ", "") != s.replace(" ", "") or not toks:
        raise ValueError(s)
    toks = [t.strip() for t in toks]
    pos = 0
    def peek():
        return toks[pos] if pos < len(toks) else None
    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]
    def expr():
        v = term()
        while peek() in ("+", "-"):
            op = take(); r = term()
            v = v + r if op == "+" else v - r
        return v
    def term():
        v = unary()
        while peek() in ("*", "/", "//", "%"):
            op = take(); r = unary()
            v = v * r if op == "*" else v / r if op == "/" else v // r if op == "//" else v % r
        return v
    def unary():
        if peek() in ("+", "-"):
            op = take(); v = unary()
            return v if op == "+" else -v
        return power()
    def power():
        v = atom()
        if peek() == "**":
            take()
            return v ** unary()
        return v
    def atom():
        t = peek()
        if t is None:
            raise ValueError("unexpected end")
        if t == "(":
            take(); v = expr()
            if peek() != ")":
                raise ValueError("missing )")
            take(); return v
        if re.fullmatch(r"\\d+\\.\\d+", t):
            take(); return float(t)
        if t.isdigit():
            take(); return int(t)
        raise ValueError("unexpected " + t)
    v = expr()
    if pos != len(toks):
        raise ValueError("extra input")
    return v
""", helpers="""
def _h_no_eval():
    src = open(__file__).read().split("\\n# --- hidden helpers ---")[0]
    return "eval_expr" in globals() and not any(w in src for w in ("eval(", "exec(", "compile(", "import ast", "from ast"))
""", level="hard")

task("parse_csv_line", """
Write a Python function `parse_csv_line(line: str) -> list[str]` that splits one CSV line into fields.
- Fields are separated by commas. An empty line is one empty field: [""]. A trailing comma adds an empty field.
- A field may be quoted with double quotes. Inside quotes, commas are literal and "" stands for one double quote.
- A quote may only start a field. After a closing quote, the next character must be a comma or the end of the line.
- A double quote inside an unquoted field is an error.
- Whitespace is kept exactly as it is (no trimming), and an unquoted field may contain spaces.
- Raise ValueError for an unterminated quote, a quote in the middle of an unquoted field, or anything after a closing quote other than a comma.
- Do not use the csv module.
""", [
    'parse_csv_line("a,b,c") == ["a", "b", "c"]',
    'parse_csv_line("") == [""]',
    'parse_csv_line(",") == ["", ""]',
    'parse_csv_line("a,,c,") == ["a", "", "c", ""]',
    'parse_csv_line(\'"a,b",c\') == ["a,b", "c"]',
    'parse_csv_line(\'"say ""hi""",x\') == [\'say "hi"\', "x"]',
    'parse_csv_line(\'""\') == [""]',
    'parse_csv_line(\'"",""\') == ["", ""]',
    'parse_csv_line(" a , b ") == [" a ", " b "]',
    'parse_csv_line(\'x,"",y\') == ["x", "", "y"]',
    'parse_csv_line(\'""""\') == [\'"\']',
    ("raises", "ValueError", 'parse_csv_line(\'"abc\')'),
    ("raises", "ValueError", 'parse_csv_line(\'a"b\')'),
    ("raises", "ValueError", 'parse_csv_line(\'"a"b\')'),
    ("raises", "ValueError", 'parse_csv_line(\'"a" ,b\')'),
    ("raises", "ValueError", 'parse_csv_line(\' "a"\')'),
    '_h_no_csv()',
], """
def parse_csv_line(line):
    out, i, n = [], 0, len(line)
    while True:
        if i < n and line[i] == '"':
            i += 1; buf = []
            while True:
                if i >= n:
                    raise ValueError("unterminated quote")
                if line[i] == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        buf.append('"'); i += 2; continue
                    i += 1; break
                buf.append(line[i]); i += 1
            out.append("".join(buf))
            if i < n and line[i] != ",":
                raise ValueError("text after closing quote")
        else:
            j = line.find(",", i)
            j = n if j < 0 else j
            field = line[i:j]
            if '"' in field:
                raise ValueError("quote inside unquoted field")
            out.append(field); i = j
        if i >= n:
            return out
        i += 1  # skip the comma
""", helpers="""
def _h_no_csv():
    src = open(__file__).read().split("\\n# --- hidden helpers ---")[0]
    return "parse_csv_line" in globals() and "import csv" not in src and "from csv" not in src
""", level="hard")

task("semver_compare", """
Write a Python function `semver_compare(a: str, b: str) -> int` that compares two Semantic Versioning 2.0.0 versions.
Return -1 if a has lower precedence than b, 0 if equal precedence, 1 if higher.
- Format: MAJOR.MINOR.PATCH, then an optional pre-release "-ident.ident...", then optional build metadata "+ident.ident...".
- MAJOR, MINOR, PATCH are non-negative integers without leading zeros (0 is fine, 01 is not).
- Identifiers are non-empty and use only [0-9A-Za-z-]. A pre-release identifier that is all digits must not have leading zeros.
- Precedence: compare MAJOR, MINOR, PATCH numerically. A version with a pre-release is lower than the same version without one.
  Pre-releases compare identifier by identifier: numeric identifiers numerically, others in ASCII order, numeric lower than
  non-numeric; if all shared identifiers are equal, the one with more identifiers is higher.
- Build metadata is ignored for precedence.
- Raise ValueError for an invalid version.
""", [
    '[semver_compare(x, y) for x, y in zip(_h_CHAIN, _h_CHAIN[1:])] == [-1] * 7',
    '[semver_compare(y, x) for x, y in zip(_h_CHAIN, _h_CHAIN[1:])] == [1] * 7',
    'semver_compare("1.0.0", "1.0.0") == 0',
    'semver_compare("1.0.0+build.5", "1.0.0+other") == 0',
    'semver_compare("1.0.0-rc.1+b1", "1.0.0-rc.1") == 0',
    'semver_compare("2.0.0", "10.0.0") == -1',
    'semver_compare("1.10.0", "1.9.0") == 1',
    'semver_compare("1.0.0-alpha.10", "1.0.0-alpha.9") == 1',
    'semver_compare("1.0.0-2", "1.0.0-10") == -1',
    'semver_compare("1.0.0-0A", "1.0.0-0B") == -1',
    'semver_compare("1.0.0-x-y", "1.0.0-x") == 1',
    ("raises", "ValueError", 'semver_compare("01.0.0", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("1.0", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("1.0.0-", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("1.0.0-01", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("1.0.0+", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("1.0.0-a..b", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("v1.0.0", "1.0.0")'),
    ("raises", "ValueError", 'semver_compare("1.0.0", "1.0.0-al$pha")'),
], """
import re
_ID = r"[0-9A-Za-z-]+"
_RE = re.compile(r"(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-(%s(?:\\.%s)*))?(?:\\+(%s(?:\\.%s)*))?" % (_ID, _ID, _ID, _ID))
def _parse(v):
    m = _RE.fullmatch(v)
    if not m:
        raise ValueError(v)
    pre = m.group(4).split(".") if m.group(4) else []
    for p in pre:
        if p.isdigit() and len(p) > 1 and p[0] == "0":
            raise ValueError(v)
    return tuple(int(x) for x in m.group(1, 2, 3)), pre
def semver_compare(a, b):
    (ca, pa), (cb, pb) = _parse(a), _parse(b)
    if ca != cb:
        return -1 if ca < cb else 1
    if not pa or not pb:
        return (0 if not pa and not pb else 1 if not pa else -1)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return -1 if int(x) < int(y) else 1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return -1 if x < y else 1
    return (len(pa) > len(pb)) - (len(pa) < len(pb))
""", helpers="""
_h_CHAIN = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
""", level="hard")

task("keys_maze", """
Write a Python function `keys_maze(grid: list[str]) -> int` that returns the fewest steps from S to E.
- Cells: "." open, "#" wall, "S" start (exactly one), "E" exit (exactly one), lowercase "a"-"d" keys, uppercase "A"-"D" doors.
- Each step moves one cell up, down, left or right, inside the grid and not into a wall.
- Stepping on a key picks it up (you keep it). A door can only be entered after picking up its key (a for A, and so on).
- Return the fewest steps to reach E, or -1 if E cannot be reached.
- Raise ValueError if there is not exactly one S and exactly one E, or the rows have different lengths.
- It must handle a 40 x 40 grid with all four keys and doors within a few seconds.
""", [
    'keys_maze(["S.E"]) == 2',
    'keys_maze(["S#E", "..."]) == 4',
    'keys_maze(["SAE", "###", "a.."]) == -1',
    'keys_maze(["a.S.A.E"]) == 8',
    'keys_maze(["S.A.E", ".###.", "a...."]) == 8',
    'keys_maze(["S.......E"]) == 8',
    'keys_maze(_h_M1) == 12',   # right for key a, back through A for b, then through B
    'keys_maze(_h_M3) == 16',
    'keys_maze(_h_M2) == -1',
    'keys_maze(_h_BIG) == 152',   # 48 if the doors were open: the keys force a long detour
    ("raises", "ValueError", 'keys_maze(["..E"])'),
    ("raises", "ValueError", 'keys_maze(["S.S", "..E"])'),
    ("raises", "ValueError", 'keys_maze(["S..", ".E"])'),
], """
from collections import deque
def keys_maze(grid):
    if not grid or len({len(r) for r in grid}) != 1:
        raise ValueError("bad grid")
    cells = [(r, c, ch) for r, row in enumerate(grid) for c, ch in enumerate(row)]
    starts = [(r, c) for r, c, ch in cells if ch == "S"]
    ends = [(r, c) for r, c, ch in cells if ch == "E"]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("need exactly one S and one E")
    H, W = len(grid), len(grid[0])
    sr, sc = starts[0]
    seen = {(sr, sc, 0)}
    q = deque([(sr, sc, 0, 0)])
    while q:
        r, c, keys, d = q.popleft()
        if grid[r][c] == "E":
            return d
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if not (0 <= nr < H and 0 <= nc < W):
                continue
            ch = grid[nr][nc]
            if ch == "#":
                continue
            nk = keys
            if ch in "abcd":
                nk |= 1 << (ord(ch) - 97)
            elif ch in "ABCD" and not keys & (1 << (ord(ch) - 65)):
                continue
            if (nr, nc, nk) not in seen:
                seen.add((nr, nc, nk))
                q.append((nr, nc, nk, d + 1))
    return -1
""", helpers="""
_h_M1 = ["E.B.b.A.S.a"]
_h_M3 = ["S..#.....",
         ".#.#.###.",
         ".#...#a#.",
         ".#####.#.",
         "...A...#E"]
_h_M2 = ["S.a.A.b", "#######", "E.B...."]
def _h_big():
    import random
    rnd = random.Random(20261023)
    g = [["." if rnd.random() > 0.25 else "#" for _ in range(40)] for _ in range(40)]
    for r in range(35, 40):          # exit room, locked by door D
        for c in range(35, 40):
            g[r][c] = "#" if r == 35 or c == 35 else "."
    g[35][37] = "D"; g[38][38] = "E"
    for r in range(0, 5):            # room with key d, locked by door C
        for c in range(0, 5):
            g[r][c] = "#" if r == 4 or c == 4 else "."
    g[4][2] = "C"; g[1][1] = "d"
    g[20][20] = "c"; g[2][38] = "S"
    for ch, (r, c) in zip("abAB", [(10, 30), (30, 10), (25, 25), (12, 12)]):
        g[r][c] = ch
    for r, c in ((19, 20), (21, 20), (20, 19), (20, 21), (3, 38), (34, 37), (5, 2)):
        g[r][c] = "."               # keep the key, the door and the start reachable
    return ["".join(row) for row in g]
_h_BIG = _h_big()
""", level="hard")

task("lfu_cache", """
Write a Python class `LFUCache` (least frequently used cache) with:
- `__init__(self, capacity: int)`: raise ValueError if capacity < 1.
- `get(self, key)`: return the value, or None if missing. A successful get counts as one use of the key.
- `put(self, key, value)`: insert or update. Updating an existing key counts as one use. A new key starts with one use.
  When inserting a new key into a full cache, first evict the key with the fewest uses; if several keys tie,
  evict the one among them that was used least recently.
- `__len__(self)`: the number of stored keys.
get and put must be O(1) on average (a 5,000-entry cache must handle 200,000 operations within a few seconds).
""", [
    '_h_seq(2, [("put", 1, 1), ("put", 2, 2), ("get", 1), ("put", 3, 3), ("get", 2), ("get", 3), ("put", 4, 4), ("get", 1), ("get", 3), ("get", 4)]) == [1, None, 3, None, 3, 4]',
    '_h_seq(2, [("put", 1, 1), ("put", 2, 2), ("put", 3, 3), ("get", 1), ("get", 2), ("get", 3)]) == [None, 2, 3]',
    '_h_seq(2, [("put", 1, 1), ("put", 2, 2), ("put", 1, 10), ("put", 3, 3), ("get", 1), ("get", 2), ("get", 3)]) == [10, None, 3]',
    '_h_seq(1, [("put", 1, 1), ("get", 1), ("get", 1), ("put", 2, 2), ("get", 1), ("get", 2)]) == [1, 1, None, 2]',
    '_h_seq(3, [("put", "a", 1), ("put", "b", 2), ("put", "c", 3), ("get", "a"), ("get", "b"), ("put", "d", 4), ("get", "c"), ("get", "a"), ("get", "b"), ("get", "d")]) == [1, 2, None, 1, 2, 4]',
    '_h_seq(2, [("put", 1, None), ("put", 2, 2), ("put", 3, 3), ("get", 1)]) == [None]',
    '(lambda c: (c.put(1, 1), c.put(1, 2), len(c))[2] == 1)(LFUCache(3))',
    '_h_perf()',
    ("raises", "ValueError", 'LFUCache(0)'),
], """
from collections import defaultdict, OrderedDict
class LFUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError(capacity)
        self.cap = capacity
        self.val, self.freq = {}, {}
        self.groups = defaultdict(OrderedDict)
        self.minf = 0
    def _touch(self, key):
        f = self.freq[key]
        del self.groups[f][key]
        if not self.groups[f]:
            del self.groups[f]
            if self.minf == f:
                self.minf = f + 1
        self.freq[key] = f + 1
        self.groups[f + 1][key] = None
    def get(self, key):
        if key not in self.val:
            return None
        self._touch(key)
        return self.val[key]
    def put(self, key, value):
        if key in self.val:
            self.val[key] = value
            self._touch(key)
            return
        if len(self.val) >= self.cap:
            old, _ = self.groups[self.minf].popitem(last=False)
            if not self.groups[self.minf]:
                del self.groups[self.minf]
            del self.val[old], self.freq[old]
        self.val[key], self.freq[key] = value, 1
        self.groups[1][key] = None
        self.minf = 1
    def __len__(self):
        return len(self.val)
""", helpers="""
def _h_seq(cap, ops):
    c, out = LFUCache(cap), []
    for op in ops:
        if op[0] == "put":
            c.put(op[1], op[2])
        else:
            out.append(c.get(op[1]))
    return out
def _h_perf():
    import random, time
    rnd, c, t0 = random.Random(5), LFUCache(5000), time.time()
    for i in range(200000):
        k = rnd.randrange(20000)
        if i % 3:
            c.put(k, i)
        else:
            c.get(k)
    return len(c) == 5000 and time.time() - t0 < 6
""", level="hard")

task("diff_lines", """
Write a Python function `diff_lines(a: list[str], b: list[str]) -> list[tuple[str, str]]` that returns a minimal line diff.
- Each item is (op, line) with op " " (line in both), "-" (line only in a) or "+" (line only in b).
- Reading the items in order, the " " and "-" lines must give exactly a, and the " " and "+" lines must give exactly b.
- The diff must be minimal: as few "-" and "+" items as possible (that is, keep a longest common subsequence).
- It must handle two 1,500-line inputs within a few seconds.
""", [
    'diff_lines([], []) == []',
    'diff_lines(["a"], ["a"]) == [(" ", "a")]',
    'sorted(diff_lines(["a"], ["b"])) == [("+", "b"), ("-", "a")]',
    '_h_ok(["a", "b", "c"], ["a", "c"])',
    '_h_ok(["x", "a", "b"], ["a", "b", "y"])',
    '_h_ok([], ["a", "b"]) and _h_ok(["a", "b"], [])',
    '_h_ok(list("ABCABBA"), list("CBABAC"))',
    '_h_random(300)',
    '_h_big()',
], """
def diff_lines(a, b):
    n, m = len(a), len(b)
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        Li, Li1, ai = L[i], L[i + 1], a[i]
        for j in range(m - 1, -1, -1):
            Li[j] = Li1[j + 1] + 1 if ai == b[j] else max(Li1[j], Li[j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append((" ", a[i])); i += 1; j += 1
        elif L[i + 1][j] >= L[i][j + 1]:
            out.append(("-", a[i])); i += 1
        else:
            out.append(("+", b[j])); j += 1
    out += [("-", x) for x in a[i:]] + [("+", y) for y in b[j:]]
    return out
""", helpers="""
def _h_lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]
def _h_ok(a, b):
    d = diff_lines(list(a), list(b))
    if not all(isinstance(t, tuple) and len(t) == 2 and t[0] in (" ", "-", "+") for t in d):
        return False
    ra = [l for o, l in d if o in (" ", "-")]
    rb = [l for o, l in d if o in (" ", "+")]
    changes = sum(o != " " for o, _ in d)
    return ra == list(a) and rb == list(b) and changes == len(a) + len(b) - 2 * _h_lcs(a, b)
def _h_random(n):
    import random
    rnd = random.Random(11)
    for _ in range(n):
        a = [rnd.choice("abc") for _ in range(rnd.randrange(9))]
        b = [rnd.choice("abc") for _ in range(rnd.randrange(9))]
        if not _h_ok(a, b):
            return False
    return True
def _h_big():
    import random, time
    rnd = random.Random(3)
    a = ["line %d" % rnd.randrange(400) for _ in range(1500)]
    b = list(a)
    for _ in range(200):
        i = rnd.randrange(len(b))
        if rnd.random() < 0.5:
            del b[i]
        else:
            b.insert(i, "new %d" % rnd.randrange(400))
    t0 = time.time()
    d = diff_lines(a, b)
    ok = [l for o, l in d if o != "+"] == a and [l for o, l in d if o != "-"] == b
    return ok and time.time() - t0 < 8
""", level="hard")

task("fix_spiral", """
This function has bugs. Fix it and return the corrected function.

```python
def spiral_order(matrix):
    \"\"\"Return the elements of a rectangular matrix in clockwise spiral order, starting top-left.
    spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 2, 3, 6, 9, 8, 7, 4, 5]
    Works for any rectangular shape, including 1 x n, n x 1 and empty ([] or [[]]).
    Raise ValueError if the rows have different lengths.\"\"\"
    out = []
    top, bottom, left, right = 0, len(matrix) - 1, 0, len(matrix[0]) - 1
    while top <= bottom and left <= right:
        for c in range(left, right + 1):
            out.append(matrix[top][c])
        for r in range(top + 1, bottom + 1):
            out.append(matrix[r][right])
        for c in range(right - 1, left - 1, -1):
            out.append(matrix[bottom][c])
        for r in range(bottom - 1, top, -1):
            out.append(matrix[r][left])
        top, bottom, left, right = top + 1, bottom - 1, left + 1, right - 1
    return out
```
""", [
    'spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 2, 3, 6, 9, 8, 7, 4, 5]',
    'spiral_order([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]) == [1, 2, 3, 4, 8, 12, 11, 10, 9, 5, 6, 7]',
    'spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]) == [1, 2, 3, 6, 9, 12, 11, 10, 7, 4, 5, 8]',
    'spiral_order([[1, 2, 3, 4]]) == [1, 2, 3, 4]',
    'spiral_order([[1], [2], [3]]) == [1, 2, 3]',
    'spiral_order([[1, 2], [3, 4]]) == [1, 2, 4, 3]',
    'spiral_order([[7]]) == [7]',
    'spiral_order([]) == []',
    'spiral_order([[]]) == []',
    'spiral_order([[1, 2, 3], [4, 5, 6]]) == [1, 2, 3, 6, 5, 4]',
    ("raises", "ValueError", 'spiral_order([[1, 2], [3]])'),
], """
def spiral_order(matrix):
    if not matrix:
        return []
    if len({len(r) for r in matrix}) != 1:
        raise ValueError("rows have different lengths")
    out = []
    top, bottom, left, right = 0, len(matrix) - 1, 0, len(matrix[0]) - 1
    while top <= bottom and left <= right:
        for c in range(left, right + 1):
            out.append(matrix[top][c])
        for r in range(top + 1, bottom + 1):
            out.append(matrix[r][right])
        if top < bottom:
            for c in range(right - 1, left - 1, -1):
                out.append(matrix[bottom][c])
        if left < right:
            for r in range(bottom - 1, top, -1):
                out.append(matrix[r][left])
        top, bottom, left, right = top + 1, bottom - 1, left + 1, right - 1
    return out
""", level="hard")


# ------------------------------------------------------------------ running

SYSTEM = ("You are an expert Python programmer. Reply with exactly one complete ```python code block "
          "containing the full solution, including any imports. Use only the Python standard library. "
          "Do not include tests, examples or explanations.")

RUNNER = r'''
import json, signal, sys
def _check_timeout(*_):
    raise TimeoutError("check took longer than 10 s")
signal.signal(signal.SIGALRM, _check_timeout)
ns = {"__name__": "candidate", "__file__": CANDIDATE_PATH, "__builtins__": __builtins__}
exec(compile(open(CANDIDATE_PATH).read(), CANDIDATE_PATH, "exec"), ns)   # load like an import
results = []
for c in CHECKS:
    signal.alarm(10)        # each check gets 10 s, so one slow check fails alone
    try:
        if isinstance(c, list):
            exc = getattr(__builtins__, c[1]) if not isinstance(__builtins__, dict) else __builtins__[c[1]]
            try:
                eval(c[2], ns)
                results.append(False)
            except exc:
                results.append(True)
            except Exception:
                results.append(False)
        else:
            results.append(bool(eval(c, ns)))
    except Exception:
        results.append(False)
    finally:
        signal.alarm(0)
print("__RESULTS__" + json.dumps(results))
'''


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))


def run_checks(code, checks, helpers=""):
    """Returns (list of bools, error text). helpers: hidden code added after the model's code."""
    src = code + ("\n\n# --- hidden helpers ---\n" + helpers if helpers else "")
    with tempfile.TemporaryDirectory() as d:
        cand, runner = os.path.join(d, "candidate.py"), os.path.join(d, "run_checks.py")
        with open(cand, "w") as f:
            f.write(src)
        with open(runner, "w") as f:
            f.write("CANDIDATE_PATH = %r\nCHECKS = %s\n%s" % (cand, json.dumps(checks), RUNNER))
        try:
            p = subprocess.run(["unshare", "-rn", sys.executable, "-I", runner], cwd=d, capture_output=True,
                               text=True, timeout=90, preexec_fn=limits)
        except subprocess.TimeoutExpired:
            return [False] * len(checks), "timeout"
    m = re.search(r"__RESULTS__(\[.*\])", p.stdout)
    if not m:
        return [False] * len(checks), (p.stderr.strip().splitlines() or ["no output"])[-1][:200]
    return json.loads(m.group(1)), ""


def extract_code(text, task):
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.S)
    if not blocks:
        return text
    names = re.findall(r"`(\w+)\(", task["prompt"]) + re.findall(r"class `(\w+)`", task["prompt"])
    for b in reversed(blocks):  # last block that defines a required name
        if any(re.search(r"(def|class)\s+" + n + r"\b", b) for n in names):
            return b
    return max(blocks, key=len)


def ask(base, key, model, prompt, thinking, max_tokens, timeout, sampling=None):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
    body.update(sampling or {})   # request-level sampling overrides router.ini
    if not thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return d, time.time() - t0



def table(results):
    """Checks passed per task (added up over attempts), solved attempts, time and speed per model."""
    models = list(dict.fromkeys(r["model"] for r in results))
    tasks = list(dict.fromkeys(r["task"] for r in results))
    w = max([8] + [len(t) for t in tasks])
    print("\n%-17s " % "MODEL" + " ".join("%*s" % (w, t) for t in tasks) + "   CHECKS          SOLVED     TIME   TOK/S")
    for m in models:
        cells, p, n, solved, tries, wall, tps = [], 0, 0, 0, 0, 0.0, []
        for t in tasks:
            rows = [r for r in results if r["model"] == m and r["task"] == t]
            if not rows:
                cells.append("%*s" % (w, "-")); continue
            ok = [r for r in rows if "passed" in r]
            tp, tn = sum(r["passed"] for r in ok), sum(r["total"] for r in ok)
            cells.append("%*s" % (w, "%d/%d" % (tp, tn) + ("" if len(ok) == len(rows) else " err")))
            p += tp; n += tn; tries += len(rows)
            solved += sum(r["passed"] == r["total"] for r in ok)
            wall += sum(r["wall_s"] for r in ok)
            tps += [r["tps"] for r in ok if r.get("tps")]
        print("%-17s " % m + " ".join(cells) + "   %4d/%-4d %3.0f%%  %3d/%-3d  %6.0fs  %5.1f" % (
            p, n, 100.0 * p / n if n else 0, solved, tries, wall, sum(tps) / len(tps) if tps else 0))


def main():
    ap = argparse.ArgumentParser(description="Coding benchmark for the local models")
    ap.add_argument("--url", default="http://localhost:9700")
    ap.add_argument("--key-file", default=os.path.expanduser("~/locai/llama/api-key.txt"))
    ap.add_argument("--models", default="", help="comma-separated; default: every model the router lists except quick")
    ap.add_argument("--set", default="basic", choices=["basic", "hard", "all"], help="task set (default: basic)")
    ap.add_argument("--tasks", default="", help="comma-separated task names; default: all in the set")
    ap.add_argument("--repeat", type=int, default=1, help="attempts per task (default 1); more attempts smooth out lucky or unlucky answers")
    ap.add_argument("--thinking", action="store_true", help="leave thinking on (default: off, except models that always reason)")
    ap.add_argument("--max-tokens", type=int, default=0, help="default: 6000, 16000 with --thinking, 32000 for glimmer (16000 before 2026-10-30; pass --max-tokens 16000 to compare with older results)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per request")
    ap.add_argument("--temperature", type=float, default=None, help="send this temperature (default: the server's, from router.ini)")
    ap.add_argument("--presence-penalty", type=float, default=None, help="send this presence_penalty (default: the server's)")
    ap.add_argument("--out", default="", help="results file (default: code_test_<date-time>.json here)")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--list-tasks", action="store_true")
    ap.add_argument("--report", default="", help="print the table from a saved results file and exit")
    ap.add_argument("--rescore", default="", help="re-run the current checks on the answers in a saved results file, update it, print the table")
    a = ap.parse_args()

    if a.list_tasks:
        for t in TASKS: print("%-20s %s" % (t["name"], t["level"]))
        return
    if a.report:
        table(json.load(open(a.report))); return
    if a.rescore:
        results, by_name = json.load(open(a.rescore)), {t["name"]: t for t in TASKS}
        for r in results:
            t = by_name.get(r.get("task"))
            if t and "answer" in r:
                old = (r.get("passed"), r.get("total"))
                res, err = run_checks(extract_code(r["answer"], t), t["checks"], t["helpers"])
                r.update(passed=sum(res), total=len(res), checks=res, error=err)
                if old != (r["passed"], r["total"]):
                    print("%-17s %-20s %s/%s -> %d/%d" % (r["model"], r["task"], old[0], old[1], r["passed"], r["total"]))
        json.dump(results, open(a.rescore, "w"), indent=1)
        table(results); return
    if not shutil.which("unshare"):
        sys.exit("unshare is needed to run generated code without network access")
    tasks = [t for t in TASKS if (a.tasks and t["name"] in a.tasks.split(",")) or (not a.tasks and a.set in ("all", t["level"]))]
    if a.validate:
        ok = True
        for t in tasks:
            r, err = run_checks(t["reference"], t["checks"], t["helpers"])
            print("%-20s %d/%d %s" % (t["name"], sum(r), len(r), err)); ok &= all(r)
        r, _ = run_checks("pass", tasks[0]["checks"], tasks[0]["helpers"])
        print("an empty answer passes %d check(s) (expected 0)" % sum(r))
        sys.exit(0 if ok and not any(r) else 1)

    key = os.environ.get("LLAMA_API_KEY", "").strip()
    if not key:
        try: key = open(a.key_file).read().strip()
        except OSError as e: sys.exit("cannot read the key file (%s). Use --key-file or set LLAMA_API_KEY." % e)
    base = a.url.rstrip("/")
    if a.models:
        models = [m.strip() for m in a.models.split(",") if m.strip()]
    else:
        req = urllib.request.Request(base + "/v1/models", headers={"Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                models = [m["id"] for m in json.loads(r.read())["data"] if not m["id"].startswith("quick")]
        except Exception as e:
            sys.exit("cannot list models at %s: %s" % (base, e))
    out = a.out or os.path.join(os.getcwd(), time.strftime("code_test_%Y%m%d-%H%M.json"))
    print("models: %s\ntasks:  %s\nthinking: %s   attempts per task: %d   results: %s\n" % (", ".join(models), ", ".join(t["name"] for t in tasks), "on" if a.thinking else "off", a.repeat, out))

    sampling = {k: v for k, v in (("temperature", a.temperature), ("presence_penalty", a.presence_penalty)) if v is not None}
    if sampling:
        print("sampling sent with each request: %s\n" % sampling)
    results = []
    for model in models:
        max_tokens = a.max_tokens or (32000 if "glimmer" in model else 16000 if a.thinking else 6000)
        for t, attempt in [(t, n) for t in tasks for n in range(1, a.repeat + 1)]:
            print("%-17s %-20s %s" % (model, t["name"], "#%d " % attempt if a.repeat > 1 else ""), end="", flush=True)
            try:
                d, wall = ask(base, key, model, t["prompt"], a.thinking, max_tokens, a.timeout, sampling)
            except urllib.error.HTTPError as e:
                print("HTTP %s %s" % (e.code, e.read()[:200])); results.append({"model": model, "task": t["name"], "attempt": attempt, "error": "HTTP %s" % e.code}); continue
            except Exception as e:
                print("REQUEST ERROR", e); results.append({"model": model, "task": t["name"], "attempt": attempt, "error": str(e)}); continue
            msg = d["choices"][0]["message"]
            text = msg.get("content") or ""
            r, err = run_checks(extract_code(text, t), t["checks"], t["helpers"])
            tm = d.get("timings", {})
            row = {"model": model, "task": t["name"], "attempt": attempt, "thinking": a.thinking, "sampling": sampling, "passed": sum(r), "total": len(r),
                   "checks": r, "error": err, "wall_s": round(wall, 1), "gen_tokens": tm.get("predicted_n"),
                   "tps": round(tm.get("predicted_per_second", 0), 1), "finish": d["choices"][0].get("finish_reason"),
                   "reasoning_chars": len(msg.get("reasoning_content") or ""), "answer": text}
            results.append(row)
            json.dump(results, open(out, "w"), indent=1)
            print("%2d/%-2d %6.1fs %6s tok %5.1f tok/s %s %s" % (sum(r), len(r), wall, row["gen_tokens"] or 0, row["tps"], row["finish"], err), flush=True)
    table(results)
    print("\nresults saved to", out)


if __name__ == "__main__":
    main()
