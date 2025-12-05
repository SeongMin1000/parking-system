# 🚗 아파트 주차 통합 관제 시스템 (Apartment Parking Management System)

이 프로젝트는 아파트 입주민이 자신의 유휴 주차 공간을 방문객과 공유하고, 관리자가 이를 통합 관제할 수 있는 **웹 기반 주차 관리 시스템**입니다.

입주민은 비움 시간을 등록하여 편의를 제공할 수 있으며, 방문객은 사전에 주차 공간을 예약하여 편리하게 이용할 수 있습니다. 또한, LPR(번호판 인식)을 가정한 입출차 시뮬레이션과 관리자 승인 시스템을 포함합니다.

## ✨ 주요 기능

- **👥 입주민 (Resident)**
  - 자신의 전용 주차 공간 확인
  - 주차 공간 비움 시간(공유) 등록 및 취소
  - 내 공간 이용 내역 및 방문자 확인
  - 불법 주차 차량 신고 (블랙리스트 요청)
- **🚙 방문자 (Visitor)**
  - 입주민이 공유한 시간대의 주차 공간 검색 및 예약
  - 게이트 입/출차 시뮬레이션 (자동 승인/대기)
  - 자신의 예약 내역 확인 및 취소
- **🛡️ 관리자 (Admin)**
  - 실시간 주차 현황 모니터링 (대시보드)
  - 입차 승인 대기 요청 수동 처리
  - 특정 구역 폐쇄/해제 및 비상 강제 출차 제어
  - 블랙리스트 승인/반려 및 관리
  - 통합 로그(입출차, 예약) 조회

## 🛠️ 기술 스택 (Tech Stack)

- **Frontend:** HTML5, CSS3, JavaScript (Vanilla)
- **Backend:** Python (Flask)
- **Database:** PostgreSQL
- **Real-time:** Flask-SocketIO (실시간 게이트 상태 동기화)
- **Security:** Bcrypt (비밀번호 암호화)

## ⚙️ 설치 및 실행 방법 (Setup Guide)

### 1. 사전 요구 사항 (Prerequisites)

- Python 3.8 이상
- PostgreSQL 데이터베이스

### 2. 프로젝트 클론 및 패키지 설치

```bash
# 레포지토리 클론 (예시)
git clone https://github.com/SeongMin1000/parking-system.git
cd parking-system

# 가상환경 생성 (권장)
python -m venv venv

# 가상환경 활성화
# Mac/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 의존성 패키지 설치
pip install -r requirements.txt
```

### 3. 환경 변수 설정 (.env)

프로젝트 루트 경로에 `.env` 파일을 생성하고 아래 내용을 본인의 DB 설정에 맞게 수정하여 작성하세요.

```ini
# .env 파일 예시
DB_NAME=db_term        # 사용할 데이터베이스 이름 (예: db_term)
DB_USER=postgres       # DB 슈퍼유저/소유자 ID
DB_PASSWORD=your_pw    # DB 비밀번호
DB_HOST=localhost
DB_PORT=5432

# DCL(권한 제어) 테스트를 위한 게스트 계정 (dcl.sql 실행 후 사용됨)
# 실제 sql/dcl.sql 파일에 정의된 계정명과 비밀번호를 입력해야 합니다.
DB_GUEST_USER=role_guest
DB_GUEST_PASSWORD=guest123
```

### 4. 데이터베이스 초기화 (Database Init)

PostgreSQL에 접속하여 데이터베이스를 생성한 후, 제공된 SQL 파일과 파이썬 스크립트를 순서대로 실행합니다.

1. **데이터베이스 생성**: `CREATE DATABASE db_term;` (Postgres CLI 또는 pgAdmin 사용)
2. **테이블 생성 (DDL)**: `sql/ddl.sql` 실행
3. **뷰 생성 (Views)**: `sql/views.sql` 실행
4. **권한 설정 (DCL)**: `sql/dcl.sql` 실행
   - _주의: 이 단계에서 `role_guest` 계정이 생성됩니다._
5. **기초 데이터(구역, 관리자 등) 삽입**:
   ```bash
   python setup_db.py
   ```

### 5. 서버 실행

```bash
python app.py
```

서버가 정상적으로 실행되면 터미널에 로컬 주소가 표시됩니다 (기본: http://127.0.0.1:5000).

## 🔑 초기 계정 정보

`setup_db.py`를 실행하면 자동으로 관리자 계정이 생성됩니다.

- **관리자 (Admin)**

  - ID (차량번호): `admin`
  - PW: `admin123`

- **입주민/방문자**
  - 메인 화면의 [회원가입] 메뉴를 통해 직접 생성 가능합니다.
  - 입주민 가입 시 `A-1`과 같이 `구역-번호` 형식을 입력해야 합니다. (초기 데이터: A, B, C, D 구역 각 1~10번)

## 📂 디렉토리 구조

```
parking-system/
├── app.py              # 메인 Flask 애플리케이션
├── setup_db.py         # DB 초기 데이터 삽입 스크립트
├── .env                # 환경 변수 (직접 생성 필요)
├── .gitignore
├── sql/
│   ├── ddl.sql         # 테이블 생성
│   ├── dcl.sql         # 권한 관리
│   └── views.sql       # 뷰 생성
└── templates/
    ├── index.html      # 메인 현황판
    ├── login.html      # 로그인
    ├── register.html   # 회원가입
    ├── admin.html      # 관리자 페이지
    ├── resident.html   # 입주민 페이지
    └── visitor.html    # 방문자 페이지
```
