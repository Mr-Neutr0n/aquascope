"""Publish a harvested folder to a Hugging Face dataset repo."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aquascope.utils.imports import require

logger = logging.getLogger(__name__)

DEFAULT_REPO_ID = "Rekin226/aquascope-gauges"


def publish_folder(
    folder: str | Path,
    repo_id: str = DEFAULT_REPO_ID,
    *,
    token: str | None = None,
    commit_message: str | None = None,
    create: bool = True,
) -> str:
    """Upload ``folder`` to the ``repo_id`` dataset and return the commit URL.

    The token comes from ``token``, then ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN``,
    then the local ``huggingface_hub`` login. Nothing is ever bundled in the
    package. Needs the ``archive`` extra.
    """
    hub = require("huggingface_hub", feature="archive publishing", group="archive")
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a directory")
    token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    api = hub.HfApi(token=token)
    if create:
        api.create_repo(repo_id, repo_type="dataset", private=False, exist_ok=True)
    info = api.upload_folder(
        folder_path=str(folder),
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=commit_message or "aquascope harvest",
        allow_patterns=["*.parquet", "*.geojson", "*.json", "*.csv.gz", "*.fgb", "*.pmtiles", "README.md"],
    )
    url = getattr(info, "commit_url", None) or str(info)
    logger.info("Published %s to https://huggingface.co/datasets/%s (%s)", folder, repo_id, url)
    return url
