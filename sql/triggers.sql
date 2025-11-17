-- =====================================================================
-- 로직 1: ParkingSpace INSERT 시 소유주(OwnerID)가 'Resident'인지 검증
-- =====================================================================

-- 함수 생성
CREATE OR REPLACE FUNCTION fn_check_owner_role()
RETURNS TRIGGER AS $$
DECLARE
    v_role VARCHAR(10);
BEGIN
    -- ParkingSpace에 INSERT/UPDATE 될 OwnerID의 Role을 조회
    SELECT Role INTO v_role FROM "User" WHERE UserID = NEW.OwnerID;

    -- Role이 'Resident'가 아니면 오류 발생
    IF v_role != 'Resident' THEN
        RAISE EXCEPTION 'OwnerID(%)는 ''Resident'' 역할이 아닙니다. (현재 역할: %)', NEW.OwnerID, v_role;
    END IF;

    -- 'Resident'가 맞으면 INSERT/UPDATE 계속 진행
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 트리거 연결
CREATE TRIGGER tr_check_owner_role
BEFORE INSERT OR UPDATE ON ParkingSpace
FOR EACH ROW
EXECUTE FUNCTION fn_check_owner_role();

-- =====================================================================
-- 로직 2: 관리자가 ParkingZone을 'Closed'로 변경 시, 관련 예약 자동 취소
-- =====================================================================

-- 함수 생성
CREATE OR REPLACE FUNCTION fn_cancel_reservations_on_zone_close()
RETURNS TRIGGER AS $$
BEGIN
    -- 만약 Zone의 상태가 'Available'에서 'Closed'로 변경된 경우에만 실행
    IF NEW.Status = 'Closed' AND OLD.Status != 'Closed' THEN
        -- 해당 ZoneID에 속한 모든 SpaceID를 찾음
        -- 그 SpaceID를 참조하는 모든 ShareID를 찾음
        -- 그 ShareID를 참조하는 모든 Reservation 중 'Pending' 또는 'InUse' 상태인 것을 'Canceled'로 변경
        UPDATE Reservation
        SET Status = 'Canceled'
        WHERE Status IN ('Pending', 'InUse')
          AND ShareID IN (
              SELECT ShareID
              FROM ShareSchedule ss
              JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
              WHERE ps.ZoneID = NEW.ZoneID
          );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 트리거 연결
CREATE TRIGGER tr_cancel_reservations_on_zone_close
AFTER UPDATE ON ParkingZone
FOR EACH ROW
EXECUTE FUNCTION fn_cancel_reservations_on_zone_close();

-- =====================================================================
-- 로직 3: 입주민이 ShareSchedule(공유 시간)을 변경/삭제 시, 관련 예약 자동 취소
-- =====================================================================

-- 함수 생성
CREATE OR REPLACE FUNCTION fn_cancel_reservations_on_schedule_change()
RETURNS TRIGGER AS $$
BEGIN
    -- OLD는 트리거가 발생하기 전의 데이터 (즉, 삭제/수정되기 전의 ShareID)
    -- 해당 공유 일정(OLD.ShareID)을 참조하는 'Pending' 상태의 예약을 모두 'Canceled'로 변경합니다.
    -- (UPDATE의 경우, 시간 충돌을 계산하는 것보다 일괄 취소 후 재예약을 유도하는 것이 로직이 단순합니다.)
    UPDATE Reservation
    SET Status = 'Canceled'
    WHERE ShareID = OLD.ShareID
      AND Status = 'Pending';
      
    IF (TG_OP = 'DELETE') THEN
        RETURN OLD; -- DELETE 시에는 OLD 반환
    ELSE
        RETURN NEW; -- UPDATE 시에는 NEW 반환
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 트리거 연결 (UPDATE 또는 DELETE 시)
CREATE TRIGGER tr_cancel_reservations_on_schedule_change
AFTER UPDATE OR DELETE ON ShareSchedule
FOR EACH ROW
EXECUTE FUNCTION fn_cancel_reservations_on_schedule_change();

-- =====================================================================
-- 로직 4: Reservation INSERT 시 유효성 검사 (시간/구역상태/중복)
-- =====================================================================

-- 함수 생성
CREATE OR REPLACE FUNCTION fn_validate_reservation()
RETURNS TRIGGER AS $$
DECLARE
    v_share_start TIMESTAMP;
    v_share_end TIMESTAMP;
    v_zone_status VARCHAR(10);
    v_overlap_count INT;
BEGIN
    -- 검사 1: 예약 시간이 원본 ShareSchedule 시간 내에 포함되는가?
    SELECT ShareStartTime, ShareEndTime INTO v_share_start, v_share_end
    FROM ShareSchedule
    WHERE ShareID = NEW.ShareID;

    IF NOT (NEW.ReserveStartTime >= v_share_start AND NEW.ReserveEndTime <= v_share_end) THEN
        RAISE EXCEPTION '예약 시간(%)이 공유 가능 시간(%)을 벗어납니다.', 
            tsrange(NEW.ReserveStartTime, NEW.ReserveEndTime), 
            tsrange(v_share_start, v_share_end);
    END IF;

    -- 검사 2: 해당 ParkingZone이 'Closed' 상태가 아닌가?
    SELECT pz.Status INTO v_zone_status
    FROM ParkingZone pz
    JOIN ParkingSpace ps ON pz.ZoneID = ps.ZoneID
    JOIN ShareSchedule ss ON ps.SpaceID = ss.SpaceID
    WHERE ss.ShareID = NEW.ShareID;

    IF v_zone_status = 'Closed' THEN
        RAISE EXCEPTION '해당 주차 구역(ShareID: %)은 현재 폐쇄 상태입니다.', NEW.ShareID;
    END IF;

    -- 검사 3: (1단계 피드백 핵심) 다른 예약과 시간이 겹치지 않는가?
    SELECT COUNT(*) INTO v_overlap_count
    FROM Reservation
    WHERE ShareID = NEW.ShareID
      AND Status IN ('Pending', 'InUse') -- 취소된 예약은 중복 검사에서 제외
      -- tsrange(A, B) && tsrange(C, D) : 두 시간 범위(tsrange)가 겹치는지(&&) 확인
      AND tsrange(NEW.ReserveStartTime, NEW.ReserveEndTime) && tsrange(ReserveStartTime, ReserveEndTime);

    IF v_overlap_count > 0 THEN
        RAISE EXCEPTION '해당 시간대(%)에 이미 다른 예약이 존재합니다.', 
            tsrange(NEW.ReserveStartTime, NEW.ReserveEndTime);
    END IF;

    -- 모든 검사를 통과하면 INSERT 계속 진행
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 트리거 연결
CREATE TRIGGER tr_validate_reservation
BEFORE INSERT ON Reservation
FOR EACH ROW
EXECUTE FUNCTION fn_validate_reservation();
