-- 1. 创建数据库
CREATE DATABASE IF NOT EXISTS email_ocr_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE email_ocr_db;

-- 如果需要彻底重置，可以取消下面两行的注释（警告：这会删除所有现有数据）
-- DROP TABLE IF EXISTS ocr_results;
-- DROP TABLE IF EXISTS mail_image_details;

-- 2. 创建：邮件图片明细表
-- 存储下载的附件信息及处理进度
CREATE TABLE IF NOT EXISTS mail_image_details (
    id INT AUTO_INCREMENT PRIMARY KEY,
    image_id VARCHAR(50) NOT NULL COMMENT '系统生成的唯一图片ID',
    mail_id VARCHAR(150) COMMENT '邮件Message-ID',
    mail_title VARCHAR(255) COMMENT '邮件主题',
    file_name VARCHAR(255) COMMENT '原始文件名',
    file_path VARCHAR(500) COMMENT '本地磁盘存储绝对路径',
    -- 核心修正：使用 VARCHAR 替代 ENUM，容纳 '识别失败' 等更多状态
    status VARCHAR(20) DEFAULT '未识别' COMMENT '状态: 未识别/已识别/识别失败',
    download_time DATETIME COMMENT '下载入库时间',
    ocr_time DATETIME COMMENT 'OCR完成时间',
    UNIQUE KEY uk_image_id (image_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 创建：OCR识别结果表
-- 存储从图片中提取的结构化财务数据
CREATE TABLE IF NOT EXISTS ocr_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    image_id VARCHAR(50) NOT NULL COMMENT '关联明细表的图片ID',
    trans_time VARCHAR(100) COMMENT '提取的交易时间',
    payer VARCHAR(255) COMMENT '提取的付款人信息',
    payee VARCHAR(255) COMMENT '提取的收款户名',
    amount VARCHAR(50) COMMENT '提取的收款金额',
    create_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    -- 建立外键关联，确保数据一致性
    CONSTRAINT fk_mail_image FOREIGN KEY (image_id) REFERENCES mail_image_details(image_id) ON DELETE CASCADE,
    UNIQUE KEY uk_res_image_id (image_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
