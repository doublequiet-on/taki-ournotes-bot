"""Replay one local story-navigation experiment and print evidence metadata only."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ournotes_bot.application.story_service import StoryService
from ournotes_bot.query.story_retrieval import normalized, rewrite, terms
from ournotes_bot.sources.story_files import digest, read_json


HERE = Path(__file__).resolve().parent
WORK = 'BanG Dream! Our Notes'
LAYERS = ('official_original', 'fan_analysis', 'official_summary')
CANDIDATE_HASH = 'cdf617f6eda0b446cade28be7eb6aae4936743bf38df73edc4a30852bc829baf'


def chapter_from_catalog(service, query, scope):
    catalog = service.coverage(**scope)
    chapters = {
        row['chapter'] for source in catalog['sources'] for row in source['chapters']
        if source['source_kind'] in LAYERS
    }
    if 'chapter' in scope:
        return next(iter(chapters)) if len(chapters) == 1 else None
    rewritten, _ = rewrite(query, service.current().aliases)
    query_terms = {term for term in terms(rewritten) if len(term) >= 2}
    scores = {name: len(query_terms.intersection(terms(name))) for name in chapters}
    best = max(scores.values(), default=0)
    matches = [name for name, score in scores.items() if score == best]
    return matches[0] if best >= 2 and len(matches) == 1 else None


def read_layer(service, query, scope, layer, source_id=None):
    filters = {**scope, 'source_kind': layer}
    if source_id is not None:
        filters['source_id'] = source_id
    result = service.search(query, filters, limit=1)
    if not result['items']:
        return None
    item = result['items'][0]
    material = service.read(item['handle'], before=0, after=0)
    scene = material['scenes'][0] if material['status'] == 'ok' else None
    # Keep only descriptors used by this experiment, never the dialogue.
    descriptor = None if scene is None else {
        key: scene[key] for key in (
            'scene_id', 'source_id', 'source_kind', 'work', 'chapter', 'source_version',
            'source_content_sha256', 'use_scope',
        )
    }
    if descriptor:
        descriptor['lines'] = len(scene['utterances'])
    return {'hit': item['scene_id'], 'read': descriptor}


def read_scope(service, query, scope):
    return [read for layer in LAYERS if (read := read_layer(service, query, scope, layer))]


def candidate_version(candidate):
    return candidate['story_release'], candidate['revocation_revision']


def candidate_lookup(service, candidate, query, scope):
    if candidate['status'] != 'offline_candidate':
        return 'disabled', []
    target_scope = candidate['scope']
    if any(normalized(value) != normalized(target_scope[key]) for key, value in scope.items()):
        return 'out_of_scope', []
    if not service.valid(*candidate_version(candidate)):
        return 'stale', []
    cues = candidate['navigation']
    question = normalized(query)
    def seen(key):
        return any(normalized(term) in question for term in cues[key])
    if not (seen('topic_terms') or seen('material_terms') and seen('shape_terms')):
        return 'no_cue', []

    inventory = service.coverage(**target_scope)
    if (inventory['source_revision'], inventory['revocation_revision']) != candidate_version(candidate):
        return 'stale', []
    sources = {source['source_id']: source for source in inventory['sources']}
    results = []
    for ref in candidate['references']:
        source = sources.get(ref['source_id'])
        if source is None or (source['coverage'], source['source_kind'], source['version'],
                              source['content_sha256'], source['use_scope']) != (
            'collected', ref['source_kind'], ref['source_version'], ref['content_sha256'], ref['use_scope'],
        ):
            return 'source_mismatch', []
        result = read_layer(service, target_scope['chapter'], target_scope, ref['source_kind'], ref['source_id'])
        scene = result['read'] if result else None
        if scene is None or (scene['scene_id'], scene['source_id'], scene['source_content_sha256']) != (
            ref['scene_id'], ref['source_id'], ref['content_sha256'],
        ):
            return 'stale_or_missing_read', []
        results.append(result)
    if not service.valid(*candidate_version(candidate)):
        return 'stale', []
    return 'used', results


def score(results, scope, need, refs):
    hits = [entry['hit'] for entry in results]
    scenes = [entry['read'] for entry in results if entry['read'] is not None]
    target_reads = [scene for scene in scenes if refs.get(scene['scene_id']) == scene['source_kind']]
    original = [scene for scene in target_reads if scene['source_kind'] == 'official_original']
    summary = [scene for scene in target_reads if scene['source_kind'] == 'fan_analysis']
    wrong_scope = [scene['scene_id'] for scene in scenes if any(
        normalized(scene[key]) != normalized(value) for key, value in scope.items()
    )]
    target_hit = any(hit in refs for hit in hits)
    supported = {
        'locate': target_hit,
        'summary': bool(summary),
        'stored_last_line': any(scene['lines'] == 1 for scene in original),
    }.get(need)
    return {
        'hit_ids': hits,
        'read_ids': [scene['scene_id'] for scene in scenes],
        'target_hit': target_hit,
        'original_target_reads': len(original),
        'summary_target_reads': len(summary),
        'scope_violations': wrong_scope,
        'irrelevant_target': target_hit if need in ('outside_scope', 'different_topic') else False,
        'request_supported': supported,
        'verbatim_support': (
            ('located_single_requested_utterance' if supported else 'target_line_not_read')
            if need == 'stored_last_line' else
            'missing_requested_text' if need in ('missing_full_dialogue', 'missing_opening_line') else None
        ),
        'dialogue_lines_emitted': 0,
    }


def compare(service, candidate, case):
    scope = {'work': case.get('work', WORK)}
    if 'chapter' in case:
        scope['chapter'] = case['chapter']
    query = case['query']
    plain = read_scope(service, query, scope)
    chapter = chapter_from_catalog(service, query, scope)
    via_catalog = read_scope(service, chapter, {**scope, 'chapter': chapter}) if chapter else plain
    if chapter:
        status, via_candidate = 'skipped_catalog_match', []
    else:
        status, via_candidate = candidate_lookup(service, candidate, query, scope)
    refs = {ref['scene_id']: ref['source_kind'] for ref in candidate['references']}
    return {
        'id': case['id'],
        'development': case.get('development', False),
        'need': case['need'],
        'catalog_chapter': chapter,
        'candidate_status': status,
        **{route: score(results, scope, case['need'], refs) for route, results in (
            ('A', plain),
            ('B', via_catalog),
            ('C', via_candidate if status == 'used' else via_catalog),
        )},
    }


def totals(cases):
    replay = [case for case in cases if not case['development']]
    answerable = sum(case['A']['request_supported'] is not None for case in replay)
    return {
        route: {
            'target_hits': sum(case[route]['target_hit'] for case in replay),
            'original_target_reads': sum(case[route]['original_target_reads'] for case in replay),
            'supported_requests': sum(case[route]['request_supported'] is True for case in replay),
            'answerable_requests': answerable,
            'wrong_scope_reads': sum(len(case[route]['scope_violations']) for case in replay),
            'irrelevant_target_hits': sum(case[route]['irrelevant_target'] for case in replay),
            'dialogue_lines_emitted': sum(case[route]['dialogue_lines_emitted'] for case in replay),
        } for route in ('A', 'B', 'C')
    }


def lifecycle(service, candidate, bundle):
    scope = candidate['scope']
    cue = candidate['navigation']['topic_terms'][0]
    original_revision = candidate['story_release']
    assert candidate_lookup(service, candidate, cue, scope)[0] == 'used'
    disabled = candidate_lookup(service, {**candidate, 'status': 'withdrawn'}, cue, scope)
    wrong_work = candidate_lookup(service, candidate, cue, {**scope, 'work': "BanG Dream! It's MyGO!!!!!"})
    wrong_chapter = candidate_lookup(service, candidate, cue, {**scope, 'chapter': scope['chapter']+'-other'})
    assert wrong_work == wrong_chapter == ('out_of_scope', [])
    assert disabled == ('disabled', [])

    # A distinct bundle revision built only in the temporary directory.
    changed = deepcopy(bundle)
    changed['aliases']['navigation-lifecycle-fixture'] = '離線試験'
    assert service.import_value(changed)['source_revision'] != original_revision
    on_switch = candidate_lookup(service, candidate, cue, scope)
    assert on_switch == ('stale', [])
    service.rollback(original_revision)
    assert candidate_lookup(service, candidate, cue, scope)[0] == 'used'

    original_ref = next(ref for ref in candidate['references'] if ref['source_kind'] == 'official_original')
    original_hit = service.search(scope['chapter'], {**scope, 'source_id': original_ref['source_id']})['items'][0]
    service.withdraw(original_ref['source_id'], 'offline story-navigation experiment')
    on_withdrawal = candidate_lookup(service, candidate, cue, scope)
    assert on_withdrawal == ('stale', [])
    assert service.read(original_hit['handle'])['status'] == 'stale'
    service.rollback(original_revision)
    after_rollback = candidate_lookup(service, candidate, cue, scope)
    assert after_rollback == ('stale', [])
    refs = {ref['scene_id']: ref['source_kind'] for ref in candidate['references']}
    remaining = score(read_scope(service, scope['chapter'], scope), scope, 'summary', refs)
    assert remaining['original_target_reads'] == 0 and remaining['summary_target_reads'] == 1
    return {
        'wrong_work': wrong_work[0], 'wrong_chapter': wrong_chapter[0],
        'disabled_candidate': disabled[0],
        'version_switch': on_switch[0], 'withdrawal': on_withdrawal[0],
        'old_handle': 'stale', 'rollback_after_withdrawal': after_rollback[0],
        'post_withdrawal_catalog': remaining,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True, help='Authorized local snapshot with the pinned hash')
    args = parser.parse_args()
    candidate = read_json(HERE/'candidate.json')
    if digest(candidate) != CANDIDATE_HASH:
        parser.error('candidate revision differs from the frozen experiment')
    bundle = read_json(args.bundle)
    if digest(bundle) != candidate['story_release']:
        parser.error('bundle revision differs from the frozen navigation candidate')
    with TemporaryDirectory(dir=Path.cwd(), prefix='.story-navigation-') as directory:
        service = StoryService(directory)
        service.import_value(bundle)
        cases = [compare(service, candidate, case) for case in read_json(HERE/'cases.json')]
        report = {
            'story_release': candidate['story_release'],
            'candidate_hash': digest(candidate),
            'cases': cases,
            'totals': totals(cases),
            'lifecycle': lifecycle(service, candidate, bundle),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
