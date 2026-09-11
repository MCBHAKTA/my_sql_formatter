"""PropSol house-style SQL formatter.

Matches the leading-comma, right-aligned keyword layout used in the EPC SQL:
    SELECT col1
          ,col2
      FROM t
     WHERE x = 1
       AND y = 2
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlparse import tokens as T
from sqlparse.engine import FilterStack

KW_WIDTH = 6
DEFAULT_WRAP = 110

CLAUSE_KWS = {
    "SELECT",
    "FROM",
    "WHERE",
    "GROUP BY",
    "ORDER BY",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "QUALIFY",
    "WINDOW",
    "UNION",
    "UNION ALL",
    "EXCEPT",
    "INTERSECT",
    "MINUS",
    "ON",
    "USING",
    "RETURNING",
}

JOIN_RE = re.compile(
    r"^((LEFT|RIGHT|FULL|INNER|CROSS|NATURAL|OUTER|STRAIGHT|ANTI|SEMI)\s+)+JOIN$|^JOIN$",
    re.I,
)

SECTION_COMMENT_RE = re.compile(r"^--\s*-{3,}")
PYTHON_SQL_STRING_RE = re.compile(
    r"(?P<pre>(?:r|f|b|u|fr|rf|br|rb)?)"
    r'(?P<q>"""|\'\'\')'
    r"(?P<body>.*?)"
    r"(?P=q)",
    re.S | re.I,
)


@dataclass
class Tok:
    type: str
    value: str

    @property
    def n(self) -> str:
        return self.value.upper() if self.type == "kw" else self.value


def _is_keyword_ttype(ttype) -> bool:
    if ttype is None:
        return False
    return ttype in T.Keyword or ttype.parent is T.Keyword


def flatten_statement(stmt) -> list[Tok]:
    out: list[Tok] = []
    for tok in stmt.flatten():
        if tok.is_whitespace:
            continue
        ttype = tok.ttype
        value = tok.value
        if ttype in T.Comment or (ttype is not None and ttype.parent is T.Comment):
            text = value.replace("\r\n", "\n").replace("\r", "\n")
            text = text.split("\n")[0].rstrip()
            if text:
                out.append(Tok("comment", text))
            continue
        if _is_keyword_ttype(ttype):
            out.append(Tok("kw", value.upper()))
            continue
        if ttype in T.Operator or (ttype is not None and ttype.parent is T.Operator):
            out.append(Tok("op", value))
            continue
        if ttype == T.Punctuation:
            out.append(Tok("punct", value))
            continue
        if ttype == T.Wildcard:
            out.append(Tok("wildcard", value))
            continue
        if ttype in T.Number or (ttype is not None and ttype.parent is T.Number):
            out.append(Tok("num", value))
            continue
        if ttype in T.String or (ttype is not None and ttype.parent is T.String):
            out.append(Tok("str", value))
            continue
        if ttype == T.Error:
            out.append(Tok("error", value))
            continue
        out.append(Tok("name", value))
    return _merge_compound_keywords(_merge_placeholders(out))


def _merge_placeholders(tokens: list[Tok]) -> list[Tok]:
    merged: list[Tok] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        # ${name}
        if (
            t.value == "$"
            and i + 3 < len(tokens)
            and tokens[i + 1].value == "{"
            and tokens[i + 3].value == "}"
        ):
            name = tokens[i + 2].value
            merged.append(Tok("placeholder", f"${{{name}}}"))
            i += 4
            continue
        # {name}  (Python f-string interpolations inside SQL)
        if (
            t.value == "{"
            and i + 2 < len(tokens)
            and tokens[i + 2].value == "}"
            and tokens[i + 1].type in {"name", "num"}
        ):
            merged.append(Tok("placeholder", "{" + tokens[i + 1].value + "}"))
            i += 3
            continue
        merged.append(t)
        i += 1
    return merged


def _merge_compound_keywords(tokens: list[Tok]) -> list[Tok]:
    merged: list[Tok] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if (
            t.type in {"name", "kw"}
            and t.value.upper() == "WITHIN"
            and nxt is not None
            and nxt.type == "kw"
            and nxt.n == "GROUP"
        ):
            merged.append(Tok("kw", "WITHIN GROUP"))
            i += 2
            continue
        merged.append(t)
        i += 1
    return merged


class Cursor:
    def __init__(self, tokens: list[Tok], i: int = 0):
        self.tokens = tokens
        self.i = i

    def peek(self, n: int = 0) -> Tok | None:
        j = self.i + n
        if 0 <= j < len(self.tokens):
            return self.tokens[j]
        return None

    def take(self) -> Tok:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def at_kw(self, *names: str) -> bool:
        t = self.peek()
        return bool(t and t.type == "kw" and t.n in names)

    def at_join(self) -> bool:
        t = self.peek()
        return bool(t and t.type == "kw" and JOIN_RE.match(t.n))

    def rest(self) -> list[Tok]:
        return self.tokens[self.i :]

    def remaining(self) -> bool:
        return self.i < len(self.tokens)


def kw_prefix(keyword: str, base_indent: int) -> str:
    keyword = keyword.upper()
    if JOIN_RE.match(keyword):
        return " " * (base_indent + 2) + keyword + " "
    pad = KW_WIDTH - len(keyword)
    if pad >= 0:
        return " " * (base_indent + pad) + keyword + " "
    start = max(0, base_indent + KW_WIDTH - len(keyword))
    if start == 0 and base_indent == 0:
        start = 1
    return " " * start + keyword + " "


def is_section_comment(token: Tok) -> bool:
    return token.type == "comment" and bool(SECTION_COMMENT_RE.match(token.value.strip()))


def _comma_col(base_indent: int) -> int:
    return base_indent + KW_WIDTH


def _depth_delta(token: Tok) -> tuple[int, int]:
    case_d = 0
    paren_d = 0
    if token.type == "kw":
        if token.n == "CASE":
            case_d = 1
        elif token.n == "END":
            case_d = -1
    elif token.type == "punct":
        if token.value == "(":
            paren_d = 1
        elif token.value == ")":
            paren_d = -1
    return paren_d, case_d


def split_top_level_commas(tokens: list[Tok]) -> list[list[Tok]]:
    items: list[list[Tok]] = []
    current: list[Tok] = []
    paren = 0
    case = 0
    for t in tokens:
        pd, cd = _depth_delta(t)
        if t.type == "punct" and t.value == "," and paren == 0 and case == 0:
            items.append(current)
            current = []
            paren += pd
            case = max(0, case + cd)
            continue
        current.append(t)
        paren += pd
        case = max(0, case + cd)
    if current or items:
        items.append(current)
    return items


def take_balanced_paren(cur: Cursor) -> list[Tok]:
    """Consume '(' ... ')' and return inner tokens (without the parens)."""
    assert cur.peek() and cur.peek().type == "punct" and cur.peek().value == "("
    cur.take()
    inner: list[Tok] = []
    depth = 1
    while cur.remaining() and depth:
        t = cur.take()
        if t.type == "punct" and t.value == "(":
            depth += 1
            inner.append(t)
        elif t.type == "punct" and t.value == ")":
            depth -= 1
            if depth:
                inner.append(t)
        else:
            inner.append(t)
    return inner


def contains_select(tokens: list[Tok]) -> bool:
    paren = 0
    for t in tokens:
        if t.type == "punct" and t.value == "(":
            paren += 1
        elif t.type == "punct" and t.value == ")":
            paren = max(0, paren - 1)
        elif t.type == "kw" and t.n == "SELECT" and paren == 0:
            return True
    return False


def needs_space(prev: Tok | None, curr: Tok) -> bool:
    if prev is None:
        return False
    if curr.type == "comment":
        return False
    if curr.type == "punct" and curr.value in ",)]":
        return False
    if prev.type == "punct" and prev.value in "([.":
        return False
    if curr.type == "punct" and curr.value == ".":
        return False
    if prev.type == "punct" and prev.value == ".":
        return False
    if curr.type == "punct" and curr.value == "(":
        if prev.type in {"name", "wildcard", "placeholder"}:
            return False
        if prev.type == "punct" and prev.value == ")":
            return False
        if prev.type == "kw" and prev.n in {
            "IN",
            "VALUES",
            "EXISTS",
            "FROM",
            "JOIN",
            "OVER",
            "WITHIN GROUP",
            "AS",
            "TABLE",
            "VIEW",
            "INTO",
            "ON",
        }:
            return True
        if prev.type == "kw" and JOIN_RE.match(prev.n):
            return True
        return True
    if curr.type == "op":
        if curr.value in "-+" and (
            prev.type == "op" or (prev.type == "punct" and prev.value in "(,")
        ):
            return False
        return True
    if prev.type == "op":
        if prev.value in "-+" and curr.type in {"num", "name", "placeholder", "kw"}:
            # unary minus: no space after if previous was already unary context;
            # binary minus always had space before, keep space after too.
            return True
        return True
    return True


def render_inline(tokens: list[Tok]) -> str:
    pieces: list[str] = []
    prev: Tok | None = None
    trailing_comment = ""
    for t in tokens:
        if t.type == "comment":
            trailing_comment = t.value.strip()
            continue
        text = t.n if t.type == "kw" else t.value
        if needs_space(prev, t):
            pieces.append(" ")
        pieces.append(text)
        prev = t
    rendered = "".join(pieces).strip()
    if trailing_comment:
        rendered = f"{rendered}  {trailing_comment}"
    return rendered


def split_case(tokens: list[Tok]) -> tuple[list[Tok], list[tuple[list[Tok] | None, list[Tok]]], list[Tok]]:
    """Return (prefix_before_first_when, [(cond or None, value)], suffix_after_end)."""
    assert tokens and tokens[0].type == "kw" and tokens[0].n == "CASE"
    i = 1
    prefix: list[Tok] = []
    while i < len(tokens) and not (tokens[i].type == "kw" and tokens[i].n in {"WHEN", "ELSE", "END"}):
        prefix.append(tokens[i])
        i += 1

    branches: list[tuple[list[Tok] | None, list[Tok]]] = []
    while i < len(tokens):
        t = tokens[i]
        if t.type == "kw" and t.n == "WHEN":
            i += 1
            cond: list[Tok] = []
            while i < len(tokens) and not (tokens[i].type == "kw" and tokens[i].n == "THEN"):
                cond.append(tokens[i])
                i += 1
            if i < len(tokens) and tokens[i].type == "kw" and tokens[i].n == "THEN":
                i += 1
            value: list[Tok] = []
            case_depth = 0
            while i < len(tokens):
                nt = tokens[i]
                if nt.type == "kw" and nt.n == "CASE":
                    case_depth += 1
                    value.append(nt)
                    i += 1
                    continue
                if nt.type == "kw" and nt.n == "END" and case_depth:
                    case_depth -= 1
                    value.append(nt)
                    i += 1
                    continue
                if case_depth == 0 and nt.type == "kw" and nt.n in {"WHEN", "ELSE", "END"}:
                    break
                value.append(nt)
                i += 1
            branches.append((cond, value))
            continue
        if t.type == "kw" and t.n == "ELSE":
            i += 1
            value = []
            case_depth = 0
            while i < len(tokens):
                nt = tokens[i]
                if nt.type == "kw" and nt.n == "CASE":
                    case_depth += 1
                    value.append(nt)
                    i += 1
                    continue
                if nt.type == "kw" and nt.n == "END" and case_depth:
                    case_depth -= 1
                    value.append(nt)
                    i += 1
                    continue
                if case_depth == 0 and nt.type == "kw" and nt.n == "END":
                    break
                value.append(nt)
                i += 1
            branches.append((None, value))
            continue
        if t.type == "kw" and t.n == "END":
            i += 1
            break
        i += 1
    suffix = tokens[i:]
    return prefix, branches, suffix


def render_case(tokens: list[Tok], when_col: int, wrap_width: int) -> list[str]:
    """Render CASE..END [AS alias] as one or more lines, no leading indent on first line."""
    prefix, branches, suffix = split_case(tokens)
    when_pad = " " * when_col
    lines: list[str] = []

    head = "CASE"
    if prefix:
        head += " " + render_inline(prefix)

    if not branches:
        return [render_inline(tokens)]

    first = True
    for cond, value in branches:
        val_sql = render_inline(value)
        if cond is None:
            chunk = f"ELSE {val_sql}"
            lines.append(when_pad + chunk if not first else chunk)
            first = False
            continue
        cond_sql = render_inline(cond)
        when_then = f"WHEN {cond_sql} THEN {val_sql}"
        if first:
            candidate = f"{head} {when_then}"
            if len(candidate) <= wrap_width:
                lines.append(candidate)
            else:
                lines.append(f"{head} WHEN {cond_sql}")
                lines.append(f"{when_pad}THEN {val_sql}")
            first = False
        else:
            candidate = f"{when_pad}{when_then}"
            if len(candidate) <= wrap_width:
                lines.append(candidate)
            else:
                lines.append(f"{when_pad}WHEN {cond_sql}")
                lines.append(f"{when_pad}THEN {val_sql}")

    end_line = f"{when_pad}END"
    if suffix:
        end_line += " " + render_inline(suffix)
    lines.append(end_line)
    return lines


def _split_section_comments(tokens: list[Tok]) -> tuple[list[Tok], list[Tok], list[Tok]]:
    leading: list[Tok] = []
    trailing: list[Tok] = []
    body: list[Tok] = []
    seen_body = False
    for t in tokens:
        if is_section_comment(t):
            if seen_body:
                trailing.append(t)
            else:
                leading.append(t)
        else:
            seen_body = True
            body.append(t)
    return leading, body, trailing


def _emit_section_comments(lines: list[str], comments: list[Tok]) -> None:
    for c in comments:
        if lines and lines[-1] != "":
            lines.append("")
        elif not lines:
            lines.append("")
        lines.append(c.value.strip())


def render_select_item(tokens: list[Tok], first: bool, comma_col: int, wrap_width: int) -> list[str]:
    leading, inline_body, trailing = _split_section_comments(tokens)
    lines: list[str] = []
    _emit_section_comments(lines, leading)

    if inline_body:
        item_prefix = "" if first else (" " * comma_col + ",")
        start = 0
        while start < len(inline_body) and inline_body[start].type == "comment":
            start += 1
        is_case = (
            start < len(inline_body)
            and inline_body[start].type == "kw"
            and inline_body[start].n == "CASE"
        )
        if is_case:
            when_col = comma_col + 1 + len("CASE ")
            case_lines = render_case(inline_body[start:], when_col, wrap_width)
            if first:
                lines.extend(case_lines)
            else:
                lines.append(item_prefix + case_lines[0])
                lines.extend(case_lines[1:])
        else:
            rendered = render_inline(inline_body)
            lines.append(rendered if first else item_prefix + rendered)

    _emit_section_comments(lines, trailing)
    return lines


def strip_leading_blank(lines: list[str]) -> list[str]:
    while lines and lines[0] == "":
        lines.pop(0)
    return lines


def format_select_list(items: list[list[Tok]], base_indent: int, wrap_width: int, _first_on_same_line: bool) -> list[str]:
    comma_col = _comma_col(base_indent)
    lines: list[str] = []
    first_emitted = False
    for raw_item in items:
        item = [t for t in raw_item]
        if not item:
            continue
        only_section = item and all(is_section_comment(t) or t.type == "comment" for t in item) and any(
            is_section_comment(t) for t in item
        )
        if only_section:
            if lines and lines[-1] != "":
                lines.append("")
            for t in item:
                if t.type == "comment":
                    if is_section_comment(t):
                        if lines and lines[-1] != "":
                            lines.append("")
                        lines.append(t.value.strip())
                    else:
                        lines.append(t.value.strip())
            continue

        item_lines = render_select_item(
            item,
            first=not first_emitted,
            comma_col=comma_col,
            wrap_width=wrap_width,
        )
        # First real expression goes after SELECT
        if not first_emitted:
            item_lines = strip_leading_blank(item_lines[:]) if not any(
                is_section_comment(t) for t in item
            ) else item_lines
            # Section comments before the first column
            if item_lines and item_lines[0] == "":
                # keep section-comment blank line handling
                pass
        lines.extend(item_lines)
        if any(not (ln.startswith("--") or ln == "") for ln in item_lines):
            first_emitted = True
    return lines


def take_until_clause(cur: Cursor) -> list[Tok]:
    """Take tokens until a top-level query clause / join (not consumed)."""
    taken: list[Tok] = []
    paren = 0
    case = 0
    while cur.remaining():
        t = cur.peek()
        assert t is not None
        pd, cd = _depth_delta(t)
        at_top = paren == 0 and case == 0
        if at_top:
            if t.type == "kw" and t.n in CLAUSE_KWS and taken:
                break
            if t.type == "kw" and JOIN_RE.match(t.n) and taken:
                break
            if t.type == "kw" and t.n in {"AND", "OR"} and taken:
                # AND/OR belong to WHERE/HAVING/ON; stop so caller can line-break
                # but only if we already started that clause's predicate.
                break
        taken.append(cur.take())
        paren += pd
        case = max(0, case + cd)
    return taken


def take_select_list_tokens(cur: Cursor) -> list[Tok]:
    taken: list[Tok] = []
    paren = 0
    case = 0
    while cur.remaining():
        t = cur.peek()
        assert t is not None
        pd, cd = _depth_delta(t)
        at_top = paren == 0 and case == 0
        if at_top and t.type == "kw":
            if t.n in CLAUSE_KWS - {"SELECT"}:
                break
            if JOIN_RE.match(t.n):
                break
        taken.append(cur.take())
        paren += pd
        case = max(0, case + cd)
    return taken


def is_simple_subquery(tokens: list[Tok], wrap_width: int, prefix_len: int) -> bool:
    if not tokens or not any(t.type == "kw" and t.n == "SELECT" for t in tokens):
        return False
    cur = Cursor(tokens)
    if not cur.at_kw("SELECT"):
        return False
    # no WITH / UNION / JOIN / WHERE / GROUP / HAVING
    paren = 0
    case = 0
    saw_where_join = False
    select_items = 0
    for t in tokens:
        pd, cd = _depth_delta(t)
        if paren == 0 and case == 0 and t.type == "kw":
            if t.n in {"WHERE", "GROUP BY", "ORDER BY", "HAVING", "UNION", "UNION ALL", "QUALIFY"}:
                saw_where_join = True
            if JOIN_RE.match(t.n):
                saw_where_join = True
            if t.n in {"WITH"}:
                return False
        paren += pd
        case = max(0, case + cd)
    if saw_where_join:
        return False
    # count top-level select items
    cur = Cursor(tokens)
    cur.take()  # SELECT
    if cur.at_kw("DISTINCT", "ALL"):
        cur.take()
    select_tokens = take_select_list_tokens(cur)
    items = [it for it in split_top_level_commas(select_tokens) if it]
    if len(items) != 1:
        return False
    rendered = "SELECT " + render_inline(tokens[1:])
    return prefix_len + len(rendered) + 1 <= wrap_width


def format_query(tokens: list[Tok], base_indent: int, wrap_width: int, first_on_same_line: bool = False) -> str:
    tokens = [t for t in tokens]
    if not tokens:
        return ""
    cur = Cursor(tokens)
    chunks: list[str] = []

    # Leading comments
    while cur.peek() and cur.peek().type == "comment":
        chunks.append(" " * base_indent + cur.take().value.strip())

    if cur.at_kw("WITH"):
        chunks.append(format_with(cur, base_indent, wrap_width))
        if cur.remaining():
            chunks.append("")
            chunks.append(format_query(cur.rest(), base_indent, wrap_width, first_on_same_line=False))
        return "\n".join(chunks).rstrip()

    # CREATE ... AS is handled in format_statement; here we may still see SELECT / INSERT
    chunks.append(format_select_core(cur, base_indent, wrap_width, first_on_same_line))

    # Set operations
    while cur.remaining() and cur.at_kw("UNION", "UNION ALL", "EXCEPT", "INTERSECT", "MINUS"):
        kw = cur.take().n
        extra = ""
        if kw == "UNION" and cur.at_kw("ALL"):
            kw = "UNION ALL"
            cur.take()
        chunks.append(kw_prefix(kw, base_indent).rstrip())
        chunks.append(format_select_core(cur, base_indent, wrap_width, first_on_same_line=False))

    return "\n".join(line for line in chunks if line is not None).rstrip()


def format_with(cur: Cursor, base_indent: int, wrap_width: int) -> str:
    assert cur.at_kw("WITH")
    cur.take()
    recursive = ""
    if cur.at_kw("RECURSIVE"):
        recursive = " RECURSIVE"
        cur.take()

    ctes: list[str] = []
    first = True
    while cur.remaining():
        # optional leading comma
        if cur.peek() and cur.peek().type == "punct" and cur.peek().value == ",":
            cur.take()
        if cur.at_kw("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE OR REPLACE", "CREATE"):
            break
        name_tok = cur.peek()
        if not name_tok or name_tok.type not in {"name", "placeholder", "kw"}:
            break
        name = cur.take().value
        cols = ""
        if cur.peek() and cur.peek().type == "punct" and cur.peek().value == "(":
            # CTE column list, not the query
            # Heuristic: if next query is AS (, this paren is column list
            inner = take_balanced_paren(cur)
            if cur.at_kw("AS"):
                cols = "(" + render_inline(inner) + ")"
            else:
                # shouldn't happen
                cols = "(" + render_inline(inner) + ")"
        if not cur.at_kw("AS"):
            break
        cur.take()  # AS
        if not (cur.peek() and cur.peek().type == "punct" and cur.peek().value == "("):
            break
        inner = take_balanced_paren(cur)
        body = format_query(inner, 0, wrap_width, first_on_same_line=False).rstrip()
        cte = f"{name}{cols} AS (\n{body})"
        if first:
            ctes.append(f"{' ' * base_indent}WITH{recursive} {cte}")
            first = False
        else:
            ctes.append(f"\n, {cte}")
        # continue if comma next
        if cur.peek() and cur.peek().type == "punct" and cur.peek().value == ",":
            continue
        break
    return "\n".join(ctes)


def _split_and_or(tokens: list[Tok]) -> list[tuple[str | None, list[Tok]]]:
    """Split top-level AND/OR, keeping BETWEEN ... AND together."""
    parts: list[tuple[str | None, list[Tok]]] = []
    current: list[Tok] = []
    conj: str | None = None
    paren = 0
    case = 0
    i = 0
    while i < len(tokens):
        t = tokens[i]
        pd, cd = _depth_delta(t)
        if (
            paren == 0
            and case == 0
            and t.type == "kw"
            and t.n in {"AND", "OR"}
            and current
            and not _preceded_by_between(current)
        ):
            parts.append((conj, current))
            conj = t.n
            current = []
            i += 1
            continue
        current.append(t)
        paren += pd
        case = max(0, case + cd)
        i += 1
    if current or parts:
        parts.append((conj, current))
    return parts


def _preceded_by_between(current: list[Tok]) -> bool:
    """True if last top-level keyword in current is BETWEEN (so AND is the BETWEEN pair)."""
    paren = 0
    case = 0
    last_kw = None
    for t in current:
        pd, cd = _depth_delta(t)
        if paren == 0 and case == 0 and t.type == "kw":
            last_kw = t.n
        paren += pd
        case = max(0, case + cd)
    return last_kw == "BETWEEN"


def format_predicate(tokens: list[Tok], base_indent: int, first_kw: str, wrap_width: int) -> list[str]:
    parts = _split_and_or(tokens)
    lines: list[str] = []
    for i, (conj, expr) in enumerate(parts):
        sql = render_inline(expr)
        if i == 0:
            lines.append(kw_prefix(first_kw, base_indent) + sql)
        else:
            lines.append(kw_prefix(conj or "AND", base_indent) + sql)
    return lines


def format_from_item(tokens: list[Tok], base_indent: int, wrap_width: int, kw: str) -> list[str]:
    """Format FROM / JOIN source, expanding parenthesized subqueries when needed."""
    cur = Cursor(tokens)
    alias_and_rest: list[Tok] = []

    # Optional LATERAL
    prefix_kws = []
    while cur.at_kw("LATERAL", "ONLY"):
        prefix_kws.append(cur.take().n)

    if cur.peek() and cur.peek().type == "punct" and cur.peek().value == "(":
        inner = take_balanced_paren(cur)
        while cur.remaining():
            alias_and_rest.append(cur.take())
        alias_sql = render_inline(alias_and_rest)
        head = kw_prefix(kw, base_indent)
        if prefix_kws:
            head += " ".join(prefix_kws) + " "
        if contains_select(inner):
            open_prefix = head + "("
            if is_simple_subquery(inner, wrap_width, len(open_prefix)):
                line = open_prefix + render_inline(inner) + ")"
                if alias_sql:
                    line += " " + alias_sql
                return [line]
            inner_base = len(open_prefix)
            inner_sql = format_query(inner, inner_base, wrap_width, first_on_same_line=True)
            line = open_prefix + inner_sql + ")"
            if alias_sql:
                line += " " + alias_sql
            return line.split("\n")
        else:
            line = head + "(" + render_inline(inner) + ")"
            if alias_sql:
                line += " " + alias_sql
            return [line]

    # Table / function
    return [kw_prefix(kw, base_indent) + render_inline(tokens)]


def format_select_core(cur: Cursor, base_indent: int, wrap_width: int, first_on_same_line: bool) -> str:
    lines: list[str] = []

    if cur.at_kw("SELECT"):
        cur.take()
        distinct = ""
        if cur.at_kw("DISTINCT", "ALL"):
            distinct = cur.take().n + " "
        select_tokens = take_select_list_tokens(cur)
        items = split_top_level_commas(select_tokens)
        item_lines = format_select_list(items, base_indent, wrap_width, first_on_same_line)
        select_kw = "SELECT " if not distinct else f"SELECT {distinct}"
        if first_on_same_line:
            prefix = select_kw
        else:
            prefix = kw_prefix("SELECT", base_indent)
            if distinct:
                # SELECT DISTINCT: keep DISTINCT on the SELECT line
                prefix = (" " * base_indent) + "SELECT " + distinct
        if not item_lines:
            lines.append(prefix.rstrip())
        else:
            # Attach first non-blank/non-comment expression to SELECT
            attached = False
            pending: list[str] = []
            for ln in item_lines:
                if not attached and (ln.startswith("--") or ln == ""):
                    pending.append(ln)
                    continue
                if not attached:
                    lines.extend(pending)
                    lines.append(prefix + ln)
                    attached = True
                else:
                    lines.append(ln)
            if not attached:
                lines.extend(pending)
                lines.append(prefix.rstrip())

    while cur.remaining():
        t = cur.peek()
        assert t is not None
        if t.type == "kw" and t.n in {"UNION", "UNION ALL", "EXCEPT", "INTERSECT", "MINUS"}:
            break
        if t.type == "comment" and is_section_comment(t):
            lines.append("")
            lines.append(cur.take().value.strip())
            continue
        if t.type == "comment":
            if lines:
                lines[-1] = lines[-1] + "  " + cur.take().value.strip()
            else:
                lines.append(cur.take().value.strip())
            continue
        if cur.at_kw("FROM") or cur.at_join():
            kw = cur.take().n
            src_tokens = []
            paren = 0
            case = 0
            while cur.remaining():
                nt = cur.peek()
                assert nt is not None
                pd, cd = _depth_delta(nt)
                at_top = paren == 0 and case == 0
                if at_top:
                    if nt.type == "kw" and nt.n in CLAUSE_KWS:
                        break
                    if nt.type == "kw" and JOIN_RE.match(nt.n):
                        break
                    if nt.type == "kw" and nt.n in {"AND", "OR"}:
                        break
                src_tokens.append(cur.take())
                paren += pd
                case = max(0, case + cd)
            lines.extend(format_from_item(src_tokens, base_indent, wrap_width, kw))
            continue
        if cur.at_kw("ON"):
            cur.take()
            pred = take_until_clause(cur)
            # take_until_clause stops at AND — include AND/OR chain
            extra: list[Tok] = []
            while cur.at_kw("AND", "OR"):
                extra.append(cur.take())
                extra.extend(take_until_clause(cur))
            lines.extend(format_predicate(pred + extra, base_indent, "ON", wrap_width))
            continue
        if cur.at_kw("WHERE", "HAVING", "QUALIFY"):
            kw = cur.take().n
            pred = take_until_clause(cur)
            extra = []
            while cur.at_kw("AND", "OR"):
                extra.append(cur.take())
                extra.extend(take_until_clause(cur))
            lines.extend(format_predicate(pred + extra, base_indent, kw, wrap_width))
            continue
        if cur.at_kw("GROUP BY", "ORDER BY"):
            kw = cur.take().n
            list_tokens = take_until_clause(cur)
            items = [it for it in split_top_level_commas(list_tokens) if it]
            prefix = kw_prefix(kw, base_indent)
            comma_col = len(prefix) - 1  # column of the space before first expr... want comma under first expr
            # prefix ends with space; first expr starts at len(prefix)
            expr_col = len(prefix)
            if not items:
                lines.append(prefix.rstrip())
            elif len(items) == 1:
                lines.append(prefix + render_inline(items[0]))
            else:
                lines.append(prefix + render_inline(items[0]))
                for it in items[1:]:
                    lines.append(" " * (expr_col - 1) + "," + render_inline(it))
            continue
        if cur.at_kw("LIMIT", "OFFSET"):
            kw = cur.take().n
            args = take_until_clause(cur)
            lines.append(kw_prefix(kw, base_indent) + render_inline(args))
            continue
        # Fallback: emit remaining inline so we never drop SQL
        rest = cur.rest()
        if rest:
            lines.append(" " * base_indent + render_inline(rest))
            cur.i = len(cur.tokens)
        break

    return "\n".join(lines)


def format_statement_tokens(tokens: list[Tok], wrap_width: int) -> str:
    if not tokens:
        return ""
    cur = Cursor(tokens)
    semi = False
    if tokens[-1].type == "punct" and tokens[-1].value == ";":
        semi = True
        tokens = tokens[:-1]
        cur = Cursor(tokens)

    if cur.at_kw("CREATE", "CREATE OR REPLACE"):
        head: list[Tok] = []
        while cur.remaining():
            if cur.at_kw("AS") and _lookahead_query(cur):
                head.append(cur.take())
                break
            head.append(cur.take())
        header = render_inline(head)
        body = format_query(cur.rest(), 0, wrap_width)
        out = header + "\n" + body if body else header
        return out + (";" if semi else "")

    out = format_query(tokens, 0, wrap_width)
    return out + (";" if semi else "")


def _lookahead_query(cur: Cursor) -> bool:
    nxt = cur.peek(1)
    if nxt and nxt.type == "kw" and nxt.n in {"SELECT", "WITH"}:
        return True
    if nxt and nxt.type == "punct" and nxt.value == "(":
        return True
    return False


def _parse_statements(sql: str):
    """Lex SQL into statements without sqlparse's grouping pass.

    Grouping is a DoS guard in sqlparse and raises once a statement has more
    than 10,000 tokens. Long SELECT lists (EPC certificates SQL, notebook
    cells) go past that because every indent space is a token. This formatter
    walks a flat token stream, so grouping is unnecessary.
    """
    stack = FilterStack()
    return tuple(stack.run(sql))


def format_sql(sql: str, wrap_width: int = DEFAULT_WRAP) -> str:
    sql = sql.strip()
    if not sql:
        return ""
    formatted_parts: list[str] = []
    for stmt in _parse_statements(sql):
        tokens = flatten_statement(stmt)
        if not tokens:
            continue
        formatted_parts.append(format_statement_tokens(tokens, wrap_width))
    return "\n\n".join(p.rstrip() for p in formatted_parts if p.strip()).rstrip() + ("\n" if sql.endswith("\n") else "")


def _looks_like_python(text: str) -> bool:
    if re.search(r"^\s*(import |from \w|def |class |if __name__)", text, re.M):
        return True
    if re.search(r"=\s*(?:r|f|b|u|fr|rf)*['\"]{3}", text, re.I):
        return True
    return False


def _looks_like_sql(text: str) -> bool:
    u = text.upper()
    return bool(
        re.search(r"\bSELECT\b", u)
        or re.search(r"\bWITH\b", u)
        or re.search(r"\bCREATE\b", u)
        or re.search(r"\bINSERT\b", u)
        or re.search(r"\bCOPY\b", u)
    )


def format_python_sql_strings(source: str, wrap_width: int = DEFAULT_WRAP) -> str:
    def repl(match: re.Match) -> str:
        pre = match.group("pre") or ""
        quote = match.group("q")
        body = match.group("body")
        if not _looks_like_sql(body):
            return match.group(0)
        formatted = format_sql(body, wrap_width=wrap_width).strip("\n")
        nl = "\n"
        return f"{pre}{quote}{nl}{formatted}{nl}{quote}"

    return PYTHON_SQL_STRING_RE.sub(repl, source)


def format_text(text: str, wrap_width: int = DEFAULT_WRAP, python_strings: bool | None = None) -> str:
    if python_strings is None:
        python_strings = _looks_like_python(text)
    if python_strings:
        return format_python_sql_strings(text, wrap_width=wrap_width)
    return format_sql(text, wrap_width=wrap_width)
