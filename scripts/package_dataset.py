"""검증된 공통 CSV 폴더를 단일 최상위 폴더가 있는 배포 ZIP으로 묶는다."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import zipfile
from pathlib import Path

from validate_dataset import package_fingerprints

NAME = "listenbrainz-preprocessing"


def create_zip(package: Path, output: Path) -> None:
    """기존 ZIP을 보존하고 manifest에 등록된 파일만 동일한 바이트로 압축한다."""
    if output.exists():
        raise FileExistsError("기존 ZIP을 보존하도록 새 출력 경로를 지정하세요")
    manifest = json.loads((package / "dataset_manifest.json").read_text(encoding="utf-8"))
    execution = manifest["execution"]
    if (set(execution) != {"source_and_config_sha256", "reproduction_argv"}
            or "local_config" in execution["source_and_config_sha256"]):
        raise ValueError("공개 범위를 벗어난 실행 정보가 있습니다. 공개 형식의 manifest로 다시 생성하세요")
    before = package_fingerprints(package, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-zip-", dir=output.parent) as work:
        staged = Path(work) / "package.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(set(before) | {"dataset_manifest.json"}):
                info = zipfile.ZipInfo(f"{NAME}/{name}", date_time=(2026, 10, 12, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, (package / name).read_bytes(), compresslevel=9)
        if before != package_fingerprints(package, manifest):
            raise RuntimeError("ZIP 생성 중 패키지가 변경됐습니다")
        # 원자적 hard link는 검사 이후 다른 실행이 만든 ZIP도 덮어쓰지 않는다.
        os.link(staged, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(".cache/reproduced/listenbrainz-preprocessing"))
    parser.add_argument("--output", type=Path, default=Path(".cache/reproduced/listenbrainz-preprocessing.zip"))
    args = parser.parse_args()
    create_zip(args.package, args.output)
    print(f"패키지 ZIP 생성 완료: {args.output}")


if __name__ == "__main__":
    main()
