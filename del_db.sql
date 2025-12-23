USE email_ocr_db;

-- 暂时关闭外键检查
SET FOREIGN_KEY_CHECKS=0;

-- 清空 OCR识别结果表
TRUNCATE TABLE ocr_results;

-- 清空 邮件图片明细表
TRUNCATE TABLE mail_image_details;

-- 重新启用外键检查
SET FOREIGN_KEY_CHECKS=1;
