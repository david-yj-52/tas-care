"""
Java 코드 청크 추출기
- AST 기반으로 메소드 단위 청크 추출
- 파일명, 클래스명, 메소드명, 어노테이션, 레이어 메타데이터 포함
- 의존성: pip install javalang
"""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import javalang


# ──────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────

@dataclass
class MethodChunk:
    id: str  # 고유 ID: 파일경로::클래스명::메소드명
    code: str  # 메소드 전체 소스 코드
    file_path: str  # 상대 경로 (예: com/example/UserService.java)
    class_name: str  # 클래스명
    method_name: str  # 메소드명
    layer: str  # controller / service / repository / domain / unknown
    annotations: list[str]  # 메소드에 붙은 어노테이션 목록
    return_type: str  # 반환 타입
    parameters: list[str]  # 파라미터 목록 (타입명만)
    class_annotations: list[str]  # 클래스 레벨 어노테이션


# ──────────────────────────────────────────
# 레이어 판별
# ──────────────────────────────────────────

LAYER_KEYWORDS = {
    "controller": ["Controller", "RestController", "Resource"],
    "service": ["Service", "ServiceImpl", "Facade"],
    "repository": ["Repository", "Dao", "Mapper", "Persistence"],
    "domain": ["Entity", "Domain", "Model", "Vo", "Dto"],
}


def detect_layer(class_name: str, class_annotations: list[str], file_path: str) -> str:
    """클래스명, 어노테이션, 패키지 경로를 조합해 레이어를 판별합니다."""
    combined = class_name + " ".join(class_annotations) + file_path

    annotation_map = {
        "@RestController": "controller",
        "@Controller": "controller",
        "@Service": "service",
        "@Repository": "repository",
        "@Entity": "domain",
    }
    for ann, layer in annotation_map.items():
        if ann in class_annotations:
            return layer

    for layer, keywords in LAYER_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in combined.lower():
                return layer

    return "unknown"


# ──────────────────────────────────────────
# 소스 코드에서 메소드 원문 추출 (줄 번호 기반)
# ──────────────────────────────────────────

def extract_method_source(source_lines: list[str], method_node, next_method_pos: Optional[int] = None) -> str:
    """
    javalang은 메소드 종료 줄 번호를 제공하지 않으므로,
    시작 줄부터 중괄호 깊이를 추적해 메소드 본문을 추출합니다.
    """
    if method_node.position is None:
        return ""

    start_line = method_node.position.line - 1  # 0-indexed
    depth = 0
    started = False
    result_lines = []

    for i, line in enumerate(source_lines[start_line:], start=start_line):
        result_lines.append(line)
        for ch in line:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1

        if started and depth == 0:
            break

        # 안전 장치: 다음 메소드 시작 전에 중괄호가 닫히지 않으면 중단
        if next_method_pos and i >= next_method_pos - 2:
            break

    return "\n".join(result_lines).strip()


# ──────────────────────────────────────────
# 단일 .java 파일 파싱
# ──────────────────────────────────────────

def parse_java_file(file_path: str, project_root: str = "") -> list[MethodChunk]:
    """
    하나의 .java 파일을 파싱하여 메소드 단위 청크 리스트를 반환합니다.
    """
    chunks = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        print(f"  [WARN] 파일 읽기 실패: {file_path} - {e}")
        return []

    try:
        tree = javalang.parse.parse(source)
    except javalang.parser.JavaSyntaxError as e:
        print(f"  [WARN] 파싱 실패: {file_path} - {e}")
        return []

    source_lines = source.splitlines()

    # 상대 경로 계산
    rel_path = os.path.relpath(file_path, project_root) if project_root else file_path
    rel_path = rel_path.replace("\\", "/")  # Windows 경로 정규화

    # 클래스 단위 순회
    for _, class_node in tree.filter(javalang.tree.ClassDeclaration):
        class_name = class_node.name

        # 클래스 어노테이션
        class_annotations = []
        if class_node.annotations:
            class_annotations = [f"@{ann.name}" for ann in class_node.annotations]

        layer = detect_layer(class_name, class_annotations, rel_path)

        # 메소드 단위 순회
        methods = class_node.methods or []
        for idx, method in enumerate(methods):
            method_name = method.name

            # 메소드 어노테이션
            method_annotations = []
            if method.annotations:
                method_annotations = [f"@{ann.name}" for ann in method.annotations]

            # 반환 타입
            if method.return_type:
                return_type = method.return_type.name if hasattr(method.return_type, 'name') else str(
                    method.return_type)
            else:
                return_type = "void"

            # 파라미터 타입 목록
            parameters = []
            if method.parameters:
                for param in method.parameters:
                    ptype = param.type.name if hasattr(param.type, 'name') else str(param.type)
                    parameters.append(ptype)

            # 메소드 소스 추출
            next_pos = methods[idx + 1].position.line if idx + 1 < len(methods) and methods[idx + 1].position else None
            code = extract_method_source(source_lines, method, next_pos)

            if not code:
                continue

            # 고유 ID 생성 (오버로딩 대응: 파라미터 타입을 ID에 포함)
            params_for_id = "_".join(parameters) if parameters else "void"
            chunk_id = f"{rel_path}::{class_name}::{method_name}({params_for_id})"

            chunk = MethodChunk(
                id=chunk_id,
                code=code,
                file_path=rel_path,
                class_name=class_name,
                method_name=method_name,
                layer=layer,
                annotations=method_annotations,
                return_type=return_type,
                parameters=parameters,
                class_annotations=class_annotations,
            )
            chunks.append(chunk)

    return chunks


# ──────────────────────────────────────────
# 프로젝트 전체 파싱
# ──────────────────────────────────────────

def parse_java_project(project_root: str, skip_tests: bool = True) -> list[MethodChunk]:
    """
    프로젝트 루트 하위의 모든 .java 파일을 재귀 탐색하여 청크를 추출합니다.
    skip_tests=True (기본값) 이면 src/test 경로는 제외합니다.
    """
    all_chunks = []
    all_files = list(Path(project_root).rglob("*.java"))

    if skip_tests:
        java_files = [
            f for f in all_files
            if "/test/" not in str(f).replace("\\", "/")
        ]
        excluded = len(all_files) - len(java_files)
        print(f"[INFO] .java 파일 {len(java_files)}개 발견 (테스트 파일 {excluded}개 제외)")
    else:
        java_files = all_files
        print(f"[INFO] .java 파일 {len(java_files)}개 발견 (테스트 포함)")

    for java_file in java_files:
        chunks = parse_java_file(str(java_file), project_root)
        if chunks:
            all_chunks.extend(chunks)
            print(f"  [OK] {os.path.relpath(java_file, project_root)} → {len(chunks)}개 청크")

    return all_chunks


# ──────────────────────────────────────────
# 결과 저장
# ──────────────────────────────────────────

def save_chunks_to_json(chunks: list[MethodChunk], output_path: str):
    """추출된 청크를 JSON 파일로 저장합니다."""
    data = [asdict(c) for c in chunks]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[INFO] {len(chunks)}개 청크 저장 완료 → {output_path}")


# ──────────────────────────────────────────
# 통계 출력
# ──────────────────────────────────────────

def print_stats(chunks: list[MethodChunk]):
    """레이어별 청크 통계를 출력합니다."""
    from collections import Counter
    layer_count = Counter(c.layer for c in chunks)

    print("\n" + "=" * 50)
    print(f"  총 청크 수: {len(chunks)}")
    print("  레이어별 분포:")
    for layer, count in sorted(layer_count.items()):
        bar = "█" * (count // 1)
        print(f"    {layer:<12}: {count:>4}개  {bar}")
    print("=" * 50)

    # 샘플 출력 (첫 번째 청크)
    if chunks:
        sample = chunks[0]
        print("\n[샘플 청크]")
        print(f"  ID          : {sample.id}")
        print(f"  Layer       : {sample.layer}")
        print(f"  Class       : {sample.class_name}")
        print(f"  Method      : {sample.method_name}")
        print(f"  Return Type : {sample.return_type}")
        print(f"  Parameters  : {sample.parameters}")
        print(f"  Annotations : {sample.annotations}")
        print(f"  Code Preview:\n{'-' * 40}")
        preview = "\n".join(sample.code.splitlines()[:10])
        print(preview)
        if len(sample.code.splitlines()) > 10:
            print("  ...")
        print("-" * 40)


# ──────────────────────────────────────────
# 실행 진입점
# ──────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # 인자로 프로젝트 경로를 받거나 기본값 사용
    project_path = sys.argv[1] if len(sys.argv) > 1 else "../../target_code/java/spring-petclinic"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "chunks.json"

    print(f"[INFO] 프로젝트 경로: {os.path.abspath(project_path)}")

    if not os.path.exists(project_path):
        print(f"[ERROR] 경로가 존재하지 않습니다: {project_path}")
        print("  → git clone https://github.com/spring-projects/spring-petclinic.git")
        sys.exit(1)

    chunks = parse_java_project(project_path)
    print_stats(chunks)
    save_chunks_to_json(chunks, output_file)
