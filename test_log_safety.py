import logging

from safe_logging import RedactSecretsFilter, short_id


def test_short_id_hides_the_original_and_is_stable():
    wamid = "wamid.HBgMOTIzMDAxMjM0NTY3FQIA"
    assert short_id(wamid) == short_id(wamid)
    assert short_id(wamid) != short_id(wamid + "x")
    assert len(short_id(wamid)) == 10
    assert "OTIz" not in short_id(wamid) and "wamid" not in short_id(wamid)


def test_filter_hides_verify_token_in_uvicorn_style_access_line():
    url = ("/meta/webhook?hub.mode=subscribe&hub.challenge=1&hub.verify_token=SECRETTOKEN123"
           "&hub_mode=subscribe&hub_verify_token=SECRETTOKEN123")
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1,
                            '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", url, "1.1", 200), None)
    RedactSecretsFilter().filter(rec)
    assert len(rec.args) == 5                      # uvicorn unpacks exactly 5 items
    text = rec.getMessage()
    assert "SECRETTOKEN123" not in text
    assert "hub.verify_token=***" in text and "hub_verify_token=***" in text
    assert "hub.challenge=1" in text               # everything else is untouched


def test_filter_leaves_normal_lines_alone():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1, "plain message", (), None)
    RedactSecretsFilter().filter(rec)
    assert rec.getMessage() == "plain message"


def test_setup_logging_protects_uvicorn_access_log():
    from safe_logging import setup_logging
    setup_logging()
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, RedactSecretsFilter) for f in access.filters)