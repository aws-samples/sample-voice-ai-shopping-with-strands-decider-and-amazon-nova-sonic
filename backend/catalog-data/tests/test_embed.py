"""curate.embed(): one unit vector per product from its words and its photo (ADR-35).

The search index compares vectors by inner product, which only equals cosine
similarity when every vector has length 1, so that is what these assert.
Bedrock is never called: embed_once is replaced with canned vectors.
"""
import math

import curate


def unit(values):
    length = math.sqrt(sum(v * v for v in values))
    return [v / length for v in values]


PRODUCT = {'asin': 'V7709897A5', 'title': 'Tee', 'brand': 'PixelRiot', 'department': 'Kids & Teens',
           'category': 'Teen Streetwear', 'gender': 'boys', 'color': 'yellow', 'material': 'cotton',
           'features': ['a', 'b', 'c']}


def fake_embed_once(text_vector, image_vector):
    """Returns the text vector for a text call and the image vector for an image call."""
    def embed_once(params, asin, what):      # noqa: ARG001 -- same signature as the real one
        return text_vector if 'text' in params else image_vector
    return embed_once


def test_averages_words_and_picture_into_one_unit_vector(monkeypatch):
    text, image = unit([1.0, 0.0, 0.0]), unit([0.0, 1.0, 0.0])
    monkeypatch.setattr(curate, 'embed_once', fake_embed_once(text, image))
    vector = curate.embed(PRODUCT, b'webp-bytes')
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)
    # Halfway between the two, so it answers to both.
    assert math.isclose(vector[0], vector[1], rel_tol=1e-9)
    assert math.isclose(vector[2], 0.0, abs_tol=1e-12)


def test_identical_inputs_are_unchanged(monkeypatch):
    text = unit([0.3, -0.4, 0.5])
    monkeypatch.setattr(curate, 'embed_once', fake_embed_once(text, list(text)))
    vector = curate.embed(PRODUCT, b'webp-bytes')
    assert all(math.isclose(a, b, rel_tol=1e-9) for a, b in zip(vector, text))


def test_a_product_is_skipped_when_either_call_fails(monkeypatch):
    text = unit([1.0, 0.0])
    monkeypatch.setattr(curate, 'embed_once', fake_embed_once(None, text))
    assert curate.embed(PRODUCT, b'webp-bytes') is None
    monkeypatch.setattr(curate, 'embed_once', fake_embed_once(text, None))
    assert curate.embed(PRODUCT, b'webp-bytes') is None


def test_opposite_vectors_are_skipped_rather_than_divided_by_zero(monkeypatch):
    monkeypatch.setattr(curate, 'embed_once', fake_embed_once([1.0, 0.0], [-1.0, 0.0]))
    assert curate.embed(PRODUCT, b'webp-bytes') is None


def test_the_model_matches_the_one_the_query_side_uses():
    # search-catalog embeds the query with this same model; a mismatch makes every
    # comparison meaningless. The CDK suites assert the IAM and endpoint-policy copies.
    assert curate.EMBEDDING_MODEL_ID == 'amazon.nova-2-multimodal-embeddings-v1:0'
    assert curate.EMBEDDING_DIMENSIONS == 1024
