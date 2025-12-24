import gradio as gr
import imaplib
import email
import os
import json
import re
import datetime
import mysql.connector
import pandas as pd
import base64
from openai import OpenAI
from email.header import decode_header
from pathlib import Path

# --- 1. 基础配置与路径 ---
EMAIL_CONFIG_FILE = Path("mail_account.json")
LLM_CONFIG_FILE = Path("LLM_Api_Key.json")
DB_CONFIG_FILE = Path("db_config.json")
DOWNLOAD_DIR = Path("download")
EXPORT_DIR = Path("exports")

DOWNLOAD_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

ALIYUN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_ID = "qwen-vl-ocr-latest"
IMAP_SERVER = "imap.163.com"

# --- 2. 配置文件管理 ---

def save_all_configs(user_email, auth_code, api_key, db_host, db_user, db_pass, db_name):
    try:
        with open(EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"email": user_email.strip(), "auth_code": auth_code.strip()}, f, indent=4)
        with open(LLM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"api_key": api_key.strip()}, f, indent=4)
        db_data = {
            "host": db_host.strip(), "user": db_user.strip(), "password": db_pass.strip(), "database": db_name.strip()
        }
        with open(DB_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, indent=4)
        gr.Info("🎉 配置已成功保存！")
        return f"### ✅ 保存成功\n**时间：** `{datetime.datetime.now().strftime('%H:%M:%S')}`"
    except Exception as e:
        gr.Warning(f"保存失败: {str(e)}")
        return f"❌ 失败: {str(e)}"

def load_configs():
    e, a, api = "", "", ""
    dh, du, dp, dn = "localhost", "root", "", "email_ocr_db"
    def safe_load(path):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f: return json.load(f)
            except: pass
        return {}
    d_e = safe_load(EMAIL_CONFIG_FILE)
    e, a = d_e.get("email", ""), d_e.get("auth_code", "")
    api = safe_load(LLM_CONFIG_FILE).get("api_key", "")
    d_b = safe_load(DB_CONFIG_FILE)
    return e, a, api, d_b.get("host", dh), d_b.get("user", du), d_b.get("password", dp), d_b.get("database", dn)

# --- 3. 核心业务逻辑 ---

def get_db_connection(host, user, password, database):
    return mysql.connector.connect(host=host, user=user, password=password, database=database, connection_timeout=5)

def generate_seq_id(db_info):
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    try:
        conn = get_db_connection(*db_info)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM mail_image_details WHERE image_id LIKE %s", (f"{today_str}%",))
        count = cursor.fetchone()[0]
        conn.close()
        return f"{today_str}{(count + 1):05d}"
    except:
        return f"{today_str}_{int(datetime.datetime.now().timestamp())}"

def download_emails(user_email, auth_code, db_host, db_user, db_pass, db_name):
    db_info = (db_host, db_user, db_pass, db_name)
    if not user_email or not auth_code: return "❌ 请先配置邮箱", [], None
    new_ids, mail = [], None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        mail.login(user_email, auth_code)
        imaplib.Commands['ID'] = ('AUTH')
        mail._simple_command('ID', '("name" "my-client" "version" "1.0.0")')
        mail.select("INBOX")
        status, messages = mail.search(None, 'UNSEEN')
        mail_ids = messages[0].split()
        if not mail_ids: return "📬 暂无新邮件。", [], get_display_data([], db_info)
        
        conn = get_db_connection(*db_info)
        for m_id in mail_ids:
            res, msg_data = mail.fetch(m_id, "(RFC822)")
            for part in email.message_from_bytes(msg_data[0][1]).walk():
                if part.get('Content-Disposition') is None: continue
                filename = decode_header(part.get_filename() or "")[0][0]
                if isinstance(filename, bytes): filename = filename.decode()
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    img_id = generate_seq_id(db_info)
                    filepath = DOWNLOAD_DIR / f"{img_id}_{filename}"
                    with open(filepath, "wb") as f: f.write(part.get_payload(decode=True))
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO mail_image_details (image_id, file_name, file_path, status, download_time) VALUES (%s, %s, %s, %s, %s)",
                                   (img_id, filename, str(filepath), '未识别', datetime.datetime.now()))
                    conn.commit()
                    new_ids.append(img_id)
            mail.store(m_id, '+FLAGS', '\\Seen')
        conn.close()
        mail.logout()
        return f"✨ 成功下载 {len(new_ids)} 个附件。", new_ids, get_display_data(new_ids, db_info)
    except Exception as e: return f"❌ 失败: {str(e)}", [], None

def run_ocr_process(id_list, api_key, db_host, db_user, db_pass, db_name):
    db_info = (db_host, db_user, db_pass, db_name)
    if not id_list: return "⚠️ 无图片", None
    client = OpenAI(api_key=api_key, base_url=ALIYUN_BASE_URL)
    conn = get_db_connection(*db_info)
    cursor = conn.cursor(dictionary=True)
    format_strings = ','.join(['%s'] * len(id_list))
    cursor.execute(f"SELECT * FROM mail_image_details WHERE image_id IN ({format_strings})", tuple(id_list))
    rows = cursor.fetchall()
    success = 0
    for row in rows:
        if row['status'] == '已识别': continue
        try:
            with open(row['file_path'], "rb") as f: img_b64 = base64.b64encode(f.read()).decode()
            resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":[{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},{"type":"text","text":"提取JSON：交易时间, 付款户名, 收款户名, 收款金额"}]}])
            data = json.loads(re.search(r'\{.*\}', resp.choices[0].message.content, re.DOTALL).group())
            cursor.execute("REPLACE INTO ocr_results (image_id, trans_time, payer, payee, amount) VALUES (%s, %s, %s, %s, %s)", (row['image_id'], data.get('交易时间'), data.get('付款户名'), data.get('收款户名'), data.get('收款金额')))
            cursor.execute("UPDATE mail_image_details SET status='已识别', ocr_time=%s WHERE image_id=%s", (datetime.datetime.now(), row['image_id']))
            conn.commit()
            success += 1
        except: pass
    conn.close()
    return f"✅ 识别成功 {success} 条。", get_display_data(id_list, db_info)

def get_display_data(id_list, db_info):
    if not id_list: return pd.DataFrame(columns=["序号", "图片ID", "状态", "交易时间", "付款用户", "收款户名", "收款金额", "附件名", "本地路径"])
    conn = get_db_connection(*db_info)
    format_strings = ','.join(['%s'] * len(id_list))
    query = f"SELECT m.image_id AS 图片ID, m.status AS 状态, r.trans_time AS 交易时间, r.payer AS 付款用户, r.payee AS 收款户名, r.amount AS 收款金额, m.file_name AS 附件名, m.file_path AS 本地路径 FROM mail_image_details m LEFT JOIN ocr_results r ON m.image_id = r.image_id WHERE m.image_id IN ({format_strings})"
    df = pd.read_sql(query, conn, params=tuple(id_list))
    conn.close()
    df.insert(0, "序号", range(1, len(df) + 1))
    return df

# --- 4. 导出规则优化：YYYYMMDD000x ---
def export_to_xls(id_list, db_host, db_user, db_pass, db_name):
    db_info = (db_host, db_user, db_pass, db_name)
    df = get_display_data(id_list, db_info)
    if df.empty: return None
    
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    # 扫描已有的当天导出文件，确定序号
    existing_files = list(EXPORT_DIR.glob(f"{today_str}*.xlsx"))
    max_num = 0
    for f in existing_files:
        match = re.search(rf"{today_str}(\d{{4}})", f.name)
        if match: max_num = max(max_num, int(match.group(1)))
    
    new_filename = f"{today_str}{(max_num + 1):04d}.xlsx"
    save_path = EXPORT_DIR / new_filename
    df[["序号", "交易时间", "付款用户", "收款户名", "收款金额"]].to_excel(save_path, index=False)
    return str(save_path)

# --- 5. UI 辅助函数 ---
def toggle_pass_visibility(current_state):
    new_state = not current_state
    return gr.update(type="text" if new_state else "password"), gr.update(value="隐藏密码" if new_state else "显示密码"), new_state

def on_select_row(evt: gr.SelectData, df):
    if df is not None and not df.empty and evt.index[0] < len(df):
        path = df.iloc[evt.index[0]]["本地路径"]
        if os.path.exists(path): return path
    return None

# --- 6. UI 界面 ---
with gr.Blocks(theme=gr.themes.Soft(), title="自动OCR系统") as demo:
    batch_ids = gr.State([])
    vis_auth, vis_api, vis_db = gr.State(False), gr.State(False), gr.State(False)
    e, a, api, dh, du, dp, dn = load_configs()

    gr.Markdown("# 📩 财务转账截图自动处理系统")
    
    with gr.Accordion("⚙️ 系统参数设置", open=not e):
        with gr.Tabs():
            with gr.Tab("📧 邮箱配置"):
                in_email = gr.Textbox(label="163邮箱", value=e)
                with gr.Row(equal_height=True):
                    in_auth = gr.Textbox(label="授权码", value=a, type="password", scale=8)
                    btn_auth = gr.Button("显示密码", scale=1)
            with gr.Tab("🤖 AI配置"):
                with gr.Row(equal_height=True):
                    in_api = gr.Textbox(label="阿里 API KEY", value=api, type="password", scale=8)
                    btn_api = gr.Button("显示密码", scale=1)
            with gr.Tab("💾 数据库配置"):
                with gr.Row():
                    in_host = gr.Textbox(label="Host", value=dh)
                    in_user = gr.Textbox(label="User", value=du)
                with gr.Row(equal_height=True):
                    in_pass = gr.Textbox(label="Password", value=dp, type="password", scale=4)
                    btn_db = gr.Button("显示密码", scale=1)
                    in_db = gr.Textbox(label="Database", value=dn, scale=4)
        btn_save = gr.Button("💾 保存所有配置", variant="secondary")
        save_msg = gr.Markdown()

    with gr.Row():
        with gr.Column(scale=3):
            with gr.Row():
                btn_mail = gr.Button("📥 1. 下载新邮件", variant="primary")
                btn_ocr = gr.Button("🔍 2. 开始AI识别", variant="secondary")
                btn_export = gr.Button("📤 3. 导出报表", variant="stop")
            status_msg = gr.Textbox(label="系统通知", interactive=False)
            table_display = gr.Dataframe(label="任务清单", interactive=False)
            file_output = gr.File(label="Excel下载")
        with gr.Column(scale=2):
            image_preview = gr.Image(label="原图预览")

    # 显隐切换绑定
    btn_auth.click(toggle_pass_visibility, [vis_auth], [in_auth, btn_auth, vis_auth])
    btn_api.click(toggle_pass_visibility, [vis_api], [in_api, btn_api, vis_api])
    btn_db.click(toggle_pass_visibility, [vis_db], [in_pass, btn_db, vis_db])
    
    # 业务绑定
    btn_save.click(save_all_configs, [in_email, in_auth, in_api, in_host, in_user, in_pass, in_db], [save_msg])
    btn_mail.click(download_emails, [in_email, in_auth, in_host, in_user, in_pass, in_db], [status_msg, batch_ids, table_display])
    btn_ocr.click(run_ocr_process, [batch_ids, in_api, in_host, in_user, in_pass, in_db], [status_msg, table_display])
    btn_export.click(export_to_xls, [batch_ids, in_host, in_user, in_pass, in_db], [file_output])
    table_display.select(on_select_row, [table_display], [image_preview])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
