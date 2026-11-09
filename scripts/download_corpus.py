"""corpus/{id}/sources.yaml의 url 문서를 받아 {AXB_DATA_DIR}/workspaces/{id}/raw/에 저장한다.

사용: python scripts/download_corpus.py bank
실패한 문서가 있어도 나머지를 계속 받고, 마지막에 실패 목록을 출력한 뒤 종료 코드 1로 끝난다.
로직은 테스트할 수 있도록 axbuilder.corpus에 있고, 이 파일은 명령줄 껍데기다.
"""

import argparse
import sys
from pathlib import Path

import httpx

from axbuilder.corpus import MANIFEST_NAME, USER_AGENT, download_sources, load_sources
from axbuilder.settings import Settings
from axbuilder.workspace import WorkspaceStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("workspace_id", help="예: bank")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    # id를 먼저 검사한다: "../x" 같은 값으로 corpus/ 밖이나 data/ 밖을 가리키지 못하게
    raw_dir = WorkspaceStore(settings.data_dir / "workspaces").path(args.workspace_id) / "raw"
    sources = load_sources(REPO_ROOT / "corpus" / args.workspace_id / "sources.yaml")

    with httpx.Client(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        result = download_sources(sources, raw_dir, client, REPO_ROOT)

    for entry in result.downloaded:
        size = (raw_dir / entry.file).stat().st_size
        print(f"[ok]   {entry.doc_id:<40} {entry.format:<5} -> {entry.file} ({size:,} B)")
    for doc_id in result.in_repo:
        source = next(s for s in sources if s.doc_id == doc_id)
        print(f"[repo] {doc_id:<40} {source.format:<5} -> {source.path}")
    for doc_id, reason in result.failed.items():
        print(f"[FAIL] {doc_id:<40} {reason}")

    print(
        f"\n{len(sources)} sources: {len(result.downloaded)} downloaded, "
        f"{len(result.in_repo)} in repo, {len(result.failed)} failed"
    )
    print(f"raw dir: {raw_dir.resolve()}  (manifest: {MANIFEST_NAME})")
    if result.failed:
        print("failed: " + ", ".join(result.failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
