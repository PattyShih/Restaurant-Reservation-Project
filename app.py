from flask import Flask, render_template, jsonify, request, session
import pyodbc  # <--- 改用這個
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'foodie_table_2024_pro'

# --- 資料庫連線設定 (不要直接把密碼寫死在這裡，建議用環境變數，但測試時先這樣寫) ---
# 如果你是用 Windows 驗證 (不需要帳號密碼)，Trusted_Connection=yes
# SERVER 名稱通常是 "localhost\SQLEXPRESS" 或只是 "."
DB_CONFIG = {
    'DRIVER': '{ODBC Driver 17 for SQL Server}',
    'SERVER': 'PC37',  # <--- 請改成你的 SSMS Server Name
    'DATABASE': 'RestaurantDB',
    'Trusted_Connection': 'yes',  # 如果是用帳密登入，改成 UID=sa;PWD=你的密碼;
}

def get_db_connection():
    conn_str = f"DRIVER={DB_CONFIG['DRIVER']};SERVER={DB_CONFIG['SERVER']};DATABASE={DB_CONFIG['DATABASE']};Trusted_Connection={DB_CONFIG['Trusted_Connection']};"
    conn = pyodbc.connect(conn_str)
    return conn

# 輔助函式：把 pyodbc 的查詢結果轉成 Python 字典 (Dictionary)
# 因為 pyodbc 預設回傳的是 tuple (無法用 row['name'] 取值)，所以要轉換
def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

# --- 初始化資料庫 ---
# 因為我們已經在 SSMS 建立好資料表了，這裡的 init_db 不需要再 CREATE TABLE
# 我們可以保留它用來測試連線
def init_db():
    try:
        conn = get_db_connection()
        print("SQL Server 連線成功！")
        conn.close()
    except Exception as e:
        print(f"資料庫連線失敗: {e}")

init_db()

# --- 路由區 (大部分邏輯不變，但 SQL 語法微調) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查詢現有用戶
    cursor.execute('SELECT * FROM Customers WHERE phone_number = ?', (data['phone'],))
    row = cursor.fetchone()
    
    if row:
        # pyodbc 的 row 可以透過 index 存取，或我們手動轉 dict
        user_id = row.customer_id # pyodbc 支援物件屬性存取
        cursor.execute('UPDATE Customers SET name = ? WHERE customer_id = ?', (data['name'], user_id))
    else:
        # SQL Server 取得剛插入的 ID 語法不同 (OUTPUT inserted.ID 或 @@IDENTITY)
        cursor.execute('INSERT INTO Customers (name, phone_number) VALUES (?, ?)', (data['name'], data['phone']))
        cursor.execute('SELECT @@IDENTITY AS id') # 取得剛才新增的 ID
        user_id = int(cursor.fetchone()[0])
        
    conn.commit()
    conn.close()
    session['user_id'] = user_id
    session['user_name'] = data['name']
    return jsonify({'status': 'success', 'user_name': data['name']})

@app.route('/api/restaurants')
def get_restaurants():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Restaurants')
    
    # 將結果轉為 List of Dicts
    columns = [column[0] for column in cursor.description]
    results = []
    for row in cursor.fetchall():
        results.append(dict(zip(columns, row)))
        
    conn.close()
    return jsonify(results)

@app.route('/api/time-slots')
def get_time_slots():
    restaurant_id = request.args.get('restaurant_id')
    date_str = request.args.get('date')
    if not restaurant_id or not date_str: return jsonify([])

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM Restaurants WHERE restaurant_id = ?', (restaurant_id,))
    row = cursor.fetchone()
    if not row: return jsonify([])
    
    # 存取欄位方式改為屬性存取
    total_tables = row.total_tables
    open_time = row.open_time
    close_time = row.close_time
    
    try:
        open_h = int(open_time.split(':')[0])
        close_h = int(close_time.split(':')[0])
        if close_h < open_h: close_h += 24
    except:
        open_h, close_h = 11, 22

    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    current_hour = now.hour

    slots = []
    for hour in range(open_h, close_h):
        display_hour = hour % 24
        if date_str == today_str and display_hour <= current_hour: continue
        if date_str < today_str: continue

        time_str = f"{display_hour:02d}:00"
        
        # 查詢已訂位數量
        cursor.execute('''
            SELECT count(*) FROM Reservations 
            WHERE restaurant_id = ? AND reservation_date = ? AND reservation_time = ? AND status != 'Cancelled'
        ''', (restaurant_id, date_str, time_str))
        
        booked_count = cursor.fetchone()[0]
        is_full = (booked_count >= total_tables)
        slots.append({'time': time_str, 'is_full': is_full})
        
    conn.close()
    return jsonify(slots)

@app.route('/api/reserve', methods=['POST'])
def make_reservation():
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': '請先登入'}), 401
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # SQL Server 的 Insert 語法
    cursor.execute('''
        INSERT INTO Reservations (customer_id, restaurant_id, reservation_date, reservation_time, party_size, status)
        VALUES (?, ?, ?, ?, ?, 'Confirmed')
    ''', (session['user_id'], data['restaurant_id'], data['date'], data['time'], data['party_size']))
    
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': '訂位成功！'})

@app.route('/api/my-reservations')
def my_reservations():
    if 'user_id' not in session: return jsonify([])
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 注意 SQL JOIN 語法與 table 名稱要對應新的 schema (Customers, Restaurants)
    cursor.execute('''
        SELECT r.record_id, res.name as restaurant_name, r.reservation_date, r.reservation_time, r.party_size, r.status
        FROM Reservations r
        JOIN Restaurants res ON r.restaurant_id = res.restaurant_id
        WHERE r.customer_id = ? ORDER BY r.record_id DESC
    ''', (session['user_id'],))
    
    columns = [column[0] for column in cursor.description]
    results = []
    for row in cursor.fetchall():
        # 因為日期在 SQL Server 拿出來是 datetime 物件，轉成字串才不會讓 JSON 報錯
        row_dict = dict(zip(columns, row))
        row_dict['reservation_date'] = str(row_dict['reservation_date']) 
        results.append(row_dict)
        
    conn.close()
    return jsonify(results)

@app.route('/api/cancel', methods=['POST'])
def cancel_reservation():
    if 'user_id' not in session: return jsonify({'status': 'error'}), 401
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE Reservations SET status = 'Cancelled' WHERE record_id = ?", (data['record_id'],))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': '訂位已取消'})

@app.route('/admin')
def admin():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查詢 Customers
    cursor.execute('SELECT * FROM Customers')
    custs = cursor.fetchall()
    
    # 查詢 Restaurants
    cursor.execute('SELECT * FROM Restaurants')
    rests = cursor.fetchall()
    
    # 查詢 Reservations
    cursor.execute('SELECT * FROM Reservations')
    resvs = cursor.fetchall()
    
    conn.close()
    return render_template('admin.html', customers=custs, restaurants=rests, reservations=resvs)

if __name__ == '__main__':
    app.run(debug=True, port=5000)