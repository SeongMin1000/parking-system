-- 1. 차량이 현재 주차장에 있는지 확인하는 함수
CREATE OR REPLACE FUNCTION fn_is_vehicle_in(p_vehicle_id VARCHAR)
RETURNS BOOLEAN AS $$
DECLARE
    v_last_action VARCHAR(5);
BEGIN
    IF p_vehicle_id IS NULL THEN RETURN FALSE; END IF;

    -- 마지막으로 '승인된' 입출차 기록 조회
    SELECT Action INTO v_last_action
    FROM GateLog
    WHERE VehicleID = p_vehicle_id
      AND Status IN ('Automatic', 'Approved')
    ORDER BY Timestamp DESC
    LIMIT 1;

    -- 기록이 없거나 마지막이 'Exit'이면 -> 밖에 있음(FALSE)
    IF v_last_action IS NULL OR v_last_action = 'Exit' THEN
        RETURN FALSE;
    -- 마지막이 'Entry'이면 -> 안에 있음(TRUE)
    ELSE
        RETURN TRUE;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 2. 주차 현황판 조회용 뷰
CREATE OR REPLACE VIEW View_ParkingStatus AS
SELECT 
    ps.SpaceID,
    ps.SpaceNumber,
    pz.ZoneName,
    
    -- 상태에 따른 색상 결정 (op_status)
    CASE 
        WHEN pz.Status = 'Closed' THEN 'closed'
        WHEN EXISTS (SELECT 1 FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID WHERE ss.SpaceID = ps.SpaceID AND r.Status = 'InUse') THEN 'external'
        WHEN EXISTS (SELECT 1 FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID WHERE ss.SpaceID = ps.SpaceID AND r.Status IN ('Pending', 'Approved') AND NOW() BETWEEN r.ReserveStartTime AND r.ReserveEndTime) THEN 'reserved'
        WHEN EXISTS (SELECT 1 FROM ShareSchedule ss WHERE ss.SpaceID = ps.SpaceID AND NOW() BETWEEN ss.ShareStartTime AND ss.ShareEndTime) THEN 'shared'
        WHEN ps.OwnerVehicleID IS NOT NULL THEN 'occupied'
        ELSE 'unassigned'
    END AS op_status,

    -- 차량 아이콘 표시 여부 (is_occupied)
    CASE
        WHEN EXISTS (SELECT 1 FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID WHERE ss.SpaceID = ps.SpaceID AND r.Status = 'InUse') THEN TRUE
        WHEN ps.OwnerVehicleID IS NOT NULL AND fn_is_vehicle_in(ps.OwnerVehicleID) = TRUE THEN TRUE
        ELSE FALSE
    END AS is_occupied

FROM ParkingSpace ps
JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID;