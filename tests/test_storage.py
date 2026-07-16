from __future__ import annotations

import pandas as pd

from imovel_scrapper.storage import read_csv_auto, write_csv


def test_write_csv_uses_excel_compatible_utf8_semicolon_and_decimal_comma(tmp_path):
    path = tmp_path / "listings.csv"
    source = pd.DataFrame({"titulo": ["Imóvel com móveis"], "preco_m2": [11111.11]})

    write_csv(source, path)

    content = path.read_bytes()
    assert content.startswith(b"\xef\xbb\xbf")
    assert b"titulo;preco_m2" in content
    assert "11111,11" in content.decode("utf-8-sig")
    pd.testing.assert_frame_equal(read_csv_auto(path), source)
