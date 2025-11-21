-- 기존 테이블 및 관련 객체 삭제 (생성 역순)
DROP TABLE IF EXISTS GateLog;
DROP TABLE IF EXISTS Gate;
DROP TABLE IF EXISTS Reservation;
DROP TABLE IF EXISTS ShareSchedule;
DROP TABLE IF EXISTS ParkingSpace;
DROP TABLE IF EXISTS ParkingZone;
DROP TABLE IF EXISTS "User";

-- 0. GIST 확장 활성화 (ShareSchedule의 시간 중복 방지용)
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1. User (사용자 테이블)
CREATE TABLE "User" (
    VehicleID VARCHAR(100) PRIMARY KEY, -- 차량 번호를 고유 ID로 사용
    Password VARCHAR(255) NOT NULL,
    Name VARCHAR(100) NOT NULL,
    Role VARCHAR(10) NOT NULL CHECK (Role IN ('Resident', 'Visitor', 'Admin')),
    Contact VARCHAR(100),
    Building VARCHAR(100) -- 입주민의 경우 거주하는 동 (예: '101동')
);

-- 2. ParkingZone (주차 구역 테이블)
CREATE TABLE ParkingZone (
    ZoneID SERIAL PRIMARY KEY,
    ZoneName VARCHAR(100) NOT NULL, -- 예: 'A구역', 'B구역'
    Status VARCHAR(10) NOT NULL DEFAULT 'Available' CHECK (Status IN ('Available', 'Closed'))
);

-- 3. ParkingSpace (주차 공간 테이블)
CREATE TABLE ParkingSpace (
    SpaceID SERIAL PRIMARY KEY,
    ZoneID INT NOT NULL,
    SpaceNumber INT NOT NULL CHECK (SpaceNumber BETWEEN 1 AND 10), -- [추가] 각 구역 당 1~10번 자리만 허용
    OwnerVehicleID VARCHAR(100), -- 입주민 차량 번호 (입주민 탈퇴 시 NULL 처리됨)

    FOREIGN KEY (ZoneID) REFERENCES ParkingZone(ZoneID),
    FOREIGN KEY (OwnerVehicleID) REFERENCES "User"(VehicleID) ON DELETE SET NULL,
    
    -- [추가] 한 구역 내에서 같은 번호의 주차공간 중복 생성 방지 (A구역 1번은 하나여야 함)
    UNIQUE (ZoneID, SpaceNumber)
);

-- 4. ShareSchedule (공유 등록 테이블)
CREATE TABLE ShareSchedule (
    ShareID SERIAL PRIMARY KEY,
    SpaceID INT NOT NULL,
    ShareStartTime TIMESTAMP NOT NULL,
    ShareEndTime TIMESTAMP NOT NULL,
    
    FOREIGN KEY (SpaceID) REFERENCES ParkingSpace(SpaceID) ON DELETE CASCADE,
    
    CHECK (ShareEndTime > ShareStartTime),
    
    -- [추가] 1시간 단위 제약 조건: 시작/종료 시간의 분과 초가 0이어야 함
    CHECK (EXTRACT(MINUTE FROM ShareStartTime) = 0 AND EXTRACT(SECOND FROM ShareStartTime) = 0),
    CHECK (EXTRACT(MINUTE FROM ShareEndTime) = 0 AND EXTRACT(SECOND FROM ShareEndTime) = 0),

    -- 동일 SpaceID에 대해 시간이 겹치는 항목 방지
    EXCLUDE USING gist (
        SpaceID WITH =,
        tsrange(ShareStartTime, ShareEndTime) WITH &&
    )
);

-- 5. Reservation (주차 예약 테이블)
CREATE TABLE Reservation (
    ReservationID SERIAL PRIMARY KEY,
    ShareID INT NOT NULL,
    VisitorVehicleID VARCHAR(100) NOT NULL,
    ReserveStartTime TIMESTAMP NOT NULL,
    ReserveEndTime TIMESTAMP NOT NULL,
    Status VARCHAR(10) NOT NULL DEFAULT 'Pending' CHECK (Status IN ('Pending', 'Approved', 'InUse', 'Completed', 'Canceled')),
    
    FOREIGN KEY (ShareID) REFERENCES ShareSchedule(ShareID) ON DELETE CASCADE,
    FOREIGN KEY (VisitorVehicleID) REFERENCES "User"(VehicleID),
    
    CHECK (ReserveEndTime > ReserveStartTime),

    -- [추가] 1시간 단위 제약 조건
    CHECK (EXTRACT(MINUTE FROM ReserveStartTime) = 0 AND EXTRACT(SECOND FROM ReserveStartTime) = 0),
    CHECK (EXTRACT(MINUTE FROM ReserveEndTime) = 0 AND EXTRACT(SECOND FROM ReserveEndTime) = 0)
);

-- 6. Gate (게이트 테이블)
CREATE TABLE Gate (
    GateID SERIAL PRIMARY KEY,
    GateName VARCHAR(100) NOT NULL,
    GateType VARCHAR(5) NOT NULL CHECK (GateType IN ('Entry', 'Exit')),
    Status VARCHAR(6) NOT NULL DEFAULT 'Closed' CHECK (Status IN ('Open', 'Closed'))
);

-- 7. GateLog (입출차 기록 테이블)
CREATE TABLE GateLog (
    LogID SERIAL PRIMARY KEY,
    ReservationID INT, -- 입주민일 경우 NULL
    VehicleID VARCHAR(100) NOT NULL, -- 차량 번호
    GateID INT NOT NULL,
    Timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Action VARCHAR(5) NOT NULL CHECK (Action IN ('Entry', 'Exit')),
    
    -- 상태 설명 업데이트
    -- Automatic: 예약 시간 내 방문자 또는 입주민 (자동 열림)
    -- PendingApproval: 예약 시간 외 방문자 (관리자 승인 대기)
    -- Approved: 관리자가 수동 승인함
    -- Denied: 거절됨
    Status VARCHAR(20) NOT NULL CHECK (Status IN ('Automatic', 'PendingApproval', 'Approved', 'Denied')),
    
    FOREIGN KEY (ReservationID) REFERENCES Reservation(ReservationID) ON DELETE SET null,
    FOREIGN KEY (GateID) REFERENCES Gate(GateID),
    FOREIGN KEY (VehicleID) REFERENCES "User"(VehicleID)
);