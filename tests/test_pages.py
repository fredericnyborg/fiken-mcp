from starlette.testclient import TestClient

import main


def _client():
    return TestClient(main.app)


def test_index_is_indexable_with_seo_meta():
    r = _client().get("/")
    assert r.status_code == 200
    assert "noindex" not in r.text
    assert "<meta name=description" in r.text
    assert "<link rel=canonical" in r.text
    assert "application/ld+json" in r.text


def test_other_pages_are_noindex():
    assert "noindex" in main.pages.render("Feil", "")


def test_robots_and_sitemap():
    c = _client()
    robots = c.get("/robots.txt")
    assert robots.status_code == 200
    assert "Disallow: /konto" in robots.text
    assert "Sitemap: " in robots.text
    sm = c.get("/sitemap.xml")
    assert sm.status_code == 200
    assert "<loc>" in sm.text
