-- 1. 기존 역할 및 권한 초기화 (에러 방지용)
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'role_guest') THEN
        DROP OWNED BY role_guest; 
        DROP ROLE role_guest;
    END IF;
END
$$;

-- 2. 역할(Role) 생성
CREATE ROLE role_guest WITH LOGIN PASSWORD 'guest123';

-- 3. DB 접속 및 스키마 사용 권한
GRANT CONNECT ON DATABASE db_term TO role_guest; -- DB이름 확인 필요 (.env의 DB_NAME)
GRANT USAGE ON SCHEMA public TO role_guest;

-- 4. [기본] 주차 현황 뷰(View) 조회 권한
GRANT SELECT ON View_ParkingStatus TO role_guest;

-- 5. [추가됨] 예약 테이블 조회 권한 (오류 해결)
-- 유령 차량(예약 없이 들어온 차)을 판별할 때 Reservation 테이블을 확인해야 하므로 필요합니다.
-- 보안을 위해 필요한 컬럼(방문자ID, 상태)만 조회하도록 설정합니다.
GRANT SELECT (VisitorVehicleID, Status) ON Reservation TO role_guest;

-- 6. [기본] 함수 실행 및 관련 테이블 권한
GRANT EXECUTE ON FUNCTION fn_is_vehicle_in(VARCHAR) TO role_guest;
GRANT SELECT (VehicleID, Role) ON "User" TO role_guest;
GRANT SELECT (VehicleID, Action, Status, Timestamp) ON GateLog TO role_guest;