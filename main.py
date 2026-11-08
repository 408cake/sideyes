import io
import json
import os
import sys
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
import zipfile
from dotenv import load_dotenv
import requests

# --- 실행 환경(일반 파이썬 vs PyInstaller 배포판)에 맞춘 기준 디렉터리 설정 ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
API_LOCK = threading.Lock()

BASE_URL = "https://openapivts.koreainvestment.com:29443"

TOKEN_FILE = os.path.join(BASE_DIR, "kis_token.json")
MASTER_FILE = os.path.join(BASE_DIR, "stock_master.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

COLOR_UP = "#D32F2F"      # 상승 (빨강)
COLOR_DOWN = "#1976D2"    # 하락 (파랑)
COLOR_EQUAL = "#000080"   # 보합/기본 (클래식 네이비)

CODE_TO_NAME = {
    "AAPL": "애플", "TSLA": "테슬라", "NVDA": "엔비디아",
    "MSFT": "마이크로소프트", "GOOGL": "구글", "AMZN": "아마존", "SOXL": "SOXL"
}
NAME_TO_CODE = {v: k for k, v in CODE_TO_NAME.items()}


def load_user_config():
    """저장된 종목 목록([code, name]) 및 창 위치 로드"""
    default_config = {
        "items": [
            {"code": "005930", "name": "삼성전자"},
            {"code": "AAPL", "name": "애플"},
            {"code": "TSLA", "name": "테슬라"}
        ],
        "geometry": "+150+150"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "tickers" in data and "items" not in data:
                    items = []
                    for c in data["tickers"]:
                        items.append({"code": c, "name": CODE_TO_NAME.get(c, c).strip()})
                    data["items"] = items
                return data
        except Exception as e:
            print(f"설정 로드 실패: {e}")
    return default_config


def save_user_config(items, geometry):
    """현재 종목 목록과 창 위치를 JSON에 영구 저장"""
    data = {
        "items": items,
        "geometry": geometry
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"설정 저장 실패: {e}")


def is_korean_stock_code(s: str) -> bool:
    return s.isdigit() and len(s) == 6


def is_english_ticker(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]+", s))


def init_stock_master():
    """한투 마스터 테이블 로컬 로드 또는 바이트 슬라이싱으로 파싱 후 저장"""
    global CODE_TO_NAME, NAME_TO_CODE

    if os.path.exists(MASTER_FILE):
        try:
            with open(MASTER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw_map = data.get("code_to_name", {})
                for k, v in raw_map.items():
                    c = k.strip()
                    n = v.strip()
                    CODE_TO_NAME[c] = n
                    NAME_TO_CODE[n] = c
                return
        except Exception:
            pass

    print("한투 공식 종목 마스터 테이블을 구성하는 중입니다...")
    markets = [
        ("kospi_code.mst", "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"),
        ("kosdaq_code.mst", "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip")
    ]

    downloaded_map = {}
    for fname, url in markets:
        try:
            res = requests.get(url, timeout=10)
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                with z.open(fname) as f:
                    for line_bytes in f:
                        code_bytes = line_bytes[0:9].strip()
                        code_str = code_bytes.decode("cp949", errors="ignore").strip()

                        if len(code_str) >= 6:
                            short_code = code_str[-6:]
                            name_bytes = line_bytes[21:61]
                            kor_name = name_bytes.decode("cp949", errors="ignore").strip()

                            if short_code.isdigit() and kor_name:
                                downloaded_map[short_code] = kor_name
        except Exception as e:
            print(f"마스터 로드 실패({fname}):", e)

    CODE_TO_NAME.update(downloaded_map)
    for c, n in downloaded_map.items():
        NAME_TO_CODE[n] = c

    try:
        with open(MASTER_FILE, "w", encoding="utf-8") as f:
            json.dump({"code_to_name": CODE_TO_NAME}, f, ensure_ascii=False, indent=2)
        print(f"마스터 캐싱 완료 (총 {len(CODE_TO_NAME)}개 종목)")
    except Exception:
        pass


def search_stock(query: str):
    q = query.strip()
    q_upper = q.upper()

    if is_korean_stock_code(q):
        name = CODE_TO_NAME.get(q, q).strip()
        return q, name

    for name, code in NAME_TO_CODE.items():
        if name.upper() == q_upper:
            return code, name

    if is_english_ticker(q):
        ticker = q_upper
        name = CODE_TO_NAME.get(ticker, ticker).strip()
        return ticker, name

    return None, None


def get_kis_access_token():
    now = time.time()
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if now < data.get("expired_at", 0) and data.get("access_token"):
                    return data.get("access_token")
        except Exception:
            pass

    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    try:
        res = requests.post(url, json=body, timeout=5).json()
        if "access_token" in res:
            token = res["access_token"]
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"access_token": token, "expired_at": now + 82800}, f)
            return token
        print("토큰 발급 실패:", res)
    except Exception as e:
        print("토큰 통신 오류:", e)
    return None


def fetch_domestic_price(ticker: str, token: str):
    """국내 주식 조회 (성공여부, 종목명, 시세문자열, 색상 4개 고정 반환)"""
    name = CODE_TO_NAME.get(ticker, ticker).strip()
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100",
        "custtype": "P"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker
    }

    for attempt in range(2):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=3)
            if res.status_code == 500 and attempt == 0:
                time.sleep(0.3)
                continue
            if res.status_code != 200:
                return False, name, f"HTTP {res.status_code}", COLOR_EQUAL

            data = res.json()
            rt_cd = data.get("rt_cd")
            msg_cd = data.get("msg_cd", "ERR")
            msg1 = data.get("msg1", "응답 오류")

            if rt_cd != "0":
                return False, name, f"{msg_cd}: {msg1}", COLOR_EQUAL

            output = data.get("output", {})
            price = output.get("stck_prpr")
            oprc = output.get("stck_oprc")
            sdpr = output.get("stck_sdpr")

            if price:
                color = COLOR_EQUAL
                if oprc and sdpr:
                    f_oprc, f_sdpr = float(oprc), float(sdpr)
                    if f_oprc > f_sdpr:
                        color = COLOR_UP
                    elif f_oprc < f_sdpr:
                        color = COLOR_DOWN
                return True, name, f"{int(price):,}원", color

            return False, name, "데이터 누락", COLOR_EQUAL
        except requests.exceptions.Timeout:
            return False, name, "타임아웃", COLOR_EQUAL
        except requests.exceptions.ConnectionError:
            return False, name, "네트워크 단절", COLOR_EQUAL
        except Exception as e:
            return False, name, f"오류({type(e).__name__})", COLOR_EQUAL

    return False, name, "서버 지연(500)", COLOR_EQUAL


def fetch_overseas_price(ticker: str, token: str):
    """해외 주식 조회: 전일 종가(base) 대비 현재가(last) 등락에 따라 빨강/파랑 색상 지정"""
    name = CODE_TO_NAME.get(ticker, ticker).strip()
    url = f"{BASE_URL}/uapi/overseas-price/v1/quotations/price"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "HHDFS00000300",
        "custtype": "P"
    }

    last_err = "거래소 불일치"
    for excd in ["NAS", "NYS", "AMS"]:
        params = {"AUTH": "", "EXCD": excd, "SYMB": ticker.upper()}
        try:
            res = requests.get(url, headers=headers, params=params, timeout=2.5)
            if res.status_code != 200:
                last_err = f"HTTP {res.status_code}"
                continue

            data = res.json()
            if data.get("rt_cd") != "0":
                last_err = f"{data.get('msg_cd')}: {data.get('msg1')}"
                continue

            output = data.get("output", {})
            price = output.get("last")      # 현재가 (또는 마감가)
            base = output.get("base")       # 전일 종가 (기준가)

            if price and float(price) > 0:
                color = COLOR_EQUAL
                if base:
                    f_price = float(price)
                    f_base = float(base)
                    
                    # 전일 종가 대비 현재가 비교: 상승(빨강), 하락(파랑)
                    if f_price > f_base:
                        color = COLOR_UP
                    elif f_price < f_base:
                        color = COLOR_DOWN

                return True, name, f"${float(price):,.2f}", color
        except requests.exceptions.Timeout:
            last_err = "타임아웃"
        except Exception as e:
            last_err = f"오류({type(e).__name__})"

    return False, name, last_err, COLOR_EQUAL

def fetch_stock_price(ticker: str):
    """통합 시세 조회: 무조건 (성공여부, 종목명, 가격문자열, 색상) 4개 반환 보장"""
    name = CODE_TO_NAME.get(ticker, ticker).strip()
    with API_LOCK:
        token = get_kis_access_token()
        if not token:
            return False, name, "토큰 오류", COLOR_EQUAL

        if is_korean_stock_code(ticker):
            res = fetch_domestic_price(ticker, token)
        else:
            res = fetch_overseas_price(ticker, token)

        time.sleep(0.1)
        return res


class Win98StockWidget:
    def __init__(self, root):
        self.root = root
        self.root.title("sideyes")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        self.WIN_BG = "#C0C0C0"
        self.TITLE_BG = "#000080"
        self.TITLE_FG = "#FFFFFF"
        self.TEXT_COLOR = "#000000"

        self.root.configure(bg=self.WIN_BG)
        self.label_items = {}

        self.outer_border = tk.Frame(self.root, bg=self.WIN_BG, bd=3, relief="raised")
        self.outer_border.pack(fill=tk.BOTH, expand=True)

        self.title_bar = tk.Frame(self.outer_border, bg=self.TITLE_BG, height=22)
        self.title_bar.pack(fill=tk.X, padx=2, pady=2)
        self.title_bar.pack_propagate(False)

        self.title_text = tk.Label(
            self.title_bar, text="sideyes",
            font=("굴림", 9, "bold"), fg=self.TITLE_FG, bg=self.TITLE_BG, anchor="w"
        )
        self.title_text.pack(side=tk.LEFT)

        self.close_btn = tk.Button(
            self.title_bar, text="✕", font=("굴림", 7, "bold"), width=2, height=1,
            bg=self.WIN_BG, fg="black", bd=2, relief="raised", padx=0, pady=0,
            command=self.close_app
        )
        self.close_btn.pack(side=tk.RIGHT, padx=2, pady=1)

        self._offset_x = 0
        self._offset_y = 0
        self.title_bar.bind("<ButtonPress-1>", self.start_move)
        self.title_bar.bind("<B1-Motion>", self.do_move)
        self.title_text.bind("<ButtonPress-1>", self.start_move)
        self.title_text.bind("<B1-Motion>", self.do_move)

        self.menu = tk.Menu(self.root, tearoff=0, bg=self.WIN_BG, fg=self.TEXT_COLOR, relief="raised")
        self.menu.add_command(label="➕ 종목 추가", command=self.add_ticker_dialog)
        self.menu.add_command(label="➖ 종목 삭제", command=self.remove_ticker_dialog)
        self.menu.add_separator()
        self.menu.add_command(label="❌ 종료", command=self.close_app)
        self.root.bind("<Button-3>", self.show_context_menu)
        self.outer_border.bind("<Button-3>", self.show_context_menu)

        self.config = load_user_config()
        self.items = self.config.get("items", [])
        saved_geo = self.config.get("geometry", "+150+150")
        self.root.geometry(saved_geo)

        self.content_box = tk.Frame(self.outer_border, bg=self.WIN_BG, padx=4, pady=4)
        self.content_box.pack(fill=tk.BOTH, expand=True)

        self.rebuild_ui()
        threading.Thread(target=self.worker_loop, daemon=True).start()

    def start_move(self, event):
        self._offset_x = event.x
        self._offset_y = event.y

    def do_move(self, event):
        x = self.root.winfo_x() + (event.x - self._offset_x)
        y = self.root.winfo_y() + (event.y - self._offset_y)
        self.root.geometry(f"+{x}+{y}")

    def show_context_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def save_current_state(self):
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        geo = f"+{x}+{y}"
        save_user_config(self.items, geo)

    def close_app(self):
        self.save_current_state()
        self.root.destroy()

    def rebuild_ui(self):
        for widget in self.content_box.winfo_children():
            widget.destroy()
        self.label_items.clear()

        for item in self.items:
            code = item["code"]
            name = item["name"]

            row = tk.Frame(self.content_box, bg=self.WIN_BG)
            row.pack(fill=tk.X, pady=2)

            name_lbl = tk.Label(
                row, text=name, font=("굴림", 9, "bold"),
                fg=self.TEXT_COLOR, bg=self.WIN_BG, anchor="w", width=10
            )
            name_lbl.pack(side=tk.LEFT, padx=(2, 4))

            price_box = tk.Frame(row, bg="#FFFFFF", bd=2, relief="sunken")
            price_box.pack(side=tk.RIGHT, fill=tk.X, expand=True)

            price_lbl = tk.Label(
                price_box, text="조회 중...", font=("굴림체", 9, "bold"),
                fg=COLOR_EQUAL, bg="#FFFFFF", anchor="e", width=14, padx=4
            )
            price_lbl.pack(fill=tk.BOTH, expand=True)

            self.label_items[code] = {
                "name_lbl": name_lbl,
                "price_lbl": price_lbl
            }

    def add_ticker_dialog(self):
        try:
            user_input = simpledialog.askstring(
                "종목 추가", 
                "회사 이름, 6자리 코드(005930), 또는 티커(AAPL):", 
                parent=self.root
            )
        except Exception:
            return

        if not user_input or not user_input.strip():
            return

        code, name = search_stock(user_input.strip())
        if not code:
            messagebox.showerror(
                "입력 오류",
                f"'{user_input}' 종목을 찾을 수 없습니다.\n정확한 회사 이름이나 6자리 코드를 입력해주세요.",
                parent=self.root
            )
            return

        if any(item.get("code") == code for item in self.items):
            messagebox.showinfo("알림", f"[{name}] 이미 등록된 종목입니다.", parent=self.root)
            return

        success, _, err_msg, _ = fetch_stock_price(code)
        if not success:
            messagebox.showerror(
                "조회 실패",
                f"[{name} ({code})]\n\n{err_msg}\n\n몇 초 뒤에 재시도 해주세요..",
                parent=self.root
            )
            return

        self.items.append({"code": code, "name": name})
        self.rebuild_ui()
        self.save_current_state()

    def remove_ticker_dialog(self):
        try:
            target = simpledialog.askstring(
                "종목 삭제", 
                "삭제할 회사 이름, 종목 코드 또는 티커를 입력하세요:",
                parent=self.root
            )
        except Exception:
            return

        if not target or not target.strip():
            return

        query_upper = target.strip().upper()
        found_item = None

        for item in list(self.items):
            item_name = item.get("name", "").strip().upper()
            item_code = item.get("code", "").strip().upper()

            if item_name == query_upper or item_code == query_upper:
                found_item = item
                break

        if found_item:
            self.items.remove(found_item)
            self.rebuild_ui()
            self.save_current_state()
            messagebox.showinfo("삭제 완료", f"[{found_item['name']}] 목록에서 삭제되었습니다.", parent=self.root)
        else:
            messagebox.showwarning("삭제 실패", f"'{target.strip()}' 종목을 현재 등록 목록에서 찾을 수 없습니다.", parent=self.root)

    def worker_loop(self):
        while True:
            try:
                for item in list(self.items):
                    code = item["code"]
                    success, name, text, color = fetch_stock_price(code)
                    if code in self.label_items:
                        self.root.after(0, self.update_row_ui, code, name, text, color)
                    time.sleep(0.3)
            except Exception as e:
                print("worker_loop 일시 예외 (자동 복구):", e)
            time.sleep(3)

    def update_row_ui(self, code, name, price_text, color):
        if code in self.label_items:
            self.label_items[code]["name_lbl"].config(text=name)
            self.label_items[code]["price_lbl"].config(text=price_text, fg=color)


if __name__ == "__main__":
    init_stock_master()
    app = tk.Tk()
    Win98StockWidget(app)
    app.mainloop()