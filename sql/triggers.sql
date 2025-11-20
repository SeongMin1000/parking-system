-- =====================================================================
-- 0. [초기화] 기존 트리거 및 함수 삭제 (깨끗하게 재설치)
-- =====================================================================
DROP TRIGGER IF EXISTS tr_check_owner_role ON ParkingSpace;
DROP TRIGGER IF EXISTS tr_assign_specific_space ON "User";
DROP TRIGGER IF EXISTS tr_auto_assign_space ON "User";
DROP TRIGGER IF EXISTS tr_cancel_reservations_on_zone_close ON ParkingZone;
DROP TRIGGER IF EXISTS tr_cancel_reservations_on_schedule_change ON ShareSchedule;
DROP TRIGGER IF EXISTS tr_validate_reservation ON Reservation;
DROP TRIGGER IF EXISTS tr_gate_access_control_before ON GateLog;
DROP TRIGGER IF EXISTS tr_update_reservation_status_after_gate ON GateLog;
DROP TRIGGER IF EXISTS tr_check_resident_return ON GateLog;

DROP FUNCTION IF EXISTS fn_check_owner_role;
DROP FUNCTION IF EXISTS fn_assign_specific_space;
DROP FUNCTION IF EXISTS fn_auto_assign_space;
DROP FUNCTION IF EXISTS fn_cancel_reservations_on_zone_close;
DROP FUNCTION IF EXISTS fn_cancel_reservations_on_schedule_change;
DROP FUNCTION IF EXISTS fn_validate_reservation;
DROP FUNCTION IF EXISTS fn_gate_access_control;
DROP FUNCTION IF EXISTS fn_update_reservation_status_after_gate;
DROP FUNCTION IF EXISTS fn_check_resident_return;
DROP FUNCTION IF EXISTS fn_is_vehicle_in;

-- =====================================================================
-- 1. 유틸리티 함수: 차량 주차 여부 확인 (필수)
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_is_vehicle_in(p_vehicle_id VARCHAR)
RETURNS BOOLEAN AS $$
DECLARE
    v_last_action VARCHAR(5);
BEGIN
    IF p_vehicle_id IS NULL THEN RETURN FALSE; END IF;

    SELECT Action INTO v_last_action
    FROM GateLog
    WHERE VehicleID = p_vehicle_id
    ORDER BY Timestamp DESC
    LIMIT 1;

    IF v_last_action IS NULL OR v_last_action = 'Exit' THEN
        RETURN FALSE;
    ELSE
        RETURN TRUE;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- 2. 로직: ParkingSpace 소유주 검증
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_check_owner_role()
RETURNS TRIGGER AS $$
DECLARE
    v_role VARCHAR(10);
BEGIN
    IF NEW.OwnerVehicleID IS NULL THEN RETURN NEW; END IF;
    SELECT Role INTO v_role FROM "User" WHERE VehicleID = NEW.OwnerVehicleID;
    IF v_role IS NULL OR v_role != 'Resident' THEN
        RAISE EXCEPTION '차량번호(%)는 ''Resident'' 역할이 아닙니다.', NEW.OwnerVehicleID;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_check_owner_role
BEFORE INSERT OR UPDATE ON ParkingSpace
FOR EACH ROW
EXECUTE FUNCTION fn_check_owner_role();

-- =====================================================================
-- 3. 로직: 입주민 가입 시 주차공간 지정 할당
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_assign_specific_space()
RETURNS TRIGGER AS $$
DECLARE
    v_input_text TEXT;
    v_zone_part TEXT;
    v_space_part TEXT;
    v_zone_id INT;
    v_space_id INT;
    v_current_owner VARCHAR;
BEGIN
    IF NEW.Role = 'Resident' THEN
        v_input_text := NEW.Building;
        IF v_input_text NOT LIKE '%-%' THEN
            RAISE EXCEPTION '거주 정보 형식이 올바르지 않습니다. (예: a-1, B-5)';
        END IF;

        v_zone_part := UPPER(SPLIT_PART(v_input_text, '-', 1)); 
        v_space_part := SPLIT_PART(v_input_text, '-', 2);

        SELECT ZoneID INTO v_zone_id
        FROM ParkingZone
        WHERE UPPER(ZoneName) = v_zone_part 
           OR UPPER(ZoneName) = v_zone_part || '구역';

        IF v_zone_id IS NULL THEN
            RAISE EXCEPTION '존재하지 않는 주차 구역입니다: %', v_zone_part;
        END IF;

        SELECT SpaceID, OwnerVehicleID INTO v_space_id, v_current_owner
        FROM ParkingSpace
        WHERE ZoneID = v_zone_id 
          AND SpaceNumber = v_space_part::INT;

        IF v_space_id IS NULL THEN
            RAISE EXCEPTION '%구역에 %번 자리는 존재하지 않습니다.', v_zone_part, v_space_part;
        END IF;

        IF v_current_owner IS NOT NULL THEN
            RAISE EXCEPTION '해당 주차 공간(%구역 %번)은 이미 다른 입주민이 사용 중입니다.', v_zone_part, v_space_part;
        END IF;

        UPDATE ParkingSpace
        SET OwnerVehicleID = NEW.VehicleID
        WHERE SpaceID = v_space_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_assign_specific_space
AFTER INSERT ON "User"
FOR EACH ROW
EXECUTE FUNCTION fn_assign_specific_space();

-- =====================================================================
-- 4. 로직: 구역 폐쇄 시 예약 취소
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_cancel_reservations_on_zone_close()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.Status = 'Closed' AND OLD.Status != 'Closed' THEN
        UPDATE Reservation
        SET Status = 'Canceled'
        WHERE Status IN ('Pending', 'Approved', 'InUse')
          AND ShareID IN (
              SELECT ShareID FROM ShareSchedule ss
              JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
              WHERE ps.ZoneID = NEW.ZoneID
          );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_cancel_reservations_on_zone_close
AFTER UPDATE ON ParkingZone
FOR EACH ROW
EXECUTE FUNCTION fn_cancel_reservations_on_zone_close();

-- =====================================================================
-- 5. 로직: 스케줄 변경 시 예약 취소
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_cancel_reservations_on_schedule_change()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE Reservation
    SET Status = 'Canceled'
    WHERE ShareID = OLD.ShareID
      AND Status IN ('Pending', 'Approved');
    IF (TG_OP = 'DELETE') THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_cancel_reservations_on_schedule_change
AFTER UPDATE OR DELETE ON ShareSchedule
FOR EACH ROW
EXECUTE FUNCTION fn_cancel_reservations_on_schedule_change();

-- =====================================================================
-- 6. 로직: 예약 유효성 검사 (취소 시 패스 기능 포함)
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_validate_reservation()
RETURNS TRIGGER AS $$
DECLARE
    v_share_start TIMESTAMP;
    v_share_end TIMESTAMP;
    v_zone_status VARCHAR(10);
    v_overlap_count INT;
BEGIN
    IF NEW.Status IN ('Canceled', 'Completed') THEN RETURN NEW; END IF;

    SELECT ShareStartTime, ShareEndTime INTO v_share_start, v_share_end
    FROM ShareSchedule WHERE ShareID = NEW.ShareID;

    IF NOT (NEW.ReserveStartTime >= v_share_start AND NEW.ReserveEndTime <= v_share_end) THEN
        RAISE EXCEPTION '예약 시간이 공유 가능 시간을 벗어납니다.';
    END IF;

    SELECT pz.Status INTO v_zone_status
    FROM ParkingZone pz
    JOIN ParkingSpace ps ON pz.ZoneID = ps.ZoneID
    JOIN ShareSchedule ss ON ps.SpaceID = ss.SpaceID
    WHERE ss.ShareID = NEW.ShareID;

    IF v_zone_status = 'Closed' THEN
        RAISE EXCEPTION '해당 주차 구역은 현재 폐쇄 상태입니다.';
    END IF;

    SELECT COUNT(*) INTO v_overlap_count
    FROM Reservation
    WHERE ShareID = NEW.ShareID
      AND Status IN ('Pending', 'Approved', 'InUse')
      AND tsrange(NEW.ReserveStartTime, NEW.ReserveEndTime) && tsrange(ReserveStartTime, ReserveEndTime)
      AND ReservationID != COALESCE(NEW.ReservationID, -1);

    IF v_overlap_count > 0 THEN
        RAISE EXCEPTION '해당 시간대에 이미 예약이 존재합니다.';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_validate_reservation
BEFORE INSERT OR UPDATE ON Reservation
FOR EACH ROW
EXECUTE FUNCTION fn_validate_reservation();

-- =====================================================================
-- [핵심 수정됨] 7. 로직: 게이트 제어 (입주민 프리패스 + 방문자 예약확인)
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_gate_access_control()
RETURNS TRIGGER AS $$
DECLARE
    v_reservation_id INT;
    v_user_role VARCHAR(10);
BEGIN
    -- 1. 사용자 역할 확인
    SELECT Role INTO v_user_role FROM "User" WHERE VehicleID = NEW.VehicleID;

    -- [입차 로직]
    IF NEW.Action = 'Entry' THEN
        
        -- (A) 입주민이면 무조건 자동 승인
        IF v_user_role = 'Resident' THEN
            NEW.Status := 'Automatic';
        
        -- (B) 방문자면 예약 확인
        ELSE
            SELECT ReservationID INTO v_reservation_id
            FROM Reservation
            WHERE VisitorVehicleID = NEW.VehicleID
              AND Status = 'Approved'
              AND (COALESCE(NEW.Timestamp, CURRENT_TIMESTAMP) BETWEEN ReserveStartTime AND ReserveEndTime);
            
            IF v_reservation_id IS NOT NULL THEN
                NEW.Status := 'Automatic';
                NEW.ReservationID := v_reservation_id;
            ELSE
                NEW.Status := 'PendingApproval'; -- 예약 없거나 시간 안 맞음
            END IF;
        END IF;

    -- [출차 로직]
    ELSIF NEW.Action = 'Exit' THEN
        NEW.Status := 'Approved'; -- 출차는 항상 승인
        
        IF v_user_role = 'Visitor' THEN
            SELECT ReservationID INTO v_reservation_id
            FROM Reservation
            WHERE VisitorVehicleID = NEW.VehicleID AND Status = 'InUse';
            NEW.ReservationID := v_reservation_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_gate_access_control_before
BEFORE INSERT ON GateLog
FOR EACH ROW
EXECUTE FUNCTION fn_gate_access_control();

-- =====================================================================
-- 8. 로직: 게이트 통과 후 예약 상태 변경
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_update_reservation_status_after_gate()
RETURNS TRIGGER AS $$
BEGIN
    -- 1. 입차(Entry) 시 -> 'InUse' (사용 중)
    IF NEW.Action = 'Entry' AND NEW.Status IN ('Automatic', 'Approved') AND NEW.ReservationID IS NOT NULL THEN
        UPDATE Reservation
        SET Status = 'InUse'
        WHERE ReservationID = NEW.ReservationID;
        
    -- 2. 출차(Exit) 시 -> 조건에 따라 분기
    ELSIF NEW.Action = 'Exit' AND NEW.Status = 'Approved' AND NEW.ReservationID IS NOT NULL THEN
        
        UPDATE Reservation
        SET Status = CASE 
            -- (A) 아직 예약 종료 시간이 안 지났으면 -> 'Approved'로 되돌림 (재입차 가능)
            WHEN NOW() < ReserveEndTime THEN 'Approved'
            -- (B) 예약 시간이 끝났으면 -> 'Completed' (완료 처리)
            ELSE 'Completed'
        END
        WHERE ReservationID = NEW.ReservationID;
        
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_update_reservation_status_after_gate
AFTER INSERT ON GateLog
FOR EACH ROW
EXECUTE FUNCTION fn_update_reservation_status_after_gate();