"""
임베딩 + ChromaDB 저장 모듈
- chunks.json → nomic-embed-text (via Ollama) → ChromaDB upsert
- 의존성: pip install chromadb requests
- Ollama가 로컬에서 실행 중이어야 합니다 (ollama serve)
"""

import json
import time
from pathlib import Path

import chromadb
import requests

# ──────────────────────────────────────────
# 설정
# ──────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
CHROMA_PATH = "./chroma_db"  # ChromaDB 저장 경로
COLLECTION_NAME = "java_code_chunks"
BATCH_SIZE = 20  # 한 번에 처리할 청크 수 (메모리/속도 조절)


# ──────────────────────────────────────────
# Ollama 임베딩
# ──────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    """단일 텍스트를 nomic-embed-text로 임베딩합니다."""
    response = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """텍스트 리스트를 순차적으로 임베딩합니다."""
    embeddings = []
    for i, text in enumerate(texts):
        emb = embed_text(text)
        embeddings.append(emb)
        if (i + 1) % 5 == 0:
            print(f"    임베딩 진행: {i + 1}/{len(texts)}")
    return embeddings


# ──────────────────────────────────────────
# 임베딩용 텍스트 구성
# ──────────────────────────────────────────

def build_natural_description(chunk: dict) -> str:
    """
    메소드 메타데이터로부터 자연어 설명을 생성합니다.
    예) "OwnerController.processFindForm finds owners by last name in controller layer"
    이 설명이 사용자 질문과 의미적으로 가까워 유사도를 높입니다.
    """
    method = chunk["method_name"]
    cls = chunk["class_name"]
    layer = chunk["layer"]
    params = chunk.get("parameters", [])
    ret = chunk["return_type"]
    anns = chunk.get("annotations", [])

    # 메소드명 camelCase → 자연어 변환 (예: findByLastName → find by last name)
    import re
    words = re.sub(r"([A-Z])", r" \1", method).lower().strip()

    # 파라미터 설명
    param_desc = f"with parameters {', '.join(params)}" if params else "with no parameters"

    # 어노테이션 설명
    ann_desc = f"annotated with {' '.join(anns)}" if anns else ""

    desc = f"{cls}.{method} {words} {param_desc} returns {ret} in {layer} layer. {ann_desc}"
    return desc.strip()


def build_embed_text(chunk: dict) -> str:
    """
    자연어 설명 + 메타데이터 + 코드를 결합한 임베딩 입력 텍스트를 만듭니다.
    자연어 설명을 앞에 붙이면 사용자의 비즈니스 언어 질문과
    의미적 거리가 줄어들어 유사도가 높아집니다.
    """
    annotations_str = " ".join(chunk.get("annotations", []))
    params_str = ", ".join(chunk.get("parameters", []))
    description = build_natural_description(chunk)

    return f"""Description: {description}
Layer: {chunk['layer']}
Class: {chunk['class_name']}
Method: {chunk['method_name']}({params_str}) -> {chunk['return_type']}
Annotations: {annotations_str}

{chunk['code']}""".strip()


# ──────────────────────────────────────────
# ChromaDB 연결
# ──────────────────────────────────────────

def get_collection():
    """ChromaDB 클라이언트와 컬렉션을 반환합니다."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # 코사인 유사도 사용
    )
    return collection


# ──────────────────────────────────────────
# 청크 upsert (추가 or 업데이트)
# ──────────────────────────────────────────

def upsert_chunks(chunks: list[dict], collection):
    """청크 리스트를 배치 단위로 임베딩 후 ChromaDB에 upsert합니다."""
    total = len(chunks)
    success = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = chunks[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n[배치 {batch_num}/{total_batches}] {len(batch)}개 청크 처리 중...")

        # 임베딩 텍스트 준비
        texts = [build_embed_text(c) for c in batch]

        # 임베딩 생성
        try:
            embeddings = embed_batch(texts)
        except requests.RequestException as e:
            print(f"  [ERROR] 임베딩 실패 - Ollama 연결 확인 필요: {e}")
            continue

        # ChromaDB upsert용 데이터 구성
        ids = [c["id"] for c in batch]
        documents = [c["code"] for c in batch]  # 원본 코드를 document로 저장
        metadatas = [
            {
                "file_path": c["file_path"],
                "class_name": c["class_name"],
                "method_name": c["method_name"],
                "layer": c["layer"],
                "return_type": c["return_type"],
                "annotations": ",".join(c.get("annotations", [])),
                "class_annotations": ",".join(c.get("class_annotations", [])),
                "parameters": ",".join(c.get("parameters", [])),
            }
            for c in batch
        ]

        # upsert (이미 있으면 업데이트, 없으면 삽입)
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        success += len(batch)
        print(f"  ✓ {success}/{total} 완료")

    return success


# ──────────────────────────────────────────
# 저장 결과 검증
# ──────────────────────────────────────────

def verify_collection(collection):
    """저장된 데이터를 간단히 검증합니다."""
    count = collection.count()
    print(f"\n[검증] ChromaDB 저장 수: {count}개")

    # 레이어별 통계 (peek으로 샘플 확인)
    sample = collection.peek(limit=count if count < 200 else 200)
    if sample and sample.get("metadatas"):
        from collections import Counter
        layers = Counter(m["layer"] for m in sample["metadatas"])
        print("  레이어별 분포 (샘플 기준):")
        for layer, cnt in sorted(layers.items()):
            print(f"    {layer:<12}: {cnt}개")


# ──────────────────────────────────────────
# 빠른 검색 테스트
# ──────────────────────────────────────────

def build_query_text(question: str) -> str:
    """
    사용자 질문을 코드 검색에 유리한 형태로 확장합니다.
    예) "오너를 성으로 검색" → 자연어 + 코드 키워드 조합
    """
    return f"""User question: {question}
Java method that {question}
findBy search query filter repository service controller"""


def test_query(collection, question: str = "find all owners"):
    """임베딩 후 간단한 쿼리 테스트를 수행합니다."""
    print(f"\n[테스트 쿼리] \"{question}\"")

    try:
        query_text = build_query_text(question)
        q_embedding = embed_text(query_text)
    except Exception as e:
        print(f"  [ERROR] 쿼리 임베딩 실패: {e}")
        return

    results = collection.query(
        query_embeddings=[q_embedding],
        n_results=3,
        include=["documents", "metadatas", "distances"],
    )

    print("  TOP 3 검색 결과:")
    for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
    )):
        score = 1 - dist  # cosine distance → similarity
        print(f"\n  [{i + 1}] 유사도: {score:.4f}")
        print(f"       {meta['layer']} / {meta['class_name']}.{meta['method_name']}")
        print(f"       파일: {meta['file_path']}")
        preview = "\n       ".join(doc.splitlines()[:5])
        print(f"       코드:\n       {preview}")
        if len(doc.splitlines()) > 5:
            print("       ...")


# ──────────────────────────────────────────
# 실행 진입점
# ──────────────────────────────────────────

if __name__ == "__main__":
    import sys

    chunks_file = sys.argv[1] if len(
        sys.argv) > 1 else "/Users/davidkim/Documents/Workspace/tas/care/app/extractor/chunks.json"

    # 1. chunks.json 로드
    print(f"[1/4] 청크 파일 로드: {chunks_file}")
    if not Path(chunks_file).exists():
        print(f"[ERROR] 파일 없음: {chunks_file}")
        print("  → 먼저 java_chunk_extractor.py를 실행하세요")
        sys.exit(1)

    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    print(f"  총 {len(chunks)}개 청크 로드 완료")

    # 2. Ollama 연결 확인
    print(f"\n[2/4] Ollama 연결 확인 ({OLLAMA_URL})")
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        print(f"  사용 가능한 모델: {models}")
        if not any(EMBED_MODEL in m for m in models):
            print(f"\n  [WARN] '{EMBED_MODEL}' 모델이 없습니다.")
            print(f"  → ollama pull {EMBED_MODEL}")
            sys.exit(1)
        print(f"  ✓ {EMBED_MODEL} 확인됨")
    except requests.RequestException:
        print("  [ERROR] Ollama에 연결할 수 없습니다.")
        print("  → ollama serve 명령으로 Ollama를 먼저 실행하세요")
        sys.exit(1)

    # 3. ChromaDB 컬렉션 준비
    print(f"\n[3/4] ChromaDB 준비 ({CHROMA_PATH})")
    collection = get_collection()
    existing = collection.count()
    print(f"  현재 저장된 청크 수: {existing}개")

    # 4. 임베딩 + upsert
    print(f"\n[4/4] 임베딩 + ChromaDB upsert 시작")
    start = time.time()
    saved = upsert_chunks(chunks, collection)
    elapsed = time.time() - start

    print(f"\n✅ 완료! {saved}개 저장 / 소요 시간: {elapsed:.1f}초")

    # 검증 + 테스트 쿼리
    verify_collection(collection)
    test_query(collection, "find all owners by last name")
    test_query(collection, "save pet information")
