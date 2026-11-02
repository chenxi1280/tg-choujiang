"""Render synthetic samples against baseline and working sources without a database."""
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
RENDER = '''
import json
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from tests.test_campaign_card_layout import card_payload
from tests.v30_helpers import FIRST, rules_for
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.participation_rules import public_rule_blocks, rule_manifest
from tg_lottery.telegram.creator_views import preview_text, dashboard_rows
from tg_lottery.telegram.draft_config import new_draft
from tg_lottery.telegram.member_missing_targets import build_missing_targets_reply
from tg_lottery.domain.enums import QualificationState
samples = {}
for mode in ['period', 'daily', 'calendar']:
    rule = dict(count_mode='period' if mode=='period' else 'daily',daily_messages=66,consecutive_days=3 if mode=='calendar' else 1)
    rules = rules_for('message',groups={str(FIRST):rule})
    if mode=='calendar':
        rules = CampaignRules.model_validate(rules.model_dump()|{'message_task_calendar':'campaign-days-v1'})
    payload = card_payload(bonuses=())|{'rules':rules.model_dump(mode='json')}
    text,pages = rule_manifest(payload)
    samples[mode] = {'card':text,'pages':pages,'rules':public_rule_blocks(payload)}
draft = new_draft()
samples['draft'] = {'text':preview_text(draft),'buttons':dashboard_rows(draft)}
targets = tuple(SimpleNamespace(chat_id=-1000-n,title=f'测试群{n}',chat_type='supergroup',kind='participation_group',platform=False,public_join_url=f'https://t.me/example_group{n}',encrypted_join_url=None,revoked_at=None) for n in range(3))
campaign = SimpleNamespace(public_ref='ux_r4',name='UX验收示例',rules={},presentation={},cutoff_at=datetime.now(UTC)+timedelta(days=1))
observations = tuple({'chat_id':target.chat_id,'state':'eligible' if n==0 else 'ineligible'} for n,target in enumerate(targets))
text,keyboard = build_missing_targets_reply(campaign,QualificationState.INELIGIBLE,targets,observations=observations,page=1,target_url=lambda campaign,target:target.public_join_url)
samples['conditions'] = {'text':text,'buttons':keyboard.model_dump(mode='json')}
print(json.dumps(samples,ensure_ascii=False,indent=2))
'''


def render(source, destination):
    env = os.environ | {'PYTHONPATH': str(source / 'src') + os.pathsep + str(ROOT)}
    result = subprocess.run([str(ROOT / '.venv/bin/python'), '-c', RENDER], cwd=source, env=env, text=True, capture_output=True, check=True, timeout=60)
    destination.write_text(result.stdout)
    json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix='lottery-ux-baseline-') as temp:
    source = Path(temp)
    archive = source / 'baseline.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git','archive','8804401','src'],cwd=ROOT,stdout=stream,check=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(source,filter='data')
    render(source,OUTPUT/'baseline-samples.json')
render(ROOT, OUTPUT/'p0-samples.json')
print('Rendered baseline-samples.json and p0-samples.json')
