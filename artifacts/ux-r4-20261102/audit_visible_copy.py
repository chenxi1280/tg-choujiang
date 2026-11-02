"""Inventory public-copy literals; internal errors, logs, commands and matchers are excluded."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HAN = re.compile('[\u3400-\u9fff]')
MATCHER_FILES = {'ui_style.py', 'command_input.py', 'bot_commands.py'}
INPUT_FORMAT_OWNERS = {'custom_draw_prompt', 'custom_draw_value', 'parse_draw_at'}
INTERNAL_ERRORS = {'ValueError', 'RuntimeError', 'AuthorizationDenied', 'SourceQualificationUnknown'}
MATCH_METHODS = {'startswith', 'endswith', 'removeprefix', 'removesuffix', 'split', 'partition', 'find', 'index'}
LOG_METHODS = {'debug', 'info', 'warning', 'error', 'exception', 'critical'}


def ancestry(node, parents):
    result = []
    while node in parents:
        node = parents[node]
        result.append(node)
    return result


def call_name(call):
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, 'attr', '')


def text_of(node):
    if isinstance(node, ast.Constant):
        return node.value
    return ''.join(value.value if isinstance(value, ast.Constant) else '{…}' for value in node.values)


def exclusion(path, node, chain, text):
    if path.name in MATCHER_FILES:
        return 'matcher/command definition (not rendered directly)'
    if any(isinstance(parent, ast.Expr) for parent in chain[:1]):
        return 'docstring'
    if text.lstrip().startswith(('http://', 'https://', '/')):
        return 'URL/command/code input'
    for parent in chain:
        if isinstance(parent, ast.Call) and call_name(parent) in MATCH_METHODS:
            return 'matching/parsing literal (not directly rendered)'
        if isinstance(parent, ast.Call) and call_name(parent) == 'replace' and parent.args and parent.args[0] is node:
            return 'replacement search pattern (not directly rendered)'
        if isinstance(parent, ast.Call) and call_name(parent) in LOG_METHODS:
            return 'internal log'
        if isinstance(parent, ast.Raise) and isinstance(parent.exc, ast.Call):
            if call_name(parent.exc) in INTERNAL_ERRORS:
                return 'internal exception'
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if parent.name in INPUT_FORMAT_OWNERS:
                return 'time input example/format'
            break
    return ''


def inventory():
    rows = []
    for area in ['telegram', 'application', 'presentation']:
        for path in sorted((ROOT/'src/tg_lottery'/area).glob('*.py')):
            source = path.read_text()
            tree = ast.parse(source)
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if isinstance(parents.get(node), ast.JoinedStr):
                        continue
                elif not isinstance(node, ast.JoinedStr):
                    continue
                text = text_of(node)
                if not HAN.search(text):
                    continue
                chain = ancestry(node, parents)
                owner = next((p.name for p in chain if isinstance(p,(ast.FunctionDef,ast.AsyncFunctionDef))), '<module>')
                excluded = exclusion(path,node,chain,text)
                rows.append({'path':str(path.relative_to(ROOT)), 'line':node.lineno, 'owner':owner, 'text':text, 'classification':'excluded' if excluded else 'public copy', 'reason':excluded})
    return rows


if __name__ == '__main__':
    rows = inventory()
    output = ROOT/'artifacts/ux-r4-20261102/user-visible-copy.json'
    output.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    public = [row for row in rows if row['classification']=='public copy']
    print(f'{len(public)} public-copy candidates; {len(rows)-len(public)} explicit exclusions; saved {output.name}')
    for row in public:
        if re.search('[;(),]|(?<![0-9]):(?![0-9/])',row['text']):
            print(f"{row['path']}:{row['line']} {row['text'][:90]!r}")
