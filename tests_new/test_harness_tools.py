"""M0-AC5 verification: sdcv + epubcheck wrappers raise RuntimeError when tool absent."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests_new.harness import epubcheck, sdcv


def test_sdcv_raises_when_missing() -> None:
    with patch.object(sdcv.shutil, "which", return_value=None):
        with pytest.raises(RuntimeError, match="M6"):
            sdcv.lookup("any", "/tmp")


def test_epubcheck_raises_when_missing() -> None:
    with patch.object(epubcheck.shutil, "which", return_value=None):
        with pytest.raises(RuntimeError, match="M7"):
            epubcheck.validate("/tmp/some.epub")


def test_sdcv_is_installed_reflects_path() -> None:
    with patch.object(sdcv.shutil, "which", return_value="/usr/bin/sdcv"):
        assert sdcv.is_installed() is True
    with patch.object(sdcv.shutil, "which", return_value=None):
        assert sdcv.is_installed() is False


def test_epubcheck_is_installed_reflects_path() -> None:
    with patch.object(epubcheck.shutil, "which", return_value="/usr/bin/epubcheck"):
        assert epubcheck.is_installed() is True
    with patch.object(epubcheck.shutil, "which", return_value=None):
        assert epubcheck.is_installed() is False
