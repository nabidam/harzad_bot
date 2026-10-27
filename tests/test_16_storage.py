"""#16 utils.storage — post-upload cleanup and the GC pass (plan 1.5)."""
import os
import time

from utils.storage import cleanup_media, gc_old_files, media_and_cover


def _mk(path: str, age_seconds: float = 0.0) -> str:
    """Create a file and backdate its mtime by `age_seconds`."""
    with open(path, "wb") as f:
        f.write(b"media")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_cleanup_media_removes_file_and_adjacent_cover(tmp_path):
    video = _mk(str(tmp_path / "Clip.mp4"))
    cover = _mk(str(tmp_path / "Clip.webp"))
    other = _mk(str(tmp_path / "Other.webp"))

    cleanup_media(video)

    assert not os.path.exists(video)
    assert not os.path.exists(cover)
    # unrelated files with the same base stem must survive
    assert os.path.exists(other)


def test_cleanup_media_tolerates_missing_files(tmp_path):
    # must not raise for a file that is already gone (GC already ran,
    # or the download failed before the file existed)
    cleanup_media(str(tmp_path / "ghost.mp4"))


def test_media_and_cover_yields_media_plus_existing_thumbnails(tmp_path):
    video = _mk(str(tmp_path / "Song.mp3"))
    _mk(str(tmp_path / "Song.jpg"))
    paths = list(media_and_cover(video))
    assert paths[0] == video
    assert str(tmp_path / "Song.jpg") in paths
    assert str(tmp_path / "Song.webp") in paths  # listed even if absent


def test_gc_removes_only_stale_files(tmp_path):
    root = tmp_path / "dl"
    root.mkdir()
    stale = _mk(str(root / "old.mp4"), age_seconds=3 * 3600)
    stale_cover = _mk(str(root / "old.webp"), age_seconds=3 * 3600)
    fresh = _mk(str(root / "new.mp4"), age_seconds=60)
    (root / "inflight").mkdir()  # directories are skipped

    removed = gc_old_files(roots=(str(root),), max_age_seconds=2 * 3600)

    assert removed == 2
    assert not os.path.exists(stale)
    assert not os.path.exists(stale_cover)
    assert os.path.exists(fresh)


def test_gc_ignores_missing_roots(tmp_path):
    # a missing root is not an error (fresh checkout before first download)
    assert gc_old_files(roots=(str(tmp_path / "nope"),)) == 0


def test_gc_default_roots_are_download_dirs():
    # the module constants point at the configured download folders
    from configurations.settings import DOWNLOAD_MP3_PATH, DOWNLOAD_VIDEO_PATH
    from utils import storage

    assert storage.GC_MAX_AGE_SECONDS == 2 * 60 * 60
    assert storage.GC_INTERVAL_SECONDS == 60 * 60
    # sanity: the configured roots exist in this environment
    assert os.path.isdir(DOWNLOAD_MP3_PATH)
    assert os.path.isdir(DOWNLOAD_VIDEO_PATH)
