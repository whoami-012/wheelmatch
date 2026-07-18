from app.modules.catalogue.service import normalize_catalogue_name


def test_catalogue_name_normalization_is_stable() -> None:
    assert normalize_catalogue_name("  BMW\u00a0  M\uff14  ") == "bmw m4"
