import asyncio
import httpx
from app.agents.content_assessment import public_literature as public
from app.agents.content_assessment.schemas import ContentAssessmentState, Claim


def test_publisher_abstract_does_not_include_surrounding_site_content():
    source = 'Dense retrieval encodes questions and passages separately for semantic search. ' * 3
    async def handler(request):
        return httpx.Response(200, text=f'<div id="abstract"><span>Abstract</span>{source}<br>Evaluation uses retrieval tasks.</div><footer>Unrelated site navigation</footer>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            value = await public.source_abstract(client,'https://aclanthology.org/2020.emnlp-main.550/','metadata')
            assert source.strip() in value
            assert 'Evaluation uses retrieval tasks.' in value
            assert 'Unrelated site navigation' not in value
            assert await public.source_abstract(client,'https://example.com/','saved abstract') == 'saved abstract'
    asyncio.run(run())


def test_public_supplement_respects_year_range_and_remains_abstract(monkeypatch):
    state = ContentAssessmentState(raw_user_input='检索方法比较')
    state.claims = [Claim(claim_id='C1',text='Dense passage retrieval',claim_fields={'method':'Dense Passage Retrieval'})]
    state.output_json['year_range'] = [2020,2022]
    captured = []
    async def handler(request):
        captured.append(request)
        def work(year):
            return {'id':f'https://openalex.org/W{year}','display_name':'Dense passage retrieval methods',
                'publication_year':year,'abstract_inverted_index':{'Dense':[0],'retrieval':[1],'methods':[2]},
                'primary_location':{'landing_page_url':f'https://example.com/{year}'}}
        return httpx.Response(200,json={'results':[work(2019),work(2021),work(2023)]})
    factory = httpx.AsyncClient
    monkeypatch.setattr(public.httpx,'AsyncClient',lambda **kwargs:factory(transport=httpx.MockTransport(handler),**kwargs))
    asyncio.run(public.supplement(state))
    assert 'from_publication_date:2020-01-01,to_publication_date:2022-12-31' == captured[0].url.params['filter']
    assert [e.year for e in state.evidence_records] == [2021]
    assert state.evidence_records[0].access_status == 'abstract_only'
    assert not state.evidence_records[0].fulltext_available
