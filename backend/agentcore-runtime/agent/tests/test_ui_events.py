

def test_a_long_shelf_is_sent_in_frames_under_the_websocket_limit():
    """47 items with presigned URLs passed the 64 KB frame limit and the session reconnected in a loop."""
    import json
    from ui_events import MAX_FRAME_BYTES, frames, shelf
    url = 'https://voiceshop-images.s3.amazonaws.com/products/V1234567890/image.webp?' + 'X-Amz-Signature=' + 'a' * 1100
    items = [{'asin': f'V{i:09d}', 'title': 'Amberlux Saffron Bloom Eau de Parfum ' * 2, 'brand': 'Amberlux', 'color': 'gold',
              'priceCents': 8199, 'rating': 4.5, 'ratingCount': 120, 'available': True, 'imageUrl': url} for i in range(47)]
    message = shelf(3, items, False, 2500, 47, 47)
    assert len(json.dumps(message)) > 64_000
    parts = frames(message)
    assert [p['type'] for p in parts] == ['ui.shelf', 'ui.shelf_append', 'ui.shelf_append', 'ui.shelf_append']
    assert sum(len(p['items']) for p in parts) == 47 and all(len(json.dumps(p)) < MAX_FRAME_BYTES for p in parts)
    assert frames(shelf(3, items[:12], False, 2500, 12, 12)) == [shelf(3, items[:12], False, 2500, 12, 12)]
