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
    """사용자 회원가입을 처리합니다."""
    
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
    """사용자 로그인을 처리합니다."""
    
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
    """
    입주민이 자신의 주차 공간에 대한 비움 시간(공유)을 등록합니다.
    (인증 생략: JSON 본문에 UserID를 포함한다고 가정)
    """
    
    # 1. 클라이언트로부터 JSON 데이터 받기
    try:
        data = request.get_json()
        user_id = data['UserID'] # (인증 대신) 요청자가 누구인지
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

        # 2. (중요) 본인 소유 공간 검증
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

# --- (기존 create_share_schedule 함수 코드) ---

# ====================================================
# 7. 방문자: 예약 가능한 공간 조회 (GET /spaces)
# ====================================================
@app.route('/spaces', methods=['GET'])
def get_available_spaces():
    """
    현재 예약 가능한 모든 공유 공간 목록을 조회합니다.
    (현재 시간 이후, 'Available' 구역만)
    """
    
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

        # (참고)
        # 이 기본 조회 결과에 더해, "이미 예약이 꽉 찼는지"를 확인하는
        # 서브쿼리를 추가하면 더 완벽한 API가 될 수 있습니다.
        # (지금은 1단계로 기본 조회만 구현)

        return jsonify(available_spaces=spaces), 200

    except Exception as e:
        # 이 API는 SELECT만 하므로 DB 오류 외에는 특별히 처리할 pgcode가 적습니다.
        return jsonify(error="서버 내부 오류가 발생했습니다.", details=str(e)), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# --- (기존 Flask 앱 실행 코드) ---
# ... (이하 동일)

# --- Flask 앱 실행 ---
if __name__ == '__main__':
    app.run(debug=True)
