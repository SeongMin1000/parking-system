import os
import re
from datetime import datetime, timedelta, timezone
import psycopg2
from flask import Flask, jsonify, request, render_template
from flask_socketio import SocketIO
from dotenv import load_dotenv
import bcrypt
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

# .env 파일에서 환경 변수 로드
load_dotenv()

app = Flask(__name__)
socketio = SocketIO(app)

# --- 데이터베이스 연결 설정 ---
def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT')
    )
    return conn

# --- 페이지 렌더링 라우트 ---
@app.route('/')
def index(): return render_template('index.html')
@app.route('/resident')
def resident_page(): return render_template('resident.html')
@app.route('/visitor')
def visitor_page(): return render_template('visitor.html')
@app.route('/admin')
def admin_page(): return render_template('admin.html')
@app.route('/login')
def login_page(): return render_template('login.html')
@app.route('/register')
def register_page(): return render_template('register.html')


# ========================================
# 1. 사용자 회원가입 (POST /register) - 수정됨
# ========================================
@app.route('/register', methods=['POST'])
def register_user():
    try:
        data = request.get_json()
        vehicleid = data['VehicleID']
        password = data['Password']
        name = data['Name']
        role = data['Role']
        contact = data.get('Contact') # 필수값
        building = data.get('Building') if role == 'Resident' else None
    except Exception as e:
        return jsonify(error="데이터 누락", details=str(e)), 400

    # [추가] 연락처 누락 검사
    if not contact or contact.strip() == "":
        return jsonify(error="비상 연락처는 필수 입력 항목입니다."), 400

    if not re.match(r'^\d{2}[가-힣]\d{4}$', vehicleid):
        return jsonify(error="차량번호 형식이 올바르지 않습니다. (예: 12가3456)"), 400

    if role == 'Resident' and not building:
        return jsonify(error="입주민은 거주 정보(예: a-1)를 입력해야 합니다."), 400

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            'INSERT INTO "User" (VehicleID, Password, Name, Role, Contact, Building) VALUES (%s, %s, %s, %s, %s, %s)',
            (vehicleid, hashed_password.decode('utf-8'), name, role, contact, building)
        )

        conn.commit()
        return jsonify(message="회원가입 성공!", VehicleID=vehicleid), 201

    except psycopg2.Error as e:
        if conn: conn.rollback()
        if e.pgcode == 'P0001': return jsonify(error="주차 공간 할당 실패", details=e.diag.message_primary), 400
        if e.pgcode == '23505': return jsonify(error="이미 가입된 차량번호입니다."), 409
        return jsonify(error="데이터베이스 오류", details=str(e)), 500
    finally:
        if conn: conn.close()

# ==================================
# 2. 사용자 로그인 (POST /login)
# ==================================
@app.route('/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json()
        vehicleid = data['VehicleID']
        password = data['Password']
    except:
        return jsonify(error="요청 데이터 부족"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM "User" WHERE VehicleID = %s', (vehicleid,))
        user = cur.fetchone()

        if user and bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            return jsonify(message="로그인 성공", VehicleID=user['vehicleid'], Role=user['role']), 200
        else:
            return jsonify(error="아이디 또는 비밀번호 불일치"), 401
    finally:
        conn.close()

# ===============================================
# 3. 입주민: 비움 시간 등록 (POST /schedule)
# ===============================================
@app.route('/schedule', methods=['POST'])
def create_share_schedule():
    try:
        data = request.get_json()
        vehicle_id, space_id, start_time, end_time = data['VehicleID'], data['SpaceID'], data['ShareStartTime'], data['ShareEndTime']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 소유주 확인
        cur.execute('SELECT OwnerVehicleID FROM ParkingSpace WHERE SpaceID = %s', (space_id,))
        space = cur.fetchone()
        if not space or space['ownervehicleid'] != vehicle_id:
            return jsonify(error="본인의 주차 공간만 등록할 수 있습니다."), 403

        # 일정 등록
        cur.execute(
            "INSERT INTO ShareSchedule (SpaceID, ShareStartTime, ShareEndTime) VALUES (%s, %s, %s) RETURNING ShareID",
            (space_id, start_time, end_time)
        )
        new_id = cur.fetchone()['shareid']
        conn.commit()
        return jsonify(message="등록되었습니다.", new_share_id=new_id), 201

    except psycopg2.Error as e:
        conn.rollback()
        if e.pgcode == '23P01': 
            return jsonify(error="이미 겹치는 공유 시간이 존재합니다."), 409

        if e.pgcode == '23514': 
            return jsonify(error="시간 설정 오류: 종료 시간이 시작 시간보다 늦어야 하며, '분'과 '초'는 0이어야 합니다. (1시간 단위)"), 400
            
        return jsonify(error="DB 오류", details=str(e)), 500
    finally:
        conn.close()

# ====================================================
# 4. 방문자: 예약 가능한 공간 조회 (GET /spaces)
# ====================================================
@app.route('/spaces', methods=['GET'])
def get_available_spaces():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        search_start = request.args.get('start_time')
        search_end = request.args.get('end_time')

        if not search_start or not search_end:
            return jsonify(error="시간을 입력해주세요"), 400

        # [수정됨] 입주민 부재 확인 로직(fn_is_vehicle_in) 완전 삭제
        # 공유 시간표(ShareSchedule)에 등록되어 있고, 다른 예약(Reservation)과 겹치지 않으면 무조건 통과
        query = sql.SQL("""
            SELECT 
                ss.ShareID,
                ss.ShareStartTime,
                ss.ShareEndTime,
                ps.SpaceID,
                pz.ZoneName,
                ps.SpaceNumber
            FROM ShareSchedule ss
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            WHERE 
                pz.Status = 'Available'
                -- 1. 사용자가 검색한 시간이 공유 시간 범위 안에 들어가는가?
                AND ss.ShareStartTime <= %s 
                AND ss.ShareEndTime >= %s
                
                -- 2. 해당 시간에 이미 확정된 예약이 없는가?
                AND NOT EXISTS (
                    SELECT 1 FROM Reservation r 
                    WHERE r.ShareID = ss.ShareID 
                    AND r.Status IN ('Pending', 'Approved', 'InUse')
                    AND tsrange(r.ReserveStartTime, r.ReserveEndTime) && tsrange(%s::timestamp, %s::timestamp)
                )
            ORDER BY ss.ShareStartTime ASC;
        """)
        
        cur.execute(query, (search_start, search_end, search_start, search_end))
        spaces = cur.fetchall()
        return jsonify(available_spaces=spaces), 200
    finally:
        conn.close()

# ===============================================
# 5. 방문자: 주차 예약 (POST /reservation)
# ===============================================
@app.route('/reservation', methods=['POST'])
def create_reservation():
    try:
        data = request.get_json()
        vehicle_id, share_id, start, end = data['VehicleID'], data['ShareID'], data['ReserveStartTime'], data['ReserveEndTime']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # 트리거가 중복 등을 검증하므로 바로 Insert
        cur.execute(
            "INSERT INTO Reservation (ShareID, VisitorVehicleID, ReserveStartTime, ReserveEndTime, Status) VALUES (%s, %s, %s, %s, 'Approved') RETURNING ReservationID",
            (share_id, vehicle_id, start, end)
        )
        rid = cur.fetchone()['reservationid']
        conn.commit()
        return jsonify(message="예약되었습니다.", reservation_id=rid), 201
    except psycopg2.Error as e:
        conn.rollback()
        if e.pgcode == 'P0001': return jsonify(error="예약 실패", details=e.diag.message_primary), 400
        return jsonify(error="DB 오류", details=str(e)), 500
    finally:
        conn.close()

# =========================================================
# 6. 게이트 입차 요청 (POST /entry-request)
# =========================================================
@app.route('/entry-request', methods=['POST'])
def request_entry():
    try:
        data = request.get_json()
        vehicle_id, gate_id = data['VehicleID'], data['GateID']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 트리거(fn_gate_access_control)가 Status를 결정함
        cur.execute(
            """
            INSERT INTO GateLog (VehicleID, GateID, Action)
            VALUES (%s, %s, 'Entry')
            RETURNING LogID, Status
            """, 
            (vehicle_id, gate_id)
        )
        result = cur.fetchone()
        log_status = result['status']
        conn.commit()

        if log_status == 'Automatic':
            cur.execute("UPDATE Gate SET Status = 'Open' WHERE GateID = %s", (gate_id,))
            conn.commit()
            socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open'})
            return jsonify(message="자동 승인되었습니다. 어서오세요.", status="Automatic"), 200
        else:
            return jsonify(message="예약 시간 외 방문입니다. 관리자 호출 중...", status="Pending"), 202

    except psycopg2.Error as e:
        conn.rollback()
        if e.pgcode == 'P0001': return jsonify(error="입차 거부", details=e.diag.message_primary), 403
        return jsonify(error="DB 오류", details=str(e)), 500
    finally:
        conn.close()

# =========================================================
# 7. 게이트 출차 요청 (POST /exit-request)
# =========================================================
@app.route('/exit-request', methods=['POST'])
def request_exit():
    try:
        data = request.get_json()
        vehicle_id, gate_id = data['VehicleID'], data['GateID']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 출차는 항상 승인. 트리거가 Reservation 상태를 Completed로 자동 변경
        cur.execute(
            "INSERT INTO GateLog (VehicleID, GateID, Action) VALUES (%s, %s, 'Exit') RETURNING LogID",
            (vehicle_id, gate_id)
        )
        conn.commit()

        cur.execute("UPDATE Gate SET Status = 'Open' WHERE GateID = %s", (gate_id,))
        conn.commit()
        socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open'})
        
        return jsonify(message="안녕히 가세요.", status="Approved"), 200
    except Exception as e:
        conn.rollback()
        return jsonify(error="오류 발생", details=str(e)), 500
    finally:
        conn.close()

# =========================================================
# 8. 관리자: 입차 승인 (POST /approve-entry)
# =========================================================
@app.route('/approve-entry', methods=['POST'])
def approve_entry():
    try:
        log_id = request.get_json()['LogID']
    except: return jsonify(error="LogID 필요"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # 로그 상태 승인으로 변경
        cur.execute("UPDATE GateLog SET Status = 'Approved' WHERE LogID = %s RETURNING GateID", (log_id,))
        res = cur.fetchone()
        if not res: return jsonify(error="유효하지 않은 LogID"), 404

        gate_id = res['gateid']
        cur.execute("UPDATE Gate SET Status = 'Open' WHERE GateID = %s", (gate_id,))
        conn.commit()
        socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open'})

        return jsonify(message="승인 완료"), 200
    except Exception as e:
        conn.rollback()
        return jsonify(error="오류", details=str(e)), 500
    finally:
        conn.close()

# ===============================================================
# 9. 전체 주차 공간 현황 조회 (GET /parking-status) - Index 페이지용
# ===============================================================
@app.route('/parking-status')
def parking_status():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # [수정] 상태 우선순위 변경: 공유 시간이 되면 입주민 주차 여부 무시
        query = sql.SQL("""
            SELECT 
                ps.SpaceID,
                ps.SpaceNumber,
                pz.ZoneName,
                
                -- [1] 운영 상태 (배경색)
                CASE 
                    WHEN pz.Status = 'Closed' THEN 'closed'
                    
                    -- 외부인 이용 중 (가장 확실한 점유)
                    WHEN EXISTS (SELECT 1 FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID WHERE ss.SpaceID = ps.SpaceID AND r.Status = 'InUse') THEN 'external'
                    
                    -- 예약됨
                    WHEN EXISTS (SELECT 1 FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID WHERE ss.SpaceID = ps.SpaceID AND r.Status IN ('Pending', 'Approved') AND NOW() BETWEEN r.ReserveStartTime AND r.ReserveEndTime) THEN 'reserved'
                    
                    -- [순서 변경] 공유 중이면 무조건 'shared' (입주민이 있어도 무시)
                    WHEN EXISTS (SELECT 1 FROM ShareSchedule ss WHERE ss.SpaceID = ps.SpaceID AND NOW() BETWEEN ss.ShareStartTime AND ss.ShareEndTime) THEN 'shared'
                    
                    -- 입주민 점유 (위의 'shared' 조건에 걸리지 않았을 때만 체크됨)
                    WHEN ps.OwnerVehicleID IS NOT NULL THEN 'occupied'
                    
                    ELSE 'unassigned'
                END AS op_status,

                -- [2] 물리적 점유 상태 (자동차 아이콘)
                CASE
                    -- 외부인: 무조건 표시
                    WHEN EXISTS (SELECT 1 FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID WHERE ss.SpaceID = ps.SpaceID AND r.Status = 'InUse') THEN TRUE
                    
                    -- [핵심 수정] 입주민: 실제로 주차장에 있고(fn_is_vehicle_in) + "공유 시간이 아닐 때만" 표시
                    WHEN ps.OwnerVehicleID IS NOT NULL 
                         AND fn_is_vehicle_in(ps.OwnerVehicleID) = TRUE
                         AND NOT EXISTS (SELECT 1 FROM ShareSchedule ss WHERE ss.SpaceID = ps.SpaceID AND NOW() BETWEEN ss.ShareStartTime AND ss.ShareEndTime) -- 공유 시간엔 차 숨김
                    THEN TRUE
                    
                    ELSE FALSE
                END AS is_occupied

            FROM ParkingSpace ps
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            ORDER BY pz.ZoneName, ps.SpaceNumber;
        """)
        
        cur.execute(query)
        spaces = cur.fetchall()
        
        zones_data = {}
        for space in spaces:
            zname = space['zonename']
            if zname not in zones_data: zones_data[zname] = []
            
            zones_data[zname].append({
                'id': space['spaceid'],
                'num': space['spacenumber'],
                'op_status': space['op_status'],
                'is_occupied': space['is_occupied']
            })
            
        return jsonify(zones_data)
    finally:
        conn.close()

# =========================================================
# 10. 관리자: 주차 구역 상태 변경 (PUT /zone/<int:zone_id>)
# =========================================================
@app.route('/zone/<int:zone_id>', methods=['PUT'])
def update_zone_status(zone_id):
    try:
        data = request.get_json()
        new_status = data['Status']
        if new_status not in ['Available', 'Closed']:
            return jsonify(error="Status는 Available 또는 Closed여야 합니다."), 400
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # 트리거가 관련 예약을 자동 취소함
        cur.execute(
            "UPDATE ParkingZone SET Status = %s WHERE ZoneID = %s RETURNING ZoneID, ZoneName, Status",
            (new_status, zone_id)
        )
        updated = cur.fetchone()
        if not updated: return jsonify(error="존재하지 않는 ZoneID"), 404
        conn.commit()
        return jsonify(message="상태 변경 성공", zone=updated), 200
    finally:
        conn.close()

# =============================================================
# 11. 방문자: 예약 취소 (DELETE /reservation/<int:reservation_id>)
# =============================================================
@app.route('/reservation/<int:reservation_id>', methods=['DELETE'])
def cancel_reservation(reservation_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "UPDATE Reservation SET Status = 'Canceled' WHERE ReservationID = %s AND Status = 'Approved' RETURNING ReservationID",
            (reservation_id,)
        )
        if not cur.fetchone():
            return jsonify(error="취소할 수 없는 예약이거나 존재하지 않습니다."), 400
        conn.commit()
        return jsonify(message="예약이 취소되었습니다."), 200
    finally:
        conn.close()

# =============================================================
# 12. 입주민: 비움 시간 수정 (PUT /schedule/<int:schedule_id>)
# =============================================================
@app.route('/schedule/<int:schedule_id>', methods=['PUT'])
def update_share_schedule(schedule_id):
    try:
        data = request.get_json()
        start, end = data['ShareStartTime'], data['ShareEndTime']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "UPDATE ShareSchedule SET ShareStartTime = %s, ShareEndTime = %s WHERE ShareID = %s RETURNING ShareID",
            (start, end, schedule_id)
        )
        if not cur.fetchone(): return jsonify(error="존재하지 않는 스케줄"), 404
        conn.commit()
        return jsonify(message="수정되었습니다."), 200
    except psycopg2.Error as e:
        conn.rollback()
        return jsonify(error="DB 오류 (시간 중복 등)", details=str(e)), 400
    finally:
        conn.close()

# ===============================================================
# 13. 입주민: 비움 시간 삭제 (DELETE /schedule/<int:schedule_id>)
# ===============================================================
@app.route('/schedule/<int:schedule_id>', methods=['DELETE'])
def delete_share_schedule(schedule_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("DELETE FROM ShareSchedule WHERE ShareID = %s RETURNING ShareID", (schedule_id,))
        if not cur.fetchone(): return jsonify(error="존재하지 않는 스케줄"), 404
        conn.commit()
        return jsonify(message="삭제되었습니다."), 200
    except psycopg2.Error as e:
        conn.rollback()
        return jsonify(error="삭제 실패", details=str(e)), 409
    finally:
        conn.close()

# ===============================================================
# 14. 방문자: 내 예약 내역 조회 (GET /my-reservations) - 수정됨
# ===============================================================
@app.route('/my-reservations', methods=['GET'])
def get_my_reservations():
    vid = request.args.get('vehicle_id')
    if not vid: return jsonify(error="vehicle_id 필요"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # [수정] 입주민(Owner)의 연락처(Contact)를 조인하여 가져옴
        cur.execute("""
            SELECT 
                r.*, 
                pz.ZoneName, 
                ps.SpaceID,
                ps.SpaceNumber,
                u.Contact AS OwnerContact -- 주차장 주인의 연락처
            FROM Reservation r
            JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            JOIN "User" u ON ps.OwnerVehicleID = u.VehicleID -- 주인 정보 조인
            WHERE r.VisitorVehicleID = %s
            ORDER BY r.ReserveStartTime DESC
        """, (vid,))
        
        return jsonify(reservations=cur.fetchall()), 200
    finally:
        conn.close()

# ===============================================================
# 15. 입주민: 내 주차 공간 조회 (GET /my-spaces)
# ===============================================================
@app.route('/my-spaces', methods=['GET'])
def get_my_spaces():
    vid = request.args.get('vehicle_id')
    if not vid: return jsonify(error="vehicle_id 필요"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ps.SpaceID, ps.SpaceNumber, pz.ZoneName, pz.Status as ZoneStatus
            FROM ParkingSpace ps
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            WHERE ps.OwnerVehicleID = %s
            ORDER BY ps.SpaceID
        """, (vid,))
        return jsonify(spaces=cur.fetchall()), 200
    finally:
        conn.close()

# ===============================================================
# 16. 입주민: 내 공유 내역 조회 (GET /my-shares)
# ===============================================================
@app.route('/my-shares', methods=['GET'])
def get_my_shares():
    vid = request.args.get('vehicle_id')
    if not vid: return jsonify(error="vehicle_id 필요"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # [수정됨] ShareStartTime/EndTime도 예약 데이터와 똑같이 '문자열 포맷'으로 통일
        # 이렇게 해야 프론트엔드에서 시차(Timezone) 문제없이 정확한 % 계산이 가능함
        query = sql.SQL("""
            SELECT 
                ss.ShareID,
                ss.SpaceID,
                -- [핵심 수정] datetime 객체가 아니라 문자열로 변환해서 반환
                to_char(ss.ShareStartTime, 'YYYY-MM-DD"T"HH24:MI:SS') as sharestarttime,
                to_char(ss.ShareEndTime, 'YYYY-MM-DD"T"HH24:MI:SS') as shareendtime,
                
                COALESCE(
                    json_agg(
                        json_build_object(
                            'start', to_char(r.ReserveStartTime, 'YYYY-MM-DD"T"HH24:MI:SS'),
                            'end', to_char(r.ReserveEndTime, 'YYYY-MM-DD"T"HH24:MI:SS'),
                            'status', r.Status,
                            'visitor', r.VisitorVehicleID
                        ) ORDER BY r.ReserveStartTime
                    ) FILTER (WHERE r.ReservationID IS NOT NULL AND r.Status IN ('Approved', 'InUse', 'Completed')),
                    '[]'
                ) as bookings,
                
                (
                    SELECT COUNT(*)
                    FROM Reservation r2
                    WHERE r2.ShareID = ss.ShareID AND r2.Status = 'InUse'
                ) = 0 AS is_deletable
            FROM ShareSchedule ss
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            LEFT JOIN Reservation r ON ss.ShareID = r.ShareID
            WHERE ps.OwnerVehicleID = %s
            GROUP BY ss.ShareID, ss.SpaceID, ss.ShareStartTime, ss.ShareEndTime, ps.SpaceID
            ORDER BY ss.ShareStartTime DESC;
        """)
        
        cur.execute(query, (vid,))
        shares = cur.fetchall()
        
        return jsonify(shares=shares), 200

    except Exception as e:
        return jsonify(error="서버 내부 오류", details=str(e)), 500
    finally:
        conn.close()

# ===============================================================
# 17. 관리자: 승인 대기 목록 (GET /admin/pending-requests)
# ===============================================================
@app.route('/admin/pending-requests', methods=['GET'])
def get_pending_requests():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM GateLog WHERE Action='Entry' AND Status='PendingApproval' ORDER BY Timestamp ASC")
        return jsonify(requests=cur.fetchall()), 200
    finally:
        conn.close()

# ===============================================================
# 18. 관리자: 로그 조회 (GET /admin/logs)
# ===============================================================
@app.route('/admin/logs', methods=['GET'])
def get_admin_logs():
    log_type = request.args.get('log_type', 'reservation')
    zone_id = request.args.get('zone_id')
    vehicle_id = request.args.get('vehicle_id')
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        if log_type == 'gate':
            query_str = """
                SELECT gl.*, pz.ZoneName 
                FROM GateLog gl
                LEFT JOIN Reservation r ON gl.ReservationID = r.ReservationID
                LEFT JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
                LEFT JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
                LEFT JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
                WHERE 1=1
            """
            params = []
            if zone_id: 
                query_str += " AND pz.ZoneID = %s"
                params.append(zone_id)
            if vehicle_id:
                query_str += " AND gl.VehicleID = %s"
                params.append(vehicle_id)
            query_str += " ORDER BY gl.Timestamp DESC"
            cur.execute(query_str, tuple(params))
            
        else: # reservation
            query_str = """
                SELECT r.*, pz.ZoneName 
                FROM Reservation r
                JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
                JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
                JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
                WHERE 1=1
            """
            params = []
            if zone_id:
                query_str += " AND pz.ZoneID = %s"
                params.append(zone_id)
            if vehicle_id:
                query_str += " AND r.VisitorVehicleID = %s"
                params.append(vehicle_id)
            query_str += " ORDER BY r.ReserveStartTime DESC"
            cur.execute(query_str, tuple(params))

        return jsonify(logs=cur.fetchall()), 200
    finally:
        conn.close()

# ===============================================================
# 19. 관리자: 모든 구역 조회 (GET /zones)
# ===============================================================
@app.route('/zones', methods=['GET'])
def get_all_zones():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM ParkingZone ORDER BY ZoneID ASC")
        return jsonify(zones=cur.fetchall()), 200
    finally:
        conn.close()

# ===============================================================
# 20. 관리자: 게이트 강제 개방 (POST /gate/open)
# ===============================================================
@app.route('/gate/<int:gate_id>/open', methods=['POST'])
def open_gate(gate_id):
    try:
        admin_id = request.get_json()['AdminVehicleID']
    except: return jsonify(error="AdminVehicleID 필요"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT Role FROM "User" WHERE VehicleID = %s', (admin_id,))
        user = cur.fetchone()
        if not user or user['role'] != 'Admin':
            return jsonify(error="관리자 권한 없음"), 403

        cur.execute("UPDATE Gate SET Status='Open' WHERE GateID=%s", (gate_id,))
        conn.commit()
        socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open'})
        return jsonify(message="강제 개방 완료"), 200
    finally:
        conn.close()

# ===============================================================
# 22. 입주민: 내 공간 이용 내역 조회 (GET /my-space-history) - 수정됨
# ===============================================================
@app.route('/my-space-history', methods=['GET'])
def get_my_space_history():
    vehicle_id = request.args.get('vehicle_id')
    if not vehicle_id: return jsonify(error="vehicle_id required"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # [수정] 방문자(Visitor)의 연락처(Contact)를 조인하여 가져옴
        query = sql.SQL("""
            SELECT 
                r.VisitorVehicleID,
                to_char(r.ReserveStartTime, 'YYYY-MM-DD HH24:MI') as start_time,
                to_char(r.ReserveEndTime, 'HH24:MI') as end_time,
                r.Status,
                u.Contact AS VisitorContact -- 방문자의 연락처
            FROM Reservation r
            JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN "User" u ON r.VisitorVehicleID = u.VehicleID -- 방문자 정보 조인
            WHERE ps.OwnerVehicleID = %s
              AND r.Status IN ('Completed', 'InUse', 'Approved')
            ORDER BY r.ReserveStartTime DESC
            LIMIT 20;
        """)
        
        cur.execute(query, (vehicle_id,))
        history = cur.fetchall()
        
        return jsonify(history=history), 200
    except Exception as e:
        return jsonify(error="서버 오류", details=str(e)), 500
    finally:
        conn.close()

if __name__ == '__main__':
    socketio.run(app, debug=True)