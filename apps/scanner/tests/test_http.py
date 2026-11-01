import httpx
import pytest
import respx

from scanner.http import Fetcher, FileTooLargeError

URL = "https://gisn.tel-aviv.gov.il/taba_raster/4963_NE.pdf"


@respx.mock
async def test_download_refuses_files_over_the_limit(tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"%PDF" + b"0" * 2000))
    async with Fetcher() as fetcher:
        with pytest.raises(FileTooLargeError) as caught:
            await fetcher.download(URL, tmp_path / "big.pdf", max_bytes=1000)
    assert caught.value.size == 2004
    assert not list(tmp_path.iterdir())


@respx.mock
async def test_download_keeps_files_within_the_limit(tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"%PDF small"))
    async with Fetcher() as fetcher:
        size = await fetcher.download(URL, tmp_path / "small.pdf", max_bytes=1000)
    assert size == 10
    assert (tmp_path / "small.pdf").read_bytes() == b"%PDF small"
