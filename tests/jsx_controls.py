"""Tiny JSX opening-tag scanner (brace/quote aware) shared by the control-label test and the codemod."""
import re

CONTROL_TAGS = ("Button", "button", "Input", "Select", "Textarea", "Switch", "input", "select", "textarea", "a")
TAG_RE = re.compile(r"<(" + "|".join(CONTROL_TAGS) + r")(?=[\s/>])")


def opening_tags(text):
    """Yield (tag, start, attrs_start, end, attrs, self_closing) for each control opening tag."""
    for m in TAG_RE.finditer(text):
        i, depth, q = m.end(), 0, None
        n = len(text)
        while i < n:
            c = text[i]
            if q:
                if c == "\\":
                    i += 2
                    continue
                if c == q:
                    q = None
            elif c in "\"'`" and (depth > 0 or c in "\"'"):
                q = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == ">" and depth == 0:
                break
            i += 1
        attrs = text[m.end():i]
        yield m.group(1), m.start(), m.end(), i, attrs, attrs.rstrip().endswith("/")


def labelled(tag, attrs):
    if re.search(r"(^|\s)(data-testid|aria-label|aria-labelledby)=", attrs) or re.search(r"\{\s*\.\.\.", attrs):
        return True
    if tag == "Switch" and re.search(r"(^|\s)(testid|label)=", attrs):
        return True
    return False


def controls(text):
    for tag, s, a, e, attrs, sc in opening_tags(text):
        if tag == "a" and not re.search(r"(^|\s)href=", attrs):
            continue
        if tag in ("input",) and re.search(r'type="hidden"', attrs):
            continue
        yield tag, s, a, e, attrs, sc
