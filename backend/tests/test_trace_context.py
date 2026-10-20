"""Run lineage must distinguish a version's role within each comparison."""
import asyncio
from backend.tests.test_platform import harness, drain


def test_evaluation_context_labels_and_snapshots_survive_case_edits(harness):
    p, agent, _ = harness
    async def scenario():
        try:
            p.add_case(agent['id'], {'name':'Training task','prompt':'train','files':[], 'expected_name':'clean.csv','expected_content':'value\n1\n','split':'train'})
            validation=p.add_case(agent['id'], {'name':'Validation task','prompt':'validate','files':[], 'expected_name':'clean.csv','expected_content':'value\n1\n','split':'validation'})
            batch=await p.start_batch(agent['id'])
            await drain(p)
            records=p.db.all('runs', batch_id=batch['id'])
            contexts=[p.run_view(r['id'])['trace_context'] for r in records]
            assert {c['kind'] for c in contexts} == {'baseline','candidate'}
            assert {c['version_number'] for c in contexts} == {1,2}
            assert all(c['batch_number']==1 and c['metadata_source']=='recorded' for c in contexts)
            confirmed=[c for c in contexts if c['phase']=='confirmation']
            assert len(confirmed)==4
            assert {c['attempt'] for c in confirmed}=={2,3}
            assert all(c['case_split']=='validation' for c in confirmed)
            # Later suite edits cannot relabel the original measured case.
            p.db.update('cases',validation['id'], name='Renamed task', split='train')
            repeated=[p.run_view(r['id'])['trace_context'] for r in records if r['case_id']==validation['id']]
            assert all(c['case_name']=='Validation task' and c['case_split']=='validation' for c in repeated)
            # Legacy saved records can still be understood without new writes.
            for r in records:
                r.pop('evaluation_context',None)
                p.db.put('runs',r)
            legacy=p.trace_lookups(agent['id'])
            derived=[p.run_view(r['id'], lookups=legacy)['trace_context'] for r in records]
            assert {c['role'] for c in derived}=={'baseline','candidate'}
            assert all(c['metadata_source']=='derived' for c in derived)
        finally:
            await p.close()
    asyncio.run(scenario())


def test_chat_and_one_shot_have_distinct_lineage(harness):
    p, agent, _=harness
    async def scenario():
        try:
            one=p.submit_run(agent['id'],'one shot',[])
            await drain(p)
            assert p.run_view(one['id'])['trace_context']['kind']=='one_shot'
            chat=p.create_session(agent['id'])
            # Inspect queued metadata without executing a synthetic chat runtime.
            v=p.need('versions',chat['version_id'])
            run=p.prepare_run(v,'chat message',[])
            p.db.update('runs',run['id'],session_id=chat['id'],turn_index=2)
            c=p.run_view(run['id'])['trace_context']
            assert c['kind']=='chat' and c['turn_index']==2 and c['session_id']==chat['id']
            assert c['batch_id'] is None and c['phase'] is None
        finally:
            await p.close()
    asyncio.run(scenario())
