import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import time
from flask import Flask, request, Response
from concurrent.futures import ThreadPoolExecutor
import threading
import os

app = Flask(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept-Language': 'ja',
    'Cache-Control': 'no-cache'
}

VENUE_MAP = {
    '03': '帯広', '10': '盛岡', '11': '水沢', '18': '浦和', '19': '船橋',
    '20': '大井', '21': '川崎', '22': '金沢', '23': '笠松', '24': '名古屋',
    '25': '姫路', '27': '園田', '29': '高知', '30': '佐賀', '31': '高知',
    '32': '佐賀', '36': '門別'
}

cache = {}
cache_lock = threading.Lock()
today_data = None  # 起動時に取得した当日データ

def get_venues_today(date_yyyymmdd):
    """当日向け FAST版: nankankeibaトップから base_code を一発取得"""
    try:
        r = requests.get("https://www.nankankeiba.com/", headers=HEADERS, timeout=10)
        r.encoding = 'shift_jis'
        codes = sorted(set(re.findall(r'program/(\d{14})\.do', r.text)))

        venues = []
        seen = set()
        for base_code in codes:
            if not base_code.startswith(date_yyyymmdd):
                continue
            venue_code = base_code[8:10]
            if venue_code not in seen and venue_code in VENUE_MAP:
                seen.add(venue_code)
                venues.append({
                    'code': venue_code,
                    'name': VENUE_MAP[venue_code],
                    'base_code': base_code
                })
                print(f"    \u2713 {VENUE_MAP[venue_code]}: {base_code}")
        return venues
    except Exception as e:
        print(f"  \u2717 \u30a8\u30e9\u30fc: {e}")
        return []


def get_baba_codes_from_keibago(date_str):
    """keiba.go.jp から指定日の開催場コードを取得"""
    url = "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/TodayRaceInfoTop"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        date_slash = date_str.replace('-', '/')
        codes = set()
        for link in soup.find_all('a', href=lambda x: x and 'babaCode' in x):
            href = link.get('href', '')
            href_dec = href.replace('%2F', '/')
            if f'k_raceDate={date_slash}' in href_dec and 'k_babaCode=' in href:
                code = href.split('k_babaCode=')[1][:2]
                codes.add(code)
        return sorted(codes)
    except Exception as e:
        print(f"  \u2717 keiba.go.jp \u30a8\u30e9\u30fc: {e}")
        return []


def find_base_code(date_yyyymmdd, baba_code):
    """回次・日次を並列総当たりして正しい base_code を特定"""
    def check(args):
        kai, nichi = args
        base = f"{date_yyyymmdd}{baba_code}{kai:02d}{nichi:02d}"
        url = f"https://www.nankankeiba.com/program/{base}.do"
        try:
            r = requests.get(url, headers=HEADERS, timeout=5)
            if r.status_code == 200 and 'nk23_c-block01__label' in r.text and date_yyyymmdd in r.text:
                return base
        except:
            pass
        return None

    candidates = [(kai, nichi) for kai in range(1, 8) for nichi in range(1, 16)]
    with ThreadPoolExecutor(max_workers=30) as ex:
        for res in ex.map(check, candidates):
            if res:
                return res
    return None


def get_venues_other_day(date_str):
    """当日以外向け: keiba.go.jpで場コード取得 → 日次総当たりで base_code 特定"""
    date_yyyymmdd = date_str.replace('-', '')
    baba_codes = get_baba_codes_from_keibago(date_str)

    if not baba_codes:
        print(f"  \u2717 \u958b\u50ac\u5834\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093")
        return []

    print(f"  \u958b\u50ac\u5834\u30b3\u30fc\u30c9: {baba_codes}")

    def resolve(baba):
        base = find_base_code(date_yyyymmdd, baba)
        return baba, base

    venues = []
    with ThreadPoolExecutor(max_workers=max(len(baba_codes), 1)) as ex:
        for baba, base in ex.map(resolve, baba_codes):
            if base and baba in VENUE_MAP:
                venues.append({
                    'code': baba,
                    'name': VENUE_MAP[baba],
                    'base_code': base
                })
                print(f"    \u2713 {VENUE_MAP[baba]}: {base}")
            else:
                print(f"    \u2717 \u5834{baba}: base_code\u7279\u5b9a\u5931\u6557")
    return venues


def get_race_times(baba_code, date_str):
    """発走時刻を取得"""
    encoded_date = date_str.replace('-', '%2f')
    url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_raceDate={encoded_date}&k_babaCode={baba_code}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        
        times = {}
        table = soup.find('table')
        if table:
            rows = table.find_all('tr', class_='data')
            for row in rows:
                tds = row.find_all('td')
                if len(tds) >= 2:
                    race_no_text = tds[0].get_text(strip=True)
                    time_text = tds[1].get_text(strip=True)
                    
                    if race_no_text and time_text:
                        try:
                            race_no = int(race_no_text.replace('R', ''))
                            times[race_no] = time_text
                        except:
                            pass
        return times
    except:
        return {}

def get_sales(base_code, race_no):
    """売上を取得"""
    url = f"https://www.nankankeiba.com/odds/{base_code}{race_no:02d}01.do"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.encoding = 'shift_jis'
        
        if response.status_code == 404:
            return None
        
        pattern = r'総票数合計.*?<td[^>]*>([0-9,]+)'
        match = re.search(pattern, response.text, re.DOTALL)
        if match:
            return int(match.group(1).replace(',', ''))
        
        return None
    except:
        return None

def round_time_to_30min(time_str):
    """発走時刻を30分刻みに丸める"""
    try:
        hour, minute = map(int, time_str.split(':'))
        rounded_minute = 30 if minute >= 15 else 0
        return f"{hour:02d}:{rounded_minute:02d}"
    except:
        return time_str

def fetch_sales_batch(args):
    """複数の売上をバッチ取得（並列用）"""
    base_code, race_no = args
    return race_no, get_sales(base_code, race_no)

def fetch_all_data(date_str):
    """全データを取得"""
    print(f"[データ取得開始] {date_str}")
    
    # 今日の日付と比較
    today = datetime.now().strftime('%Y-%m-%d')
    
    if date_str == today or date_str > today:
        print(f"  [当日向け FAST版]")
        venues = get_venues_today(date_str.replace('-', ''))
    else:
        print(f"  [当日以外向け]")
        venues = get_venues_other_day(date_str)
    print(f"[取得場数] {len(venues)} 場")
    
    if len(venues) == 0:
        print("  ⚠️ 開催なし")
        return {}
    
    all_data = {}
    
    for venue in venues:
        name = venue['name']
        base_code = venue['base_code']
        code = venue['code']
        
        times = get_race_times(code, date_str)
        
        if not times:
            print(f"  {name}: レースなし")
            all_data[name] = {}
            continue
        
        races = {}
        
        sales_args = [(base_code, race_no) for race_no in range(1, 13) if race_no in times]
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(fetch_sales_batch, arg) for arg in sales_args]
            
            for future in futures:
                race_no, sales = future.result()
                
                if race_no in times:
                    original_time = times[race_no]
                    rounded_time = round_time_to_30min(original_time)
                    
                    races[race_no] = {
                        'rounded_time': rounded_time,
                        'time': original_time,
                        'sales': sales,
                        'base_code': base_code
                    }
        
        all_data[name] = races
    
    return all_data

def generate_html(data, date_str):
    """HTMLを生成"""
    totals = {}
    for venue, races in data.items():
        total = sum(race['sales'] for race in races.values() if race['sales'])
        totals[venue] = total
    
    times = ['10:00', '10:30', '11:00', '11:30', '12:00', '12:30', '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', '16:00', '16:30', '17:00', '17:30', '18:00', '18:30', '19:00', '19:30', '20:00', '20:30', '21:00']
    venues_list = sorted(data.keys())
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    date_display = date_obj.strftime('%Y年%m月%d日')
    
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>競馬発売状況</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif, 'ヒラギノ角ゴ Pro';
            background: #f8f8f8;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        h1 {{
            font-size: 24px;
            margin-bottom: 10px;
        }}
        .info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .date-selector {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .date-selector input {{
            padding: 8px 12px;
            font-size: 14px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }}
        .date-selector button {{
            padding: 8px 16px;
            background: #222;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }}
        .date-selector button:hover {{
            background: #444;
        }}
        #dateDisplay {{
            font-size: 14px;
            color: #666;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
            font-size: 14px;
        }}
        thead {{
            position: sticky;
            top: 0;
            z-index: 11;
        }}
        th {{
            background: #222;
            color: white;
            padding: 12px 10px;
            text-align: center;
            font-weight: 600;
            border: 1px solid #ddd;
            font-size: 14px;
        }}
        td {{
            padding: 0;
            border: 1px solid #f0f0f0;
            text-align: center;
        }}
        td.time-col {{
            background: #f9f9f9;
            font-weight: 600;
            width: 80px;
            font-size: 16px;
            padding: 10px;
        }}
        .race-cell {{
            display: flex;
            flex-direction: column;
            min-height: 50px;
        }}
        .race-item {{
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 10px;
            border-bottom: 1px solid #f0f0f0;
        }}
        .race-item:last-child {{
            border-bottom: none;
        }}
        .race-info {{
            font-size: 13px;
            line-height: 1.6;
        }}
        .race-no {{
            font-weight: 600;
            font-size: 15px;
        }}
        .race-time {{
            color: #0066cc;
            font-size: 14px;
            font-weight: 600;
        }}
        .sales {{
            font-weight: 600;
            color: #222;
            font-family: 'Monaco', monospace;
            font-size: 14px;
        }}
        .sales a {{
            color: #222;
            text-decoration: none;
        }}
        .sales a:hover {{
            color: #0066cc;
            text-decoration: underline;
        }}
        tr:hover {{
            background: #f5f5f5;
        }}
        .total-row {{
            background: #fffbf0;
            font-weight: bold;
            border-top: 2px solid #ff9800;
            font-size: 14px;
            position: sticky;
            bottom: 0;
            z-index: 10;
        }}
        .total-row td {{
            border-top: 2px solid #ff9800;
            padding: 10px;
        }}
        button.csv-btn {{
            padding: 12px 24px;
            background: #222;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-bottom: 20px;
            font-size: 14px;
            font-weight: 600;
        }}
        button.csv-btn:hover {{
            background: #444;
        }}
        .timestamp {{
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>競馬発売状況（時刻表）</h1>
        
        <div class="info">
            <div class="date-selector">
                <input type="date" id="dateInput" value="{date_str}">
                <button onclick="goToDate()">検索</button>
                <button onclick="goToToday()">今日</button>
            </div>
        </div>

        <div id="dateDisplay">日付: {date_display}</div>
        <div class="timestamp">更新: {datetime.now().strftime('%H:%M:%S')}</div>
        
        <button class="csv-btn" onclick="saveDataCSV()">保存（CSV）</button>

        <table id="dataTable">
            <thead>
                <tr>
                    <th>時刻</th>
"""
    
    for venue in venues_list:
        html += f"                    <th>{venue}</th>\n"
    
    html += """                </tr>
            </thead>
            <tbody>
"""
    
    for time_slot in times:
        html += f"                <tr>\n                    <td class=\"time-col\">{time_slot}</td>\n"
        for venue in venues_list:
            html += "                    <td>\n                        <div class=\"race-cell\">\n"
            
            if venue in data:
                races_in_slot = []
                for race_no in sorted(data[venue].keys()):
                    race_data = data[venue][race_no]
                    if race_data['rounded_time'] == time_slot:
                        races_in_slot.append((race_no, race_data))
                
                if races_in_slot:
                    for race_no, race_data in races_in_slot:
                        html += """                            <div class="race-item">
                                <div class="race-info">
"""
                        html += f"                                    <div class=\"race-no\">{race_no}R</div>\n"
                        html += f"                                    <div class=\"race-time\">{race_data['time']}</div>\n"
                        html += "                                    <div class=\"sales\">\n"
                        
                        if race_data['sales']:
                            url = f"https://www.nankankeiba.com/odds/{race_data['base_code']}{race_no:02d}01.do"
                            html += f'                                        <a href="{url}" target="_blank">{race_data["sales"]:,}</a>\n'
                        else:
                            html += "                                        -\n"
                        
                        html += """                                    </div>
                                </div>
                            </div>
"""
                else:
                    html += """                            <div class="race-item">
                                <div>-</div>
                            </div>
"""
            else:
                html += """                            <div class="race-item">
                                <div>-</div>
                            </div>
"""
            
            html += "                        </div>\n                    </td>\n"
        html += "                </tr>\n"
    
    html += """                <tr class="total-row">
                    <td class="time-col">合計</td>
"""
    
    for venue in venues_list:
        total = totals.get(venue, 0)
        html += f"                    <td>{total:,}</td>\n" if total > 0 else "                    <td>-</td>\n"
    
    html += """                </tr>
            </tbody>
        </table>
    </div>

    <script>
        const dateInput = document.getElementById('dateInput');
        dateInput.value = '""" + date_str + """';

        function goToDate() {
            const selectedDate = dateInput.value;
            if (selectedDate) {
                const timestamp = new Date().getTime();
                window.location.href = '/?date=' + selectedDate + '&t=' + timestamp;
            }
        }

        function goToToday() {
            const today = new Date();
            dateInput.value = today.toISOString().split('T')[0];
            goToDate();
        }

        function saveDataCSV() {
            const table = document.getElementById('dataTable');
            const rows = table.querySelectorAll('tr');
            const csv = [];
            
            rows.forEach(row => {
                const cells = row.querySelectorAll('td, th');
                const rowData = [];
                cells.forEach(cell => {
                    rowData.push('"' + cell.innerText.replace(/"/g, '""') + '"');
                });
                csv.push(rowData.join(','));
            });
            
            const csvContent = csv.join('\\n');
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            const url = URL.createObjectURL(blob);
            
            const selectedDate = dateInput.value.replace(/-/g, '');
            const now = new Date();
            const filename = `競馬発売_${selectedDate}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}.csv`;
            
            link.setAttribute('href', url);
            link.setAttribute('download', filename);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    </script>
</body>
</html>
"""
    
    return html

@app.route('/')
def index():
    """メインページ"""
    global today_data
    
    date_str = request.args.get('date')
    
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 当日はキャッシュ（起動時先読み or 遅延ロード）を使う
    if date_str == today:
        data = ensure_today_data()
        print(f"[当日データ] {date_str}")
        html_content = generate_html(data, date_str)
        
        response = Response(html_content, mimetype='text/html')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    
    # 過去日付はキャッシュまたは新規取得
    with cache_lock:
        if date_str in cache:
            cached_time, html_content = cache[date_str]
            if (datetime.now() - cached_time).total_seconds() < 300:
                print(f"[キャッシュ] {date_str}")
                response = Response(html_content, mimetype='text/html')
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                return response
    
    print(f"\n[リクエスト] {date_str}")
    data = fetch_all_data(date_str)
    html_content = generate_html(data, date_str)
    
    with cache_lock:
        cache[date_str] = (datetime.now(), html_content)
    
    response = Response(html_content, mimetype='text/html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

def ensure_today_data():
    """当日データを必要時に取得（lazy load・スレッドセーフ）"""
    global today_data
    if today_data is None:
        with cache_lock:
            if today_data is None:  # ダブルチェック
                today_str = datetime.now().strftime('%Y-%m-%d')
                print(f"[遅延ロード] 当日データ取得: {today_str}")
                today_data = fetch_all_data(today_str)
    return today_data


@app.route('/health')
def health():
    """ヘルスチェック用（即200を返す・スクレイピングしない）"""
    return Response('OK', mimetype='text/plain')


if __name__ == '__main__':
    print("="*60)
    print("[初期化] 当日分のデータを先に取得中...")
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_data = fetch_all_data(today_str)
    
    print("\n" + "="*60)
    print("[Flask サーバー起動]")
    print("="*60 + "\n")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
