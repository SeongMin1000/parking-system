import os
import psycopg2
from flask import Flask, jsonify, request
from dotenv import load_dotenv
import bcrypt
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

# .env 파일에서 환경 변수 로드
load_dotenv()

app = Flask(__name__)

# --- 데이터베이스 연결 설정 ---
def get_db_connection():
    """PostgreSQL 데이터베이스에 연결합니다."""
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT')
    )
    return conn

@app.route('/')
def index():
    return "Flask 서버가 정상적으로 실행 중입니다!"

# ========================================
# 사용자 회원가입 API (POST /register)
# ========================================
@app.route('/register', methods=['POST'])
def register_user():
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        userid = data['UserID']
        password = data['Password']
        name = data['Name']
        role = data['Role']
        contact = data.get('Contact') # Contact는 선택 사항일 수 있음
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. UserID, Password, Name, Role이 필요합니다.", details=str(e)), 400

    # 2. 비밀번호 해시 (bcrypt 사용)
    # 비밀번호를 바이트로 인코딩하고 해시 처리
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor() 

        # 3. SQL INSERT 실행
        query = sql.SQL(
            """
            INSERT INTO "User" (UserID, Password, Name, Role, Contact)
            VALUES (%s, %s, %s, %s, %s)
            """
        )
        
        # 4. 데이터베이스에 저장
        cur.execute(query, (userid, hashed_password.decode('utf-8'), name, role, contact))
        
        # 5. 변경사항 커밋
        conn.commit()

        return jsonify(message="회원가입에 성공했습니다.", UserID=userid), 201 # 201: Created

    except psycopg2.Error as e:
        # 6. 오류 처리
        if conn:
            conn.rollback() # 오류 발생 시 변경사항 롤백
            
        # UserID 중복 오류 (unique_violation)
        if e.pgcode == '23505':
            return jsonify(error="이미 사용 중인 UserID입니다.", UserID=userid), 409 # 409: Conflict
        
        # CHECK 제약 조건 위반 (예: Role이 'Resident' 등이 아님)
        if e.pgcode == '23514':
            return jsonify(error="Role 값이 유효하지 않습니다. ('Resident', 'Visitor', 'Admin' 중 하나여야 합니다.)"), 400

        # 그 외 DB 오류
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e)), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        # 7. 연결 종료
        if cur:
            cur.close()
        if conn:
            conn.close()

# ==================================
# 사용자 로그인 API (POST /login)
# ==================================
@app.route('/login', methods=['POST'])
def login_user():
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        userid = data['UserID']
        password = data['Password']
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. UserID와 Password가 필요합니다.", details=str(e)), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. 사용자 ID로 DB에서 사용자 정보 조회
        query = sql.SQL('SELECT * FROM "User" WHERE UserID = %s')
        cur.execute(query, (userid,))
        
        user = cur.fetchone()

        # 3. 사용자 존재 여부 확인
        if not user:
            # ID가 존재하지 않음
            return jsonify(error="아이디 또는 비밀번호가 잘못되었습니다."), 401 # 401: Unauthorized

        # 4. 비밀번호 비교 (bcrypt)
        # user['password'] : DB에 저장된 해시값 (문자열)
        # password : 사용자가 방금 입력한 평문 비밀번호 (문자열)
        
        # .encode('utf-8') : 비교를 위해 두 값 모두 바이트로 변환
        if bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            return jsonify(
                message="로그인 성공!",
                UserID=user['userid'],
                Role=user['role']
            ), 200
        else:
            # 비밀번호 불일치
            return jsonify(error="아이디 또는 비밀번호가 잘못되었습니다."), 401

    except psycopg2.Error as e:
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e)), 500
    except Exception as e:
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ===============================================
# 6. 입주민: 비움 시간(공유) 등록 (POST /schedule)
# ===============================================
@app.route('/schedule', methods=['POST'])
def create_share_schedule():
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        user_id = data['UserID'] # 요청자가 누구인지
        space_id = data['SpaceID']
        start_time = data['ShareStartTime'] # 예: "2025-11-10 14:00:00"
        end_time = data['ShareEndTime']     # 예: "2025-11-10 18:00:00"
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. UserID, SpaceID, ShareStartTime, ShareEndTime이 필요합니다.", details=str(e)), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. 본인 소유 공간 검증
        # 요청한 SpaceID가 요청한 UserID(입주민)의 소유가 맞는지 확인
        query_check_owner = sql.SQL('SELECT OwnerID FROM ParkingSpace WHERE SpaceID = %s')
        cur.execute(query_check_owner, (space_id,))
        space = cur.fetchone()

        if not space:
            return jsonify(error="존재하지 않는 주차 공간(SpaceID)입니다."), 404 # 404: Not Found
        
        if space['ownerid'] != user_id:
            return jsonify(error="해당 주차 공간의 소유주가 아닙니다. (접근 거부)"), 403 # 403: Forbidden

        # 3. 비움 시간 등록 (INSERT)
        query_insert = sql.SQL(
            """
            INSERT INTO ShareSchedule (SpaceID, ShareStartTime, ShareEndTime)
            VALUES (%s, %s, %s)
            RETURNING ShareID
            """
            # RETURNING ShareID: 방금 생성된 ShareID 값을 반환받음
        )
        cur.execute(query_insert, (space_id, start_time, end_time))
        
        new_schedule = cur.fetchone()
        conn.commit()

        return jsonify(
            message="비움 시간이 성공적으로 등록되었습니다.",
            new_share_id=new_schedule['shareid']
        ), 201

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
            
        # DDL의 CHECK 제약 조건 위반 (예: 종료 시간이 시작 시간보다 빠름)
        if e.pgcode == '23514':
            return jsonify(error="CHECK 제약 조건 위반: ShareEndTime은 ShareStartTime보다 늦어야 합니다."), 400
        
        # DDL의 GIST 제약 조건 위반 (시간 중첩)
        if e.pgcode == '23P01': # 'exclusion_violation'
            return jsonify(error="시간 중첩 오류: 해당 공간에 이미 등록된 공유 시간이 있습니다."), 409 # 409: Conflict
            
        # DDL의 FK 제약 조건 위반 (없는 SpaceID)
        if e.pgcode == '23503':
            return jsonify(error="존재하지 않는 SpaceID입니다. (FK 오류)"), 404

        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ====================================================
# 7. 방문자: 예약 가능한 공간 조회 (GET /spaces)
# ====================================================
@app.route('/spaces', methods=['GET'])
def get_available_spaces():
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 3개 테이블을 JOIN
        # 1. Zone이 'Available' 상태여야 함
        # 2. 공유 종료 시간이 현재 시간(NOW())보다 늦어야 함 (지난 일정 안보기)
        query = sql.SQL(
            """
            SELECT 
                ss.ShareID,
                ss.ShareStartTime,
                ss.ShareEndTime,
                ps.SpaceID,
                pz.ZoneName
            FROM ShareSchedule ss
            JOIN ParkingSpace ps ON ss.SpaceID = ps.SpaceID
            JOIN ParkingZone pz ON ps.ZoneID = pz.ZoneID
            WHERE 
                pz.Status = 'Available'
                AND ss.ShareEndTime > NOW()
            ORDER BY 
                ss.ShareStartTime ASC; 
            """
        )
        
        cur.execute(query)
        spaces = cur.fetchall()

        return jsonify(available_spaces=spaces), 200

    except Exception as e:
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ===============================================
# 8. 방문자: 주차 예약 생성 (POST /reservation)
# ===============================================
@app.route('/reservation', methods=['POST'])
def create_reservation():    
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        user_id = data['UserID'] # (인증 대신) 예약하는 방문자 ID
        share_id = data['ShareID'] # 'GET /spaces'에서 봤던 그 ShareID
        start_time = data['ReserveStartTime'] # 예: "2025-11-13 14:00:00"
        end_time = data['ReserveEndTime']     # 예: "2025-11-13 16:00:00"
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. UserID, ShareID, ReserveStartTime, ReserveEndTime이 필요합니다.", details=str(e)), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. 예약 생성 (INSERT)
        # INSERT만 시도
        # 모든 유효성 검사(시간 범위, 중복, 구역 폐쇄)는
        # DB에 심어둔 'fn_validate_reservation' 트리거가 자동으로 수행
        #
        query_insert = sql.SQL(
            """
            INSERT INTO Reservation (ShareID, VisitorID, ReserveStartTime, ReserveEndTime, Status)
            VALUES (%s, %s, %s, %s, 'Pending')
            RETURNING ReservationID
            """
        )
        
        cur.execute(query_insert, (share_id, user_id, start_time, end_time))
        
        new_reservation = cur.fetchone()
        conn.commit()

        return jsonify(
            message="주차 공간 예약에 성공했습니다.",
            reservation_id=new_reservation['reservationid']
        ), 201

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
            
        # 3. 트리거가 보낸 오류 잡기 (RAISE EXCEPTION)
        # 'fn_validate_reservation' 함수에서 RAISE EXCEPTION으로 보낸
        # 사용자 정의 오류(예: '시간 중첩', '범위 초과')는 pgcode 'P0001'로 잡힘
        if e.pgcode == 'P0001': # 'raise_exception'
            # e.diag.message_primary는 트리거에서 설정한 'RAISE EXCEPTION' 메시지 본문
            return jsonify(error="예약 생성 실패 (트리거 검증 오류)", details=e.diag.message_primary), 400

        # DDL의 CHECK 제약 조건 위반 (예: 종료 시간이 시작 시간보다 빠름)
        if e.pgcode == '23514':
            return jsonify(error="CHECK 제약 조건 위반: ReserveEndTime은 ReserveStartTime보다 늦어야 합니다."), 400
        
        # DDL의 FK 제약 조건 위반 (없는 ShareID 또는 VisitorID)
        if e.pgcode == '23503':
            return jsonify(error="존재하지 않는 ShareID 또는 VisitorID입니다. (FK 오류)"), 404

        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# =========================================================
# 9. 관리자: 주차 구역 상태 변경 (PUT /zone/<int:zone_id>)
# =========================================================
@app.route('/zone/<int:zone_id>', methods=['PUT'])
def update_zone_status(zone_id):
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        new_status = data['Status']
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. 'Status'가 필요합니다.", details=str(e)), 400

    # 2. Status 값 유효성 검사 (DB CHECK 제약 조건과 동일)
    if new_status not in ['Available', 'Closed']:
        return jsonify(error="Status 값은 'Available' 또는 'Closed'여야 합니다."), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 3. 구역 상태 업데이트 (UPDATE)
        # 만약 new_status가 'Closed'라면, 이 UPDATE 명령이
        # 'fn_cancel_reservations_on_zone_close' 트리거를 작동시킴
        # 트리거는 이 ZoneID에 물려있는 모든 'Pending' 예약을 'Canceled'로 자동 변경한다.
        query_update = sql.SQL(
            """
            UPDATE ParkingZone
            SET Status = %s
            WHERE ZoneID = %s
            RETURNING ZoneID, ZoneName, Status
            """
        )
        
        cur.execute(query_update, (new_status, zone_id))
        
        updated_zone = cur.fetchone()
        
        # 4. 업데이트 성공 여부 확인
        if not updated_zone:
            return jsonify(error="존재하지 않는 ZoneID입니다."), 404 # 404: Not Found

        conn.commit()

        return jsonify(
            message="주차 구역 상태가 성공적으로 변경되었습니다.",
            zone=updated_zone
        ), 200

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        # (기타 DB 오류 처리)
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# =========================================================
# 10. 방문자: 입차 요청 (POST /entry-request)
# =========================================================
@app.route('/entry-request', methods=['POST'])
def request_entry():
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        user_id = data['UserID']
        gate_id = data['GateID'] # 어느 게이트로 들어왔는지
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. 'UserID'와 'GateID'가 필요합니다.", details=str(e)), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. 유효한 예약 확인 (제안서 기능)
        # "현재 시간(NOW())"에 "Pending" 상태인 "본인(user_id)"의 예약이 있는지 확인
        query_find_reservation = sql.SQL(
            """
            SELECT ReservationID, Status
            FROM Reservation
            WHERE VisitorID = %s
              AND Status = 'Pending'
              AND NOW() BETWEEN ReserveStartTime AND ReserveEndTime
            LIMIT 1;
            """
        )
        cur.execute(query_find_reservation, (user_id,))
        valid_reservation = cur.fetchone()

        if not valid_reservation:
            return jsonify(error="유효한 예약이 없습니다. (시간 확인 또는 이미 사용된 예약)"), 404 # 404: Not Found
        
        reservation_id = valid_reservation['reservationid']

        # 3. 게이트 로그 기록 (제안서 기능)
        # "관리자 승인 대기" 상태로 로그를 남깁니다.
        query_insert_log = sql.SQL(
            """
            INSERT INTO GateLog (ReservationID, GateID, Action)
            VALUES (%s, %s, 'Entry')
            RETURNING LogID, Timestamp
            """
        )
        cur.execute(query_insert_log, (reservation_id, gate_id))
        new_log = cur.fetchone()
        
        # 4. 예약 상태를 'InUse'(이용중)로 변경
        cur.execute(
            sql.SQL('UPDATE Reservation SET Status = %s WHERE ReservationID = %s'),
            ('InUse', reservation_id)
        )

        conn.commit()

        return jsonify(
            message="입차 요청이 기록되었습니다. 관리자 승인을 대기합니다.",
            log_id=new_log['logid'],
            reservation_id=reservation_id,
            timestamp=new_log['timestamp']
        ), 200

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        # 'Pending' 예약을 못 찾았는데 'InUse' 예약을 찾은 경우 (중복 요청)
        if e.pgcode == '23514': # (만약 Status에 CHECK 제약이 있다면)
             return jsonify(error="이미 입차 요청(InUse) 상태입니다."), 409
        if e.pgcode == '23503': # FK 오류
            return jsonify(error="존재하지 않는 GateID입니다."), 404
            
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# =========================================================
# 11. 관리자: 입차 승인 (POST /approve-entry)
# =========================================================
@app.route('/approve-entry', methods=['POST'])
def approve_entry():
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        reservation_id = data['ReservationID'] # 승인할 예약 ID
        gate_id = data['GateID']               # 개방할 게이트 ID
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. 'ReservationID'와 'GateID'가 필요합니다.", details=str(e)), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. 승인할 예약이 'InUse' 상태인지 확인
        query_check_reservation = sql.SQL(
            "SELECT Status FROM Reservation WHERE ReservationID = %s"
        )
        cur.execute(query_check_reservation, (reservation_id,))
        reservation = cur.fetchone()

        if not reservation:
            return jsonify(error="존재하지 않는 ReservationID입니다."), 404
        
        if reservation['status'] != 'InUse':
            return jsonify(error="승인 대상 예약이 'InUse'(이용중/입차대기) 상태가 아닙니다.", details=f"현재 상태: {reservation['status']}"), 400

        # 3. 게이트 상태를 'Open'으로 변경
        query_open_gate = sql.SQL(
            """
            UPDATE Gate SET Status = 'Open' WHERE GateID = %s
            RETURNING GateID, GateName, Status
            """
        )
        cur.execute(query_open_gate, (gate_id,))
        opened_gate = cur.fetchone()

        if not opened_gate:
            return jsonify(error="존재하지 않는 GateID입니다."), 404

        conn.commit()


        return jsonify(
            message="입차가 승인되었습니다. 게이트를 개방합니다.",
            opened_gate=opened_gate
        ), 200

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        if e.pgcode == '23503': # FK 오류
            return jsonify(error="존재하지 않는 ReservationID 또는 GateID입니다."), 404
            
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# =========================================================
# 12. 방문자: 출차 요청 (POST /exit-request)
# =========================================================
@app.route('/exit-request', methods=['POST'])
def request_exit():
    """
    방문자가 이용을 마치고 출차를 요청합니다.
    (제안서 기능: 'Completed'로 상태 변경 및 로그 기록)
    """
    
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        user_id = data['UserID']
        gate_id = data['GateID'] # 어느 게이트로 나가는지
    except Exception as e:
        return jsonify(error="잘못된 요청 데이터입니다. 'UserID'와 'GateID'가 필요합니다.", details=str(e)), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. 'InUse' 상태인 본인의 예약 확인
        # (출차는 아무 때나 할 수 있어야 하므로 시간(NOW()) 검사는 제외)
        query_find_reservation = sql.SQL(
            """
            SELECT ReservationID, Status
            FROM Reservation
            WHERE VisitorID = %s
              AND Status = 'InUse'
            LIMIT 1;
            """
        )
        cur.execute(query_find_reservation, (user_id,))
        valid_reservation = cur.fetchone()

        if not valid_reservation:
            return jsonify(error="출차할 수 있는 '이용중(InUse)' 상태의 예약이 없습니다."), 404
        
        reservation_id = valid_reservation['reservationid']

        # 3. 게이트 로그 기록 ('Exit')
        query_insert_log = sql.SQL(
            """
            INSERT INTO GateLog (ReservationID, GateID, Action)
            VALUES (%s, %s, 'Exit')
            RETURNING LogID, Timestamp
            """
        )
        cur.execute(query_insert_log, (reservation_id, gate_id))
        new_log = cur.fetchone()
        
        # 4. 예약 상태를 'Completed'(완료)로 변경
        cur.execute(
            sql.SQL('UPDATE Reservation SET Status = %s WHERE ReservationID = %s'),
            ('Completed', reservation_id)
        )

        conn.commit()

        return jsonify(
            message="출차가 기록되었습니다. 이용해 주셔서 감사합니다.",
            log_id=new_log['logid'],
            reservation_id=reservation_id,
            timestamp=new_log['timestamp']
        ), 200

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        if e.pgcode == '23503': # FK 오류
            return jsonify(error="존재하지 않는 GateID입니다."), 404
            
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# =============================================================
# 13. 방문자: 예약 취소 (DELETE /reservation/<int:reservation_id>)
# =============================================================
@app.route('/reservation/<int:reservation_id>', methods=['DELETE'])
def cancel_reservation(reservation_id):
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. 예약을 'Canceled' 상태로 변경
        query_update = sql.SQL(
            """
            UPDATE Reservation
            SET Status = 'Canceled'
            WHERE ReservationID = %s
              AND Status = 'Pending' -- 'Pending' 상태인 것만 취소 가능
            RETURNING ReservationID, Status;
            """
        )
        
        cur.execute(query_update, (reservation_id,))
        
        canceled_reservation = cur.fetchone()
        
        # 2. 취소 성공 여부 확인
        if not canceled_reservation:
            # 2-1. (확인) 애초에 ID가 없는지?
            cur.execute(sql.SQL("SELECT Status FROM Reservation WHERE ReservationID = %s"), (reservation_id,))
            existing_reservation = cur.fetchone()
            
            if not existing_reservation:
                return jsonify(error="존재하지 않는 ReservationID입니다."), 404
            
            # 2-2. ID는 있으나 'Pending' 상태가 아님
            return jsonify(
                error="예약을 취소할 수 없습니다. 'Pending'(대기) 상태의 예약만 취소 가능합니다.",
                current_status=existing_reservation['status']
            ), 400 # 400: Bad Request

        conn.commit()

        return jsonify(
            message="예약이 성공적으로 취소되었습니다.",
            canceled_reservation=canceled_reservation
        ), 200

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        return jsonify(error="데이터베이스 오류가 발생했습니다.", details=str(e), pgcode=e.pgcode), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# --- Flask 앱 실행 ---
if __name__ == '__main__':
    app.run(debug=True)
