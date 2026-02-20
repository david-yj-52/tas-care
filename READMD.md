## 프로젝트 이름

CARE = Code Analysis & Reverse Engineering

## 프로젝트 개요

AI 기반 코드 분석 시스템으로, 로컬 PC에 Agent를 설치하여 GitHub 저장소의 Java 프로젝트를 자동으로 분석하고, 사용자 질문에 비즈니스 로직을 설명하며 다이어그램으로 시각화해주는 프로그램입니다.

## 아키텍처

[GitHub Repository]
│ git push
▼
[GitHub Webhook]
│ HTTP POST
▼
[Backend Server - FastAPI]
│ Agent에 Pull 명령
▼
[Local PC - Agent]
├── Git Pull (최신 소스)
├── JavaParser (코드 파싱 - 메소드 단위 청크)
├── Embedding (nomic-embed-text via Ollama)
└── ChromaDB 업데이트 (변경분만 upsert/delete)
│
├── [Ollama LLM] - Deepseek-coder-v2
└── [ChromaDB]
│
▼
[React Frontend]
사용자 질문 → RAG → 응답 + 시각화

---

### 기술 스택

| 영역        | 기술                          |
|-----------|-----------------------------|
| Frontend  | React + TypeScript          |
| Backend   | Python FastAPI              |
| Agent     | Python                      |
| LLM       | Ollama (Deepseek-coder-v2)  |
| Embedding | Ollama (nomic-embed-text)   |
| Vector DB | ChromaDB (로컬)               |
| 코드 파서     | JavaParser (AST 기반)         |
| 시각화       | Mermaid.js + React Flow     |
| DB        | PostgreSQL (분석 이력 저장)       |
| 통신        | WebSocket (Backend ↔ Agent) |
| Webhook   | GitHub Webhook              |

---

### 컴포넌트 상세 설계

**Agent** 로컬 PC에 설치되며 핵심 파이프라인을 담당합니다. GitHub Webhook 수신 후 변경된 .java 파일만 감지하여 파싱 → 임베딩 → ChromaDB upsert/delete를 처리합니다.
Backend와는 WebSocket으로 통신합니다.

**JavaParser 청크 전략** 단순 텍스트 분할이 아닌 AST 기반 메소드 단위로 청크를 분리합니다. 각 청크에는 파일명, 클래스명, 메소드명, 어노테이션, 레이어(
controller/service/repository/domain) 메타데이터를 함께 저장합니다.

**ChromaDB 데이터 구조** ID는 `파일경로::클래스명::메소드명` 형태로 고유하게 관리하고, 메타데이터로 레이어 정보와 commit hash, 업데이트 시각을 저장하여 변경 추적이 가능하도록 합니다.

**RAG 흐름** 사용자 질문 → 질문 임베딩 → ChromaDB 유사 청크 검색 (Top K) → 관련 코드 + 질문을 Ollama에 전달 → 응답 생성 → 필요시 Mermaid 문법으로 다이어그램 출력

**시각화** LLM이 분석 결과를 Mermaid 문법으로 출력하도록 프롬프트를 설계하고, Frontend에서 Mermaid.js로 렌더링합니다. Flow 다이어그램, ERD, 클래스/메소드 관계도를 지원합니다.