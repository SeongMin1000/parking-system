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

# --- [수정 1] 데이터베이스 연결 함수 (계정 분리 기능 추가) ---
def get_db_connection(user=None, password=None):
    # 인자가 있으면 그 계정 사용, 없으면 .env의 기본값(관리자) 사용
    target_user = user if user else os.getenv('DB_USER')
    target_pw = password if password else os.getenv('DB_PASSWORD')
    
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=target_user,
        password=target_pw,
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

# =========================================================
# 1. 사용자 회원가입 (트리거 제거 -> 파이썬 로직 구현)
# =========================================================
@app.route('/register', methods=['POST'])
def register_user():
    try:
        data = request.get_json()
        vehicleid = data['VehicleID']
        password = data['Password']
        name = data['Name']
        role = data['Role']
        contact = data.get('Contact')
        building = data.get('Building') if role == 'Resident' else None
    except Exception as e:
        return jsonify(error="데이터 누락", details=str(e)), 400

    if not contact: return jsonify(error="연락처 필수"), 400
    if not re.match(r'^\d{2}[가-힣]\d{4}$', vehicleid): return jsonify(error="차량번호 형식 오류"), 400

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. 사용자 INSERT
        cur.execute(
            'INSERT INTO "User" (VehicleID, Password, Name, Role, Contact, Building) VALUES (%s, %s, %s, %s, %s, %s)',
            (vehicleid, hashed_password.decode('utf-8'), name, role, contact, building)
        )

        # 2. [로직 추가] 입주민이면 주차 공간 찾아서 할당 (기존 트리거 역할)
        if role == 'Resident' and building:
            if '-' not in building: raise Exception("형식 오류 (예: A-1)")
            
            parts = building.split('-')
            zone_part = parts[0].upper()
            space_part = parts[1]

            # 구역/번호로 SpaceID 찾기
            cur.execute("""
                SELECT ps.SpaceID, ps.OwnerVehicleID 
                FROM ParkingSpace ps
                JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
                WHERE (UPPER(pz.ZoneName) = %s OR UPPER(pz.ZoneName) = %s) 
                  AND ps.SpaceNumber = %s
            """, (zone_part, zone_part+'구역', space_part))
            
            space = cur.fetchone()
            if not space: raise Exception(f"{zone_part}구역 {space_part}번 없음")
            if space['ownervehicleid']: raise Exception("이미 사용 중인 공간")

            # 주인 업데이트
            cur.execute("UPDATE ParkingSpace SET OwnerVehicleID = %s WHERE SpaceID = %s", (vehicleid, space['spaceid']))

        conn.commit()
        return jsonify(message="회원가입 성공!", VehicleID=vehicleid), 201
    except Exception as e:
        conn.rollback()
        return jsonify(error="가입 실패", details=str(e)), 400
    finally:
        conn.close()

# ==================================
# 2. 사용자 로그인
# ==================================
@app.route('/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json()
        vehicleid = data['VehicleID']
        password = data['Password']
    except: return jsonify(error="요청 데이터 부족"), 400

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
# 3. 입주민: 비움 시간 등록
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
            return jsonify(error="본인 공간만 등록 가능"), 403

        # 일정 등록
        cur.execute(
            "INSERT INTO ShareSchedule (SpaceID, ShareStartTime, ShareEndTime) VALUES (%s, %s, %s) RETURNING ShareID",
            (space_id, start_time, end_time)
        )
        new_id = cur.fetchone()['shareid']
        conn.commit()
        return jsonify(message="등록되었습니다.", new_share_id=new_id), 201
    except Exception as e:
        conn.rollback()
        return jsonify(error="등록 실패 (중복 등)", details=str(e)), 400
    finally:
        conn.close()

# ====================================================
# 4. 방문자: 예약 가능한 공간 조회
# ====================================================
@app.route('/spaces', methods=['GET'])
def get_available_spaces():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        search_start = request.args.get('start_time')
        search_end = request.args.get('end_time')
        if not search_start or not search_end: return jsonify(error="시간 입력 필요"), 400

        query = sql.SQL("""
            SELECT ss.ShareID, ss.ShareStartTime, ss.ShareEndTime, ps.SpaceID, pz.ZoneName, ps.SpaceNumber
            FROM ShareSchedule ss
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            WHERE pz.Status = 'Available'
              AND ss.ShareStartTime <= %s AND ss.ShareEndTime >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM Reservation r 
                  WHERE r.ShareID = ss.ShareID 
                  AND r.Status IN ('Pending', 'Approved', 'InUse')
                  AND tsrange(r.ReserveStartTime, r.ReserveEndTime) && tsrange(%s::timestamp, %s::timestamp)
              )
            ORDER BY ss.ShareStartTime ASC;
        """)
        cur.execute(query, (search_start, search_end, search_start, search_end))
        return jsonify(available_spaces=cur.fetchall()), 200
    finally:
        conn.close()

# =========================================================
# 5. 방문자: 주차 예약 (트리거 제거 -> 파이썬 로직 구현)
# =========================================================
@app.route('/reservation', methods=['POST'])
def create_reservation():
    try:
        data = request.get_json()
        vehicle_id, share_id = data['VehicleID'], data['ShareID']
        start, end = data['ReserveStartTime'], data['ReserveEndTime']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. [로직 추가] 블랙리스트 검사
        cur.execute("SELECT 1 FROM BlacklistRequest WHERE TargetVehicleID = %s AND Status = 'Approved'", (vehicle_id,))
        if cur.fetchone(): return jsonify(error="블랙리스트 차량입니다."), 403

        # 2. [로직 추가] 중복 예약 검사
        cur.execute("""
            SELECT 1 FROM Reservation
            WHERE ShareID = %s
              AND Status IN ('Pending', 'Approved', 'InUse')
              AND tsrange(%s::timestamp, %s::timestamp) && tsrange(ReserveStartTime, ReserveEndTime)
        """, (share_id, start, end))
        if cur.fetchone(): return jsonify(error="이미 예약된 시간입니다."), 409

        # 3. 예약 INSERT
        cur.execute(
            "INSERT INTO Reservation (ShareID, VisitorVehicleID, ReserveStartTime, ReserveEndTime, Status) VALUES (%s, %s, %s, %s, 'Approved') RETURNING ReservationID",
            (share_id, vehicle_id, start, end)
        )
        rid = cur.fetchone()['reservationid']
        conn.commit()
        return jsonify(message="예약되었습니다.", reservation_id=rid), 201
    except Exception as e:
        conn.rollback()
        return jsonify(error="DB 오류", details=str(e)), 500
    finally:
        conn.close()

# =========================================================
# 6. 게이트 입차 요청 
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

        # 0. 블랙리스트 확인
        cur.execute("SELECT 1 FROM BlacklistRequest WHERE TargetVehicleID = %s AND Status = 'Approved'", (vehicle_id,))
        if cur.fetchone():
            return jsonify(error="⛔ 블랙리스트로 차단된 차량입니다. 진입할 수 없습니다."), 403

        # 1. 중복 입차 확인
        cur.execute("""
            SELECT Action FROM GateLog 
            WHERE VehicleID = %s AND Status IN ('Automatic', 'Approved') 
            ORDER BY Timestamp DESC LIMIT 1
        """, (vehicle_id,))
        last_log = cur.fetchone()
        
        if last_log and last_log['action'] == 'Entry':
            return jsonify(error="이미 주차장에 입차해 있는 차량입니다. (중복 입차 불가)"), 400

        # 2. 사용자 역할 확인
        cur.execute('SELECT Role FROM "User" WHERE VehicleID = %s', (vehicle_id,))
        user_row = cur.fetchone()
        role = user_row['role'] if user_row else 'Visitor'

        # 3. 입차 심사
        entry_status = 'PendingApproval'
        reservation_id = None
        owner_to_kick = None

        if role == 'Resident':
            # [핵심 로직 추가] 내 자리를 누군가(방문자) 사용 중인지 확인
            cur.execute("""
                SELECT r.VisitorVehicleID 
                FROM Reservation r
                JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
                JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
                WHERE ps.OwnerVehicleID = %s 
                  AND r.Status = 'InUse'
            """, (vehicle_id,))
            
            conflict_visitor = cur.fetchone()
            
            if conflict_visitor:
                # 방문자가 사용 중이면 입주민 입차 거부
                visitor_car = conflict_visitor['visitorvehicleid']
                return jsonify(error=f"현재 본인의 주차 공간을 방문차량({visitor_car})이 이용 중입니다. 입차하실 수 없습니다."), 403
            
            # 문제 없으면 입주민 프리패스
            entry_status = 'Automatic'

        else:
            # 방문자 예약 확인
            cur.execute("""
                SELECT r.ReservationID, ps.OwnerVehicleID
                FROM Reservation r
                JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
                JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
                WHERE r.VisitorVehicleID = %s 
                  AND r.Status = 'Approved'
                  AND NOW() BETWEEN r.ReserveStartTime AND r.ReserveEndTime
            """, (vehicle_id,))
            res = cur.fetchone()
            
            if res:
                entry_status = 'Automatic'
                reservation_id = res['reservationid']
                
                # 방문자 입차 시 입주민 강제 출차 (기존 로직 유지)
                owner_vid = res['ownervehicleid']
                if owner_vid:
                    cur.execute("SELECT Action FROM GateLog WHERE VehicleID=%s AND Status IN ('Automatic','Approved') ORDER BY Timestamp DESC LIMIT 1", (owner_vid,))
                    olog = cur.fetchone()
                    if olog and olog['action'] == 'Entry':
                        owner_to_kick = owner_vid

        # 4. 강제 출차 실행 (입주민)
        if owner_to_kick:
            cur.execute("INSERT INTO GateLog (VehicleID, GateID, Action, Status) VALUES (%s, 2, 'Exit', 'Approved')", (owner_to_kick,))

        # 5. 로그 기록
        cur.execute(
            "INSERT INTO GateLog (VehicleID, GateID, Action, Status, ReservationID) VALUES (%s, %s, 'Entry', %s, %s)",
            (vehicle_id, gate_id, entry_status, reservation_id)
        )

        # 6. 게이트 및 예약 상태 변경
        if entry_status == 'Automatic':
            cur.execute("UPDATE Gate SET Status = 'Open' WHERE GateID = %s", (gate_id,))
            if reservation_id:
                cur.execute("UPDATE Reservation SET Status = 'InUse' WHERE ReservationID = %s", (reservation_id,))

        conn.commit()

        if entry_status == 'Automatic':
            socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open', 'auto_close': True})
            return jsonify(message="어서오세요. (자동 승인)", status="Automatic"), 200
        else:
            return jsonify(message="예약 확인 불가. 관리자 호출 중...", status="Pending"), 202

    except Exception as e:
        conn.rollback()
        return jsonify(error="입차 오류", details=str(e)), 500
    finally:
        conn.close()

# =========================================================
# 7. 게이트 출차 요청 (트리거 제거 -> 파이썬 로직 구현)
# =========================================================
# [app.py] request_exit 함수 수정
# - 블랙리스트 체크
# - 입차하지 않은 차량의 출차 시도 거부 (중복 출차 방지)

@app.route('/exit-request', methods=['POST'])
def request_exit():
    try:
        data = request.get_json()
        vehicle_id, gate_id = data['VehicleID'], data['GateID']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 0. 블랙리스트 확인
        cur.execute("SELECT 1 FROM BlacklistRequest WHERE TargetVehicleID = %s AND Status = 'Approved'", (vehicle_id,))
        if cur.fetchone():
            return jsonify(error="⛔ 블랙리스트 차량입니다. (시스템에 의해 이미 강제 퇴거 처리되었습니다)"), 403

        # 1. [요청하신 기능] 현재 주차장에 있는지 확인 (중복 출차 방지)
        # 마지막으로 '승인된' 기록이 'Entry'여야만 나갈 수 있음
        cur.execute("""
            SELECT Action FROM GateLog 
            WHERE VehicleID = %s AND Status IN ('Automatic', 'Approved') 
            ORDER BY Timestamp DESC LIMIT 1
        """, (vehicle_id,))
        last_log = cur.fetchone()

        if not last_log or last_log['action'] == 'Exit':
            return jsonify(error="주차장에 없는 차량이거나 이미 출차했습니다."), 400

        # 2. 출차 로그 기록
        cur.execute("INSERT INTO GateLog (VehicleID, GateID, Action, Status) VALUES (%s, %s, 'Exit', 'Approved')", (vehicle_id, gate_id))

        # 3. 예약 상태 변경 (시간 남았으면 Approved, 아니면 Completed)
        cur.execute("""
            UPDATE Reservation 
            SET Status = CASE 
                WHEN NOW() < ReserveEndTime THEN 'Approved' 
                ELSE 'Completed' 
            END 
            WHERE VisitorVehicleID = %s AND Status = 'InUse'
        """, (vehicle_id,))

        # 4. 게이트 열기
        cur.execute("UPDATE Gate SET Status = 'Open' WHERE GateID = %s", (gate_id,))
        conn.commit()

        socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open', 'auto_close': True})
        return jsonify(message="안녕히 가세요.", status="Approved"), 200

    except Exception as e:
        conn.rollback()
        return jsonify(error="출차 오류", details=str(e)), 500
    finally:
        conn.close()

# =========================================================
# 8. 관리자: 입차 승인 (수동)
# =========================================================
# [app.py] approve_entry 함수 수정 (블랙리스트 최종 검사 추가)

@app.route('/approve-entry', methods=['POST'])
def approve_entry():
    try:
        log_id = request.get_json()['LogID']
    except: return jsonify(error="LogID 필요"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. 해당 로그의 차량 번호 조회
        cur.execute("SELECT VehicleID FROM GateLog WHERE LogID = %s", (log_id,))
        log_entry = cur.fetchone()
        
        if not log_entry:
            return jsonify(error="유효하지 않은 요청입니다."), 404
            
        target_vehicle = log_entry['vehicleid']

        # 2. [추가] 승인 직전 블랙리스트 여부 재확인
        cur.execute("SELECT 1 FROM BlacklistRequest WHERE TargetVehicleID = %s AND Status = 'Approved'", (target_vehicle,))
        if cur.fetchone():
            return jsonify(error=f"⛔ 차단된 차량({target_vehicle})입니다. 승인할 수 없습니다."), 403

        # 3. 이상 없으면 승인 처리
        cur.execute("UPDATE GateLog SET Status = 'Approved' WHERE LogID = %s RETURNING GateID", (log_id,))
        res = cur.fetchone()

        gate_id = res['gateid']
        cur.execute("UPDATE Gate SET Status = 'Open' WHERE GateID = %s", (gate_id,))
        conn.commit()

        socketio.emit('gate_status_changed', {'gate_id': gate_id, 'status': 'Open', 'auto_close': True})
        return jsonify(message="승인 완료 (게이트 개방)"), 200

    except Exception as e:
        conn.rollback()
        return jsonify(error="오류 발생", details=str(e)), 500
    finally:
        conn.close()

# ===============================================================
# 9. [수정 2] 전체 주차 현황 조회 (DCL Guest 계정 사용)
# ===============================================================
@app.route('/parking-status')
def parking_status():
    # [DCL 적용] Guest 계정으로 접속
    guest_user = os.getenv('DB_GUEST_USER')
    guest_pw = os.getenv('DB_GUEST_PASSWORD')
    conn = get_db_connection(user=guest_user, password=guest_pw)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. 뷰 조회
        cur.execute("SELECT * FROM View_ParkingStatus ORDER BY ZoneName, SpaceNumber")
        spaces = cur.fetchall()
        
        # 2. 유령 차량(미예약 차량) 조회
        cur.execute("""
            SELECT u.VehicleID
            FROM "User" u
            WHERE u.Role = 'Visitor'
              AND fn_is_vehicle_in(u.VehicleID) = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM Reservation r 
                  WHERE r.VisitorVehicleID = u.VehicleID 
                    AND r.Status = 'InUse'
              )
        """)
        ghost_cars = [row['vehicleid'] for row in cur.fetchall()]

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
            
        return jsonify(zones_data=zones_data, ghost_cars=ghost_cars)
    except Exception as e:
        print("Parking Status Error:", e)
        # Guest 권한 오류 시 처리
        return jsonify(error="조회 권한 오류"), 403
    finally:
        conn.close()

# =========================================================
# 10. 관리자: 구역 상태 변경
# =========================================================
# [app.py] update_zone_status 함수 수정 (구역 폐쇄 시 점거 차량 강제 출차 추가)

# [app.py] update_zone_status 함수 수정 (공유 스케줄 삭제 로직 추가)

@app.route('/zone/<int:zone_id>', methods=['PUT'])
def update_zone_status(zone_id):
    try:
        new_status = request.get_json()['Status']
    except: return jsonify(error="데이터 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. 구역 상태 업데이트
        cur.execute("UPDATE ParkingZone SET Status = %s WHERE ZoneID = %s RETURNING ZoneID", (new_status, zone_id))
        if not cur.fetchone(): return jsonify(error="존재하지 않는 ZoneID"), 404
        
        # 2. 구역 '폐쇄(Closed)' 시 강력 조치
        if new_status == 'Closed':
            print(f"[System] {zone_id}번 구역 폐쇄로 인한 정리 작업 시작...")

            # (A) 현재 이용 중인 '방문객' 강제 출차
            cur.execute("""
                SELECT r.VisitorVehicleID, r.ReservationID 
                FROM Reservation r
                JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
                JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
                WHERE ps.ZoneID = %s AND r.Status = 'InUse'
            """, (zone_id,))
            active_visitors = cur.fetchall()

            for v in active_visitors:
                cur.execute("INSERT INTO GateLog (VehicleID, GateID, Action, Status) VALUES (%s, 2, 'Exit', 'Approved')", (v['visitorvehicleid'],))
                cur.execute("UPDATE Reservation SET Status = 'Completed' WHERE ReservationID = %s", (v['reservationid'],))

            # (B) 현재 주차 중인 '입주민' 강제 출차
            cur.execute("""
                SELECT OwnerVehicleID FROM ParkingSpace 
                WHERE ZoneID = %s AND OwnerVehicleID IS NOT NULL
            """, (zone_id,))
            owners = cur.fetchall()

            for o in owners:
                vid = o['ownervehicleid']
                cur.execute("""
                    SELECT Action FROM GateLog 
                    WHERE VehicleID = %s AND Status IN ('Automatic', 'Approved') 
                    ORDER BY Timestamp DESC LIMIT 1
                """, (vid,))
                last_log = cur.fetchone()
                if last_log and last_log['action'] == 'Entry':
                    cur.execute("INSERT INTO GateLog (VehicleID, GateID, Action, Status) VALUES (%s, 2, 'Exit', 'Approved')", (vid,))

            # (C) 미래 예약(대기/승인) 모두 취소
            cur.execute("""
                UPDATE Reservation SET Status = 'Canceled'
                WHERE Status IN ('Pending', 'Approved')
                AND ShareID IN (
                    SELECT ShareID FROM ShareSchedule ss
                    JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
                    WHERE ps.ZoneID = %s
                )
            """, (zone_id,))

            # (D) [핵심 추가] 해당 구역의 공유 스케줄(ShareSchedule) 자체를 삭제
            #     이걸 해줘야 입주민 화면에서 바(Bar)가 사라집니다.
            cur.execute("""
                DELETE FROM ShareSchedule
                WHERE SpaceID IN (
                    SELECT SpaceID FROM ParkingSpace WHERE ZoneID = %s
                )
            """, (zone_id,))
            
        conn.commit()
        return jsonify(message="구역 상태가 변경되었으며, 관련 차량, 예약, 공유 일정이 모두 정리되었습니다."), 200

    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify(error="상태 변경 실패", details=str(e)), 500
    finally:
        conn.close()

# --- 기타 단순 CRUD 라우트들 (크게 수정할 부분 없음) ---
@app.route('/reservation/<int:reservation_id>', methods=['DELETE'])
def cancel_reservation(reservation_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("UPDATE Reservation SET Status = 'Canceled' WHERE ReservationID = %s RETURNING ReservationID", (reservation_id,))
        conn.commit()
        return jsonify(message="예약 취소됨"), 200
    finally:
        conn.close()

@app.route('/schedule/<int:schedule_id>', methods=['DELETE'])
def delete_share_schedule(schedule_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("DELETE FROM ShareSchedule WHERE ShareID = %s", (schedule_id,))
        conn.commit()
        return jsonify(message="삭제되었습니다."), 200
    finally:
        conn.close()

@app.route('/my-reservations', methods=['GET'])
def get_my_reservations():
    vid = request.args.get('vehicle_id')
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT r.*, pz.ZoneName, ps.SpaceNumber, u.Contact as OwnerContact
            FROM Reservation r
            JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            JOIN "User" u ON ps.OwnerVehicleID = u.VehicleID
            WHERE r.VisitorVehicleID = %s
            ORDER BY r.ReserveStartTime DESC
        """, (vid,))
        return jsonify(reservations=cur.fetchall()), 200
    finally:
        conn.close()

# [app.py] get_my_shares 함수 수정
# - 예약 종료 시간('end') 추가 (타임라인 바 오류 해결)
# - 취소된 예약은 목록에서 제외 (건수 오류 해결)

@app.route('/my-shares', methods=['GET'])
def get_my_shares():
    vid = request.args.get('vehicle_id')
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = sql.SQL("""
            SELECT 
                ss.ShareID, 
                ss.SpaceID,
                to_char(ss.ShareStartTime, 'YYYY-MM-DD"T"HH24:MI:SS') as sharestarttime,
                to_char(ss.ShareEndTime, 'YYYY-MM-DD"T"HH24:MI:SS') as shareendtime,
                
                -- [수정] 'end' 필드 추가 및 'Canceled' 상태 제외
                COALESCE(
                    json_agg(
                        json_build_object(
                            'start', to_char(r.ReserveStartTime, 'YYYY-MM-DD"T"HH24:MI:SS'),
                            'end', to_char(r.ReserveEndTime, 'YYYY-MM-DD"T"HH24:MI:SS'),
                            'status', r.Status,
                            'visitor', r.VisitorVehicleID
                        )
                    ) FILTER (
                        WHERE r.ReservationID IS NOT NULL 
                        AND r.Status IN ('Pending', 'Approved', 'InUse', 'Completed') -- 취소된 예약 제외
                    ), 
                    '[]'
                ) as bookings,
                
                (SELECT COUNT(*) FROM Reservation r2 WHERE r2.ShareID = ss.ShareID AND r2.Status = 'InUse') = 0 AS is_deletable
            FROM ShareSchedule ss
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            LEFT JOIN Reservation r ON ss.ShareID = r.ShareID
            WHERE ps.OwnerVehicleID = %s
            GROUP BY ss.ShareID, ss.SpaceID, ss.ShareStartTime, ss.ShareEndTime, ps.SpaceID
            ORDER BY ss.ShareStartTime DESC
        """)
        cur.execute(query, (vid,))
        return jsonify(shares=cur.fetchall()), 200
    finally:
        conn.close()

@app.route('/my-spaces', methods=['GET'])
def get_my_spaces():
    vid = request.args.get('vehicle_id')
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ps.SpaceID, ps.SpaceNumber, pz.ZoneName, pz.Status as ZoneStatus
            FROM ParkingSpace ps
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            WHERE ps.OwnerVehicleID = %s
        """, (vid,))
        return jsonify(spaces=cur.fetchall()), 200
    finally:
        conn.close()

@app.route('/admin/pending-requests', methods=['GET'])
def get_pending_requests():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM GateLog WHERE Action='Entry' AND Status='PendingApproval' ORDER BY Timestamp ASC")
        return jsonify(requests=cur.fetchall()), 200
    finally:
        conn.close()

@app.route('/admin/logs', methods=['GET'])
def get_admin_logs():
    log_type = request.args.get('log_type', 'gate')
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if log_type == 'gate':
            cur.execute("SELECT gl.*, pz.ZoneName FROM GateLog gl LEFT JOIN Reservation r ON gl.ReservationID = r.ReservationID LEFT JOIN ShareSchedule ss ON r.ShareID = ss.ShareID LEFT JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID LEFT JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID ORDER BY gl.Timestamp DESC LIMIT 50")
        else:
            cur.execute("SELECT r.*, pz.ZoneName FROM Reservation r JOIN ShareSchedule ss ON r.ShareID = ss.ShareID JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID ORDER BY r.ReserveStartTime DESC LIMIT 50")
        return jsonify(logs=cur.fetchall()), 200
    finally:
        conn.close()

@app.route('/zones', methods=['GET'])
def get_all_zones():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM ParkingZone ORDER BY ZoneID ASC")
        return jsonify(zones=cur.fetchall()), 200
    finally:
        conn.close()

# --- 블랙리스트 관련 API (기존 유지) ---
@app.route('/blacklist/request', methods=['POST'])
def request_blacklist():
    data = request.get_json()
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("INSERT INTO BlacklistRequest (RequesterVehicleID, TargetVehicleID, Reason) VALUES (%s, %s, %s)", (data['RequesterVehicleID'], data['TargetVehicleID'], data['Reason']))
        conn.commit()
        return jsonify(message="신고 접수됨"), 201
    finally:
        conn.close()

@app.route('/admin/blacklist-requests', methods=['GET'])
def get_blacklist_requests():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM BlacklistRequest WHERE Status = 'Pending' ORDER BY CreatedAt DESC")
        return jsonify(requests=cur.fetchall()), 200
    finally:
        conn.close()

@app.route('/admin/blacklist/approve', methods=['POST'])
# [app.py] approve_blacklist 함수 수정 (강제 출차 기능 추가)

@app.route('/admin/blacklist/approve', methods=['POST'])
def approve_blacklist():
    try:
        req_id = request.get_json()['RequestID']
    except: return jsonify(error="RequestID 누락"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. 블랙리스트 상태 'Approved'로 변경
        cur.execute("UPDATE BlacklistRequest SET Status='Approved', ProcessedAt=NOW() WHERE RequestID=%s RETURNING TargetVehicleID", (req_id,))
        res = cur.fetchone()
        
        if res:
            target_vehicle = res['targetvehicleid']
            
            # 2. [핵심] 현재 입차 중인지 확인
            cur.execute("""
                SELECT Action FROM GateLog 
                WHERE VehicleID = %s AND Status IN ('Automatic', 'Approved') 
                ORDER BY Timestamp DESC LIMIT 1
            """, (target_vehicle,))
            last_log = cur.fetchone()
            
            # 3. 입차 상태라면 -> 강제 출차(Exit) 로그 생성
            if last_log and last_log['action'] == 'Entry':
                cur.execute("INSERT INTO GateLog (VehicleID, GateID, Action, Status) VALUES (%s, 2, 'Exit', 'Approved')", (target_vehicle,))
                print(f"[System] 블랙리스트 차량({target_vehicle}) 강제 출차 처리됨")

            # 4. 기존 예약 모두 취소 (진행 중인 예약 포함)
            cur.execute("UPDATE Reservation SET Status='Canceled' WHERE VisitorVehicleID=%s AND Status IN ('Pending','Approved','InUse')", (target_vehicle,))
        
        conn.commit()
        return jsonify(message="차단 및 강제 출차 처리가 완료되었습니다."), 200
    except Exception as e:
        conn.rollback()
        return jsonify(error="오류 발생", details=str(e)), 500
    finally:
        conn.close()

@app.route('/admin/blacklist/reject', methods=['POST'])
def reject_blacklist():
    req_id = request.get_json()['RequestID']
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("UPDATE BlacklistRequest SET Status='Rejected', ProcessedAt=NOW() WHERE RequestID=%s", (req_id,))
        conn.commit()
        return jsonify(message="반려됨"), 200
    finally:
        conn.close()

@app.route('/admin/blacklist/approved', methods=['GET'])
def get_approved_blacklist():
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM BlacklistRequest WHERE Status='Approved' ORDER BY ProcessedAt DESC")
        return jsonify(requests=cur.fetchall()), 200
    finally:
        conn.close()

@app.route('/admin/blacklist/<int:request_id>', methods=['DELETE'])
def delete_blacklist(request_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("DELETE FROM BlacklistRequest WHERE RequestID=%s", (request_id,))
        conn.commit()
        return jsonify(message="해제됨"), 200
    finally:
        conn.close()

# ===============================================================
# 11. [누락된 기능 복구] 입주민: 내 공간 이용 내역 조회
# ===============================================================
@app.route('/my-space-history', methods=['GET'])
def get_my_space_history():
    vehicle_id = request.args.get('vehicle_id')
    if not vehicle_id: return jsonify(error="vehicle_id required"), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 방문자 정보(연락처 포함)와 예약 정보를 조인해서 가져옴
        query = sql.SQL("""
            SELECT 
                r.VisitorVehicleID,
                to_char(r.ReserveStartTime, 'YYYY-MM-DD HH24:MI') as start_time,
                to_char(r.ReserveEndTime, 'HH24:MI') as end_time,
                r.Status,
                u.Contact AS VisitorContact
            FROM Reservation r
            JOIN ShareSchedule ss ON r.ShareID = ss.ShareID
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN "User" u ON r.VisitorVehicleID = u.VehicleID
            WHERE ps.OwnerVehicleID = %s
              AND r.Status IN ('Completed', 'InUse', 'Approved')
            ORDER BY r.ReserveStartTime DESC
            LIMIT 20
        """)
        
        cur.execute(query, (vehicle_id,))
        return jsonify(history=cur.fetchall()), 200
    except Exception as e:
        return jsonify(error="서버 오류", details=str(e)), 500
    finally:
        conn.close()

if __name__ == '__main__':
    socketio.run(app, debug=True)