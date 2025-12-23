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
DOWNLOAD_DIR = Path("download")
EXPORT_DIR = Path("exports")

DOWNLOAD_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

ALIYUN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_ID = "qwen-vl-ocr-latest"
IMAP_SERVER = "imap.163.com"

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Abs)*m12d31",
    "database": "email_ocr_db"
}

# --- 2. 独立配置文件管理 ---

def save_all_configs(user_email, auth_code, api_key):
    """分别保存邮箱和 API Key 到两个 JSON 文件，并返回成功提示"""
    try:
        # 保存邮箱配置
        with open(EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"email": user_email, "auth_code": auth_code}, f, indent=4)
        
        # 保存 API Key 配置
        with open(LLM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"api_key": api_key}, f, indent=4)
            
        # 弹窗提示 (Gradio 4.x+)
        gr.Info("🎉 配置文件已持久化到本地！")
        
        # 返回文字提示，带上时间戳，方便用户确认是刚刚保存的
        now = datetime.datetime.now().strftime("%H:%M:%S")
        return f"### ✅ 保存成功！\n**更新时间：** `{now}`\n\n文件已存至：`mail_account.json` & `LLM_Api_Key.json`"
    except Exception as e:
        gr.Warning(f"保存出错: {str(e)}")
        return f"❌ **保存失败**：{str(e)}"

def load_configs():
    email_val, auth_val, api_val = "", "", ""
    if EMAIL_CONFIG_FILE.exists():
        try:
            with open(EMAIL_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                email_val, auth_val = data.get("email", ""), data.get("auth_code", "")
        except: pass
    if LLM_CONFIG_FILE.exists():
        try:
            with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
                api_val = json.load(f).get("api_key", "")
        except: pass
    return email_val, auth_val, api_val

# --- 3. 业务逻辑 (OCR, 下载等) ---
def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def execute_db(query, params=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def generate_seq_id():
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT COUNT(*) FROM mail_image_details WHERE image_id LIKE %s"
    cursor.execute(query, (f"{today_str}%",))
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return f"{today_str}{(count + 1):05d}"

def decode_str(s):
    if not s: return ""
    try:
        decoded_list = decode_header(s)
        result = ""
        for value, charset in decoded_list:
            if isinstance(value, bytes):
                result += value.decode(charset if charset else "utf-8", errors="ignore")
            else: result += value
        return result
    except: return str(s)

def extract_json(text):
    try:
        match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match: return json.loads(match.group(1))
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return json.loads(match.group())
        return None
    except: return None

def download_emails(user_email, auth_code):
    if not user_email or not auth_code:
        return "❌ 错误：请先设置邮箱配置！", []
    new_ids = []
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        mail.login(user_email, auth_code)
        imaplib.Commands['ID'] = ('AUTH')
        mail._simple_command('ID', '("name" "python-app")')
        mail.select("INBOX", readonly=False)
        status, messages = mail.search(None, 'UNSEEN')
        mail_ids = messages[0].split()
        if not mail_ids: return "📬 暂无新邮件附件。", []
        for m_id in mail_ids:
            res, msg_data = mail.fetch(m_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_str(msg["Subject"])
                    for part in msg.walk():
                        if part.get('Content-Disposition') is None: continue
                        filename = part.get_filename()
                        if filename:
                            filename = decode_str(filename)
                            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                                img_id = generate_seq_id()
                                filepath = DOWNLOAD_DIR / f"{img_id}_{filename}"
                                with open(filepath, "wb") as f: f.write(part.get_payload(decode=True))
                                sql = "INSERT INTO mail_image_details (image_id, mail_id, mail_title, file_name, file_path, status, download_time) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                                execute_db(sql, (img_id, msg.get("Message-ID"), subject, filename, str(filepath), '未识别', datetime.datetime.now()))
                                new_ids.append(img_id)
            mail.store(m_id, '+FLAGS', '\\Seen')
        return f"✨ 成功下载 {len(new_ids)} 个附件。", new_ids
    except Exception as e: return f"❌ 邮件连接失败: {str(e)}", []
    finally:
        if mail: 
            try: mail.logout()
            except: pass

def run_ocr_process(id_list, api_key):
    if not id_list: return "⚠️ 当前批次没有图片。"
    if not api_key: return "❌ 错误：请先配置 API KEY！"
    dynamic_client = OpenAI(api_key=api_key, base_url=ALIYUN_BASE_URL)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    format_strings = ','.join(['%s'] * len(id_list))
    cursor.execute(f"SELECT * FROM mail_image_details WHERE image_id IN ({format_strings}) AND status != '已识别'", tuple(id_list))
    rows = cursor.fetchall()
    PROMPT = '提取JSON：{"交易时间": "", "付款户名": "", "收款户名": "", "收款金额": ""}'
    success_count = 0
    for row in rows:
        img_id = row['image_id']
        try:
            with open(row['file_path'], "rb") as f: base64_img = base64.b64encode(f.read()).decode('utf-8')
            completion = dynamic_client.chat.completions.create(
                model=MODEL_ID,
                messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}, {"type": "text", "text": PROMPT}]}]
            )
            data = extract_json(completion.choices[0].message.content)
            if data:
                execute_db("REPLACE INTO ocr_results (image_id, trans_time, payer, payee, amount) VALUES (%s, %s, %s, %s, %s)",
                           (img_id, data.get('交易时间'), data.get('付款户名'), data.get('收款户名'), data.get('收款金额')))
                execute_db("UPDATE mail_image_details SET status = '已识别', ocr_time = %s WHERE image_id = %s", (datetime.datetime.now(), img_id))
                success_count += 1
        except: execute_db("UPDATE mail_image_details SET status = '识别失败' WHERE image_id = %s", (img_id,))
    cursor.close()
    conn.close()
    return f"✅ OCR完成！成功: {success_count} 条。"

def get_display_data(id_list):
    if not id_list: return pd.DataFrame(columns=["序号", "图片ID", "状态", "交易时间", "付款用户", "收款户名", "收款金额", "附件名", "本地路径"])
    conn = get_db_connection()
    format_strings = ','.join(['%s'] * len(id_list))
    query = f"SELECT m.image_id AS 图片ID, m.status AS 状态, r.trans_time AS 交易时间, r.payer AS 付款用户, r.payee AS 收款户名, r.amount AS 收款金额, m.file_name AS 附件名, m.file_path AS 本地路径 FROM mail_image_details m LEFT JOIN ocr_results r ON m.image_id = r.image_id WHERE m.image_id IN ({format_strings}) ORDER BY m.image_id ASC"
    df = pd.read_sql(query, conn, params=tuple(id_list))
    conn.close()
    df.insert(0, "序号", range(1, len(df) + 1))
    return df

def export_to_xls(id_list):
    df = get_display_data(id_list)
    if df.empty: return None
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    existing_files = list(EXPORT_DIR.glob(f"{today_str}*.xlsx"))
    filename = f"{today_str}{(len(existing_files) + 1):03d}.xlsx"
    save_path = EXPORT_DIR / filename
    df[["序号", "交易时间", "付款用户", "收款户名", "收款金额"]].to_excel(save_path, index=False)
    return str(save_path)

def on_select_row(evt: gr.SelectData, df):
    if df is None or df.empty: return None
    try:
        row_idx = evt.index[0]
        if row_idx < len(df):
            path = df.iloc[row_idx]["本地路径"]
            return path if os.path.exists(path) else None
    except: return None

# --- 4. Gradio UI ---
with gr.Blocks(theme=gr.themes.Soft(), title="自动OCR系统") as demo:
    batch_ids = gr.State([])
    init_email, init_auth, init_api = load_configs()

    gr.Markdown("# 📩 财务转账截图自动处理系统")
    
    with gr.Accordion("⚙️ 系统参数设置", open=not init_email):
        with gr.Row():
            input_email = gr.Textbox(label="163邮箱账号", value=init_email, placeholder="example@163.com")
            input_auth = gr.Textbox(label="163授权码", value=init_auth, placeholder="16位授权码", type="password")
        with gr.Row():
            input_api_key = gr.Textbox(label="阿里 API KEY", value=init_api, placeholder="sk-...", type="password")
        
        btn_save_config = gr.Button("💾 保存所有配置到本地文件", variant="secondary")
        # 增加一个专门展示保存结果的 Markdown 区域
        config_status = gr.Markdown(visible=True)

    with gr.Row():
        with gr.Column(scale=3):
            with gr.Row():
                btn_mail = gr.Button("📥 1. 下载新邮件", variant="primary")
                btn_ocr = gr.Button("🔍 2. 开始AI识别", variant="secondary")
                btn_export = gr.Button("📤 3. 导出报表", variant="stop")
            status_msg = gr.Textbox(label="系统通知", interactive=False)
            gr.Markdown("### 📋 本次任务清单 (点击行预览图片)")
            table_display = gr.Dataframe(interactive=False, wrap=True)
            file_output = gr.File(label="Excel下载")
        with gr.Column(scale=2):
            gr.Markdown("### 🖼️ 原图核对")
            image_preview = gr.Image(label="选中行原图", type="filepath")

    # --- 交互绑定 ---
    # 点击保存按钮，调用带反馈的函数
    btn_save_config.click(
        fn=save_all_configs, 
        inputs=[input_email, input_auth, input_api_key], 
        outputs=[config_status]
    )

    def flow_download(e, a):
        msg, ids = download_emails(e, a)
        df = get_display_data(ids)
        return msg, ids, df
    btn_mail.click(flow_download, inputs=[input_email, input_auth], outputs=[status_msg, batch_ids, table_display])
    
    def flow_ocr(ids, api_val):
        msg = run_ocr_process(ids, api_val)
        df = get_display_data(ids)
        return msg, df
    btn_ocr.click(flow_ocr, inputs=[batch_ids, input_api_key], outputs=[status_msg, table_display])
    
    btn_export.click(fn=export_to_xls, inputs=[batch_ids], outputs=[file_output])
    table_display.select(fn=on_select_row, inputs=[table_display], outputs=[image_preview])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
