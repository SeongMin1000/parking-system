import os
import re
import psycopg2
import bcrypt
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()

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

def setup_database():
    """
    데이터베이스 초기 샘플 데이터 설정
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # --- 1. 주차 구역 생성 (중복 방지) ---
        print("1. 주차 구역(ParkingZone) 생성 중...")
        # 트리거 로직('a-1' 파싱)과 UI 가독성을 위해 'A구역' 형태로 저장하는 것을 권장하지만,
        # 트리거가 'A'와 'A구역' 모두 처리하므로 'A'로 저장해도 무방합니다.
        # 여기서는 명확하게 'A', 'B'... 로 저장합니다.
        canonical_zones = ['A', 'B', 'C', 'D']
        
        zone_map = {} # { 'A': 1, 'B': 2 ... }

        for c_zone in canonical_zones:
            # 구역 이름으로 ID 조회
            cur.execute("SELECT ZoneID FROM ParkingZone WHERE ZoneName = %s", (c_zone,))
            row = cur.fetchone()
            
            if row:
                zone_id = row[0]
                print(f"   - '{c_zone}' 구역은 이미 존재합니다 (ID: {zone_id}).")
            else:
                # 없으면 생성
                cur.execute(
                    "INSERT INTO ParkingZone (ZoneName, Status) VALUES (%s, 'Available') RETURNING ZoneID", 
                    (c_zone,)
                )
                zone_id = cur.fetchone()[0]
                print(f"   - '{c_zone}' 구역 신규 생성 완료 (ID: {zone_id}).")
            
            zone_map[c_zone] = zone_id
        
        conn.commit()
        print("   => 주차 구역 설정 완료.\n")

        # --- 2. 주차 공간 생성 (수정된 안전한 로직) ---
        print(f"2. 주차 공간(ParkingSpace) 10개씩 체크 및 생성 중...")
        
        for c_zone in canonical_zones:
            zone_id = zone_map[c_zone]
            created_count = 0
            
            # [수정] 단순 개수(COUNT)가 아니라 1~10번 자리가 각각 있는지 확인합니다.
            for space_num in range(1, 11): # 1부터 10까지 반복
                cur.execute(
                    "SELECT 1 FROM ParkingSpace WHERE ZoneID = %s AND SpaceNumber = %s",
                    (zone_id, space_num)
                )
                if cur.fetchone() is None:
                    # 해당 번호가 없으면 생성
                    cur.execute(
                        "INSERT INTO ParkingSpace (ZoneID, SpaceNumber, OwnerVehicleID) VALUES (%s, %s, NULL)", 
                        (zone_id, space_num)
                    )
                    created_count += 1
            
            if created_count > 0:
                print(f"   - '{c_zone}' 구역(ID: {zone_id}): {created_count}개의 공간을 추가 생성했습니다.")
            else:
                print(f"   - '{c_zone}' 구역(ID: {zone_id}): 모든 공간(1~10번)이 이미 존재합니다.")
        
        conn.commit()
        print("   => 주차 공간 설정 완료.\n")

        # --- 3. 게이트 생성 (중복 방지) ---
        gates = [
            {'name': '정문 게이트', 'type': 'Entry'},
            {'name': '후문 게이트', 'type': 'Exit'}
        ]
        print("3. 게이트(Gate) 생성 중...")
        for gate in gates:
            cur.execute("SELECT GateID FROM Gate WHERE GateName = %s", (gate['name'],))
            if cur.fetchone() is None:
                cur.execute("INSERT INTO Gate (GateName, GateType, Status) VALUES (%s, %s, 'Closed')", (gate['name'], gate['type']))
                print(f"   - '{gate['name']}' ({gate['type']}) 생성 완료.")
            else:
                print(f"   - '{gate['name']}' 게이트는 이미 존재합니다.")

        conn.commit()
        print("   => 게이트 설정 완료.\n")
        
        # --- 4. 관리자 계정 생성 ---
        admin_id = 'admin'
        cur.execute('SELECT VehicleID FROM "User" WHERE VehicleID = %s', (admin_id,))
        if cur.fetchone() is None:
            hashed = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            # DDL상 Contact, Building은 Nullable이므로 생략 가능
            cur.execute(
                'INSERT INTO "User" (VehicleID, Password, Name, Role) VALUES (%s, %s, %s, %s)',
                (admin_id, hashed, '시스템관리자', 'Admin')
            )
            print(f"   - 관리자 계정({admin_id}) 생성 완료.")
        else:
            print(f"   - 관리자 계정({admin_id})은 이미 존재합니다.")
            
        conn.commit()
        print("모든 초기 데이터 설정이 성공적으로 완료되었습니다.")

    except psycopg2.Error as e:
        print(f"데이터베이스 오류가 발생했습니다: {e}")
        if conn:
            conn.rollback()
    except Exception as e:
        print(f"알 수 없는 오류가 발생했습니다: {e}")
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == '__main__':
    print("="*50)
    print("주차장 관리 시스템 데이터베이스 초기 설정 스크립트")
    print("="*50)
    setup_database()
