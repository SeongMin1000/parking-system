-- 0. GIST 확장 활성화 (ShareSchedule의 시간 중복 방지용)
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1. User (사용자 테이블)
-- 모든 사용자(입주민, 방문자, 관리자)의 기본 정보를 저장 
CREATE TABLE "User" (
    UserID VARCHAR(100) PRIMARY KEY, -- 사용자 ID (기본 키)
    Password VARCHAR(255) NOT NULL, -- 비밀번호 (암호화 저장을 권장)
    Name VARCHAR(100) NOT NULL, -- 사용자 이름
    Role VARCHAR(10) NOT NULL CHECK (Role IN ('Resident', 'Visitor', 'Admin')), -- 사용자 역할 ('Resident', 'Visitor', 'Admin') 
    Contact VARCHAR(100) -- 연락처 (전화번호 등)
);

-- 2. ParkingZone (주차 구역 테이블)
-- 주차장의 구역별 정보를 관리 (예: A구역, B구역) 
CREATE TABLE ParkingZone (
    ZoneID SERIAL PRIMARY KEY, -- 주차 구역 ID (자동 증가, 기본 키)
    ZoneName VARCHAR(100) NOT NULL, -- 주차 구역 이름 (예: 'A구역')
    Status VARCHAR(10) NOT NULL DEFAULT 'Available' CHECK (Status IN ('Available', 'Closed')) -- 구역 상태 ('Available': 사용 가능, 'Closed': 폐쇄) 
);

-- 3. ParkingSpace (주차 공간 테이블)
-- 개별 주차 공간과 소유주(입주민)를 매핑 
CREATE TABLE ParkingSpace (
    SpaceID SERIAL PRIMARY KEY, -- 주차 공간 ID (자동 증가, 기본 키)
    ZoneID INT NOT NULL, -- 주차 공간이 속한 구역 ID (FK) 
    OwnerID VARCHAR(100) NOT NULL, -- 주차 공간 소유주 ID (FK, User의 Resident 역할) 
    
    FOREIGN KEY (ZoneID) REFERENCES ParkingZone(ZoneID),
    FOREIGN KEY (OwnerID) REFERENCES "User"(UserID)
);

-- 4. ShareSchedule (공유 등록 테이블)
-- 입주민이 자신의 주차 공간을 공유(비움) 등록한 내역 
CREATE TABLE ShareSchedule (
    ShareID SERIAL PRIMARY KEY, -- 공유 일정 ID (자동 증가, 기본 키)
    SpaceID INT NOT NULL, -- 공유되는 주차 공간 ID (FK) 
    ShareStartTime TIMESTAMP NOT NULL, -- 공유 가능 시작 시간 
    ShareEndTime TIMESTAMP NOT NULL, -- 공유 가능 종료 시간 
    
    FOREIGN KEY (SpaceID) REFERENCES ParkingSpace(SpaceID) ON DELETE CASCADE, -- 원본 공간이 삭제되면 공유 일정(자식)도 삭제
    
    -- (ShareEndTime > ShareStartTime) 제약 조건
    CHECK (ShareEndTime > ShareStartTime),
   
    -- (동일 SpaceID의 중복 시간 금지) 제약 조건
    -- 동일한 SpaceID에 대해 시간이 겹치는 항목이 없도록 보장합니다.
    EXCLUDE USING gist (
        SpaceID WITH =,
        tsrange(ShareStartTime, ShareEndTime) WITH &&
    )
);

-- 5. Reservation (주차 예약 테이블)
-- 일반 시민(Visitor)이 공유된 공간을 예약한 내역 
CREATE TABLE Reservation (
    ReservationID SERIAL PRIMARY KEY, -- 예약 ID (자동 증가, 기본 키)
    ShareID INT NOT NULL, -- 예약의 대상이 되는 공유 일정 ID (FK) 
    VisitorID VARCHAR(100) NOT NULL, -- 예약한 방문자 ID (FK, User의 Visitor 역할) 
    ReserveStartTime TIMESTAMP NOT NULL, -- 예약 시작 시간 
    ReserveEndTime TIMESTAMP NOT NULL, -- 예약 종료 시간 
    Status VARCHAR(10) NOT NULL DEFAULT 'Pending' CHECK (Status IN ('Pending', 'InUse', 'Completed', 'Canceled')), -- 예약 상태 ('Pending': 대기, 'InUse': 이용중, 'Completed': 완료, 'Canceled': 취소) 
    
    FOREIGN KEY (ShareID) REFERENCES ShareSchedule(ShareID),
    FOREIGN KEY (VisitorID) REFERENCES "User"(UserID),
    
    -- 예약 시간은 당연히 종료 시간이 시작 시간보다 늦어야 함
    CHECK (ReserveEndTime > ReserveStartTime)
    
    -- "예약 시간은 공유 시간 내에 존재"
    -- "구역이 폐쇄된 경우 생성 불가"
    -- "중복 예약 차단"
    -- (위 3가지 항목은 3단계 트리거에서 구현합니다)
);

-- 6. Gate (게이트 테이블)
-- 아파트의 입출차 게이트 장비 정보 
CREATE TABLE Gate (
    GateID SERIAL PRIMARY KEY, -- 게이트 ID (자동 증가, 기본 키)
    GateName VARCHAR(100) NOT NULL, -- 게이트 이름 (예: '정문', '동문 입구')
    GateType VARCHAR(5) NOT NULL CHECK (GateType IN ('Entry', 'Exit')), -- 게이트 유형 ('Entry': 입구, 'Exit': 출구) 
    Status VARCHAR(6) NOT NULL DEFAULT 'Closed' CHECK (Status IN ('Open', 'Closed')) -- 게이트 현재 상태 ('Open', 'Closed') 
);

-- 7. GateLog (입출차 기록 테이블)
-- 차량의 모든 입출차 내역을 시간순으로 기록 
CREATE TABLE GateLog (
    LogID SERIAL PRIMARY KEY, -- 로그 ID (자동 증가, 기본 키)
    ReservationID INT NOT NULL, -- 관련 예약 ID (FK) 
    GateID INT NOT NULL, -- 작동한 게이트 ID (FK) 
    Timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 기록 시간 (자동 생성) 
    Action VARCHAR(5) NOT NULL CHECK (Action IN ('Entry', 'Exit')), -- 입출차 행동 ('Entry', 'Exit') 
    
    FOREIGN KEY (ReservationID) REFERENCES Reservation(ReservationID),
    FOREIGN KEY (GateID) REFERENCES Gate(GateID)
);
