# ruff: noqa: RUF001 -- Chinese research messages and exact price-basis contract.
"""Keep baseline weights immutable; save only verified candidate configurations."""
from __future__ import annotations

import threading
import uuid

from app.market_time import cn_now

from .comparison_store import ResearchStore


class CandidateCatalog:
    def __init__(self, comparisons):
        self.comparisons = comparisons
        self.store = ResearchStore(comparisons.jobs.runtime, 'candidates')
        self.lock = threading.Lock()

    def list(self):
        profile = self.comparisons.profile_loader(self.comparisons.jobs.runtime)
        baselines = [{'id': name, 'label': 'Base 原始基线',
            'sha256': profile['model_hashes'][name],
            'installed': (self.comparisons.jobs.runtime / 'models' / name / 'model.safetensors').is_file(),
            'verification': 'configured', 'kind': 'baseline'} for name in ('base',)]
        items = [row for identity in self.store.identities()
                 if (row := self.store.read(identity)).get('model') == 'base']
        return {'baselines': baselines, 'items': sorted(items, key=lambda row: row['created_at'], reverse=True),
                'training_enabled': False, 'automatic_promotion': False}

    def save(self, comparison_id, trial_id, label):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60:
            raise ValueError('候选名称需要1至60个字符。')
        batch = self.comparisons.store.read(comparison_id)
        trial = next((row for row in batch['trials'] if row['id'] == trial_id), None)
        if trial is not None and trial.get('model') != 'base':
            raise ValueError('Small已停用，只能登记Base候选配置；历史原件保留。')
        if trial is None or not trial['job_id'] or trial['state'] != 'complete':
            raise ValueError('只能保存已完成并通过输入核验的比较配置。')
        job = self.comparisons.jobs.get(trial['job_id'], persisted=True)
        self.comparisons.validate_trial(batch, trial, job)
        metadata, request = job['result']['worker_metadata'], batch['request']
        candidate = {'schema_version': 1, 'id': uuid.uuid4().hex, 'label': label.strip(),
            'model': trial['model'], 'lookback': trial['lookback'], 'period': request['period'],
            'horizon': request['horizon'], 'samples': request['samples'],
            'model_sha256': metadata['model_sha256'], 'tokenizer_sha256': metadata['tokenizer_sha256'],
            'seed': metadata['seed'], 'source_comparison_id': comparison_id, 'source_trial_id': trial_id,
            'created_at': cn_now().isoformat(), 'state': 'registered'}
        with self.lock:
            for identity in self.store.identities():
                prior = self.store.read(identity)
                if (prior['source_comparison_id'], prior['source_trial_id']) == (comparison_id, trial_id):
                    raise ValueError('该比较配置已经保存；原候选和成绩均保留。')
            self.store.write(candidate, new=True)
        return {key: value for key, value in candidate.items() if key != 'schema_version'}

    def retire(self, identity):
        with self.lock:
            candidate = self.store.read(identity)
            candidate['state'] = 'retired'
            self.store.write(candidate)
        return {key: value for key, value in candidate.items() if key != 'schema_version'}
