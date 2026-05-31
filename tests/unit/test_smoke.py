"""Sanity: pytest itself runs."""


def test_import() -> None:
    import predmarkbot

    assert predmarkbot.__version__
