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
DROP TRIGGER IF EXISTS tr_check_past_time_share ON ShareSchedule;

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
DROP FUNCTION IF EXISTS fn_check_past_time_share;

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

-- [수정됨] 로직 6: 예약 유효성 검사 (과거 예약 방지 추가)
CREATE OR REPLACE FUNCTION fn_validate_reservation()
RETURNS TRIGGER AS $$
DECLARE
    v_share_start TIMESTAMP;
    v_share_end TIMESTAMP;
    v_zone_status VARCHAR(10);
    v_overlap_count INT;
BEGIN
    -- 1. 취소/완료 상태 변경 시에는 검사 건너뜀
    IF NEW.Status IN ('Canceled', 'Completed') THEN
        RETURN NEW;
    END IF;

    -- [핵심 추가] 2. 과거 시간 예약 방지
    -- (단, 'InUse' 상태로 업데이트 되는 경우(입차처리)는 이미 시간이 지났을 수 있으므로 제외)
    -- 'Pending'이나 'Approved' 상태로 새로 들어오거나 변경될 때만 체크
    IF NEW.Status IN ('Pending', 'Approved') AND NEW.ReserveStartTime < CURRENT_TIMESTAMP THEN
        RAISE EXCEPTION '이미 지나간 과거 시간에는 예약할 수 없습니다.';
    END IF;

    -- 3. 공유 시간 범위 확인
    SELECT ShareStartTime, ShareEndTime INTO v_share_start, v_share_end
    FROM ShareSchedule WHERE ShareID = NEW.ShareID;

    IF NOT (NEW.ReserveStartTime >= v_share_start AND NEW.ReserveEndTime <= v_share_end) THEN
        RAISE EXCEPTION '예약 시간이 공유 가능 시간을 벗어납니다.';
    END IF;

    -- 4. 구역 폐쇄 여부 확인
    SELECT pz.Status INTO v_zone_status
    FROM ParkingZone pz
    JOIN ParkingSpace ps ON pz.ZoneID = ps.ZoneID
    JOIN ShareSchedule ss ON ps.SpaceID = ss.SpaceID
    WHERE ss.ShareID = NEW.ShareID;

    IF v_zone_status = 'Closed' THEN
        RAISE EXCEPTION '해당 주차 구역은 현재 폐쇄 상태입니다.';
    END IF;

    -- 5. 중복 예약 확인
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
-- [최종 수정] 로직 7: 게이트 제어 (시간 만료 시 방문자 강제 퇴거 처리)
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_gate_access_control()
RETURNS TRIGGER AS $$
DECLARE
    v_reservation_id INT;
    v_user_role VARCHAR(10);
    v_is_inside BOOLEAN;
    v_conflict_count INT;
BEGIN
    -- 1. 사용자 역할 및 현재 위치 확인
    SELECT Role INTO v_user_role FROM "User" WHERE VehicleID = NEW.VehicleID;
    v_is_inside := fn_is_vehicle_in(NEW.VehicleID);

    -- [입차 로직]
    IF NEW.Action = 'Entry' THEN
        
        -- (예외 1) 중복 입차 방지
        IF v_is_inside THEN
            RAISE EXCEPTION '이미 주차장에 입차해 있는 차량입니다. (중복 입차 불가)';
        END IF;
        
        -- (A) 입주민일 경우
        IF v_user_role = 'Resident' THEN
            
            -- [핵심 추가] 내 자리에 '시간이 만료된' 이용 중인 예약이 있다면 -> 강제로 'Completed' 처리
            -- (방문자가 차를 안 뺐어도, 시간 끝났으니 시스템상으로는 뺀 걸로 침)
            UPDATE Reservation r
            SET Status = 'Completed'
            FROM ShareSchedule ss, ParkingSpace ps
            WHERE r.ShareID = ss.ShareID
              AND ss.SpaceID = ps.SpaceID
              AND ps.OwnerVehicleID = NEW.VehicleID -- 내 공간
              AND r.Status = 'InUse'                -- 아직 안 나감(이용중)
              AND r.ReserveEndTime < COALESCE(NEW.Timestamp, CURRENT_TIMESTAMP); -- 근데 시간은 끝남!

            -- [충돌 확인] "아직 시간이 남은" 방문자가 있는지 확인
            SELECT COUNT(*) INTO v_conflict_count
            FROM Reservation r
            JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            WHERE ps.OwnerVehicleID = NEW.VehicleID
              AND (COALESCE(NEW.Timestamp, CURRENT_TIMESTAMP) BETWEEN r.ReserveStartTime AND r.ReserveEndTime) -- 현재 유효한 시간
              AND (
                  r.Status = 'Approved' 
                  OR 
                  (r.Status = 'InUse' AND fn_is_vehicle_in(r.VisitorVehicleID) = TRUE)
              );

            IF v_conflict_count > 0 THEN
                RAISE EXCEPTION '아직 예약 시간이 남은 방문객이 이용 중입니다.';
            END IF;

            NEW.Status := 'Automatic';
        
        -- (B) 방문자일 경우
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
                NEW.Status := 'PendingApproval';
            END IF;
        END IF;

    -- [출차 로직]
    ELSIF NEW.Action = 'Exit' THEN
        
        -- (예외 2) 중복 출차 방지
        IF NOT v_is_inside THEN
            RAISE EXCEPTION '주차장에 없는 차량이거나 이미 출차했습니다. (중복 출차 불가)';
        END IF;

        NEW.Status := 'Approved';
        
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

-- [신규] 로직: 과거 시간 공유 등록 방지 트리거
CREATE OR REPLACE FUNCTION fn_check_past_time_share()
RETURNS TRIGGER AS $$
BEGIN
    -- 시작 시간이 현재 시간보다 이전이면 에러 (약간의 오차 허용 없이 엄격하게)
    -- 수정(UPDATE)시에도 과거로 바꾸는 건 금지
    IF NEW.ShareStartTime < CURRENT_TIMESTAMP THEN
        RAISE EXCEPTION '이미 지나간 과거 시간에는 공유 일정을 등록할 수 없습니다.';
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 트리거 연결 (INSERT 또는 UPDATE 시 작동)
DROP TRIGGER IF EXISTS tr_check_past_time_share ON ShareSchedule;

CREATE TRIGGER tr_check_past_time_share
BEFORE INSERT OR UPDATE ON ShareSchedule
FOR EACH ROW
EXECUTE FUNCTION fn_check_past_time_share();