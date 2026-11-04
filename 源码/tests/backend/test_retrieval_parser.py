from app.common.retrieval.parser import SearchResultParser


def test_parser_accepts_semicolon_separated_has_author_string() -> None:
    papers = SearchResultParser().parse(
        {
            "code": 1,
            "data": [
                {
                    "id": "paper-1",
                    "title": "Training stability",
                    "hasAuthor": "Shaham, Uri;Yamada, Yutaro;Negahban, Sahand",
                    "hasSource": "Neurocomputing",
                    "year": "2018",
                }
            ],
        },
        "training stability",
    )

    assert papers[0].authors == ["Shaham, Uri", "Yamada, Yutaro", "Negahban, Sahand"]


def test_parser_accepts_author_objects_lists_and_fallback_fields() -> None:
    parser = SearchResultParser()

    papers = parser.parse(
        {
            "code": 1,
            "data": [
                {
                    "id": "paper-1",
                    "title": "Object authors",
                    "hasAuthor": [{"fullName": "Alice A."}, {"name": "Bob B."}],
                },
                {
                    "id": "paper-2",
                    "title": "Fallback author",
                    "author": {"authorName": "Carol C."},
                },
            ],
        },
        "author formats",
    )

    assert papers[0].authors == ["Alice A.", "Bob B."]
    assert papers[1].authors == ["Carol C."]
