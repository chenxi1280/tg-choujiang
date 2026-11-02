"""Apply the approved user-facing terminology without changing routes or fields."""
import io
import json
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPLACEMENTS = {
    '抽奖加成票(发言)': '发言加成票',
    '抽奖加成票（发言获得）': '发言加成票',
    '抽奖加成票（发言）': '发言加成票',
    '当前有效次数': '有效抽奖次数',
    '抽奖机会': '有效抽奖次数',
    '参与活动的群': '参与群',
    '活动群': '参与群',
    '活动标题': '抽奖标题',
    '发起抽奖': '创建抽奖',
    '准入条件': '额外参与条件',
    '已登记参与': '已报名',
    '登记参与': '报名',
    '一键报名': '报名',
    '点击参与抽奖': '报名',
    '报名本次抽奖': '报名',
    '参与成功': '报名成功',
    '重新核验': '重新检查',
    '核验并报名': '报名',
    '核验并刷新': '重新检查',
    '核验': '检查',
    '验证资格': '重新检查',
}
SKIP = {'ui_style.py'}
CLAIMS = {'admin_confirmations.py','admin_management_views.py','admin_flow.py','admin_winner_pages.py','winners.py'}
manifest_path = ROOT/'artifacts/ux-r4-20261102/terminology-changes.json'
manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
for area in ['telegram','application','presentation']:
    for path in sorted((ROOT/'src/tg_lottery'/area).glob('*.py')):
        if path.name in SKIP:
            continue
        source = path.read_text()
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        lines = source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1]+len(line))
        edits = []
        for token in tokens:
            if token.type not in {tokenize.STRING, tokenize.FSTRING_MIDDLE}:
                continue
            text = token.string
            for old,new in REPLACEMENTS.items():
                text = text.replace(old,new)
            if path.name not in CLAIMS:
                text = text.replace('登记','报名')
            if text == token.string:
                continue
            start = offsets[token.start[0]-1]+token.start[1]
            end = offsets[token.end[0]-1]+token.end[1]
            edits.append((start,end,text))
            manifest.append({'path':str(path.relative_to(ROOT)),'line':token.start[0],'before':token.string,'after':text})
        for start,end,text in reversed(edits):
            source = source[:start]+text+source[end:]
        if edits:
            path.write_text(source)
(ROOT/'artifacts/ux-r4-20261102/terminology-changes.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print(f'Updated {len(manifest)} string literals; route and field identifiers are untouched.')
